"""Panorama de enxugamento: movimentações que reduzem turnos com presença paga.

Motor determinístico, sem I/O e sem LLM decidindo trocas: trabalha só sobre a
`FotografiaPeriodo` da semana (uma leitura da planilha). Só sugere; a agenda
nunca é alterada.

Modelo de custo: o turno de um atendimento é o do horário de INÍCIO (manhã
antes de `TURNO_CORTE`, tarde a partir dele); um turno pago é um
(profissional, dia, turno) com pelo menos um atendimento. O objetivo é
liberar turnos pagos; o saldo de um cenário é turnos liberados menos turnos
novos, e nenhum movimento abre turno vazio: todo destino fica num turno que
já é pago na grade simulada.

Alavancas, da menos para a mais invasiva (cada passo marca a sua):
L1 mesmo profissional, mesmo dia, outro turno; L2 colega da mesma terapia,
mesmo dia, mesmo turno; L3 colega da mesma terapia, mesmo dia, outro turno;
L4 mesmo profissional, outro dia (só com `permitir_trocar_dia`); L5 cadeia:
deslocar até `max_deslocados_cadeia` pacientes do colega (que ficam com ele)
para abrir espaço, com a busca de `app.engine.realocacao`. A L5 só entra para
o atendimento que ficou sem destino em L1 a L4.

Passos do panorama:
A. carga: os atendimentos de cada turno pago;
B. candidatos: turnos com até N pacientes distintos (grupo conta todos);
   turno com algum atendimento imóvel (grupo, aguardando autorização, fora da
   grade, paciente ou profissional fixo) ou de profissional sem especialidade
   fica "preso" e só entra na contagem;
C. plano de absorção de cada candidato: destinos ordenados por
   `custo_do_passo`, busca com retrocesso (os K melhores destinos de cada
   atendimento, sem dois disputando a mesma vaga) e, para quem ficou sem
   destino, a cadeia; até `max_planos_por_candidato` planos;
D. cenários: guloso por perfil (menos mudança, mais turnos, equilibrado),
   replanejando cada candidato sobre a grade simulada pelos movimentos já
   aceitos, com melhoria local limitada;
E. diversidade: cenários sem repetir o mesmo conjunto de turnos liberados,
   cada um com um código opaco determinístico.

Todo cenário passa por (a) `validar_grade_final` com os movimentos combinados
e (b) uma recontagem dos turnos pagos feita do zero (saldo >= 0, nenhum turno
novo). Cenário reprovado é descartado e contado no log: falhar ali é bug.

Privacidade: ids de paciente e de profissional são nomes normalizados; os
nomes exibidos ficam só no resultado, para o bloco da tela. O log leva só
contagens e tempo.
"""

import hashlib
import logging
import time as relogio
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Final

from app.domain import Especialidade
from app.domain.constants import (
    ENXUGAMENTO_USAR_POSTOS_EXTRAS,
    HORARIO_ABERTURA,
    HORARIO_FECHAMENTO,
    HORARIO_MINIMO_DESLOCADO,
    MANTER_TURNO_DO_DESLOCADO,
    MAX_AVALIACOES_ENXUGAMENTO,
    MAX_CADEIAS_AVALIADAS,
    MAX_CENARIOS_ENXUGAMENTO,
    MAX_DESLOCADOS_CADEIA,
    MAX_DESTINOS_POR_PACIENTE,
    MAX_ITERACOES_MELHORIA,
    MAX_MINUTOS_DESLOCAMENTO_MESMO_TURNO,
    MAX_PACIENTES_TURNO_CANDIDATO,
    MAX_PLANOS_POR_CANDIDATO,
    MOVER_AGUARDANDO_AUTORIZACAO,
    MOVER_ATENDIMENTO_FORA_DA_GRADE,
    MOVER_SESSAO_EM_GRUPO,
    PACIENTES_FIXOS,
    PENALIDADE_OUTRO_DIA,
    PERMITIR_TROCAR_DIA_ENXUGAMENTO,
    PERMITIR_ZERAR_SEMANA_ENXUGAMENTO,
    PESO_POSTO_EXTRA_ENXUGAMENTO,
    PESO_TURNO_EQUILIBRADO,
    PESOS_ALAVANCA_ENXUGAMENTO,
    PROFISSIONAIS_FIXOS,
    TURNO_CORTE,
)
from app.engine.fotografia import FotografiaPeriodo
from app.engine.grade_simulada import (
    PASSO,
    AtendimentoGrade,
    Lugar,
    ModeloSemana,
    Movimento,
    Simulacao,
    hora_de,
    minutos,
    validar_grade_final,
)
from app.engine.realocacao import (
    _A,
    PedidoRealocacao,
    Regras,
    _Busca,
    _TetoAtingidoError,
)

logger = logging.getLogger(__name__)

_CORTE: Final[int] = minutos(TURNO_CORTE)

#: Nós da busca com retrocesso de um plano: depois disso, fica o melhor achado
#: (os destinos já vêm do mais barato para o mais caro, então o primeiro plano
#: completo costuma ser o ótimo).
_MAX_NOS_POR_PLANO: Final[int] = 256


class Turno(StrEnum):
    MANHA = "manha"
    TARDE = "tarde"


def turno_do_inicio(inicio_em_minutos: int) -> Turno:
    """Turno de um atendimento pelo horário de início (minutos desde 00:00)."""
    return Turno.MANHA if inicio_em_minutos < _CORTE else Turno.TARDE


class Objetivo(StrEnum):
    MENOS_MUDANCA = "menos_mudanca"
    MAIS_TURNOS = "mais_turnos"
    EQUILIBRADO = "equilibrado"


class Alavanca(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"


_ORDEM_ALAVANCA: Final[dict[Alavanca, int]] = {a: i for i, a in enumerate(Alavanca, 1)}


class MotivoPreso(StrEnum):
    """Por que um turno leve não pode ser liberado (só contagens saem)."""

    SESSAO_EM_GRUPO = "sessao_em_grupo"
    AGUARDANDO_AUTORIZACAO = "aguardando_autorizacao"
    FORA_DA_GRADE = "fora_da_grade"
    PACIENTE_FIXO = "paciente_fixo"
    PROFISSIONAL_FIXO = "profissional_fixo"
    SEM_ESPECIALIDADE = "sem_especialidade"


@dataclass(frozen=True, slots=True, order=True)
class TurnoPago:
    profissional_id: str
    data: date
    turno: Turno


def turno_do_lugar(lugar: Lugar) -> TurnoPago:
    return TurnoPago(lugar.profissional_id, lugar.data, turno_do_inicio(lugar.inicio))


@dataclass(frozen=True)
class RegrasEnxugamento:
    """As regras configuráveis (padrões de `app.domain.constants`)."""

    pacientes_fixos: frozenset[str] = PACIENTES_FIXOS
    profissionais_fixos: frozenset[str] = PROFISSIONAIS_FIXOS
    mover_grupo: bool = MOVER_SESSAO_EM_GRUPO
    mover_aguardando: bool = MOVER_AGUARDANDO_AUTORIZACAO
    mover_fora_da_grade: bool = MOVER_ATENDIMENTO_FORA_DA_GRADE
    horario_minimo_deslocado: int = minutos(HORARIO_MINIMO_DESLOCADO)
    max_deslocados_cadeia: int = MAX_DESLOCADOS_CADEIA
    max_avaliacoes: int = MAX_AVALIACOES_ENXUGAMENTO
    max_destinos_por_paciente: int = MAX_DESTINOS_POR_PACIENTE
    max_planos_por_candidato: int = MAX_PLANOS_POR_CANDIDATO
    max_iteracoes_melhoria: int = MAX_ITERACOES_MELHORIA
    peso_turno_equilibrado: int = PESO_TURNO_EQUILIBRADO
    pesos_alavanca: Mapping[str, int] = field(
        default_factory=lambda: dict(PESOS_ALAVANCA_ENXUGAMENTO)
    )
    usar_postos_extras: bool = ENXUGAMENTO_USAR_POSTOS_EXTRAS
    peso_posto_extra: int = PESO_POSTO_EXTRA_ENXUGAMENTO
    max_minutos_mesmo_turno: int | None = MAX_MINUTOS_DESLOCAMENTO_MESMO_TURNO


@dataclass(frozen=True)
class PedidoEnxugamento:
    """O que o modelo extraiu da conversa (os profissionais já resolvidos para id)."""

    especialidade: Especialidade | None = None
    profissionais_foco: tuple[str, ...] = ()
    #: Com o foco: libera só este turno das profissionais em foco (e ignora o N).
    liberar_turno: Turno | None = None
    #: Não libera turno nem mexe nos pacientes delas (ainda podem receber pacientes).
    profissionais_preservados: tuple[str, ...] = ()
    #: Vazio: a semana inteira.
    dias: tuple[date, ...] = ()
    objetivo: Objetivo = Objetivo.EQUILIBRADO
    permitir_trocar_dia: bool = PERMITIR_TROCAR_DIA_ENXUGAMENTO
    permitir_zerar_semana: bool = PERMITIR_ZERAR_SEMANA_ENXUGAMENTO
    max_pacientes_por_turno: int = MAX_PACIENTES_TURNO_CANDIDATO
    max_cenarios: int = MAX_CENARIOS_ENXUGAMENTO
    cenarios_ja_vistos: tuple[str, ...] = ()


@dataclass(frozen=True)
class PassoEnxugamento:
    """Uma mudança sugerida, na ordem de execução manual."""

    #: Nome exibido (vários unidos por " / "): só para o bloco da tela.
    paciente: str = field(repr=False)
    especialidade: Especialidade
    de: Lugar
    para: Lugar
    alavanca: Alavanca
    turno_liberado: TurnoPago
    #: Paciente do colega deslocado para abrir espaço (fica com o mesmo profissional).
    abre_espaco: bool
    posto_extra: bool

    @property
    def outro_profissional(self) -> bool:
        return self.de.profissional_id != self.para.profissional_id

    @property
    def outro_turno(self) -> bool:
        return turno_do_inicio(self.de.inicio) != turno_do_inicio(self.para.inicio)

    @property
    def outro_dia(self) -> bool:
        return self.de.data != self.para.data

    @property
    def minutos_deslocados(self) -> int:
        """Diferença do horário de início, em minutos (no relógio, mesmo noutro dia)."""
        return abs(self.para.inicio - self.de.inicio)


@dataclass(frozen=True)
class ResumoCenario:
    turnos_liberados: int
    turnos_novos: int
    pacientes_trocam_profissional: int
    pacientes_trocam_turno: int
    pacientes_trocam_dia: int
    minutos_medio: float
    minutos_maximo: int
    #: Ocupação (slots ocupados / escalados) das profissionais que absorvem, nos
    #: dias em que recebem pacientes, depois do cenário. `None`: ninguém absorve.
    ocupacao_absorventes: float | None
    #: Slots ocupados e escalados dessas profissionais (a razão é a ocupação).
    slots_ocupados_absorventes: int
    slots_escalados_absorventes: int
    #: Profissionais (ids) que ficariam sem atendimento na semana.
    profissionais_sem_presenca: tuple[str, ...]
    #: (especialidade, dia, turno) que ficaria sem nenhum profissional atendendo.
    sem_cobertura: tuple[tuple[Especialidade, date, Turno], ...]


@dataclass(frozen=True)
class Cenario:
    codigo: str
    perfil: Objetivo
    turnos_liberados: tuple[TurnoPago, ...]
    passos: tuple[PassoEnxugamento, ...]
    resumo: ResumoCenario
    custo: int


@dataclass(frozen=True)
class EstatisticaEnxugamento:
    avaliacoes: int = 0
    busca_limitada: bool = False
    descartados_na_validacao: int = 0
    segundos: float = 0.0


@dataclass(frozen=True)
class PanoramaEnxugamento:
    #: O primeiro é o recomendado para o objetivo pedido (quando houver).
    cenarios: tuple[Cenario, ...]
    objetivo: Objetivo
    turnos_pagos: int
    candidatos: int
    #: Candidatos sem nenhum plano possível, sozinhos, na grade lida.
    candidatos_sem_plano: int
    presos: Mapping[MotivoPreso, int]
    estatistica: EstatisticaEnxugamento


# ---------------------------------------------------------------- custo


#: (posição da alavanca, troca de profissional, troca de turno, troca de dia,
#: posto simultâneo, |Δ minutos|): comparado nessa ordem.
CustoPasso = tuple[int, int, int, int, int, int]


def custo_do_passo(alavanca: Alavanca, de: Lugar, para: Lugar, posto_extra: bool) -> CustoPasso:
    """Custo de um passo, comparado na ordem: alavanca, troca de profissional,
    troca de turno, troca de dia, posto simultâneo e diferença de horário."""
    return (
        _ORDEM_ALAVANCA[alavanca],
        int(de.profissional_id != para.profissional_id),
        int(turno_do_inicio(de.inicio) != turno_do_inicio(para.inicio)),
        int(de.data != para.data),
        int(posto_extra),
        abs(para.inicio - de.inicio),
    )


def custo_escalar(
    alavanca: Alavanca, de: Lugar, para: Lugar, posto_extra: bool, regras: RegrasEnxugamento
) -> int:
    """Custo do passo em minutos equivalentes (peso da alavanca + posto simultâneo
    + diferença de horário), usado para comparar cenários."""
    return (
        regras.pesos_alavanca[alavanca.value]
        + regras.peso_posto_extra * int(posto_extra)
        + abs(para.inicio - de.inicio)
    )


# ------------------------------------------------------------- internos


@dataclass(frozen=True, slots=True)
class _Mov:
    k: int
    de: Lugar
    para: Lugar
    alavanca: Alavanca
    abre_espaco: bool
    posto_extra: bool


@dataclass(frozen=True)
class _Plano:
    turno: TurnoPago
    movs: tuple[_Mov, ...]
    chave: tuple[int, ...]
    custo: int

    @property
    def usa_cadeia_ou_outro_dia(self) -> bool:
        return any(m.alavanca in (Alavanca.L4, Alavanca.L5) for m in self.movs)

    @property
    def trocas_de_profissional(self) -> int:
        return sum(m.de.profissional_id != m.para.profissional_id for m in self.movs)

    @property
    def blocos(self) -> int:
        return sum(m.para.blocos for m in self.movs if not m.abre_espaco)


class _EsgotadoError(Exception):
    """O teto global de avaliações foi atingido."""


class _Estado:
    """Uma grade simulada com os índices derivados que a busca usa."""

    def __init__(self, modelo: ModeloSemana) -> None:
        self.modelo = modelo
        self.turnos: dict[TurnoPago, list[int]] = defaultdict(list)
        self.ocupado: Counter[tuple[str, date, int]] = Counter()
        for at in modelo.atendimentos:
            self.turnos[turno_do_lugar(at.lugar)].append(at.k)
            for chave in at.chaves:
                self.ocupado[(chave[0], chave[3], chave[4])] += 1

    def ocupado_por_outro(self, lugar: Lugar, removidos: Iterable[int]) -> bool:
        """O profissional já atende alguém (não removido) no horário, noutro posto."""
        tirar: Counter[tuple[str, date, int]] = Counter()
        for k in removidos:
            for chave in self.modelo.atendimentos[k].chaves:
                tirar[(chave[0], chave[3], chave[4])] += 1
        return any(
            self.ocupado[(lugar.profissional_id, lugar.data, m)]
            - tirar[(lugar.profissional_id, lugar.data, m)]
            > 0
            for m in range(lugar.inicio, lugar.fim, PASSO)
        )


@dataclass(frozen=True)
class _Resultado:
    """Um cenário montado pelo guloso, antes da validação final."""

    perfil: Objetivo
    planos: tuple[_Plano, ...]

    @property
    def custo(self) -> int:
        return sum(plano.custo for plano in self.planos)


class _Motor:
    def __init__(
        self, foto: FotografiaPeriodo, pedido: PedidoEnxugamento, regras: RegrasEnxugamento
    ) -> None:
        self.foto = foto
        self.pedido = pedido
        self.regras = regras
        self.modelo = ModeloSemana(foto)
        self.estado0 = _Estado(self.modelo)
        self.avaliacoes = 0
        self.limitada = False
        self.descartados = 0
        self.preservados = frozenset(pedido.profissionais_preservados)
        self._cache: dict[object, tuple[_Plano, ...]] = {}

    # -- orçamento
    def contar(self, quantidade: int = 1) -> None:
        self.avaliacoes += quantidade
        if self.avaliacoes > self.regras.max_avaliacoes:
            self.limitada = True
            raise _EsgotadoError

    # -- B. candidatos
    def motivo_preso(self, turno: TurnoPago, ks: Sequence[int]) -> MotivoPreso | None:
        regras = self.regras
        if self.modelo.especialidade_por_profissional.get(turno.profissional_id) is None:
            return MotivoPreso.SEM_ESPECIALIDADE
        if turno.profissional_id in regras.profissionais_fixos:
            return MotivoPreso.PROFISSIONAL_FIXO
        for k in ks:
            at = self.modelo.atendimentos[k]
            if at.grupo and not regras.mover_grupo:
                return MotivoPreso.SESSAO_EM_GRUPO
            if at.aguardando and not regras.mover_aguardando:
                return MotivoPreso.AGUARDANDO_AUTORIZACAO
            if not at.na_grade and not regras.mover_fora_da_grade:
                return MotivoPreso.FORA_DA_GRADE
            if any(p in regras.pacientes_fixos for p in at.pacientes):
                return MotivoPreso.PACIENTE_FIXO
        return None

    def candidatos(self) -> tuple[list[TurnoPago], Counter[MotivoPreso]]:
        pedido = self.pedido
        dias = set(pedido.dias)
        foco = set(pedido.profissionais_foco)
        com_turno = bool(foco) and pedido.liberar_turno is not None
        candidatos: list[TurnoPago] = []
        presos: Counter[MotivoPreso] = Counter()
        for turno in sorted(self.estado0.turnos):
            ks = self.estado0.turnos[turno]
            especialidade = self.modelo.especialidade_por_profissional.get(turno.profissional_id)
            if pedido.especialidade is not None and especialidade is not pedido.especialidade:
                continue
            if dias and turno.data not in dias:
                continue
            if foco and turno.profissional_id not in foco:
                continue
            if com_turno and turno.turno is not pedido.liberar_turno:
                continue
            if turno.profissional_id in self.preservados:
                continue
            pacientes = {p for k in ks for p in self.modelo.atendimentos[k].pacientes}
            if not com_turno and len(pacientes) > pedido.max_pacientes_por_turno:
                continue
            motivo = self.motivo_preso(turno, ks)
            if motivo is not None:
                presos[motivo] += 1
                continue
            candidatos.append(turno)
        return candidatos, presos

    # -- C. plano de absorção
    def _destinos(
        self,
        estado: _Estado,
        turno: TurnoPago,
        at: AtendimentoGrade,
        permitidos: frozenset[TurnoPago],
        removidos: frozenset[int],
        sim: Simulacao,
    ) -> list[tuple[CustoPasso, _Mov]]:
        modelo, regras = estado.modelo, self.regras
        de = at.lugar
        piso = min(regras.horario_minimo_deslocado, de.inicio)
        opcoes: list[tuple[Alavanca, Lugar]] = []
        for lugar in modelo.lugares(de.profissional_id, de.blocos):
            if lugar.data == de.data and turno_do_inicio(lugar.inicio) is not turno.turno:
                opcoes.append((Alavanca.L1, lugar))
            elif lugar.data != de.data and self.pedido.permitir_trocar_dia:
                opcoes.append((Alavanca.L4, lugar))
        for colega in modelo.profissionais_da(at.especialidade):
            if colega == de.profissional_id:
                continue
            for lugar in modelo.lugares(colega, de.blocos):
                if lugar.data != de.data:
                    continue
                mesmo = turno_do_inicio(lugar.inicio) is turno.turno
                opcoes.append((Alavanca.L2 if mesmo else Alavanca.L3, lugar))
        destinos: list[tuple[CustoPasso, _Mov]] = []
        for alavanca, lugar in opcoes:
            self.contar()
            if turno_do_lugar(lugar) not in permitidos or lugar.inicio < piso:
                continue
            if (
                regras.max_minutos_mesmo_turno is not None
                and turno_do_inicio(lugar.inicio) is turno_do_inicio(de.inicio)
                and abs(lugar.inicio - de.inicio) > regras.max_minutos_mesmo_turno
            ):
                continue
            if sim.ocupantes(lugar, removidos, ()) != set():
                continue
            if not sim.paciente_livre(at.pacientes, lugar, removidos, ()):
                continue
            if not sim.contagem_ok(at.pacientes, at.especialidade, lugar.data, removidos, ()):
                continue
            extra = estado.ocupado_por_outro(lugar, removidos)
            if extra and not regras.usar_postos_extras:
                continue
            custo = custo_do_passo(alavanca, de, lugar, extra)
            destinos.append((custo, _Mov(at.k, de, lugar, alavanca, False, extra)))
        destinos.sort(key=lambda item: (item[0], item[1].para))
        return destinos[: regras.max_destinos_por_paciente]

    def _combinar(
        self,
        estado: _Estado,
        ks: Sequence[int],
        opcoes: Mapping[int, Sequence[tuple[CustoPasso, _Mov]]],
        removidos: frozenset[int],
        sim: Simulacao,
    ) -> tuple[_Mov, ...] | None:
        """O plano direto mais barato (soma dos custos, em ordem), por retrocesso
        com poda: dois atendimentos nunca ficam na mesma vaga nem com o mesmo
        paciente ao mesmo tempo."""
        if not ks:
            return ()
        ordem = sorted(ks, key=lambda k: (len(opcoes[k]), k))
        largura = len(next(iter(opcoes[ordem[0]]))[0])
        minimos = [tuple(min(c[i] for c, _ in opcoes[k]) for i in range(largura)) for k in ordem]
        restante = [tuple(0 for _ in range(largura))] * (len(ordem) + 1)
        for i in range(len(ordem) - 1, -1, -1):
            restante[i] = tuple(a + b for a, b in zip(restante[i + 1], minimos[i], strict=True))
        melhor: list[tuple[tuple[int, ...], tuple[_Mov, ...]]] = []
        nos = [0]

        def busca(i: int, soma: tuple[int, ...], movs: tuple[_Mov, ...]) -> None:
            self.contar()
            nos[0] += 1
            if nos[0] > _MAX_NOS_POR_PLANO and melhor:
                return
            if melhor:
                limite = tuple(a + b for a, b in zip(soma, restante[i], strict=True))
                if limite >= melhor[0][0]:
                    return
            if i == len(ordem):
                melhor[:] = [(soma, movs)]
                return
            k = ordem[i]
            at = estado.modelo.atendimentos[k]
            ja = [Movimento(m.k, m.para) for m in movs]
            for custo, mov in opcoes[k]:
                if sim.ocupantes(mov.para, removidos, ja) is None:
                    continue
                if not sim.paciente_livre(at.pacientes, mov.para, removidos, ja):
                    continue
                if not sim.contagem_ok(
                    at.pacientes, at.especialidade, mov.para.data, removidos, ja
                ):
                    continue
                extra = mov.posto_extra or any(
                    m.para.profissional_id == mov.para.profissional_id and m.para.cruza(mov.para)
                    for m in movs
                )
                if extra and not self.regras.usar_postos_extras:
                    continue
                if extra != mov.posto_extra:
                    custo = custo_do_passo(mov.alavanca, mov.de, mov.para, True)
                    mov = _Mov(mov.k, mov.de, mov.para, mov.alavanca, False, True)
                nova = tuple(a + b for a, b in zip(soma, custo, strict=True))
                busca(i + 1, nova, (*movs, mov))

        busca(0, tuple(0 for _ in range(largura)), ())
        if not melhor:
            return None
        return tuple(sorted(melhor[0][1], key=lambda m: (m.de, m.k)))

    def _cadeia(
        self,
        estado: _Estado,
        turno: TurnoPago,
        k: int,
        permitidos: frozenset[TurnoPago],
        ja_movidos: frozenset[int],
    ) -> tuple[_Mov, ...] | None:
        """L5: o atendimento k vai para um colega (ou outro turno do mesmo
        profissional) deslocando até `max_deslocados_cadeia` pacientes, que ficam
        com o mesmo profissional."""
        modelo, regras = estado.modelo, self.regras
        at = modelo.atendimentos[k]
        if len(at.pacientes) != 1:
            return None
        de = at.lugar
        profissionais = tuple(modelo.profissionais_da(at.especialidade))
        if not profissionais:
            return None
        restante = regras.max_avaliacoes - self.avaliacoes
        if restante <= 0:
            self.limitada = True
            raise _EsgotadoError
        regras_busca = Regras(
            pacientes_fixos=regras.pacientes_fixos,
            profissionais_fixos=regras.profissionais_fixos | self.preservados,
            # Quem abre espaço fica no mesmo dia (a troca de dia é só a L4).
            pode_mudar_de_dia=False,
            penalidade_outro_dia=PENALIDADE_OUTRO_DIA,
            permitir_outro_profissional=False,
            max_deslocados=regras.max_deslocados_cadeia,
            max_opcoes=1,
            max_cadeias=max(1, min(MAX_CADEIAS_AVALIADAS, restante)),
            mover_grupo=regras.mover_grupo,
            mover_aguardando=regras.mover_aguardando,
            manter_turno=MANTER_TURNO_DO_DESLOCADO,
            mover_fora_da_grade=regras.mover_fora_da_grade,
            horario_minimo_deslocado=HORARIO_MINIMO_DESLOCADO,
        )
        piso = min(regras.horario_minimo_deslocado, de.inicio)
        pedido = PedidoRealocacao(
            dias=(de.data,),
            horario_desejado=de.hora_inicio,
            horario_minimo=hora_de(max(piso, minutos(HORARIO_ABERTURA))),
            horario_maximo=HORARIO_FECHAMENTO,
        )
        a = _A(at.pacientes[0], "", at.especialidade, de.blocos, profissionais, at)

        def vetar(lugar: Lugar) -> bool:
            return turno_do_lugar(lugar) not in permitidos

        busca = _Busca(modelo, a, pedido, regras_busca, vetar_destino=vetar)
        try:
            destinos = busca.destinos_classificados()
            for nivel in range(1, regras.max_deslocados_cadeia + 1):
                busca.nivel(nivel, destinos)
        except _TetoAtingidoError:
            self.limitada = True
        self.contar(busca.cadeias)
        for folha in sorted(busca.folhas, key=lambda f: f.ordem):
            deslocados = folha.deslocados
            if deslocados & (ja_movidos | {k}):
                continue
            destino_a = folha.destino_a
            movidos = {m.k if m.k is not None else k: m.para for m in folha.movimentos}
            if not validar_grade_final(modelo, movidos, permitir_outro_profissional=True):
                continue
            movs = [
                _Mov(
                    m.k,
                    modelo.atendimentos[m.k].lugar,
                    m.para,
                    Alavanca.L5,
                    True,
                    estado.ocupado_por_outro(m.para, deslocados),
                )
                for m in folha.movimentos
                if m.k is not None
            ]
            movs.append(
                _Mov(
                    k,
                    de,
                    destino_a,
                    Alavanca.L5,
                    False,
                    estado.ocupado_por_outro(destino_a, deslocados | {k}),
                )
            )
            if not self.regras.usar_postos_extras and any(m.posto_extra for m in movs):
                continue
            return tuple(movs)
        return None

    def planejar(
        self,
        estado: _Estado,
        turno: TurnoPago,
        fechados: frozenset[TurnoPago],
        ja_movidos: frozenset[int],
    ) -> tuple[_Plano, ...]:
        """Até `max_planos_por_candidato` planos para liberar `turno` na grade de `estado`."""
        ks = tuple(sorted(estado.turnos.get(turno, ())))
        if not ks:
            return ()
        permitidos = frozenset(t for t in estado.turnos if t not in fechados and t != turno)
        removidos = frozenset(ks)
        sim = Simulacao(estado.modelo)
        opcoes = {
            k: self._destinos(
                estado, turno, estado.modelo.atendimentos[k], permitidos, removidos, sim
            )
            for k in ks
        }
        com_opcao = [k for k in ks if opcoes[k]]
        sem_opcao = [k for k in ks if not opcoes[k]]
        planos: list[_Plano] = []

        diretos = self._combinar(estado, com_opcao, opcoes, removidos, sim)
        if diretos is not None:
            movs: tuple[_Mov, ...] | None = diretos
            if sem_opcao:
                movs = self._completar_com_cadeias(
                    estado, turno, diretos, sem_opcao, fechados, ja_movidos
                )
            if movs is not None:
                planos.append(self._plano(turno, movs))
        if len(planos) < self.regras.max_planos_por_candidato and not sem_opcao:
            mesmos = {
                k: [o for o in opcoes[k] if o[1].para.profissional_id == o[1].de.profissional_id]
                for k in ks
            }
            if all(mesmos.values()):
                sem_troca = self._combinar(estado, ks, mesmos, removidos, sim)
                if sem_troca is not None:
                    plano = self._plano(turno, sem_troca)
                    if all(plano.movs != p.movs for p in planos):
                        planos.append(plano)
        return tuple(planos)

    def _completar_com_cadeias(
        self,
        estado: _Estado,
        turno: TurnoPago,
        diretos: tuple[_Mov, ...],
        sem_opcao: Sequence[int],
        fechados: frozenset[TurnoPago],
        ja_movidos: frozenset[int],
    ) -> tuple[_Mov, ...] | None:
        movs = list(diretos)
        atual = _Estado(estado.modelo.com_movimentos({m.k: m.para for m in diretos}))
        for k in sem_opcao:
            permitidos = frozenset(t for t in atual.turnos if t not in fechados and t != turno)
            movidos = ja_movidos | {m.k for m in movs}
            cadeia = self._cadeia(atual, turno, k, permitidos, frozenset(movidos))
            if cadeia is None:
                return None
            movs.extend(cadeia)
            atual = _Estado(atual.modelo.com_movimentos({m.k: m.para for m in cadeia}))
        return tuple(movs)

    def _plano(self, turno: TurnoPago, movs: tuple[_Mov, ...]) -> _Plano:
        chave = tuple(
            sum(parte)
            for parte in zip(
                *(custo_do_passo(m.alavanca, m.de, m.para, m.posto_extra) for m in movs),
                strict=True,
            )
        )
        custo = sum(
            custo_escalar(m.alavanca, m.de, m.para, m.posto_extra, self.regras) for m in movs
        )
        return _Plano(turno, movs, chave, custo)

    def planos_em_cache(
        self,
        estado: _Estado,
        turno: TurnoPago,
        fechados: frozenset[TurnoPago],
        aceitos: tuple[_Plano, ...],
    ) -> tuple[_Plano, ...]:
        movs = [m for plano in aceitos for m in plano.movs]
        if not self.pedido.permitir_trocar_dia:
            # Sem troca de dia, tudo o que o plano usa é do mesmo dia (destinos,
            # conflitos de paciente, contagem por dia e cadeias): o resto da semana
            # não muda o resultado.
            fechados = frozenset(t for t in fechados if t.data == turno.data)
            movs = [m for m in movs if turno.data in (m.de.data, m.para.data)]
        else:
            # Com troca de dia (L4): os destinos são da mesma terapia; os conflitos
            # são dos pacientes do turno (em qualquer dia) e, na cadeia, do mesmo dia.
            colegas = self._colegas(turno)
            pacientes = self._pacientes_do_turno(turno)
            fechados = frozenset(t for t in fechados if t.profissional_id in colegas)
            movs = [
                m
                for m in movs
                if m.de.profissional_id in colegas
                or m.para.profissional_id in colegas
                or turno.data in (m.de.data, m.para.data)
                or not pacientes.isdisjoint(self.modelo.atendimentos[m.k].pacientes)
            ]
        assinatura = (turno, fechados, tuple((m.k, m.para) for m in movs))
        if assinatura not in self._cache:
            ja = frozenset(m.k for m in movs)
            self._cache[assinatura] = self.planejar(estado, turno, fechados, ja)
        return self._cache[assinatura]

    def _colegas(self, turno: TurnoPago) -> frozenset[str]:
        especialidade = self.modelo.especialidade_por_profissional.get(turno.profissional_id)
        if especialidade is None:
            return frozenset({turno.profissional_id})
        return frozenset(self.modelo.profissionais_da(especialidade)) | {turno.profissional_id}

    def _pacientes_do_turno(self, turno: TurnoPago) -> frozenset[str]:
        return frozenset(
            p
            for k in self.estado0.turnos.get(turno, ())
            for p in self.modelo.atendimentos[k].pacientes
        )

    # -- D. cenários
    def _escolher_plano(
        self, perfil: Objetivo, planos: Sequence[_Plano], restrito: bool
    ) -> _Plano | None:
        if perfil is Objetivo.MENOS_MUDANCA:
            # Na primeira passada, só turnos baratos e sem cadeia nem troca de dia.
            validos = [
                p
                for p in planos
                if not restrito
                or (
                    not p.usa_cadeia_ou_outro_dia
                    and p.custo < self.regras.peso_turno_equilibrado // 2
                )
            ]
            validos.sort(key=lambda p: (p.trocas_de_profissional, p.chave, p.custo))
            return validos[0] if validos else None
        if perfil is Objetivo.EQUILIBRADO:
            validos = [p for p in planos if p.custo < self.regras.peso_turno_equilibrado]
            return min(validos, key=lambda p: (p.custo, p.chave)) if validos else None
        return planos[0] if planos else None

    def guloso(
        self,
        perfil: Objetivo,
        ordem: Sequence[TurnoPago],
        excluidos: frozenset[TurnoPago],
        restrito: bool = False,
    ) -> _Resultado:
        estado = self.estado0
        fechados: frozenset[TurnoPago] = frozenset()
        recebeu: set[TurnoPago] = set()
        aceitos: tuple[_Plano, ...] = ()
        for turno in ordem:
            if turno in excluidos or turno in recebeu or turno in fechados:
                continue
            try:
                planos = self.planos_em_cache(estado, turno, fechados, aceitos)
            except _EsgotadoError:
                if not aceitos:
                    raise
                # Teto atingido: o cenário fica com o que já foi aceito (o bloco avisa).
                break
            plano = self._escolher_plano(perfil, planos, restrito)
            if plano is None:
                continue
            novo = _Estado(estado.modelo.com_movimentos({m.k: m.para for m in plano.movs}))
            if not self.pedido.permitir_zerar_semana and not any(
                t.profissional_id == turno.profissional_id for t in novo.turnos
            ):
                continue
            estado = novo
            fechados = fechados | {turno}
            recebeu.update(turno_do_lugar(m.para) for m in plano.movs)
            aceitos = (*aceitos, plano)
        return _Resultado(perfil, aceitos)

    def pontuacao(self, resultado: _Resultado) -> tuple[float, ...]:
        liberados = len(resultado.planos)
        custo = resultado.custo
        if resultado.perfil is Objetivo.MAIS_TURNOS:
            return (-liberados, custo)
        if resultado.perfil is Objetivo.EQUILIBRADO:
            return (-(liberados * self.regras.peso_turno_equilibrado - custo), -liberados)
        return (custo / liberados if liberados else float("inf"), -liberados)

    def ordem(self, perfil: Objetivo, base: Mapping[TurnoPago, _Plano]) -> list[TurnoPago]:
        if perfil is Objetivo.MAIS_TURNOS:
            return sorted(base, key=lambda t: (base[t].blocos, base[t].custo, t))
        if perfil is Objetivo.MENOS_MUDANCA:
            return sorted(
                base,
                key=lambda t: (
                    base[t].usa_cadeia_ou_outro_dia,
                    base[t].trocas_de_profissional,
                    base[t].custo,
                    t,
                ),
            )
        return sorted(base, key=lambda t: (base[t].custo, t))

    def construir(
        self, perfil: Objetivo, base: Mapping[TurnoPago, _Plano], excluidos: frozenset[TurnoPago]
    ) -> _Resultado:
        ordem = self.ordem(perfil, base)
        restrito = perfil is Objetivo.MENOS_MUDANCA
        melhor = self.guloso(perfil, ordem, excluidos, restrito)
        if restrito and not melhor.planos:
            # Cadeias, troca de dia e turnos caros só em último caso.
            melhor = self.guloso(perfil, ordem, excluidos, restrito=False)
            restrito = False
        tentativas = 0
        while tentativas < self.regras.max_iteracoes_melhoria:
            melhorou = False
            aceitos = [p.turno for p in melhor.planos]
            por_custo = sorted(melhor.planos, key=lambda p: (-p.custo, p.turno))
            for plano in por_custo:
                if tentativas >= self.regras.max_iteracoes_melhoria:
                    break
                tentativas += 1
                recusados = [t for t in ordem if t not in aceitos and t not in excluidos]
                # Tira o turno aceito, tenta encaixar os recusados e só então o
                # devolve à fila (ele volta se ainda couber).
                nova_ordem = [t for t in aceitos if t != plano.turno] + recusados + [plano.turno]
                tentativa = self.guloso(perfil, nova_ordem, excluidos, restrito)
                if self.pontuacao(tentativa) < self.pontuacao(melhor):
                    melhor = tentativa
                    melhorou = True
                    break
            if not melhorou:
                break
        return melhor


# ------------------------------------------------------- validação final


def recontar_turnos_pagos(
    modelo: ModeloSemana, movidos: Mapping[int, Lugar]
) -> tuple[frozenset[TurnoPago], frozenset[TurnoPago]]:
    """Turnos pagos antes e depois dos `movidos`, contados do zero (sem índices)."""

    def turno(lugar: Lugar) -> TurnoPago:
        corte = TURNO_CORTE.hour * 60 + TURNO_CORTE.minute
        return TurnoPago(
            lugar.profissional_id,
            lugar.data,
            Turno.MANHA if lugar.inicio < corte else Turno.TARDE,
        )

    antes = frozenset(turno(at.lugar) for at in modelo.atendimentos)
    depois = frozenset(turno(movidos.get(at.k, at.lugar)) for at in modelo.atendimentos)
    return antes, depois


def _codigo(turnos: Iterable[TurnoPago], movidos: Mapping[int, Lugar]) -> str:
    texto = repr(
        (
            sorted((t.profissional_id, t.data.isoformat(), t.turno.value) for t in turnos),
            sorted(
                (k, lu.data.isoformat(), lu.inicio, lu.sala_id, lu.indice_posto, lu.profissional_id)
                for k, lu in movidos.items()
            ),
        )
    )
    return "E-" + hashlib.sha256(texto.encode()).hexdigest()[:4].upper()


def _resumo(
    modelo: ModeloSemana,
    passos: Sequence[tuple[int, PassoEnxugamento]],
    movidos: Mapping[int, Lugar],
    antes: frozenset[TurnoPago],
    depois: frozenset[TurnoPago],
) -> ResumoCenario:
    trocam: dict[str, set[str]] = {"prof": set(), "turno": set(), "dia": set()}
    for k, passo in passos:
        pacientes = modelo.atendimentos[k].pacientes
        if passo.outro_profissional:
            trocam["prof"].update(pacientes)
        if passo.outro_turno:
            trocam["turno"].update(pacientes)
        if passo.outro_dia:
            trocam["dia"].update(pacientes)
    deslocamentos = [p.minutos_deslocados for _, p in passos]
    absorventes = {p.para.profissional_id for _, p in passos if p.outro_profissional} or {
        p.para.profissional_id for _, p in passos
    }
    dias_absorventes = {
        (p.para.profissional_id, p.para.data)
        for _, p in passos
        if p.para.profissional_id in absorventes
    }
    ocupadas = {
        chave
        for at in modelo.atendimentos
        for chave in (movidos[at.k].chaves() if at.k in movidos else at.chaves)
    }
    escalados = sum(len(modelo.oferta_do_dia.get(chave, ())) for chave in dias_absorventes)
    ocupados = sum(
        len(modelo.oferta_do_dia.get(chave, set()) & ocupadas) for chave in dias_absorventes
    )
    com_presenca_antes = {t.profissional_id for t in antes}
    com_presenca_depois = {t.profissional_id for t in depois}
    especialidade = modelo.especialidade_por_profissional

    def cobertura(turnos: Iterable[TurnoPago]) -> set[tuple[Especialidade, date, Turno]]:
        return {
            (esp, t.data, t.turno)
            for t in turnos
            if (esp := especialidade.get(t.profissional_id)) is not None
        }

    return ResumoCenario(
        turnos_liberados=len(antes - depois),
        turnos_novos=len(depois - antes),
        pacientes_trocam_profissional=len(trocam["prof"]),
        pacientes_trocam_turno=len(trocam["turno"]),
        pacientes_trocam_dia=len(trocam["dia"]),
        minutos_medio=round(sum(deslocamentos) / len(deslocamentos), 1) if deslocamentos else 0.0,
        minutos_maximo=max(deslocamentos, default=0),
        ocupacao_absorventes=round(ocupados / escalados, 4) if escalados else None,
        slots_ocupados_absorventes=ocupados,
        slots_escalados_absorventes=escalados,
        profissionais_sem_presenca=tuple(sorted(com_presenca_antes - com_presenca_depois)),
        sem_cobertura=tuple(sorted(cobertura(antes) - cobertura(depois))),
    )


def _cenario(motor: _Motor, resultado: _Resultado) -> Cenario | None:
    """Valida o cenário do zero e o descreve; `None` (e conta) se reprovar."""
    modelo = motor.modelo
    movidos: dict[int, Lugar] = {}
    passos: list[tuple[int, PassoEnxugamento]] = []
    for plano in resultado.planos:
        for mov in plano.movs:
            movidos[mov.k] = mov.para
            at = modelo.atendimentos[mov.k]
            passos.append(
                (
                    mov.k,
                    PassoEnxugamento(
                        paciente=" / ".join(modelo.nome_do_paciente(p) for p in at.pacientes),
                        especialidade=at.especialidade,
                        de=mov.de,
                        para=mov.para,
                        alavanca=mov.alavanca,
                        turno_liberado=plano.turno,
                        abre_espaco=mov.abre_espaco,
                        posto_extra=mov.posto_extra,
                    ),
                )
            )
    movidos = {k: lugar for k, lugar in movidos.items() if lugar != modelo.atendimentos[k].lugar}
    antes, depois = recontar_turnos_pagos(modelo, movidos)
    liberados = frozenset(p.turno for p in resultado.planos)
    valido = (
        validar_grade_final(modelo, movidos, permitir_outro_profissional=True)
        and not (depois - antes)
        and len(antes - depois) >= len(liberados)
        and liberados <= antes - depois
        and all(not p.abre_espaco or not p.outro_profissional for _, p in passos)
        and all(
            p.alavanca is not Alavanca.L4 or motor.pedido.permitir_trocar_dia for _, p in passos
        )
    )
    if not valido:
        motor.descartados += 1
        return None
    return Cenario(
        codigo=_codigo(antes - depois, movidos),
        perfil=resultado.perfil,
        turnos_liberados=tuple(sorted(liberados)),
        passos=tuple(p for _, p in passos),
        resumo=_resumo(modelo, passos, movidos, antes, depois),
        custo=resultado.custo,
    )


# ---------------------------------------------------------------- entrada


_PERFIS: Final[tuple[Objetivo, ...]] = (
    Objetivo.EQUILIBRADO,
    Objetivo.MAIS_TURNOS,
    Objetivo.MENOS_MUDANCA,
)


def planejar_enxugamento(
    foto: FotografiaPeriodo,
    pedido: PedidoEnxugamento,
    regras: RegrasEnxugamento | None = None,
) -> PanoramaEnxugamento:
    """Até `pedido.max_cenarios` cenários de enxugamento, o do objetivo primeiro."""
    regras = regras or RegrasEnxugamento()
    comeco = relogio.perf_counter()
    motor = _Motor(foto, pedido, regras)
    candidatos, presos = motor.candidatos()
    cenarios: list[Cenario] = []
    sem_plano = 0
    try:
        base: dict[TurnoPago, _Plano] = {}
        for turno in candidatos:
            planos = motor.planos_em_cache(motor.estado0, turno, frozenset(), ())
            if planos:
                base[turno] = planos[0]
            else:
                sem_plano += 1
        cenarios = _diversos(motor, base)
    except _EsgotadoError:
        pass
    estatistica = EstatisticaEnxugamento(
        avaliacoes=min(motor.avaliacoes, regras.max_avaliacoes),
        busca_limitada=motor.limitada,
        descartados_na_validacao=motor.descartados,
        segundos=relogio.perf_counter() - comeco,
    )
    logger.info(
        "planejar_enxugamento: %d cenário(s), %d candidato(s), %d sem plano, %d preso(s), "
        "%d avaliação(ões), busca limitada: %s, em %.3fs",
        len(cenarios),
        len(candidatos),
        sem_plano,
        sum(presos.values()),
        estatistica.avaliacoes,
        "sim" if motor.limitada else "não",
        estatistica.segundos,
    )
    if motor.descartados:
        logger.warning(
            "planejar_enxugamento: %d cenário(s) reprovado(s) na validação independente.",
            motor.descartados,
        )
    return PanoramaEnxugamento(
        cenarios=tuple(cenarios),
        objetivo=pedido.objetivo,
        turnos_pagos=len(motor.estado0.turnos),
        candidatos=len(candidatos),
        candidatos_sem_plano=sem_plano,
        presos=dict(sorted(presos.items())),
        estatistica=estatistica,
    )


def _diversos(motor: _Motor, base: Mapping[TurnoPago, _Plano]) -> list[Cenario]:
    """Cenários sem repetir o conjunto de turnos liberados nem os já vistos."""
    pedido = motor.pedido
    alvo = max(1, pedido.max_cenarios)
    vistos = set(pedido.cenarios_ja_vistos)
    perfis = (pedido.objetivo, *(p for p in _PERFIS if p is not pedido.objetivo))
    fila: list[tuple[Objetivo, frozenset[TurnoPago]]] = [(p, frozenset()) for p in perfis]
    #: Variações dos cenários aceitos, depois dos perfis.
    variacoes: list[tuple[Objetivo, frozenset[TurnoPago]]] = []
    conjuntos: set[frozenset[TurnoPago]] = set()
    cenarios: list[Cenario] = []
    limite = 3 * (alvo + len(vistos)) + len(_PERFIS)
    construidos = 0
    while (fila or variacoes) and len(cenarios) < alvo and construidos < limite and base:
        perfil, excluidos = fila.pop(0) if fila else variacoes.pop(0)
        construidos += 1
        try:
            resultado = motor.construir(perfil, base, excluidos)
        except _EsgotadoError:
            # Teto atingido: ficam os cenários já montados (o bloco avisa).
            break
        if not resultado.planos:
            continue
        mais_caro = max(resultado.planos, key=lambda p: (p.custo, p.turno)).turno
        seguinte = (perfil, excluidos | {mais_caro})
        cenario = _cenario(motor, resultado)
        if cenario is None:
            continue
        conjunto = frozenset(cenario.turnos_liberados)
        repetido = cenario.codigo in vistos or conjunto in conjuntos
        conjuntos.add(conjunto)
        if repetido:
            # Coincide com um cenário já mostrado: o seguinte sai sem o mais caro.
            fila.insert(0, seguinte)
            continue
        variacoes.append(seguinte)
        cenarios.append(cenario)
    return cenarios
