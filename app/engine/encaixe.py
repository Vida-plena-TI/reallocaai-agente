"""Encaixe casado e sugestão de realocação (Fase 4b).

Reaproveita `listar_disponibilidade` (Fase 4a) para saber o que está livre;
aqui mora só a lógica de combinar isso em opções de agenda — ordenar
especialidades sem buraco entre si, escolher horário e propor alternativas.
"""

from datetime import date, time
from enum import StrEnum, auto
from itertools import pairwise, permutations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.data_sources.base import ScheduleDataSource
from app.domain import (
    DURACAO_SLOT_MINUTOS,
    HORARIO_ABERTURA,
    Atendimento,
    Especialidade,
    ItemSolicitacao,
    Slot,
    SolicitacaoAtendimento,
)
from app.domain.exceptions import SlotInvalidoError
from app.engine.disponibilidade import listar_disponibilidade


class ItemEncaixeResolvido(BaseModel):
    """Uma especialidade da solicitação já resolvida: profissional, sala e slots."""

    model_config = ConfigDict(frozen=True)

    especialidade: Especialidade
    profissional_id: str = Field(min_length=1)
    sala_id: str = Field(min_length=1)
    slots: list[Slot] = Field(min_length=1)

    @field_validator("slots")
    @classmethod
    def _validar_slots_contiguos(cls, slots: list[Slot]) -> list[Slot]:
        if len({slot.data for slot in slots}) > 1:
            raise ValueError("Os slots de um item resolvido precisam ser do mesmo dia.")

        ordenados = sorted(slots)
        for anterior, seguinte in pairwise(ordenados):
            if not anterior.e_contiguo_a(seguinte):
                raise ValueError("Os slots de um item resolvido precisam ser contíguos.")

        return ordenados


class OpcaoEncaixe(BaseModel):
    """Uma combinação válida de itens resolvidos, em ordem cronológica."""

    model_config = ConfigDict(frozen=True)

    itens: list[ItemEncaixeResolvido] = Field(min_length=1)

    @property
    def horario_inicio(self) -> time:
        """Início do primeiro item, cronologicamente."""
        return self.itens[0].slots[0].hora_inicio

    @property
    def horario_fim(self) -> time:
        """Fim do último item, cronologicamente."""
        return self.itens[-1].slots[-1].hora_fim


class CenarioSugestao(StrEnum):
    """Os cenários que `buscar_alternativas` pode devolver."""

    MELHOR_PARA_CLINICA = auto()
    MAIS_PROXIMO_SEGUINTE = auto()
    MAIS_PROXIMO_ANTERIOR = auto()


class OpcaoComCenario(BaseModel):
    """Uma opção de encaixe rotulada com o cenário que a gerou."""

    model_config = ConfigDict(frozen=True)

    cenario: CenarioSugestao
    opcao: OpcaoEncaixe


def _slots_consecutivos(dia: date, inicio: time, quantidade: int) -> list[Slot] | None:
    """Monta `quantidade` slots contíguos a partir de `inicio`, ou `None`
    se a sequência sair do expediente ou atravessar a pausa.
    """
    slots: list[Slot] = []
    hora_atual = inicio
    for _ in range(quantidade):
        try:
            slot_atual = Slot(data=dia, hora_inicio=hora_atual)
        except SlotInvalidoError:
            return None

        if slots and not slots[-1].e_contiguo_a(slot_atual):
            return None

        slots.append(slot_atual)
        hora_atual = slot_atual.hora_fim

    return slots


def _resolver_item(
    fonte: ScheduleDataSource,
    dia: date,
    item: ItemSolicitacao,
    slots_necessarios: list[Slot],
    excluir_atendimento_id: str | None,
) -> ItemEncaixeResolvido | None:
    """Encontra um profissional que cubra `slots_necessarios` inteiros numa
    única sala, respeitando `item.profissional_id` quando informado.
    """
    disponiveis = listar_disponibilidade(
        fonte,
        dia,
        especialidade=item.especialidade,
        profissional_id=item.profissional_id,
        excluir_atendimento_id=excluir_atendimento_id,
    )

    sala_por_slot: dict[str, dict[Slot, str]] = {}
    for disponivel in disponiveis:
        sala_por_slot.setdefault(disponivel.profissional_id, {})[disponivel.slot] = (
            disponivel.sala_id
        )

    candidatos: list[tuple[str, str]] = []
    for profissional_id, salas_por_slot in sala_por_slot.items():
        if not all(slot in salas_por_slot for slot in slots_necessarios):
            continue
        salas = {salas_por_slot[slot] for slot in slots_necessarios}
        if len(salas) != 1:
            continue
        candidatos.append((profissional_id, next(iter(salas))))

    if not candidatos:
        return None

    # Desempate simples e determinístico (não é regra de negócio, só torna o
    # resultado reprodutível): entre os profissionais que cobrem os N slots
    # inteiros, escolhe o de id lexicograficamente menor.
    candidatos.sort(key=lambda candidato: candidato[0])
    profissional_id, sala_id = candidatos[0]

    return ItemEncaixeResolvido(
        especialidade=item.especialidade,
        profissional_id=profissional_id,
        sala_id=sala_id,
        slots=slots_necessarios,
    )


def _tentar_ordem(
    fonte: ScheduleDataSource,
    dia: date,
    ordem: tuple[ItemSolicitacao, ...],
    horario_inicio: time,
    excluir_atendimento_id: str | None,
) -> OpcaoEncaixe | None:
    """Tenta encaixar `ordem` sem buraco, a partir de `horario_inicio`."""
    itens_resolvidos: list[ItemEncaixeResolvido] = []
    hora_atual = horario_inicio

    for item in ordem:
        slots = _slots_consecutivos(dia, hora_atual, item.duracao_em_slots)
        if slots is None:
            return None

        resolvido = _resolver_item(fonte, dia, item, slots, excluir_atendimento_id)
        if resolvido is None:
            return None

        itens_resolvidos.append(resolvido)
        hora_atual = slots[-1].hora_fim

    return OpcaoEncaixe(itens=itens_resolvidos)


def _tentar_a_partir_de(
    fonte: ScheduleDataSource,
    solicitacao: SolicitacaoAtendimento,
    horario_inicio_candidato: time,
    *,
    excluir_atendimento_id: str | None = None,
) -> OpcaoEncaixe | None:
    """Tenta montar uma `OpcaoEncaixe` começando exatamente em
    `horario_inicio_candidato`, testando todas as ordens possíveis dos itens.

    Desempate simples e determinístico (não é regra de negócio): a primeira
    ordem que funcionar, seguindo a sequência que `itertools.permutations`
    gera a partir da ordem original de `solicitacao.itens`, é a escolhida.
    """
    for ordem in permutations(solicitacao.itens):
        opcao = _tentar_ordem(
            fonte, solicitacao.data, ordem, horario_inicio_candidato, excluir_atendimento_id
        )
        if opcao is not None:
            return opcao

    return None


def _horarios_de_inicio_do_dia(dia: date, piso: time) -> list[time]:
    """Horários de início válidos do dia (grid, expediente, sem pausa), a
    partir de `piso`, em ordem crescente.
    """
    return [slot.hora_inicio for slot in Slot.slots_do_dia(dia) if slot.hora_inicio >= piso]


def buscar_melhor_encaixe(
    fonte: ScheduleDataSource,
    solicitacao: SolicitacaoAtendimento,
    *,
    excluir_atendimento_id: str | None = None,
) -> OpcaoEncaixe | None:
    """A melhor opção de encaixe para a solicitação.

    Quando há `horario_desejado`, o pedido exato tem prioridade máxima: se
    houver vaga, essa é a resposta. Caso contrário — sem `horario_desejado`,
    ou com ele indisponível — percorre os horários do dia em ordem crescente
    a partir de `max(solicitacao.horario_minimo, HORARIO_ABERTURA)` e devolve
    o primeiro que funcionar.
    """
    if solicitacao.horario_desejado is not None:
        opcao = _tentar_a_partir_de(
            fonte,
            solicitacao,
            solicitacao.horario_desejado,
            excluir_atendimento_id=excluir_atendimento_id,
        )
        if opcao is not None:
            return opcao

    piso = max(solicitacao.horario_minimo, HORARIO_ABERTURA)
    for horario in _horarios_de_inicio_do_dia(solicitacao.data, piso):
        opcao = _tentar_a_partir_de(
            fonte, solicitacao, horario, excluir_atendimento_id=excluir_atendimento_id
        )
        if opcao is not None:
            return opcao

    return None


def _deslocar(hora: time, minutos: int) -> time:
    """Horário deslocado em `minutos` (pode ser negativo), sem validar grid."""
    total = hora.hour * 60 + hora.minute + minutos
    return time((total // 60) % 24, total % 60)


def _slot_vizinho(dia: date, hora_inicio: time) -> Slot | None:
    """O slot naquele horário, ou `None` se ele não existir (fora do
    expediente ou dentro da pausa).
    """
    try:
        return Slot(data=dia, hora_inicio=hora_inicio)
    except SlotInvalidoError:
        return None


def _score_desfragmentacao(
    fonte: ScheduleDataSource,
    dia: date,
    opcao: OpcaoEncaixe,
    excluir_atendimento_id: str | None,
) -> int:
    """Quantos dos dois slots vizinhos da opção (o anterior ao primeiro item e
    o posterior ao último, na mesma sala e profissional de cada um) já estão
    ocupados por outro `Atendimento` do dia — quanto maior, mais a opção
    "encosta" em algo já ocupado em vez de deixar um buraco isolado.
    """
    ocupados: set[tuple[str, str, Slot]] = set()
    for atendimento in fonte.listar_atendimentos(dia):
        if atendimento.id == excluir_atendimento_id:
            continue
        for slot in atendimento.slots:
            ocupados.add((atendimento.profissional_id, atendimento.sala_id, slot))

    score = 0

    primeiro_item = opcao.itens[0]
    slot_anterior = _slot_vizinho(
        dia, _deslocar(primeiro_item.slots[0].hora_inicio, -DURACAO_SLOT_MINUTOS)
    )
    if (
        slot_anterior is not None
        and (
            primeiro_item.profissional_id,
            primeiro_item.sala_id,
            slot_anterior,
        )
        in ocupados
    ):
        score += 1

    ultimo_item = opcao.itens[-1]
    slot_posterior = _slot_vizinho(dia, ultimo_item.slots[-1].hora_fim)
    if (
        slot_posterior is not None
        and (
            ultimo_item.profissional_id,
            ultimo_item.sala_id,
            slot_posterior,
        )
        in ocupados
    ):
        score += 1

    return score


def buscar_alternativas(
    fonte: ScheduleDataSource,
    solicitacao: SolicitacaoAtendimento,
    *,
    excluir_atendimento_id: str | None = None,
) -> list[OpcaoComCenario]:
    """Até 3 cenários alternativos, quando `horario_desejado` está definido e
    indisponível: o mais próximo seguinte, o mais próximo anterior (nunca
    abaixo de `horario_minimo`) e o melhor para a desfragmentação da agenda.
    Cenários que não existem são omitidos, sem erro. Dois cenários podem
    apontar para a mesma opção — cada um entra uma vez.
    """
    desejado = solicitacao.horario_desejado
    if desejado is None:
        return []

    dia = solicitacao.data
    piso = max(solicitacao.horario_minimo, HORARIO_ABERTURA)
    horarios = _horarios_de_inicio_do_dia(dia, piso)

    resultados: list[OpcaoComCenario] = []

    for horario in horarios:
        if horario < desejado:
            continue
        opcao = _tentar_a_partir_de(
            fonte, solicitacao, horario, excluir_atendimento_id=excluir_atendimento_id
        )
        if opcao is not None:
            resultados.append(
                OpcaoComCenario(cenario=CenarioSugestao.MAIS_PROXIMO_SEGUINTE, opcao=opcao)
            )
            break

    for horario in reversed(horarios):
        if horario >= desejado:
            continue
        opcao = _tentar_a_partir_de(
            fonte, solicitacao, horario, excluir_atendimento_id=excluir_atendimento_id
        )
        if opcao is not None:
            resultados.append(
                OpcaoComCenario(cenario=CenarioSugestao.MAIS_PROXIMO_ANTERIOR, opcao=opcao)
            )
            break

    melhor_opcao: OpcaoEncaixe | None = None
    melhor_score = -1
    for horario in horarios:
        opcao = _tentar_a_partir_de(
            fonte, solicitacao, horario, excluir_atendimento_id=excluir_atendimento_id
        )
        if opcao is None:
            continue
        score = _score_desfragmentacao(fonte, dia, opcao, excluir_atendimento_id)
        if score > melhor_score:
            melhor_score = score
            melhor_opcao = opcao

    if melhor_opcao is not None:
        resultados.append(
            OpcaoComCenario(cenario=CenarioSugestao.MELHOR_PARA_CLINICA, opcao=melhor_opcao)
        )

    return resultados


def sugerir_realocacao(
    fonte: ScheduleDataSource,
    atendimento: Atendimento,
    nova_duracao_em_slots: int | None = None,
) -> OpcaoEncaixe | None:
    """Um novo horário, no mesmo dia, para o mesmo profissional e a mesma
    especialidade do atendimento original — a realocação nunca troca de
    profissional (regra de continuidade terapêutica).

    //TODO: fase futura — mover o atendimento para outro dia. Por ora a busca
    fica restrita ao dia do atendimento original.
    """
    duracao = nova_duracao_em_slots if nova_duracao_em_slots is not None else len(atendimento.slots)

    item = ItemSolicitacao(
        especialidade=atendimento.especialidade,
        duracao_em_slots=duracao,
        profissional_id=atendimento.profissional_id,
    )
    solicitacao = SolicitacaoAtendimento(
        paciente_id=atendimento.paciente_ids[0],
        data=atendimento.data,
        itens=[item],
    )

    return buscar_melhor_encaixe(fonte, solicitacao, excluir_atendimento_id=atendimento.id)
