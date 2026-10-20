"""Semana fictícia de uma clínica inteira, para o motor de relatório genérico.

Semana de 28/09/2026 (segunda) a 03/10/2026 (sábado), sem nenhum dado real.
Reproduz o que importa para os relatórios: sala com merge (Sala 2, dois
postos de profissionais diferentes), sala de número 10 para a ordem natural,
sessões de 1 h e 1 h 30, sessão em grupo, buracos entre atendimentos,
atendimento aguardando autorização, convênios variados e slot `FECHADO`
(07:00 e 07:30 do sábado na Sala 10 não estão na grade).

A agenda é consistente (todo atendimento cai numa entrada de grade do mesmo
posto): é a base dos testes de equivalência com as engines antigas.
"""

from datetime import date, time

from app.domain import (
    Atendimento,
    Convenio,
    EntradaGrade,
    Especialidade,
    Paciente,
    Profissional,
    Sala,
    Slot,
    normalizar_id,
)
from tests.support.agenda_semanal import (
    QUARTA,
    QUINTA,
    SABADO,
    SEGUNDA,
    SEMANA,
    SEXTA,
    TERCA,
    horas_da_manha,
    horas_da_tarde,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

ANA = Profissional(id="ana-lima", nome="Ana Lima", especialidade=Especialidade.PSICOLOGIA)
BETO = Profissional(id="beto-reis", nome="Beto Reis", especialidade=Especialidade.FONOAUDIOLOGIA)
CAU = Profissional(id="cau-melo", nome="Cau Melo", especialidade=Especialidade.TERAPIA_OCUPACIONAL)
DIVA = Profissional(id="diva-rocha", nome="Diva Rocha", especialidade=Especialidade.PSICOLOGIA)

SALA_1 = Sala(id="sala-1", nome="Sala 1")
SALA_2 = Sala(id="sala-2", nome="Sala 2", capacidade_simultanea=2)
SALA_10 = Sala(id="sala-10", nome="Sala 10")

#: Letra -> (nome fictício, convênio). Os nomes nunca podem aparecer em bloco
#: nenhum, exceto na agenda do próprio paciente pedido.
PACIENTES: dict[str, tuple[str, Convenio | None]] = {
    "A": ("Paciente Ficticio Alfa", Convenio.UNIMED),
    "B": ("Paciente Ficticio Bravo", Convenio.SULAMERICA),
    "C": ("Paciente Ficticio Charlie", Convenio.PARTICULAR),
    "D": ("Paciente Ficticio Delta", Convenio.KLINI_SAUDE),
    "E": ("Paciente Ficticio Echo", Convenio.UNIMED),
    "F": ("Paciente Ficticio Foxtrot", None),
    "G": ("Paciente Ficticio Golf", Convenio.PARTICULAR),
    "H": ("Paciente Ficticio Hotel", Convenio.UNIMED),
    "I": ("Paciente Ficticio India", Convenio.SULAMERICA),
    "J": ("Paciente Ficticio Juliett", None),
}
NOMES_PACIENTES = [nome for nome, _ in PACIENTES.values()]
IDS_PACIENTES = [normalizar_id(nome) for nome in NOMES_PACIENTES]

#: (profissional, sala, posto) e os dias e blocos em que está escalado.
_ESCALAS: list[tuple[Profissional, Sala, int, list[date], bool, bool]] = [
    (ANA, SALA_1, 0, [SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA], True, False),
    (BETO, SALA_2, 0, [SEGUNDA, TERCA, QUARTA], True, True),
    (CAU, SALA_2, 1, [SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA], False, True),
    (DIVA, SALA_10, 0, [QUINTA], False, True),
    (DIVA, SALA_10, 0, [SABADO], True, False),
]

#: (dia, profissional, início, slots, pacientes, aguardando autorização).
_SESSOES: list[tuple[date, Profissional, time, int, str, bool]] = [
    # Segunda
    (SEGUNDA, ANA, time(8, 0), 2, "A", False),
    (SEGUNDA, ANA, time(9, 30), 1, "B", False),
    (SEGUNDA, ANA, time(10, 30), 2, "C", False),
    (SEGUNDA, BETO, time(8, 0), 3, "F", False),
    (SEGUNDA, BETO, time(13, 0), 2, "G", False),
    (SEGUNDA, BETO, time(15, 0), 1, "H", False),
    (SEGUNDA, CAU, time(13, 0), 2, "I", False),
    (SEGUNDA, CAU, time(14, 30), 1, "J", False),
    # Terça: Alfa passa por três profissionais, com buracos entre as terapias.
    (TERCA, ANA, time(8, 0), 2, "A", False),
    (TERCA, ANA, time(10, 0), 2, "DE", False),
    (TERCA, BETO, time(8, 0), 3, "F", False),
    (TERCA, BETO, time(13, 0), 1, "A", False),
    (TERCA, CAU, time(14, 0), 1, "A", False),
    (TERCA, CAU, time(15, 0), 3, "B", False),
    # Quarta: Ana sem nenhum atendimento.
    (QUARTA, BETO, time(14, 0), 2, "G", True),
    (QUARTA, CAU, time(13, 0), 1, "C", False),
    # Quinta
    (QUINTA, ANA, time(7, 0), 2, "B", True),
    (QUINTA, CAU, time(16, 0), 2, "H", False),
    (QUINTA, DIVA, time(13, 0), 4, "D", False),
    # Sexta
    (SEXTA, ANA, time(9, 0), 2, "A", False),
    (SEXTA, CAU, time(13, 0), 1, "I", False),
    # Sábado: Sala 10 fechada às 07:00 e 07:30.
    (SABADO, DIVA, time(8, 0), 2, "E", False),
]

_POSTO_DE: dict[str, tuple[Sala, int]] = {
    ANA.id: (SALA_1, 0),
    BETO.id: (SALA_2, 0),
    CAU.id: (SALA_2, 1),
    DIVA.id: (SALA_10, 0),
}

#: Horários `FECHADO` (fora da grade) por (dia, sala).
_FECHADOS: dict[tuple[date, str], set[time]] = {(SABADO, SALA_10.id): {time(7, 0), time(7, 30)}}


def _grade() -> dict[date, list[EntradaGrade]]:
    grade: dict[date, list[EntradaGrade]] = {dia: [] for dia in SEMANA}
    for profissional, sala, posto, dias, manha, tarde in _ESCALAS:
        for dia in dias:
            horas = (horas_da_manha(dia) if manha else []) + (horas_da_tarde(dia) if tarde else [])
            fechados = _FECHADOS.get((dia, sala.id), set())
            grade[dia].extend(
                EntradaGrade(
                    sala_id=sala.id,
                    profissional_id=profissional.id,
                    especialidade=profissional.especialidade,
                    slot=Slot(data=dia, hora_inicio=hora),
                    indice_posto=posto,
                )
                for hora in horas
                if hora not in fechados
            )
    return grade


def _atendimentos() -> dict[date, list[Atendimento]]:
    atendimentos: dict[date, list[Atendimento]] = {dia: [] for dia in SEMANA}
    for dia, profissional, inicio, quantidade, letras, aguardando in _SESSOES:
        slots_do_dia = Slot.slots_do_dia(dia)
        primeiro = next(i for i, s in enumerate(slots_do_dia) if s.hora_inicio == inicio)
        sala, posto = _POSTO_DE[profissional.id]
        atendimentos[dia].append(
            Atendimento(
                id=f"{dia.isoformat()}-{profissional.id}-{inicio:%H%M}",
                paciente_ids=[normalizar_id(PACIENTES[letra][0]) for letra in letras],
                profissional_id=profissional.id,
                sala_id=sala.id,
                especialidade=profissional.especialidade,
                slots=slots_do_dia[primeiro : primeiro + quantidade],
                aguardando_autorizacao=aguardando,
                indice_posto=posto,
            )
        )
    return atendimentos


def fonte_clinica(dias_com_falha: set[date] | None = None) -> FakeScheduleDataSource:
    """A semana fictícia inteira; `dias_com_falha` simula abas ilegíveis."""
    grade = _grade()
    atendimentos = _atendimentos()
    profissionais: dict[date, list[Profissional]] = {}
    salas: dict[date, list[Sala]] = {}
    pacientes: dict[date, list[Paciente]] = {}
    por_id = {p.id: p for p in (ANA, BETO, CAU, DIVA)}
    salas_por_id = {s.id: s for s in (SALA_1, SALA_2, SALA_10)}
    for dia in SEMANA:
        profissionais[dia] = [por_id[i] for i in sorted({e.profissional_id for e in grade[dia]})]
        salas[dia] = [salas_por_id[i] for i in sorted({e.sala_id for e in grade[dia]})]
        ids = {pid for a in atendimentos[dia] for pid in a.paciente_ids}
        pacientes[dia] = [
            Paciente(id=normalizar_id(nome), nome=nome, convenio=convenio)
            for nome, convenio in PACIENTES.values()
            if normalizar_id(nome) in ids
        ]
    return FakeScheduleDataSource(
        salas=salas,
        profissionais=profissionais,
        pacientes=pacientes,
        grade=grade,
        atendimentos=atendimentos,
        dias_com_falha=dias_com_falha or set(),
    )
