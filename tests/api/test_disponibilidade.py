"""Testes de GET /agenda/disponibilidade (Fase 6a)."""

from datetime import date, time

from fastapi.testclient import TestClient

from app.domain import EntradaGrade, Especialidade, Profissional, Sala, Slot
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
        indice_posto=0,
    )


def test_filtra_por_especialidade(client: TestClient, fonte: FakeScheduleDataSource) -> None:
    fonte.salas[DIA] = [Sala(id="sala-1", nome="Sala 1")]
    fonte.profissionais[DIA] = [
        Profissional(id="prof-psico", nome="Ana", especialidade=Especialidade.PSICOLOGIA),
        Profissional(id="prof-fono", nome="Bia", especialidade=Especialidade.FONOAUDIOLOGIA),
    ]
    fonte.grade[DIA] = [
        _entrada("sala-1", "prof-psico", Especialidade.PSICOLOGIA, time(8, 0)),
        _entrada("sala-1", "prof-fono", Especialidade.FONOAUDIOLOGIA, time(8, 30)),
    ]

    resposta = client.get(
        "/agenda/disponibilidade",
        params={"data": DIA.isoformat(), "especialidade": "psicologia"},
        headers=HEADERS_AUTENTICADOS,
    )

    assert resposta.status_code == 200
    assert resposta.json() == [
        {
            "horario": "08:00",
            "sala": "Sala 1",
            "profissional": "Ana",
            "especialidade": "Psicologia",
        }
    ]


def test_sem_filtro_retorna_todos_os_horarios_livres(
    client: TestClient, fonte: FakeScheduleDataSource
) -> None:
    fonte.salas[DIA] = [Sala(id="sala-1", nome="Sala 1")]
    fonte.profissionais[DIA] = [
        Profissional(id="prof-psico", nome="Ana", especialidade=Especialidade.PSICOLOGIA)
    ]
    fonte.grade[DIA] = [
        _entrada("sala-1", "prof-psico", Especialidade.PSICOLOGIA, time(8, 0)),
        _entrada("sala-1", "prof-psico", Especialidade.PSICOLOGIA, time(8, 30)),
    ]

    resposta = client.get(
        "/agenda/disponibilidade", params={"data": DIA.isoformat()}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 200
    assert len(resposta.json()) == 2


def test_sala_e_profissional_sem_cadastro_caem_para_o_proprio_id(
    client: TestClient, fonte: FakeScheduleDataSource
) -> None:
    """Sem `listar_salas`/`listar_profissionais` preenchidos, o id vira o rótulo."""
    fonte.grade[DIA] = [
        _entrada("sala-fantasma", "prof-fantasma", Especialidade.PSICOLOGIA, time(8, 0))
    ]

    resposta = client.get(
        "/agenda/disponibilidade", params={"data": DIA.isoformat()}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo[0]["sala"] == "sala-fantasma"
    assert corpo[0]["profissional"] == "prof-fantasma"
