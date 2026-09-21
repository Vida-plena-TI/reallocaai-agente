"""Testes de GET /agenda/ocupacao (Fase 6a)."""

from datetime import date, time

from fastapi.testclient import TestClient

from app.data_sources.base import EntradaGrade
from app.domain import Atendimento, Especialidade, Profissional, Sala, Slot
from tests.api.conftest import HEADERS_AUTENTICADOS
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


def _entrada(
    sala_id: str, profissional_id: str, especialidade: Especialidade, hora: time
) -> EntradaGrade:
    return EntradaGrade(
        sala_id=sala_id,
        profissional_id=profissional_id,
        especialidade=especialidade,
        slot=Slot(data=DIA, hora_inicio=hora),
    )


def test_retorna_por_sala_e_por_especialidade_com_abaixo_da_meta(
    client: TestClient, fonte: FakeScheduleDataSource
) -> None:
    fonte.salas[DIA] = [Sala(id="sala-1", nome="Sala 1")]
    fonte.profissionais[DIA] = [
        Profissional(id="prof-1", nome="Ana", especialidade=Especialidade.PSICOLOGIA)
    ]
    fonte.grade[DIA] = [
        _entrada("sala-1", "prof-1", Especialidade.PSICOLOGIA, time(8, 0)),
        _entrada("sala-1", "prof-1", Especialidade.PSICOLOGIA, time(8, 30)),
    ]
    fonte.atendimentos[DIA] = [
        Atendimento(
            id="at-1",
            paciente_ids=["pac-1"],
            profissional_id="prof-1",
            sala_id="sala-1",
            especialidade=Especialidade.PSICOLOGIA,
            slots=[Slot(data=DIA, hora_inicio=time(8, 0))],
        )
    ]

    resposta = client.get(
        "/agenda/ocupacao", params={"data": DIA.isoformat()}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["data"] == DIA.isoformat()

    item_esperado = {
        "rotulo": "Sala 1",
        "slots_escalados": 2,
        "slots_ocupados": 1,
        "percentual": 0.5,
        "abaixo_da_meta": True,
    }
    assert corpo["por_sala"] == [item_esperado]
    assert corpo["por_especialidade"] == [{**item_esperado, "rotulo": "Psicologia"}]


def test_dia_sem_escala_retorna_listas_vazias(client: TestClient) -> None:
    resposta = client.get(
        "/agenda/ocupacao", params={"data": DIA.isoformat()}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["por_sala"] == []
    assert corpo["por_especialidade"] == []
