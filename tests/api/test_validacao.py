"""Testes de validação de query params da API (Fase 6a)."""

from fastapi.testclient import TestClient

from tests.api.conftest import HEADERS_AUTENTICADOS


def test_data_invalida_em_disponibilidade_retorna_422_com_mensagem_legivel(
    client: TestClient,
) -> None:
    resposta = client.get(
        "/agenda/disponibilidade",
        params={"data": "não-é-uma-data"},
        headers=HEADERS_AUTENTICADOS,
    )

    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert detalhe
    assert "data" in detalhe[0]["loc"]


def test_especialidade_invalida_retorna_422_com_mensagem_legivel(client: TestClient) -> None:
    resposta = client.get(
        "/agenda/disponibilidade",
        params={"data": "2026-09-08", "especialidade": "especialidade-inexistente"},
        headers=HEADERS_AUTENTICADOS,
    )

    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert detalhe
    assert "especialidade" in detalhe[0]["loc"]


def test_data_invalida_em_ocupacao_retorna_422(client: TestClient) -> None:
    resposta = client.get(
        "/agenda/ocupacao", params={"data": "não-é-uma-data"}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 422


def test_data_ausente_retorna_422(client: TestClient) -> None:
    resposta = client.get("/agenda/disponibilidade", headers=HEADERS_AUTENTICADOS)

    assert resposta.status_code == 422
