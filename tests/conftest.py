"""Fixtures globais da suíte."""

from collections.abc import Iterator

import pytest
import resend

from tests.support.resend import configurar_resend


@pytest.fixture(autouse=True)
def _resend_desligado_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem Resend por padrão: o `.env` local não decide se `enviar_relatorio` existe.

    Testes que precisam da tool chamam `configurar_resend(monkeypatch, ativo=True)`.
    """
    configurar_resend(monkeypatch, ativo=False)


@pytest.fixture(autouse=True)
def _resend_sem_rede(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Nenhum teste fala com o Resend de verdade, mesmo com credenciais reais no `.env`.

    `app.reports.envio` lê `get_settings()` do ambiente: um teste que esqueça de
    substituir o envio chegaria à API real. Aqui o envio é bloqueado e, como o
    envio transforma qualquer erro em `ReportsEnvioError` (um 502 que passaria
    despercebido), a tentativa também reprova o teste no fim. Quem precisa do
    envio substitui `resend.Emails.send` no próprio teste (o monkeypatch do
    teste vem depois e vence).
    """
    tentativas: list[object] = []

    def bloqueado(params: object) -> None:
        tentativas.append(params)
        raise AssertionError("teste tentou enviar e-mail pelo Resend real")

    monkeypatch.setattr(resend.Emails, "send", bloqueado)
    yield
    assert not tentativas, "teste tentou enviar e-mail pelo Resend real"
