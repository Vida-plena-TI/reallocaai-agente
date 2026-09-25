"""Testes de `app.reports.envio` (Fase 7), sem nenhuma chamada de rede real.

`resend.Emails.send` é sempre substituído via monkeypatch por um dublê que
só registra os argumentos recebidos.
"""

from datetime import date, time
from typing import Any

import pytest
import resend

from app.config import Settings
from app.domain import EntradaGrade, Especialidade, Profissional, Slot
from app.reports.envio import enviar_relatorio_por_email
from app.reports.exceptions import ReportsEnvioError
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


def _fonte_com_escala() -> FakeScheduleDataSource:
    return FakeScheduleDataSource(
        grade={
            DIA: [
                EntradaGrade(
                    sala_id="sala-1",
                    profissional_id="prof-1",
                    especialidade=Especialidade.PSICOLOGIA,
                    slot=Slot(data=DIA, hora_inicio=time(8, 0)),
                    indice_posto=0,
                )
            ]
        },
        profissionais={
            DIA: [Profissional(id="prof-1", nome="Ana", especialidade=Especialidade.PSICOLOGIA)]
        },
    )


def _settings_com_resend(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "resend_api_key": "re_test_key",
        "report_email_from": "relatorios@vidaplenamulti.com.br",
        "report_email_to": ["equipe@vidaplenamulti.com.br"],
    }
    base.update(overrides)
    return Settings(**base)


def test_envio_bem_sucedido_chama_resend_com_destinatarios_assunto_e_corpos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []

    def fake_send(params: dict[str, Any]) -> dict[str, str]:
        chamadas.append(params)
        return {"id": "email-fake"}

    monkeypatch.setattr(resend.Emails, "send", fake_send)
    monkeypatch.setattr("app.reports.envio.get_settings", lambda: _settings_com_resend())

    enviar_relatorio_por_email(_fonte_com_escala(), DIA)

    assert len(chamadas) == 1
    params = chamadas[0]
    assert params["to"] == ["equipe@vidaplenamulti.com.br"]
    assert params["from"] == "relatorios@vidaplenamulti.com.br"
    assert "RealocAI" in params["subject"]
    assert params["html"]
    assert params["text"]


def test_destinatarios_none_usa_report_email_to(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []

    def fake_send(params: dict[str, Any]) -> dict[str, str]:
        chamadas.append(params)
        return {"id": "email-fake"}

    monkeypatch.setattr(resend.Emails, "send", fake_send)
    monkeypatch.setattr(
        "app.reports.envio.get_settings",
        lambda: _settings_com_resend(report_email_to=["padrao@vidaplenamulti.com.br"]),
    )

    enviar_relatorio_por_email(_fonte_com_escala(), DIA, destinatarios=None)

    assert chamadas[0]["to"] == ["padrao@vidaplenamulti.com.br"]


def test_destinatarios_explicito_sobrepoe_report_email_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []

    def fake_send(params: dict[str, Any]) -> dict[str, str]:
        chamadas.append(params)
        return {"id": "email-fake"}

    monkeypatch.setattr(resend.Emails, "send", fake_send)
    monkeypatch.setattr(
        "app.reports.envio.get_settings",
        lambda: _settings_com_resend(report_email_to=["padrao@vidaplenamulti.com.br"]),
    )

    enviar_relatorio_por_email(
        _fonte_com_escala(), DIA, destinatarios=["explicito@vidaplenamulti.com.br"]
    )

    assert chamadas[0]["to"] == ["explicito@vidaplenamulti.com.br"]


def test_erro_do_resend_e_relancado_como_reports_envio_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_send_com_erro(params: dict[str, Any]) -> dict[str, str]:
        raise resend.exceptions.ApplicationError(
            message="falha simulada", error_type="internal_error", code=500
        )

    monkeypatch.setattr(resend.Emails, "send", fake_send_com_erro)
    monkeypatch.setattr("app.reports.envio.get_settings", lambda: _settings_com_resend())

    with pytest.raises(ReportsEnvioError):
        enviar_relatorio_por_email(_fonte_com_escala(), DIA)
