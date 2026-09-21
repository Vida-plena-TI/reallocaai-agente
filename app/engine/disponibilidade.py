"""Disponibilidade de horários: quais slots estão livres num dia.

Fase 4a: só le a grade e os atendimentos já existentes e diz o que está livre.
Não há ranking, priorização, encaixe casado ou realocação aqui — isso é a Fase
4b. A função funciona com qualquer `ScheduleDataSource`, nunca com uma
implementação específica.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.data_sources.base import ScheduleDataSource
from app.domain import Especialidade
from app.domain.slot import Slot


class SlotDisponivel(BaseModel):
    """Um horário livre: sala e profissional escalados, sem atendimento cobrindo."""

    model_config = ConfigDict(frozen=True)

    slot: Slot
    sala_id: str
    profissional_id: str
    especialidade: Especialidade


def listar_disponibilidade(
    origem: ScheduleDataSource,
    dia: date,
    *,
    especialidade: Especialidade | None = None,
    profissional_id: str | None = None,
    sala_id: str | None = None,
    excluir_atendimento_id: str | None = None,
) -> list[SlotDisponivel]:
    """Horários livres do dia que casam com os filtros informados.

    "Livre" significa: existe uma entrada em `listar_grade` (profissional
    escalado naquela sala+slot) e não existe nenhum `Atendimento` daquele
    profissional cobrindo aquele slot. Um atendimento em grupo ocupa o slot
    normalmente — não abre vaga extra por ter mais de um paciente.

    `excluir_atendimento_id`, quando informado, ignora esse `Atendimento`
    específico ao calcular o que está ocupado — usado para buscar realocação
    de um atendimento sem que ele conflite consigo mesmo.

    O resultado é ordenado por horário e depois por sala, de forma estável e
    sem nenhuma priorização (isso é Fase 4b).
    """
    grade = origem.listar_grade(dia)
    atendimentos = origem.listar_atendimentos(dia)
    especialidade_por_profissional = {
        profissional.id: profissional.especialidade
        for profissional in origem.listar_profissionais(dia)
    }

    slots_ocupados_por_profissional: dict[str, set[Slot]] = {}
    for atendimento in atendimentos:
        if atendimento.id == excluir_atendimento_id:
            continue
        ocupados = slots_ocupados_por_profissional.setdefault(atendimento.profissional_id, set())
        ocupados.update(atendimento.slots)

    disponiveis: list[SlotDisponivel] = []
    for entrada in grade:
        if entrada.slot in slots_ocupados_por_profissional.get(entrada.profissional_id, set()):
            continue

        especialidade_do_profissional = especialidade_por_profissional.get(
            entrada.profissional_id, entrada.especialidade
        )
        if especialidade is not None and especialidade_do_profissional != especialidade:
            continue
        if profissional_id is not None and entrada.profissional_id != profissional_id:
            continue
        if sala_id is not None and entrada.sala_id != sala_id:
            continue

        disponiveis.append(
            SlotDisponivel(
                slot=entrada.slot,
                sala_id=entrada.sala_id,
                profissional_id=entrada.profissional_id,
                especialidade=especialidade_do_profissional,
            )
        )

    return sorted(disponiveis, key=lambda disponivel: (disponivel.slot, disponivel.sala_id))
