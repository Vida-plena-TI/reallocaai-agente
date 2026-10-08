"""Testes de POST /relatorio/enviar (Fase 7).

`enviar_relatorio_por_email` é sempre substituída via monkeypatch — nenhum
teste aqui faz chamada de rede real ao Resend.
"""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.reports.exceptions import ReportsEnvioError
from tests.api.conftest import (
    API_KEY,
    HEADERS_AUTENTICADOS,
    REPORT_EMAIL_FROM_TESTE,
    RESEND_API_KEY_TESTE,
)

DIA = date(2026, 9, 8)


def test_envio_bem_sucedido_devolve_200_com_destinatarios_resolvidos(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.api.routes.enviar_relatorio_por_email",
        lambda fonte, data, destinatarios: chamadas.append(
            {"data": data, "destinatarios": destinatarios}
        ),
    )

    resposta = client.post(
        "/relatorio/enviar",
        json={"data": DIA.isoformat(), "destinatarios": ["ana@vidaplenamulti.com.br"]},
        headers=HEADERS_AUTENTICADOS,
    )

    assert resposta.status_code == 200
    assert resposta.json() == {"enviado": True, "destinatarios": ["ana@vidaplenamulti.com.br"]}
    assert chamadas == [{"data": DIA, "destinatarios": ["ana@vidaplenamulti.com.br"]}]


def test_destinatarios_omitidos_usa_report_email_to(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[Any] = []
    monkeypatch.setattr(
        "app.api.routes.enviar_relatorio_por_email",
        lambda fonte, data, destinatarios: chamadas.append(destinatarios),
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        openai_model="gpt-4o-mini",
        internal_api_key=API_KEY,
        report_email_to=["padrao@vidaplenamulti.com.br"],
        resend_api_key=RESEND_API_KEY_TESTE,
        report_email_from=REPORT_EMAIL_FROM_TESTE,
    )

    resposta = client.post("/relatorio/enviar", json={}, headers=HEADERS_AUTENTICADOS)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["destinatarios"] == ["padrao@vidaplenamulti.com.br"]
    # `destinatarios=None` é o que deve ser repassado para `enviar_relatorio_por_email`
    # (resolução do padrão acontece só para compor a resposta, não altera a chamada).
    assert chamadas == [None]


def test_erro_no_envio_devolve_502_sem_vazar_detalhe_interno(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_envio_com_erro(fonte: Any, data: date, destinatarios: list[str] | None) -> None:
        raise ReportsEnvioError("detalhe interno sensível do Resend")

    monkeypatch.setattr("app.api.routes.enviar_relatorio_por_email", fake_envio_com_erro)

    resposta = client.post("/relatorio/enviar", json={}, headers=HEADERS_AUTENTICADOS)

    assert resposta.status_code == 502
    assert "detalhe interno sensível do Resend" not in resposta.text


def test_sem_api_key_devolve_401(client: TestClient) -> None:
    resposta = client.post("/relatorio/enviar", json={})

    assert resposta.status_code == 401


def test_resend_nao_configurado_devolve_503_sem_tentar_enviar(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[Any] = []
    monkeypatch.setattr(
        "app.api.routes.enviar_relatorio_por_email",
        lambda fonte, data, destinatarios: chamadas.append(data),
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        internal_api_key=API_KEY, resend_api_key=None, report_email_from=None
    )

    resposta = client.post("/relatorio/enviar", json={}, headers=HEADERS_AUTENTICADOS)

    assert resposta.status_code == 503
    assert resposta.json() == {"detail": "O envio de e-mail não está configurado neste ambiente."}
    assert chamadas == []
