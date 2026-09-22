"""Testes unitários de `app/api/dependencies.py` que não passam pelo `TestClient`.

Os testes de rota (`tests/api/test_seguranca.py` etc.) sempre sobrescrevem
`obter_fonte`/`obter_continuidade`/`obter_armazenamento_conversas`/`obter_chat_model`
via `app.dependency_overrides` (ver `tests/api/conftest.py`), então a
implementação real dessas dependencies — em especial o caminho de erro de
`validar_api_key` e a construção preguiçosa/singleton de `obter_chat_model` —
nunca é exercitada por ali. Aqui elas são chamadas diretamente, com um `Request`
mínimo (só o que `obter_chat_model` lê: `request.app.state`), sem nenhuma
chamada de rede real.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.dependencies import obter_chat_model, validar_api_key
from app.config import Settings


def _request_com_estado(**estado: Any) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**estado)))


def test_validar_api_key_sem_internal_api_key_configurada_retorna_500() -> None:
    """Servidor mal configurado (sem `INTERNAL_API_KEY`) é um erro operacional
    (500), diferente de uma chave de cliente errada (401)."""
    settings = Settings(internal_api_key=None)

    with pytest.raises(HTTPException) as exc_info:
        validar_api_key(settings, x_api_key="qualquer-coisa")

    assert exc_info.value.status_code == 500
    assert "INTERNAL_API_KEY" in exc_info.value.detail


def test_obter_chat_model_constroi_uma_vez_e_reaproveita(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = 0

    class _ModeloFalso:
        pass

    def _criar_chat_model_falso() -> _ModeloFalso:
        nonlocal chamadas
        chamadas += 1
        return _ModeloFalso()

    monkeypatch.setattr("app.api.dependencies.criar_chat_model", _criar_chat_model_falso)
    request = _request_com_estado()

    primeira = obter_chat_model(request)
    segunda = obter_chat_model(request)

    assert primeira is segunda
    assert chamadas == 1


def test_obter_chat_model_sem_configuracao_retorna_500(monkeypatch: pytest.MonkeyPatch) -> None:
    def _levanta_erro() -> Any:
        raise ValueError('AI_PROVIDER ("openai" ou "google") não configurada.')

    monkeypatch.setattr("app.api.dependencies.criar_chat_model", _levanta_erro)
    request = _request_com_estado()

    with pytest.raises(HTTPException) as exc_info:
        obter_chat_model(request)

    assert exc_info.value.status_code == 500
    assert "AI_PROVIDER" in exc_info.value.detail
