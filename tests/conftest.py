"""Fixtures globais da suíte."""

import pytest

from tests.support.resend import configurar_resend


@pytest.fixture(autouse=True)
def _resend_desligado_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem Resend por padrão: o `.env` local não decide se `enviar_relatorio` existe.

    Testes que precisam da tool chamam `configurar_resend(monkeypatch, ativo=True)`.
    """
    configurar_resend(monkeypatch, ativo=False)
