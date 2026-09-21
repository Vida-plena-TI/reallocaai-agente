"""Testes de autenticação (X-API-Key) e da rota pública /health (Fase 6a)."""

from datetime import date

from fastapi.testclient import TestClient

from tests.api.conftest import HEADERS_AUTENTICADOS

DIA = date(2026, 9, 8)


def test_health_continua_publico_sem_exigir_api_key(client: TestClient) -> None:
    resposta = client.get("/health")

    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "service": "realocai"}


def test_disponibilidade_retorna_401_sem_api_key(client: TestClient) -> None:
    resposta = client.get("/agenda/disponibilidade", params={"data": DIA.isoformat()})

    assert resposta.status_code == 401


def test_disponibilidade_retorna_401_com_api_key_incorreta(client: TestClient) -> None:
    resposta = client.get(
        "/agenda/disponibilidade",
        params={"data": DIA.isoformat()},
        headers={"X-API-Key": "chave-errada"},
    )

    assert resposta.status_code == 401


def test_disponibilidade_retorna_200_com_api_key_correta(client: TestClient) -> None:
    resposta = client.get(
        "/agenda/disponibilidade",
        params={"data": DIA.isoformat()},
        headers=HEADERS_AUTENTICADOS,
    )

    assert resposta.status_code == 200


def test_ocupacao_retorna_401_sem_api_key(client: TestClient) -> None:
    resposta = client.get("/agenda/ocupacao", params={"data": DIA.isoformat()})

    assert resposta.status_code == 401


def test_ocupacao_retorna_200_com_api_key_correta(client: TestClient) -> None:
    resposta = client.get(
        "/agenda/ocupacao", params={"data": DIA.isoformat()}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 200
