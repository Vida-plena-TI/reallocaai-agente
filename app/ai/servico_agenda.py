"""Camada de serviço da agenda (Fase 5a).

Ponte entre a IA (Fase 5b, ainda não implementada) e a engine (Fase 4): funções
Python puras que serão a base das tools da IA. Nada aqui instancia uma fonte de
dados ou de continuidade — quem chama decide qual implementação usar, o que
mantém o módulo testável sem rede e sem depender de qual fase futura escolher.
"""

import logging
from datetime import date, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.data_sources.base import ScheduleDataSource
from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import (
    Especialidade,
    ItemSolicitacao,
    Paciente,
    SolicitacaoAtendimento,
    normalizar_id,
)
from app.engine.disponibilidade import SlotDisponivel, listar_disponibilidade
from app.engine.encaixe import (
    OpcaoComCenario,
    OpcaoEncaixe,
    buscar_alternativas,
    buscar_melhor_encaixe,
    sugerir_realocacao,
)
from app.engine.ocupacao import RelatorioOcupacaoDoDia, construir_relatorio_ocupacao_do_dia

logger = logging.getLogger(__name__)


def buscar_paciente(fonte: ScheduleDataSource, data: date, nome_ou_id: str) -> Paciente | None:
    """Paciente da agenda do dia cujo id normalizado casa com `nome_ou_id`.

    Sem fuzzy matching (decisão já tomada na Fase 3): só correspondência exata
    do id normalizado.
    """
    alvo = normalizar_id(nome_ou_id)
    return next(
        (paciente for paciente in fonte.listar_pacientes(data) if paciente.id == alvo), None
    )


class ItemDemandaBruta(BaseModel):
    """Um item de demanda antes de passar pela resolução de continuidade."""

    model_config = ConfigDict(frozen=True)

    especialidade: Especialidade
    duracao_em_slots: int = Field(ge=1)
    profissional_id: str | None = None


def montar_solicitacao(
    fonte: ScheduleDataSource,
    continuidade: ContinuidadeDataSource,
    paciente_id: str,
    data: date,
    itens: list[ItemDemandaBruta],
    horario_minimo: time,
    horario_desejado: time | None,
) -> SolicitacaoAtendimento:
    """Monta a `SolicitacaoAtendimento` resolvendo continuidade item a item.

    Um item sem `profissional_id` consulta
    `continuidade.profissional_habitual` e o preenche se houver retorno; um
    item que já veio com `profissional_id` explícito nunca é sobrescrito.
    """
    itens_resolvidos = [
        ItemSolicitacao(
            especialidade=item.especialidade,
            duracao_em_slots=item.duracao_em_slots,
            profissional_id=item.profissional_id
            or continuidade.profissional_habitual(paciente_id, item.especialidade),
        )
        for item in itens
    ]
    return SolicitacaoAtendimento(
        paciente_id=paciente_id,
        data=data,
        itens=itens_resolvidos,
        horario_minimo=horario_minimo,
        horario_desejado=horario_desejado,
    )


class ResultadoExato(BaseModel):
    """Achou vaga exatamente no horário desejado, ou a melhor vaga possível
    quando não havia horário desejado específico.
    """

    model_config = ConfigDict(frozen=True)

    tipo: Literal["exato"] = "exato"
    opcao: OpcaoEncaixe


class ResultadoAlternativas(BaseModel):
    """Não achou vaga no horário desejado, mas achou alternativas."""

    model_config = ConfigDict(frozen=True)

    tipo: Literal["alternativas"] = "alternativas"
    opcoes: list[OpcaoComCenario] = Field(min_length=1)


class ResultadoNenhum(BaseModel):
    """Nenhuma opção viável no dia."""

    model_config = ConfigDict(frozen=True)

    tipo: Literal["nenhum"] = "nenhum"


#: União discriminada pelo campo `tipo` — os três estados mutuamente exclusivos
#: de `buscar_encaixe`.
ResultadoBuscaEncaixe = Annotated[
    ResultadoExato | ResultadoAlternativas | ResultadoNenhum, Field(discriminator="tipo")
]


def buscar_encaixe(
    fonte: ScheduleDataSource,
    continuidade: ContinuidadeDataSource,
    paciente_id: str,
    data: date,
    itens: list[ItemDemandaBruta],
    horario_minimo: time,
    horario_desejado: time | None,
) -> ResultadoBuscaEncaixe:
    """Orquestra a busca de encaixe: monta a solicitação e delega à engine.

    `buscar_melhor_encaixe` sempre devolve o melhor esforço do dia, mesmo
    quando `horario_desejado` estava indisponível — por isso "exato" só é
    devolvido quando a opção encontrada começa no horário pedido (ou quando
    não havia horário pedido: nesse caso não existe "exato" a comparar, e a
    melhor vaga do dia já é a resposta). Havia horário pedido e a opção
    encontrada foi outra (ou nenhuma foi encontrada) -> tenta alternativas
    ("alternativas", ou "nenhum" se a lista vier vazia). Pedido já era
    genérico (sem `horario_desejado`) e nada foi encontrado -> "nenhum"
    direto, já que não existe "alternativa" para um pedido sem horário certo.
    """
    solicitacao = montar_solicitacao(
        fonte, continuidade, paciente_id, data, itens, horario_minimo, horario_desejado
    )

    opcao = buscar_melhor_encaixe(fonte, solicitacao)
    if opcao is not None and (
        solicitacao.horario_desejado is None or opcao.horario_inicio == solicitacao.horario_desejado
    ):
        return ResultadoExato(opcao=opcao)

    if solicitacao.horario_desejado is None:
        return ResultadoNenhum()

    opcoes = buscar_alternativas(fonte, solicitacao)
    if opcoes:
        return ResultadoAlternativas(opcoes=opcoes)
    return ResultadoNenhum()


def consultar_disponibilidade_do_dia(
    fonte: ScheduleDataSource,
    data: date,
    especialidade: Especialidade | None = None,
    profissional_id: str | None = None,
    sala_id: str | None = None,
) -> list[SlotDisponivel]:
    """Delega para `listar_disponibilidade` (Fase 4a)."""
    return listar_disponibilidade(
        fonte,
        data,
        especialidade=especialidade,
        profissional_id=profissional_id,
        sala_id=sala_id,
    )


def consultar_ocupacao_do_dia(fonte: ScheduleDataSource, data: date) -> RelatorioOcupacaoDoDia:
    """Delega para `construir_relatorio_ocupacao_do_dia` (Fase 4a)."""
    return construir_relatorio_ocupacao_do_dia(fonte, data)


def sugerir_realocacao_por_id(
    fonte: ScheduleDataSource,
    data: date,
    atendimento_id: str,
    nova_duracao_em_slots: int | None = None,
) -> OpcaoEncaixe | None:
    """Sugere realocação para o `Atendimento` de `atendimento_id`.

    Devolve `None` (com um warning logado) quando esse id não existe na agenda
    do dia.
    """
    atendimento = next(
        (item for item in fonte.listar_atendimentos(data) if item.id == atendimento_id), None
    )
    if atendimento is None:
        logger.warning(
            "Atendimento %r não encontrado na agenda de %s: nada para realocar.",
            atendimento_id,
            data.isoformat(),
        )
        return None

    return sugerir_realocacao(fonte, atendimento, nova_duracao_em_slots)
