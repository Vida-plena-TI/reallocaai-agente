"""Camada de serviço da agenda (Fase 5a).

Ponte entre a IA (Fase 5b, ainda não implementada) e a engine (Fase 4): funções
Python puras que serão a base das tools da IA. Nada aqui instancia uma fonte de
dados ou de continuidade — quem chama decide qual implementação usar, o que
mantém o módulo testável sem rede e sem depender de qual fase futura escolher.
"""

import logging
from datetime import date, time
from difflib import SequenceMatcher
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import (
    MAPA_ALIAS_PROFISSIONAL,
    Especialidade,
    ItemSolicitacao,
    Paciente,
    Profissional,
    ScheduleDataSource,
    SolicitacaoAtendimento,
    dias_da_semana_de,
    normalizar_id,
)
from app.engine.carga_profissionais import CargaProfissionais, construir_carga_profissionais
from app.engine.disponibilidade import SlotDisponivel, listar_disponibilidade
from app.engine.encaixe import (
    OpcaoComCenario,
    OpcaoEncaixe,
    buscar_alternativas,
    buscar_melhor_encaixe,
    sugerir_realocacao,
)
from app.engine.ocupacao import RelatorioOcupacaoDoDia, construir_relatorio_ocupacao_do_dia
from app.engine.ocupacao_profissional import (
    OcupacaoSemanalProfissional,
    construir_ocupacao_semanal_profissional,
)

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


#: Similaridade mínima (`difflib.SequenceMatcher.ratio`) entre ids para um nome
#: da agenda entrar em `LocalizacaoPaciente.sugestoes`.
LIMIAR_NOME_PARECIDO = 0.85

#: Máximo de nomes parecidos devolvidos em `LocalizacaoPaciente.sugestoes`.
MAX_SUGESTOES_NOME = 3


class LocalizacaoPaciente(BaseModel):
    """Onde um paciente aparece na agenda da semana de uma data.

    `paciente_no_dia` é o registro da própria data consultada; `outros_dias`,
    os demais dias da semana em que o mesmo id aparece; `paciente_referencia`,
    um registro encontrado em qualquer dia (para mostrar nome e convênio).
    `sugestoes` só vem preenchida quando o paciente não aparece em dia nenhum,
    e é apenas informativa — nunca é usada para fundir pacientes.
    """

    model_config = ConfigDict(frozen=True)

    paciente_no_dia: Paciente | None
    outros_dias: list[date]
    paciente_referencia: Paciente | None
    sugestoes: list[str]


def localizar_paciente(
    fonte: ScheduleDataSource, data: date, nome_ou_id: str
) -> LocalizacaoPaciente:
    """Procura o paciente na agenda de `data` e nos outros dias da mesma semana.

    A janela semanal existe porque a fonte atual é uma planilha com uma aba
    por dia da semana e sem cadastro de pacientes: um paciente só é visível
    nos dias em que tem atendimento. Quando a fonte passar a ser o cadastro do
    Agendador, esta varredura será substituída pela consulta direta.

    A correspondência continua sendo por igualdade exata do id normalizado —
    nomes parecidos nunca são tratados como o mesmo paciente; no máximo viram
    `sugestoes` informativas. Um dia cuja leitura falhe é pulado com warning;
    se todos falharem, o último erro sobe.
    """
    alvo = normalizar_id(nome_ou_id)
    dias = dias_da_semana_de(data)
    if data not in dias:
        dias.append(data)

    pacientes_por_dia: dict[date, list[Paciente]] = {}
    ultimo_erro: Exception | None = None
    for dia in dias:
        try:
            pacientes_por_dia[dia] = fonte.listar_pacientes(dia)
        except Exception as erro:
            ultimo_erro = erro
            logger.warning(
                "Falha ao ler os pacientes de %s ao localizar paciente: %s", dia.isoformat(), erro
            )
    if not pacientes_por_dia and ultimo_erro is not None:
        raise ultimo_erro

    encontrados = {
        dia: paciente
        for dia, pacientes in pacientes_por_dia.items()
        for paciente in pacientes
        if paciente.id == alvo
    }
    paciente_no_dia = encontrados.get(data)
    outros_dias = sorted(dia for dia in encontrados if dia != data)
    registros = [encontrados[dia] for dia in sorted(encontrados)]
    paciente_referencia = paciente_no_dia or next(
        (paciente for paciente in registros if paciente.convenio is not None),
        registros[0] if registros else None,
    )

    sugestoes: list[str] = []
    if not encontrados and alvo:
        similaridade: dict[str, tuple[float, str]] = {}
        for pacientes in pacientes_por_dia.values():
            for paciente in pacientes:
                razao = SequenceMatcher(None, alvo, paciente.id).ratio()
                if razao >= LIMIAR_NOME_PARECIDO and paciente.id not in similaridade:
                    similaridade[paciente.id] = (razao, paciente.nome)
        sugestoes = [
            nome
            for _, nome in sorted(similaridade.values(), key=lambda par: (-par[0], par[1]))[
                :MAX_SUGESTOES_NOME
            ]
        ]

    return LocalizacaoPaciente(
        paciente_no_dia=paciente_no_dia,
        outros_dias=outros_dias,
        paciente_referencia=paciente_referencia,
        sugestoes=sugestoes,
    )


class ProfissionalEncontrado(BaseModel):
    """Um único profissional da semana casou com o texto procurado."""

    model_config = ConfigDict(frozen=True)

    tipo: Literal["encontrado"] = "encontrado"
    profissional_id: str
    nome: str


class ProfissionalAmbiguo(BaseModel):
    """Mais de um profissional da semana casou: quem pergunta precisa escolher."""

    model_config = ConfigDict(frozen=True)

    tipo: Literal["ambiguo"] = "ambiguo"
    candidatos: list[Profissional] = Field(min_length=2)


class ProfissionalNaoEncontrado(BaseModel):
    """Nenhum profissional da semana casou; `nomes_disponiveis` lista os que existem."""

    model_config = ConfigDict(frozen=True)

    tipo: Literal["nao_encontrado"] = "nao_encontrado"
    nomes_disponiveis: list[str]


#: União discriminada pelo campo `tipo` — os três desfechos de `localizar_profissional`.
LocalizacaoProfissional = Annotated[
    ProfissionalEncontrado | ProfissionalAmbiguo | ProfissionalNaoEncontrado,
    Field(discriminator="tipo"),
]


def _uma_sequencia_e_prefixo_da_outra(palavras: list[str], outras: list[str]) -> bool:
    """Se a sequência de palavras de um id é prefixo da do outro, em qualquer sentido."""
    menor, maior = sorted((palavras, outras), key=len)
    return bool(menor) and maior[: len(menor)] == menor


def localizar_profissional(
    fonte: ScheduleDataSource, data: date, texto: str
) -> LocalizacaoProfissional:
    """Resolve o profissional citado em `texto` entre os escalados na semana de `data`.

    Correspondência pelo id normalizado (acento e caixa não importam), já
    trocado pelo canônico quando é uma grafia em `MAPA_ALIAS_PROFISSIONAL`: o id
    exato vence; sem ele, vale quando a sequência de palavras de um lado é
    prefixo da do outro ("Rossana Belfort" encontra `rossana`, e "Ana" encontra
    `ana-paula`). Se mais de um profissional casar, devolve todos como
    candidatos e nunca escolhe sozinho. Um dia cuja leitura falhe é pulado com
    warning; se todos falharem, o último erro sobe.
    """
    profissionais: dict[str, Profissional] = {}
    algum_dia_lido = False
    ultimo_erro: Exception | None = None
    for dia in dias_da_semana_de(data):
        try:
            do_dia = fonte.listar_profissionais(dia)
        except Exception as erro:
            ultimo_erro = erro
            logger.warning(
                "Falha ao ler os profissionais de %s ao localizar profissional: %s",
                dia.isoformat(),
                erro,
            )
            continue
        algum_dia_lido = True
        for profissional in do_dia:
            profissionais.setdefault(profissional.id, profissional)
    if not algum_dia_lido and ultimo_erro is not None:
        raise ultimo_erro

    # A grafia antiga de quem tem alias ("Larissa") nunca existe na agenda: o
    # parser já a trocou pela canônica, então a busca precisa fazer o mesmo.
    alvo = normalizar_id(texto)
    alvo = MAPA_ALIAS_PROFISSIONAL.get(alvo, alvo)
    exato = profissionais.get(alvo) if alvo else None
    if exato is not None:
        return ProfissionalEncontrado(profissional_id=exato.id, nome=exato.nome)

    palavras_alvo = alvo.split("-") if alvo else []
    candidatos = [
        profissional
        for profissional in profissionais.values()
        if _uma_sequencia_e_prefixo_da_outra(palavras_alvo, profissional.id.split("-"))
    ]
    if len(candidatos) == 1:
        return ProfissionalEncontrado(profissional_id=candidatos[0].id, nome=candidatos[0].nome)
    if candidatos:
        return ProfissionalAmbiguo(
            candidatos=sorted(candidatos, key=lambda profissional: profissional.nome)
        )
    return ProfissionalNaoEncontrado(
        nomes_disponiveis=sorted(profissional.nome for profissional in profissionais.values())
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


def consultar_ocupacao_semanal_profissional(
    fonte: ScheduleDataSource, profissional_id: str, data: date
) -> OcupacaoSemanalProfissional:
    """Delega para `construir_ocupacao_semanal_profissional`."""
    return construir_ocupacao_semanal_profissional(fonte, profissional_id, data)


def consultar_carga_profissionais(
    fonte: ScheduleDataSource, dias: list[date]
) -> CargaProfissionais:
    """Delega para `construir_carga_profissionais`."""
    return construir_carga_profissionais(fonte, dias)


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
