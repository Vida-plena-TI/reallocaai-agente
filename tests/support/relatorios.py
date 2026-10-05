"""Agenda fictícia com grupos e pacientes reconhecíveis para contratos do chat."""

from app.domain import Especialidade, Paciente, Profissional, Sala
from tests.support.agenda_semanal import SEGUNDA, TERCA, atendimentos, grade, horas_da_manha
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

ANA = Profissional(id="ana-exemplo", nome="Ana Exemplo", especialidade=Especialidade.PSICOLOGIA)
BIA = Profissional(id="bia-exemplo", nome="Bia Exemplo", especialidade=Especialidade.FONOAUDIOLOGIA)
NOMES_PACIENTES = ["Paciente Sigiloso Alfa", "Paciente Sigiloso Beta"]


def fonte_relatorios() -> FakeScheduleDataSource:
    manha = horas_da_manha(SEGUNDA)
    terca = horas_da_manha(TERCA)
    return FakeScheduleDataSource(
        profissionais={SEGUNDA: [ANA, BIA], TERCA: [ANA]},
        salas={SEGUNDA: [Sala(id="sala-12", nome="Sala Azul", capacidade_simultanea=2)]},
        pacientes={SEGUNDA: [Paciente(id=n, nome=n) for n in NOMES_PACIENTES]},
        grade={
            SEGUNDA: grade(SEGUNDA, manha, ANA) + grade(SEGUNDA, manha, BIA, indice_posto=1),
            TERCA: grade(TERCA, terca, ANA),
        },
        atendimentos={
            SEGUNDA: atendimentos(SEGUNDA, manha[:3], ANA, paciente_ids=NOMES_PACIENTES)
            + atendimentos(
                SEGUNDA, manha[3:5], BIA, indice_posto=1, paciente_ids=[NOMES_PACIENTES[0]]
            ),
            TERCA: atendimentos(TERCA, terca[:2], ANA, paciente_ids=[NOMES_PACIENTES[0]]),
        },
    )
