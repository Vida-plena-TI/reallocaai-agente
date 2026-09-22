"""Rotas HTTP de consulta direta à agenda e de conversa com o agente.

As rotas de consulta direta (Fase 6a) são só leitura: nenhuma altera a
agenda. `/agenda/chat` (Fase 6b) conversa com o agente de IA, que só lê a
agenda através das mesmas tools já usadas pelo script manual (Fase 5b) —
nenhuma escrita acontece por aqui também. `/relatorio/enviar` (Fase 7) é a
exceção quanto a efeito colateral: envia um e-mail, mas continua sem alterar
a agenda. Todas as rotas deste router exigem `X-API-Key` válida (ver
`validar_api_key`); a única rota pública da aplicação é `/health`, definida
em `app.main`.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage

from app.ai.agente import criar_agente, perguntar
from app.ai.servico_agenda import consultar_disponibilidade_do_dia, consultar_ocupacao_do_dia
from app.api.dependencies import (
    obter_armazenamento_conversas,
    obter_chat_model,
    obter_continuidade,
    obter_fonte,
    validar_api_key,
)
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    EnviarRelatorioRequest,
    EnviarRelatorioResponse,
    MensagemHistoricoResponse,
    OcupacaoItemResponse,
    OcupacaoResponse,
    SlotDisponivelResponse,
)
from app.api.sessoes import ArmazenamentoConversas
from app.config import Settings, get_settings
from app.data_sources.base import ScheduleDataSource
from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import Especialidade
from app.engine.ocupacao import OcupacaoAgregada, RelatorioOcupacaoDoDia
from app.reports.envio import enviar_relatorio_por_email
from app.reports.exceptions import ReportsEnvioError

router = APIRouter(dependencies=[Depends(validar_api_key)])

_MENSAGEM_CONVERSA_INEXISTENTE = (
    "conversa não encontrada ou expirada, inicie uma nova sem informar conversa_id"
)


def _rotulo_especialidade(especialidade: Especialidade) -> str:
    """`terapia_ocupacional` -> `Terapia Ocupacional`."""
    return especialidade.value.replace("_", " ").title()


def _item_ocupacao(rotulo: str, agregada: OcupacaoAgregada) -> OcupacaoItemResponse:
    return OcupacaoItemResponse(
        rotulo=rotulo,
        slots_escalados=agregada.slots_escalados,
        slots_ocupados=agregada.slots_ocupados,
        percentual=agregada.percentual,
        abaixo_da_meta=agregada.abaixo_da_meta,
    )


def _construir_ocupacao_response(
    fonte: ScheduleDataSource, data: date, relatorio: RelatorioOcupacaoDoDia
) -> OcupacaoResponse:
    """Resolve sala e especialidade para rótulos legíveis, ordenados de forma estável."""
    nomes_das_salas = {sala.id: sala.nome for sala in fonte.listar_salas(data)}
    por_sala = [
        _item_ocupacao(nomes_das_salas.get(sala_id, sala_id), agregada)
        for sala_id, agregada in sorted(relatorio.por_sala().items())
    ]
    por_especialidade = [
        _item_ocupacao(_rotulo_especialidade(especialidade), agregada)
        for especialidade, agregada in sorted(
            relatorio.por_especialidade().items(), key=lambda par: par[0].value
        )
    ]
    return OcupacaoResponse(
        data=data.isoformat(), por_sala=por_sala, por_especialidade=por_especialidade
    )


@router.get("/agenda/disponibilidade", response_model=list[SlotDisponivelResponse])
def obter_disponibilidade(
    data: date,
    fonte: Annotated[ScheduleDataSource, Depends(obter_fonte)],
    especialidade: Especialidade | None = None,
    profissional_id: str | None = None,
    sala_id: str | None = None,
) -> list[SlotDisponivelResponse]:
    """Slots livres do dia, com filtros opcionais de especialidade, profissional e sala."""
    slots = consultar_disponibilidade_do_dia(
        fonte,
        data,
        especialidade=especialidade,
        profissional_id=profissional_id,
        sala_id=sala_id,
    )
    nomes_das_salas = {sala.id: sala.nome for sala in fonte.listar_salas(data)}
    nomes_dos_profissionais = {
        profissional.id: profissional.nome for profissional in fonte.listar_profissionais(data)
    }
    return [
        SlotDisponivelResponse(
            horario=disponivel.slot.hora_inicio.strftime("%H:%M"),
            sala=nomes_das_salas.get(disponivel.sala_id, disponivel.sala_id),
            profissional=nomes_dos_profissionais.get(
                disponivel.profissional_id, disponivel.profissional_id
            ),
            especialidade=_rotulo_especialidade(disponivel.especialidade),
        )
        for disponivel in slots
    ]


@router.get("/agenda/ocupacao", response_model=OcupacaoResponse)
def obter_ocupacao(
    data: date, fonte: Annotated[ScheduleDataSource, Depends(obter_fonte)]
) -> OcupacaoResponse:
    """Ocupação do dia, agregada por sala e por especialidade."""
    relatorio = consultar_ocupacao_do_dia(fonte, data)
    return _construir_ocupacao_response(fonte, data, relatorio)


def _papel_da_mensagem(mensagem: BaseMessage) -> str:
    return "usuario" if isinstance(mensagem, HumanMessage) else "agente"


@router.post(
    "/agenda/chat",
    response_model=ChatResponse,
    summary="Conversa com o agente de IA sobre a agenda",
    description=(
        "Envia uma mensagem ao agente RealocAI e devolve a resposta em texto. "
        "Omita `conversa_id` para iniciar uma conversa nova; informe o "
        "`conversa_id` devolvido numa resposta anterior para continuá-la, "
        "mantendo o histórico completo do diálogo. Conversas ficam em memória "
        "e expiram após um período de inatividade — se o `conversa_id` "
        "informado já tiver expirado, a resposta é 404 e uma nova conversa "
        "deve ser iniciada. A data de referência do agente é sempre a data "
        "atual no momento da chamada."
    ),
)
def conversar_com_agente(
    corpo: ChatRequest,
    fonte: Annotated[ScheduleDataSource, Depends(obter_fonte)],
    continuidade: Annotated[ContinuidadeDataSource, Depends(obter_continuidade)],
    chat_model: Annotated[BaseChatModel, Depends(obter_chat_model)],
    conversas: Annotated[ArmazenamentoConversas, Depends(obter_armazenamento_conversas)],
) -> ChatResponse:
    if corpo.conversa_id is not None:
        historico = conversas.obter_historico(corpo.conversa_id)
        if historico is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=_MENSAGEM_CONVERSA_INEXISTENTE
            )
        conversa_id = corpo.conversa_id
    else:
        historico = []
        conversa_id = conversas.criar_conversa()

    agente = criar_agente(fonte, continuidade, chat_model, data_referencia=date.today())
    try:
        resposta = perguntar(agente, [*historico, HumanMessage(corpo.mensagem)])
    except Exception as erro:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="erro ao processar a conversa com o agente, tente novamente",
        ) from erro

    conversas.registrar_troca(conversa_id, corpo.mensagem, resposta)
    return ChatResponse(conversa_id=conversa_id, resposta=resposta)


@router.get(
    "/agenda/chat/{conversa_id}",
    response_model=list[MensagemHistoricoResponse],
    summary="Histórico bruto de uma conversa",
    description=(
        "Devolve as mensagens trocadas com o agente numa conversa, na ordem "
        "em que aconteceram — útil para depuração e para uma futura interface "
        "recuperar uma conversa em andamento. 404 se o `conversa_id` não "
        "existir ou tiver expirado."
    ),
)
def obter_historico_da_conversa(
    conversa_id: str,
    conversas: Annotated[ArmazenamentoConversas, Depends(obter_armazenamento_conversas)],
) -> list[MensagemHistoricoResponse]:
    historico = conversas.obter_historico(conversa_id)
    if historico is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_MENSAGEM_CONVERSA_INEXISTENTE
        )
    return [
        MensagemHistoricoResponse(
            papel=_papel_da_mensagem(mensagem), conteudo=str(mensagem.content)
        )
        for mensagem in historico
    ]


@router.post(
    "/relatorio/enviar",
    response_model=EnviarRelatorioResponse,
    summary="Envia o relatório de ocupação do dia por e-mail",
    description=(
        "Monta o relatório de ocupação do dia (por sala e por especialidade, já "
        "calculado pela engine) e envia por e-mail via Resend. Omita `data` para "
        "usar a data de hoje, e `destinatarios` para usar a lista padrão "
        "configurada em REPORT_EMAIL_TO. Falhas no envio (Resend fora do ar, "
        "credencial inválida etc.) devolvem 502, sem detalhar o erro interno."
    ),
)
def enviar_relatorio(
    corpo: EnviarRelatorioRequest,
    fonte: Annotated[ScheduleDataSource, Depends(obter_fonte)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EnviarRelatorioResponse:
    data_efetiva = corpo.data if corpo.data is not None else date.today()
    destinatarios_efetivos = (
        corpo.destinatarios if corpo.destinatarios is not None else settings.report_email_to
    )

    try:
        enviar_relatorio_por_email(fonte, data_efetiva, corpo.destinatarios)
    except ReportsEnvioError as erro:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="erro ao enviar o relatório por e-mail, tente novamente",
        ) from erro

    return EnviarRelatorioResponse(enviado=True, destinatarios=destinatarios_efetivos)
