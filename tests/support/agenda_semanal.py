"""Construtores de agenda de uma semana para os testes de ocupação por profissional.

Semana de referência: segunda 28/09/2026 a sábado 03/10/2026. Cada bloco do
dia tem 10 slots: manhã de 07:00 a 11:30 e tarde de 13:00 a 17:30.
"""

from datetime import date, time

from app.domain import Atendimento, EntradaGrade, Especialidade, Profissional, Slot

SEGUNDA = date(2026, 9, 28)
TERCA = date(2026, 9, 29)
QUARTA = date(2026, 9, 30)
QUINTA = date(2026, 10, 1)
SEXTA = date(2026, 10, 2)
SABADO = date(2026, 10, 3)
DOMINGO = date(2026, 10, 4)
SEMANA = [SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA, SABADO]

LUCIANA = Profissional(id="luciana", nome="Luciana", especialidade=Especialidade.PSICOLOGIA)
OUTRA = Profissional(id="outra", nome="Outra", especialidade=Especialidade.FONOAUDIOLOGIA)


def horas_da_manha(dia: date) -> list[time]:
    return [slot.hora_inicio for slot in Slot.slots_do_dia(dia) if slot.hora_inicio < time(12)]


def horas_da_tarde(dia: date) -> list[time]:
    return [slot.hora_inicio for slot in Slot.slots_do_dia(dia) if slot.hora_inicio >= time(13)]


def grade(
    dia: date,
    horas: list[time],
    profissional: Profissional = LUCIANA,
    sala_id: str = "sala-12",
    indice_posto: int = 0,
) -> list[EntradaGrade]:
    return [
        EntradaGrade(
            sala_id=sala_id,
            profissional_id=profissional.id,
            especialidade=profissional.especialidade,
            slot=Slot(data=dia, hora_inicio=hora),
            indice_posto=indice_posto,
        )
        for hora in horas
    ]


def atendimentos(
    dia: date,
    horas: list[time],
    profissional: Profissional = LUCIANA,
    sala_id: str = "sala-12",
    indice_posto: int = 0,
    paciente_ids: list[str] | None = None,
) -> list[Atendimento]:
    """Um atendimento de um slot para cada hora informada."""
    return [
        Atendimento(
            id=f"at-{profissional.id}-{dia.isoformat()}-{sala_id}-{indice_posto}-{hora:%H%M}",
            paciente_ids=paciente_ids if paciente_ids is not None else [f"pac-{hora:%H%M}"],
            profissional_id=profissional.id,
            sala_id=sala_id,
            especialidade=profissional.especialidade,
            slots=[Slot(data=dia, hora_inicio=hora)],
            indice_posto=indice_posto,
        )
        for hora in horas
    ]
