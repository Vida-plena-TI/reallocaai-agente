"""Liga ou desliga o Resend visto por `app.ai.tools`, sem depender do `.env` local."""

import pytest

from app.config import Settings

#: Destinatários padrão usados quando o Resend está ligado nos testes.
DESTINATARIOS_PADRAO = ["equipe@exemplo.com.br"]


def configurar_resend(monkeypatch: pytest.MonkeyPatch, *, ativo: bool) -> None:
    """Faz `envio_de_email_configurado()` devolver `ativo` (e `report_email_to` previsível)."""
    settings = Settings(
        resend_api_key="re_test_key" if ativo else None,
        report_email_from="relatorios@exemplo.com.br" if ativo else None,
        report_email_to=DESTINATARIOS_PADRAO,
    )
    monkeypatch.setattr("app.ai.tools.get_settings", lambda: settings)
