"""Cálculo de ocupação: quanto da grade escalada de cada profissional já está
preenchida por atendimentos, e como isso se agrega por sala e por especialidade.

Fase 4a: leitura fiel do que `ScheduleDataSource` descreve, sem ranking nem
priorização (isso é Fase 4b).
"""

import logging
from collections import defaultdict
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain import EntradaGrade, Especialidade, Profissional, ScheduleDataSource
from app.domain.constants import META_OCUPACAO_POR_SALA
from app.domain.slot import Slot

logger = logging.getLogger(__name__)


def percentual_de_ocupacao(slots_escalados: int, slots_ocupados: int) -> float:
    """Fração de slots escalados já ocupados, sem dividir por zero."""
    if slots_escalados == 0:
        return 0.0
    return slots_ocupados / slots_escalados


class OcupacaoProfissional(BaseModel):
    """Ocupação de um profissional no dia, somada em todas as salas por onde passou."""

    model_config = ConfigDict(frozen=True)

    profissional_id: str
    nome: str
    especialidade: Especialidade
    slots_escalados: int
    slots_ocupados: int

    @property
    def percentual(self) -> float:
        """Fração de `slots_escalados` já ocupada (0.0 quando não há escala)."""
        return percentual_de_ocupacao(self.slots_escalados, self.slots_ocupados)


class OcupacaoAgregada(BaseModel):
    """Escalados/ocupados consolidados de várias contribuições (sala ou especialidade)."""

    model_config = ConfigDict(frozen=True)

    slots_escalados: int
    slots_ocupados: int

    @property
    def percentual(self) -> float:
        """Fração de `slots_escalados` já ocupada (0.0 quando não há escala)."""
        return percentual_de_ocupacao(self.slots_escalados, self.slots_ocupados)

    @property
    def abaixo_da_meta(self) -> bool:
        """Se o percentual está abaixo de `META_OCUPACAO_POR_SALA`."""
        return self.percentual < META_OCUPACAO_POR_SALA


class RelatorioOcupacaoDoDia(BaseModel):
    """Ocupação consolidada de um dia, por profissional e pelas agregações derivadas."""

    model_config = ConfigDict(frozen=True)

    data: date
    por_profissional: list[OcupacaoProfissional]
    #: Escalados/ocupados por sala, já somados entre profissionais. Guardado à
    #: parte porque `por_profissional` soma um mesmo profissional em todas as
    #: salas por onde ele passou no dia, perdendo a granularidade por sala.
    detalhamento_por_sala: dict[str, OcupacaoAgregada] = Field(default_factory=dict)

    def por_sala(self) -> dict[str, OcupacaoAgregada]:
        """Ocupação agregada por sala."""
        return dict(self.detalhamento_por_sala)

    def por_especialidade(self) -> dict[Especialidade, OcupacaoAgregada]:
        """Ocupação agregada por especialidade.

        Diferente de `por_sala`, pode ser derivada direto de `por_profissional`:
        a especialidade é uma propriedade única do profissional (nunca varia
        por sala), então somar os totais por profissional não perde nada.
        """
        acumulado: dict[Especialidade, tuple[int, int]] = {}
        for profissional in self.por_profissional:
            escalados, ocupados = acumulado.get(profissional.especialidade, (0, 0))
            acumulado[profissional.especialidade] = (
                escalados + profissional.slots_escalados,
                ocupados + profissional.slots_ocupados,
            )
        return {
            especialidade: OcupacaoAgregada(slots_escalados=escalados, slots_ocupados=ocupados)
            for especialidade, (escalados, ocupados) in acumulado.items()
        }


def construir_relatorio_ocupacao_do_dia(
    origem: ScheduleDataSource, dia: date
) -> RelatorioOcupacaoDoDia:
    """Monta o relatório de ocupação do dia a partir de uma `ScheduleDataSource`."""
    grade = origem.listar_grade(dia)
    profissionais = origem.listar_profissionais(dia)
    atendimentos = origem.listar_atendimentos(dia)

    profissional_por_id = {profissional.id: profissional for profissional in profissionais}

    escalados_por_profissional: dict[str, int] = defaultdict(int)
    escalados_por_sala: dict[str, int] = defaultdict(int)
    sala_por_profissional_e_slot: dict[tuple[str, Slot], str] = {}
    for entrada in grade:
        escalados_por_profissional[entrada.profissional_id] += 1
        escalados_por_sala[entrada.sala_id] += 1
        sala_por_profissional_e_slot[(entrada.profissional_id, entrada.slot)] = entrada.sala_id

    ocupados_por_profissional: dict[str, int] = defaultdict(int)
    ocupados_por_sala: dict[str, int] = defaultdict(int)
    for atendimento in atendimentos:
        for slot in atendimento.slots:
            ocupados_por_profissional[atendimento.profissional_id] += 1
            sala_id = sala_por_profissional_e_slot.get((atendimento.profissional_id, slot))
            if sala_id is None:
                logger.warning(
                    "Atendimento %r cobre %s sem entrada correspondente na grade de %s: "
                    "contando como ocupado mesmo assim.",
                    atendimento.id,
                    slot,
                    dia.isoformat(),
                )
                continue
            ocupados_por_sala[sala_id] += 1

    ids_dos_profissionais = set(escalados_por_profissional) | set(profissional_por_id)

    por_profissional = [
        OcupacaoProfissional(
            profissional_id=profissional_id,
            nome=_nome_do_profissional(profissional_id, profissional_por_id),
            especialidade=_especialidade_do_profissional(
                profissional_id, profissional_por_id, grade
            ),
            slots_escalados=escalados_por_profissional.get(profissional_id, 0),
            slots_ocupados=ocupados_por_profissional.get(profissional_id, 0),
        )
        for profissional_id in sorted(ids_dos_profissionais)
    ]

    detalhamento_por_sala = {
        sala_id: OcupacaoAgregada(
            slots_escalados=slots_escalados,
            slots_ocupados=ocupados_por_sala.get(sala_id, 0),
        )
        for sala_id, slots_escalados in escalados_por_sala.items()
    }

    return RelatorioOcupacaoDoDia(
        data=dia,
        por_profissional=por_profissional,
        detalhamento_por_sala=detalhamento_por_sala,
    )


def _nome_do_profissional(
    profissional_id: str, profissional_por_id: dict[str, Profissional]
) -> str:
    """Nome do profissional, ou o próprio id quando ele não está em `listar_profissionais`."""
    profissional = profissional_por_id.get(profissional_id)
    return profissional.nome if profissional is not None else profissional_id


def _especialidade_do_profissional(
    profissional_id: str,
    profissional_por_id: dict[str, Profissional],
    grade: list[EntradaGrade],
) -> Especialidade:
    """Especialidade do profissional, resolvida por `listar_profissionais` e, na
    ausência dele ali, pela primeira entrada da grade em que ele aparece — todo
    profissional incluído no relatório vem de um dos dois.
    """
    profissional = profissional_por_id.get(profissional_id)
    if profissional is not None:
        return profissional.especialidade
    return next(
        entrada.especialidade for entrada in grade if entrada.profissional_id == profissional_id
    )
