"""Grade semanal simulada: índices da semana, simulação de mudanças e validação.

Peças comuns aos motores que sugerem mudanças na grade sem alterá-la
(`app.engine.realocacao` e `app.engine.enxugamento`). Determinístico e sem
I/O: trabalha só sobre a `FotografiaPeriodo` da semana.

- `ModeloSemana`: índices montados uma vez (oferta escalada, ocupação, onde
  está cada paciente, contagem de (paciente, especialidade, dia));
  `com_movimentos` devolve a semana com mudanças já aplicadas, mantendo a
  contagem ORIGINAL como referência da regra (d).
- `Simulacao`: a grade menos atendimentos removidos, mais movimentos, sem
  copiar índices.
- `validar_grade_final`: confere a grade final de um conjunto de mudanças do
  zero, sem os atalhos das buscas (rede de segurança: falhar ali é bug).

Privacidade: os ids de paciente são nomes normalizados; nada aqui vai para log.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, time
from typing import Final

from app.domain import Especialidade
from app.domain.constants import (
    DURACAO_SLOT_MINUTOS,
    FIM_PAUSA,
    HORARIO_ABERTURA,
    HORARIO_FECHAMENTO,
    INICIO_PAUSA,
)
from app.engine.fotografia import FotografiaPeriodo, SlotFotografado

PASSO: Final[int] = DURACAO_SLOT_MINUTOS


def minutos(hora: time) -> int:
    return hora.hour * 60 + hora.minute


def hora_de(total_minutos: int) -> time:
    return time(total_minutos // 60, total_minutos % 60)


#: (profissional, sala, posto, dia, minuto de início do bloco de 30 min).
Chave = tuple[str, str, int, date, int]


@dataclass(frozen=True, slots=True, order=True)
class Lugar:
    """N blocos seguidos num posto de um profissional, num dia."""

    data: date
    inicio: int
    sala_id: str
    indice_posto: int
    profissional_id: str
    blocos: int

    @property
    def fim(self) -> int:
        return self.inicio + self.blocos * PASSO

    @property
    def hora_inicio(self) -> time:
        return hora_de(self.inicio)

    @property
    def hora_fim(self) -> time:
        return hora_de(self.fim)

    @property
    def manha(self) -> bool:
        return self.inicio < minutos(INICIO_PAUSA)

    def chaves(self) -> tuple[Chave, ...]:
        return tuple(
            (self.profissional_id, self.sala_id, self.indice_posto, self.data, minuto)
            for minuto in range(self.inicio, self.fim, PASSO)
        )

    def cruza(self, outro: Lugar) -> bool:
        """Mesmo dia e horários que se sobrepõem (em qualquer posto)."""
        return self.data == outro.data and self.inicio < outro.fim and outro.inicio < self.fim


@dataclass(frozen=True, slots=True)
class AtendimentoGrade:
    k: int
    pacientes: tuple[str, ...]
    especialidade: Especialidade
    lugar: Lugar
    #: Chaves de fato ocupadas (iguais a `lugar.chaves()` quando é consistente).
    chaves: tuple[Chave, ...]
    aguardando: bool
    #: Todo bloco numa entrada da grade, num só posto, sem buraco.
    na_grade: bool

    @property
    def grupo(self) -> bool:
        return len(self.pacientes) > 1


class ModeloSemana:
    """Índices da semana, montados uma vez a partir da fotografia."""

    def __init__(self, foto: FotografiaPeriodo) -> None:
        self.foto = foto
        self.oferta: set[Chave] = set()
        self.oferta_do_dia: dict[tuple[str, date], set[Chave]] = defaultdict(set)
        self.postos_do_profissional: dict[tuple[str, date, int], int] = Counter()
        especialidade_por_profissional: dict[str, Especialidade] = {}
        slots_por_atendimento: dict[tuple[date, str], list[SlotFotografado]] = defaultdict(list)
        for slot in foto.slots:
            if slot.profissional_id is not None and slot.na_grade:
                chave = (
                    slot.profissional_id,
                    slot.sala_id,
                    slot.indice_posto,
                    slot.data,
                    minutos(slot.hora_inicio),
                )
                self.oferta.add(chave)
                self.oferta_do_dia[(slot.profissional_id, slot.data)].add(chave)
                self.postos_do_profissional[(slot.profissional_id, slot.data, chave[4])] += 1
                if slot.especialidade is not None:
                    especialidade_por_profissional.setdefault(
                        slot.profissional_id, slot.especialidade
                    )
            for atendimento_id in slot.atendimento_ids:
                slots_por_atendimento[(slot.data, atendimento_id)].append(slot)
        self.especialidade_por_profissional = especialidade_por_profissional

        self.atendimentos: list[AtendimentoGrade] = []
        for atendimento in foto.atendimentos:
            dia = atendimento.slots[0].data
            slots = sorted(
                slots_por_atendimento.get((dia, atendimento.id), []), key=lambda s: s.hora_inicio
            )
            if not slots:
                continue
            chaves = tuple(
                (
                    atendimento.profissional_id,
                    s.sala_id,
                    s.indice_posto,
                    s.data,
                    minutos(s.hora_inicio),
                )
                for s in slots
            )
            primeiro = chaves[0]
            lugar = Lugar(
                data=dia,
                inicio=primeiro[4],
                sala_id=primeiro[1],
                indice_posto=primeiro[2],
                profissional_id=atendimento.profissional_id,
                blocos=len(chaves),
            )
            consistente = chaves == lugar.chaves() and all(s.na_grade for s in slots)
            self.atendimentos.append(
                AtendimentoGrade(
                    k=len(self.atendimentos),
                    pacientes=tuple(dict.fromkeys(atendimento.paciente_ids)),
                    especialidade=slots[0].especialidade or atendimento.especialidade,
                    lugar=lugar,
                    chaves=chaves,
                    aguardando=atendimento.aguardando_autorizacao,
                    na_grade=consistente,
                )
            )

        #: Contagem ORIGINAL de (paciente, especialidade, dia): referência da regra (d).
        self.contagem: Counter[tuple[str, Especialidade, date]] = Counter()
        self._indexar()
        self.contagem = Counter(self.contagem_atual)
        self._lugares: dict[tuple[str, int], list[Lugar]] = {}

    def _indexar(self) -> None:
        self.ocupacao: dict[Chave, list[int]] = defaultdict(list)
        self.do_paciente: dict[str, list[int]] = defaultdict(list)
        #: Contagem de (paciente, especialidade, dia) na grade deste modelo.
        self.contagem_atual: Counter[tuple[str, Especialidade, date]] = Counter()
        for at in self.atendimentos:
            for chave in at.chaves:
                self.ocupacao[chave].append(at.k)
            for paciente in at.pacientes:
                self.do_paciente[paciente].append(at.k)
                self.contagem_atual[(paciente, at.especialidade, at.lugar.data)] += 1

    def com_movimentos(self, movidos: Mapping[int, Lugar]) -> ModeloSemana:
        """A mesma semana com os atendimentos `movidos` nos lugares novos.

        Oferta, ids (`k`) e a contagem original são os mesmos; ocupação, onde
        está cada paciente e a contagem atual são refeitos.
        """
        novo = object.__new__(ModeloSemana)
        novo.foto = self.foto
        novo.oferta = self.oferta
        novo.oferta_do_dia = self.oferta_do_dia
        novo.postos_do_profissional = self.postos_do_profissional
        novo.especialidade_por_profissional = self.especialidade_por_profissional
        novo.atendimentos = [
            replace(at, lugar=movidos[at.k], chaves=movidos[at.k].chaves(), na_grade=True)
            if at.k in movidos
            else at
            for at in self.atendimentos
        ]
        novo.contagem = self.contagem
        novo._indexar()
        novo._lugares = self._lugares
        return novo

    def nome_do_paciente(self, paciente_id: str) -> str:
        paciente = self.foto.pacientes.get(paciente_id)
        return paciente.nome if paciente is not None else paciente_id

    def profissionais_da(self, especialidade: Especialidade) -> list[str]:
        return sorted(
            p for p, e in self.especialidade_por_profissional.items() if e is especialidade
        )

    def lugares(self, profissional_id: str, blocos: int) -> list[Lugar]:
        """Todo lugar de `blocos` blocos seguidos na grade escalada do profissional."""
        chave = (profissional_id, blocos)
        if chave not in self._lugares:
            por_posto: dict[tuple[date, str, int], list[int]] = defaultdict(list)
            for prof, sala, posto, dia, minuto in self.oferta:
                if prof == profissional_id:
                    por_posto[(dia, sala, posto)].append(minuto)
            lugares: list[Lugar] = []
            for (dia, sala, posto), minutos_do_posto in por_posto.items():
                ofertados = set(minutos_do_posto)
                for inicio in sorted(ofertados):
                    if all(inicio + i * PASSO in ofertados for i in range(blocos)):
                        lugares.append(Lugar(dia, inicio, sala, posto, profissional_id, blocos))
            lugares.sort()
            self._lugares[chave] = lugares
        return self._lugares[chave]


@dataclass(frozen=True, slots=True)
class Movimento:
    #: `None` = o atendimento pedido (A), quando há um.
    k: int | None
    para: Lugar


class Simulacao:
    """A grade do modelo menos `removidos`, mais os `movimentos` (sem copiar índices).

    `paciente_pedido` e `especialidade_pedida` descrevem o atendimento pedido
    (o movimento com `k=None`), quando há um.
    """

    def __init__(
        self,
        modelo: ModeloSemana,
        paciente_pedido: str | None = None,
        especialidade_pedida: Especialidade | None = None,
    ) -> None:
        self.modelo = modelo
        self.paciente_pedido = paciente_pedido
        self.especialidade_pedida = especialidade_pedida

    def _pacientes(self, movimento: Movimento) -> tuple[str, ...]:
        if movimento.k is None:
            return (self.paciente_pedido,) if self.paciente_pedido else ()
        return self.modelo.atendimentos[movimento.k].pacientes

    def ocupantes(
        self, lugar: Lugar, removidos: frozenset[int], movimentos: Sequence[Movimento]
    ) -> set[int] | None:
        """Atendimentos do modelo que ocupam o lugar; `None` se um movimento já está nele."""
        chaves = set(lugar.chaves())
        for movimento in movimentos:
            if chaves.intersection(movimento.para.chaves()):
                return None
        return {
            k for chave in chaves for k in self.modelo.ocupacao.get(chave, ()) if k not in removidos
        }

    def paciente_livre(
        self,
        pacientes: Iterable[str],
        lugar: Lugar,
        removidos: frozenset[int],
        movimentos: Sequence[Movimento],
    ) -> bool:
        """Nenhum dos pacientes já está noutro lugar no mesmo horário."""
        for paciente in pacientes:
            for k in self.modelo.do_paciente.get(paciente, ()):
                if k not in removidos and self.modelo.atendimentos[k].lugar.cruza(lugar):
                    return False
            for movimento in movimentos:
                if paciente in self._pacientes(movimento) and movimento.para.cruza(lugar):
                    return False
        return True

    def contagem_ok(
        self,
        pacientes: Iterable[str],
        especialidade: Especialidade,
        dia: date,
        removidos: frozenset[int],
        movimentos: Sequence[Movimento],
    ) -> bool:
        """(d): a contagem de (paciente, especialidade, dia) não passa de max(antes, 1)."""
        for paciente in pacientes:
            antes = self.modelo.contagem[(paciente, especialidade, dia)]
            depois = self.modelo.contagem_atual[(paciente, especialidade, dia)] + 1
            for k in removidos:
                at = self.modelo.atendimentos[k]
                if paciente in at.pacientes and at.especialidade is especialidade:
                    depois -= at.lugar.data == dia
            for movimento in movimentos:
                if (
                    paciente in self._pacientes(movimento)
                    and self._especialidade(movimento) is especialidade
                    and movimento.para.data == dia
                ):
                    depois += 1
            if depois > max(antes, 1):
                return False
        return True

    def _especialidade(self, movimento: Movimento) -> Especialidade:
        if movimento.k is None:
            assert self.especialidade_pedida is not None
            return self.especialidade_pedida
        return self.modelo.atendimentos[movimento.k].especialidade


@dataclass(frozen=True)
class AtendimentoExtra:
    """Atendimento que entra na grade final sem ser um dos originais (o pedido A)."""

    pacientes: tuple[str, ...]
    especialidade: Especialidade
    lugar: Lugar
    #: Atendimento novo: os pacientes ganham esta especialidade na semana.
    novo: bool


def validar_grade_final(
    modelo: ModeloSemana,
    movidos: Mapping[int, Lugar],
    *,
    permitir_outro_profissional: bool,
    removidos: Iterable[int] = (),
    extra: AtendimentoExtra | None = None,
) -> bool:
    """Confere do zero a grade final: os originais de `modelo`, com `movidos` nos
    lugares novos, sem `removidos` e com `extra`.

    (a) um atendimento por (profissional, sala, posto, horário) e simultâneos do
    profissional até o número de postos escalados; (b) nenhum paciente em dois
    lugares (o que já era assim fica); (c) tudo na grade escalada, no expediente
    e fora da pausa, e cada movido com especialidade e duração de origem (e o
    mesmo profissional, sem `permitir_outro_profissional`); (d) contagem de
    (paciente, especialidade, dia) até max(antes, 1); (e) as especialidades de
    cada paciente na semana não mudam (o atendimento extra novo acrescenta a dele).
    """
    fora = set(movidos) | set(removidos)

    # Lugar final de cada atendimento: (pacientes, especialidade, lugar, chaves).
    finais: list[tuple[tuple[str, ...], Especialidade, Lugar, tuple[Chave, ...]]] = []
    for at in modelo.atendimentos:
        if at.k in movidos:
            lugar = movidos[at.k]
            finais.append((at.pacientes, at.especialidade, lugar, lugar.chaves()))
        elif at.k not in fora:
            finais.append((at.pacientes, at.especialidade, at.lugar, at.chaves))
    destinos = list(movidos.values())
    if extra is not None:
        finais.append((extra.pacientes, extra.especialidade, extra.lugar, extra.lugar.chaves()))
        destinos.insert(0, extra.lugar)

    tocadas = {c for lugar in destinos for c in lugar.chaves()}
    ocupacao: Counter[Chave] = Counter(c for *_, chaves in finais for c in chaves)
    # (a) um atendimento por posto e horário, e (c) só na grade escalada.
    if any(ocupacao[c] != 1 or c not in modelo.oferta for c in tocadas):
        return False
    por_profissional: Counter[tuple[str, date, int]] = Counter(
        (c[0], c[3], c[4]) for *_, chaves in finais for c in chaves
    )
    for prof, dia, minuto in ((c[0], c[3], c[4]) for c in tocadas):
        if (
            por_profissional[(prof, dia, minuto)]
            > modelo.postos_do_profissional[(prof, dia, minuto)]
        ):
            return False
    # (c) expediente e pausa.
    for c in tocadas:
        minuto = c[4]
        if minuto < minutos(HORARIO_ABERTURA) or minuto + PASSO > minutos(HORARIO_FECHAMENTO):
            return False
        if minuto < minutos(FIM_PAUSA) and minuto + PASSO > minutos(INICIO_PAUSA):
            return False
    # (c) cada movido mantém especialidade e duração (e o profissional, se for a regra).
    for k, lugar in movidos.items():
        at = modelo.atendimentos[k]
        if lugar.blocos != at.lugar.blocos or lugar == at.lugar:
            return False
        if lugar.profissional_id != at.lugar.profissional_id and not permitir_outro_profissional:
            return False
        if modelo.especialidade_por_profissional.get(lugar.profissional_id) not in (
            None,
            at.especialidade,
        ):
            return False

    pacientes_extra = extra.pacientes if extra is not None else ()
    envolvidos = set(pacientes_extra)
    for k in movidos:
        envolvidos.update(modelo.atendimentos[k].pacientes)
    lugares_finais: dict[str, list[tuple[Especialidade, Lugar]]] = defaultdict(list)
    for pacientes, especialidade, lugar, _ in finais:
        for paciente in pacientes:
            if paciente in envolvidos:
                lugares_finais[paciente].append((especialidade, lugar))
    for paciente in envolvidos:
        finais_do_paciente = lugares_finais[paciente]
        originais = [modelo.atendimentos[k] for k in modelo.do_paciente.get(paciente, ())]
        # (b) nenhum paciente em dois lugares ao mesmo tempo (o que já era assim fica).
        antes_cruzados = _cruzamentos([(at.especialidade, at.lugar) for at in originais])
        if _cruzamentos(finais_do_paciente) > antes_cruzados:
            return False
        # (d) contagem por (especialidade, dia) não passa de max(antes, 1).
        depois = Counter((e, lugar.data) for e, lugar in finais_do_paciente)
        for (especialidade, dia), quantidade in depois.items():
            if quantidade > max(modelo.contagem[(paciente, especialidade, dia)], 1):
                return False
        # (e) as especialidades da semana não mudam (o extra novo ganha só a dele).
        antes = {at.especialidade for at in originais}
        if extra is not None and extra.novo and paciente in extra.pacientes:
            antes = antes | {extra.especialidade}
        if {e for e, _ in finais_do_paciente} != antes:
            return False
    return True


def _cruzamentos(lugares: Sequence[tuple[Especialidade, Lugar]]) -> int:
    return sum(
        1
        for i, (_, primeiro) in enumerate(lugares)
        for _, segundo in lugares[i + 1 :]
        if primeiro.cruza(segundo)
    )
