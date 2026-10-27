"""Plano semanal de um paciente: uma sessão por especialidade em cada dia pedido.

Lógica pura sobre `FotografiaPeriodo` (a semana já lida uma vez), sem I/O.
Diferente do encaixe casado (`app.engine.encaixe`), as sessões NÃO precisam
ser seguidas: em cada dia, cada especialidade ganha no máximo um horário
dentro da janela, sem se sobrepor às outras do mesmo dia.

Escolha do plano de um dia, nesta ordem:
1. mais especialidades atendidas;
2. mais sessões com o profissional preferido (quando há um);
3. mais compacto: menor intervalo entre o início da primeira sessão e o fim
   da última;
4. menos trocas de sala entre sessões consecutivas;
5. horários mais cedo.

Com `seguidas`, só vale um conjunto em que cada sessão começa quando a
anterior termina: o plano do dia é a maior sequência consecutiva que cabe na
janela, e quem fica de fora não ganha alternativas (fora da sequência, elas
não respondem ao pedido).

Alternativas de quem não coube (só sem `seguidas`): apenas nos dias pedidos,
primeiro no mesmo dia e, se faltar, no outro dia pedido em que a mesma
especialidade também não coube. Cada horário aparece uma vez no plano
inteiro. Horários em dias não pedidos não entram; só se avisa que existem.

Só entra vaga real: slot livre (profissional escalado, posto sem atendimento),
fora das salas a evitar. Sessão de mais de 30 min usa slots seguidos do mesmo
profissional, sala e posto, sem atravessar a pausa.

A recorrência ("toda quarta e quinta") é a grade semanal: uma fotografia da
semana, sem datas. Feriados e exceções de calendário não são vistos.
"""

from collections.abc import Iterable, Sequence
from datetime import date, time
from itertools import pairwise, product
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.domain import Especialidade
from app.engine.fotografia import FotografiaPeriodo, SlotFotografado
from app.engine.relatorio_generico import faixas_livres

#: Quantas alternativas por especialidade que não coube.
MAX_ALTERNATIVAS: Final[int] = 3

#: Teto de especialidades num plano: a busca de cada dia é exaustiva.
MAX_ITENS_POR_PLANO: Final[int] = 6


class ItemPlano(BaseModel):
    """Uma especialidade pedida, com duração e, opcionalmente, profissional preferido."""

    model_config = ConfigDict(frozen=True)

    especialidade: Especialidade
    duracao_em_slots: int = Field(ge=1)
    #: Preferência, não exigência: sem vaga dele, outro da especialidade serve.
    profissional_preferido: str | None = None


class PedidoPlano(BaseModel):
    """O que planejar: dias, janela, especialidades e restrições."""

    model_config = ConfigDict(frozen=True)

    dias: tuple[date, ...] = Field(min_length=1)
    inicio: time
    fim: time
    itens: tuple[ItemPlano, ...] = Field(min_length=1, max_length=MAX_ITENS_POR_PLANO)
    salas_evitadas: frozenset[str] = frozenset()
    #: Só sessões uma logo após a outra (a maior sequência de cada dia).
    seguidas: bool = False


class SessaoPlano(BaseModel):
    """Um horário real: dia, especialidade, profissional, sala e posto."""

    model_config = ConfigDict(frozen=True)

    data: date
    especialidade: Especialidade
    inicio: time
    fim: time
    profissional_id: str
    sala_id: str
    indice_posto: int = Field(ge=0)
    preferido: bool = False


class NaoCoube(BaseModel):
    """Especialidade sem vaga na janela do dia, com as melhores alternativas."""

    model_config = ConfigDict(frozen=True)

    especialidade: Especialidade
    #: Nos dias pedidos: mesmo dia primeiro (mais perto da janela), depois o outro
    #: dia pedido. Nenhum horário se repete entre os `NaoCoube` do plano.
    alternativas: list[SessaoPlano]
    #: Faltaram alternativas nos dias pedidos e há horários em dias não pedidos.
    ha_em_outros_dias: bool = False


class PlanoDia(BaseModel):
    model_config = ConfigDict(frozen=True)

    data: date
    #: Em ordem de horário.
    sessoes: list[SessaoPlano]
    #: Na ordem do pedido.
    nao_couberam: list[NaoCoube]


class PlanoSemanal(BaseModel):
    model_config = ConfigDict(frozen=True)

    pedido: PedidoPlano
    dias: list[PlanoDia]

    @property
    def completo(self) -> bool:
        return all(not dia.nao_couberam for dia in self.dias)


def _minutos(hora: time) -> int:
    return hora.hour * 60 + hora.minute


def maior_sequencia(sessoes: Sequence[SessaoPlano]) -> list[SessaoPlano]:
    """A maior sequência de sessões seguidas (cada uma começa quando a anterior
    termina), entre sessões em ordem de horário; a mais cedo em caso de empate."""
    melhor: list[SessaoPlano] = []
    atual: list[SessaoPlano] = []
    for sessao in sessoes:
        atual = [*atual, sessao] if atual and atual[-1].fim == sessao.inicio else [sessao]
        if len(atual) > len(melhor):
            melhor = atual
    return melhor


def _candidatas(
    slots: Iterable[SlotFotografado], item: ItemPlano, salas_evitadas: frozenset[str]
) -> list[SessaoPlano]:
    """Toda posição em que o item cabe inteiro, em slots livres seguidos de um posto."""
    elegiveis = [
        slot
        for slot in slots
        if slot.livre
        and slot.especialidade is item.especialidade
        and slot.sala_id not in salas_evitadas
    ]
    sessoes: list[SessaoPlano] = []
    for faixa in faixas_livres(elegiveis):
        for comeco in range(len(faixa) - item.duracao_em_slots + 1):
            primeiro, ultimo = faixa[comeco], faixa[comeco + item.duracao_em_slots - 1]
            assert primeiro.profissional_id is not None
            sessoes.append(
                SessaoPlano(
                    data=primeiro.data,
                    especialidade=item.especialidade,
                    inicio=primeiro.hora_inicio,
                    fim=ultimo.hora_fim,
                    profissional_id=primeiro.profissional_id,
                    sala_id=primeiro.sala_id,
                    indice_posto=primeiro.indice_posto,
                    preferido=primeiro.profissional_id == item.profissional_preferido,
                )
            )
    return sessoes


def _melhor_representante(sessoes: Iterable[SessaoPlano]) -> dict[tuple[object, ...], SessaoPlano]:
    """Uma sessão por (dia, horário, sala): a do preferido, senão a de menor id e posto."""
    melhores: dict[tuple[object, ...], SessaoPlano] = {}
    for sessao in sessoes:
        chave = (sessao.data, sessao.inicio, sessao.fim, sessao.sala_id)
        atual = melhores.get(chave)
        if atual is None or _desempate(sessao) < _desempate(atual):
            melhores[chave] = sessao
    return melhores


def _desempate(sessao: SessaoPlano) -> tuple[bool, str, int]:
    return (not sessao.preferido, sessao.profissional_id, sessao.indice_posto)


def _se_cruzam(a: tuple[time, time], b: tuple[time, time]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


#: Caminho parcial da escolha de salas: (-preferidos, trocas de sala) e as sessões.
_Caminho = tuple[tuple[int, int], list[SessaoPlano]]


def _ordem_do_caminho(caminho: _Caminho) -> tuple[tuple[int, int], list[tuple[bool, str, int]]]:
    return caminho[0], [_desempate(s) for s in caminho[1]]


def _salas_com_menos_trocas(opcoes_por_sessao: Sequence[Sequence[SessaoPlano]]) -> _Caminho:
    """Para horários já fixados (em ordem), a sessão de cada um: mais preferidos e
    menos trocas de sala. Programação dinâmica com a sala da última sessão como estado.
    """
    caminhos: dict[str, _Caminho] = {"": ((0, 0), [])}
    for opcoes in opcoes_por_sessao:
        novos: dict[str, _Caminho] = {}
        for sessao in opcoes:
            ganho = -1 if sessao.preferido else 0
            melhor = min(
                (
                    (
                        (custo[0] + ganho, custo[1] + (sala not in ("", sessao.sala_id))),
                        [*sessoes, sessao],
                    )
                    for sala, (custo, sessoes) in caminhos.items()
                ),
                key=_ordem_do_caminho,
            )
            atual = novos.get(sessao.sala_id)
            if atual is None or _ordem_do_caminho(melhor) < _ordem_do_caminho(atual):
                novos[sessao.sala_id] = melhor
        caminhos = novos
    return min(caminhos.values(), key=_ordem_do_caminho)


def _plano_do_dia(
    candidatas_por_item: Sequence[list[SessaoPlano]], *, seguidas: bool = False
) -> list[tuple[int, SessaoPlano]]:
    """O melhor conjunto (índice do item, sessão) do dia, pelas regras do módulo."""
    # Horários possíveis de cada item; `None` = o item fica de fora.
    horarios: list[list[tuple[time, time] | None]] = [
        [*sorted({(s.inicio, s.fim) for s in candidatas}), None]
        for candidatas in candidatas_por_item
    ]
    melhor_chave: tuple[object, ...] | None = None
    melhor: list[tuple[int, SessaoPlano]] = []
    for combinacao in product(*horarios):
        escolhidos = [(i, h) for i, h in enumerate(combinacao) if h is not None]
        # Menos especialidades que o melhor já achado nunca vence (1º critério).
        if not escolhidos or len(escolhidos) < len(melhor):
            continue
        ordenados = sorted(escolhidos, key=lambda par: (par[1], par[0]))
        if any(_se_cruzam(a[1], b[1]) for a, b in pairwise(ordenados)):
            continue
        if seguidas and any(a[1][1] != b[1][0] for a, b in pairwise(ordenados)):
            continue
        opcoes = [
            [s for s in candidatas_por_item[i] if (s.inicio, s.fim) == horario]
            for i, horario in ordenados
        ]
        (menos_preferidos, trocas), sessoes = _salas_com_menos_trocas(opcoes)
        intervalo = _minutos(ordenados[-1][1][1]) - _minutos(ordenados[0][1][0])
        chave = (
            -len(escolhidos),
            menos_preferidos,
            intervalo,
            trocas,
            [h for _, h in ordenados],
            [_desempate(s) for s in sessoes],
        )
        if melhor_chave is None or chave < melhor_chave:
            melhor_chave = chave
            melhor = [(i, s) for (i, _), s in zip(ordenados, sessoes, strict=True)]
    return melhor


def _distancia_da_janela(sessao: SessaoPlano, inicio: time, fim: time) -> int:
    """Minutos entre a sessão e a janela (0 se a toca ou cai dentro)."""
    if sessao.fim <= inicio:
        return _minutos(inicio) - _minutos(sessao.fim)
    if sessao.inicio >= fim:
        return _minutos(sessao.inicio) - _minutos(fim)
    return 0


def _ordem_da_alternativa(
    sessao: SessaoPlano, pedido: PedidoPlano
) -> tuple[int, date, time, tuple[bool, str, int]]:
    return (
        _distancia_da_janela(sessao, pedido.inicio, pedido.fim),
        sessao.data,
        sessao.inicio,
        _desempate(sessao),
    )


def _horarios_fora_do_plano(
    item: ItemPlano,
    pedido: PedidoPlano,
    slots: list[SlotFotografado],
    ocupado: list[tuple[time, time]],
) -> list[SessaoPlano]:
    """Horários do item num dia, sem cruzar o plano dele: mais perto da janela
    primeiro, um por início."""
    sessoes = _melhor_representante(_candidatas(slots, item, pedido.salas_evitadas)).values()
    livres = sorted(
        (s for s in sessoes if not any(_se_cruzam((s.inicio, s.fim), p) for p in ocupado)),
        key=lambda s: _ordem_da_alternativa(s, pedido),
    )
    escolhidas: list[SessaoPlano] = []
    inicios: set[time] = set()
    for sessao in livres:
        if sessao.inicio not in inicios:
            inicios.add(sessao.inicio)
            escolhidas.append(sessao)
    return escolhidas


def _nao_couberam(
    pedido: PedidoPlano,
    slots_por_dia: dict[date, list[SlotFotografado]],
    escolhidas_por_dia: dict[date, list[tuple[int, SessaoPlano]]],
) -> dict[date, list[NaoCoube]]:
    """O que não coube em cada dia pedido, na ordem do pedido, com as alternativas.

    Por especialidade: primeiro até `MAX_ALTERNATIVAS` no mesmo dia; depois, para
    o dia que ficou com menos, sobras dos outros dias pedidos em que ela também
    não coube. Nenhum horário se repete.
    """
    resultado: dict[date, list[NaoCoube]] = {dia: [] for dia in pedido.dias}
    for indice, item in enumerate(pedido.itens):
        falhos = [
            dia for dia in pedido.dias if indice not in {i for i, _ in escolhidas_por_dia[dia]}
        ]
        if not falhos:
            continue
        if pedido.seguidas:
            for dia in falhos:
                resultado[dia].append(NaoCoube(especialidade=item.especialidade, alternativas=[]))
            continue
        livres = {
            dia: _horarios_fora_do_plano(
                item,
                pedido,
                slots_por_dia.get(dia, []),
                [(s.inicio, s.fim) for _, s in escolhidas_por_dia[dia]],
            )
            for dia in falhos
        }
        alternativas = {dia: livres[dia][:MAX_ALTERNATIVAS] for dia in falhos}
        sobras = sorted(
            (s for dia in falhos for s in livres[dia][MAX_ALTERNATIVAS:]),
            key=lambda s: _ordem_da_alternativa(s, pedido),
        )
        for dia in falhos:
            while len(alternativas[dia]) < MAX_ALTERNATIVAS and sobras:
                alternativas[dia].append(sobras.pop(0))
        outros_dias = any(
            _horarios_fora_do_plano(item, pedido, slots, [])
            for dia, slots in slots_por_dia.items()
            if dia not in pedido.dias
        )
        for dia in falhos:
            resultado[dia].append(
                NaoCoube(
                    especialidade=item.especialidade,
                    alternativas=alternativas[dia],
                    ha_em_outros_dias=outros_dias and len(alternativas[dia]) < MAX_ALTERNATIVAS,
                )
            )
    return resultado


def planejar_semana(foto: FotografiaPeriodo, pedido: PedidoPlano) -> PlanoSemanal:
    """O plano de cada dia pedido, o que não coube e as alternativas de cada um."""
    slots_por_dia: dict[date, list[SlotFotografado]] = {}
    for slot in foto.slots:
        slots_por_dia.setdefault(slot.data, []).append(slot)

    escolhidas_por_dia: dict[date, list[tuple[int, SessaoPlano]]] = {}
    for dia in pedido.dias:
        na_janela = [
            slot
            for slot in slots_por_dia.get(dia, [])
            if slot.hora_inicio >= pedido.inicio and slot.hora_fim <= pedido.fim
        ]
        candidatas_por_item = [
            list(
                _melhor_representante(_candidatas(na_janela, item, pedido.salas_evitadas)).values()
            )
            for item in pedido.itens
        ]
        escolhidas_por_dia[dia] = _plano_do_dia(candidatas_por_item, seguidas=pedido.seguidas)

    nao_couberam = _nao_couberam(pedido, slots_por_dia, escolhidas_por_dia)
    dias = [
        PlanoDia(
            data=dia,
            sessoes=sorted((s for _, s in escolhidas_por_dia[dia]), key=lambda s: s.inicio),
            nao_couberam=nao_couberam[dia],
        )
        for dia in pedido.dias
    ]
    return PlanoSemanal(pedido=pedido, dias=dias)
