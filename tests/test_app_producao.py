"""App montado para produção (`criar_app`): /health público, docs desligados, CORS restrito.

Cada teste monta o próprio app com `Settings(_env_file=None, ...)`, sem o
`lifespan` (o `TestClient` sem `with` não o executa): nada aqui toca Sheets,
IA nem Resend.
"""

import os
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.rastreamento import VARIAVEIS_DE_RASTREAMENTO, desligar_rastreamento_externo
from app.config import Settings, get_settings
from app.main import criar_app

_ORIGEM_LIBERADA = "https://painel.exemplo.com.br"


def _app(**valores: Any) -> FastAPI:
    return criar_app(Settings(_env_file=None, **valores))


@pytest.fixture
def producao() -> TestClient:
    return TestClient(_app(app_env="production", cors_allowed_origins=_ORIGEM_LIBERADA))


def test_health_e_publico_e_nao_depende_de_configuracao(producao: TestClient) -> None:
    resposta = producao.get("/health")

    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}


@pytest.mark.parametrize("caminho", ["/docs", "/redoc", "/openapi.json"])
def test_docs_desligados_em_producao(producao: TestClient, caminho: str) -> None:
    assert producao.get(caminho).status_code == 404


def test_docs_ligados_em_desenvolvimento() -> None:
    cliente = TestClient(_app(app_env="development"))

    assert cliente.get("/docs").status_code == 200
    assert cliente.get("/openapi.json").status_code == 200


def test_docs_enabled_explicito_vence_o_ambiente() -> None:
    assert TestClient(_app(app_env="production", docs_enabled=True)).get("/docs").status_code == 200
    assert (
        TestClient(_app(app_env="development", docs_enabled=False)).get("/docs").status_code == 404
    )


def test_rotas_exigem_api_key_em_producao() -> None:
    aplicacao = _app(app_env="production", cors_allowed_origins=_ORIGEM_LIBERADA)
    aplicacao.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, internal_api_key="chave-certa"
    )
    cliente = TestClient(aplicacao)

    sem_chave = cliente.get("/agenda/ocupacao", params={"data": "2026-09-08"})
    chave_errada = cliente.get(
        "/agenda/ocupacao", params={"data": "2026-09-08"}, headers={"X-API-Key": "chave-errada"}
    )

    assert sem_chave.status_code == 401
    assert chave_errada.status_code == 401


def test_cors_libera_so_a_origem_configurada(producao: TestClient) -> None:
    liberada = producao.get("/health", headers={"Origin": _ORIGEM_LIBERADA})
    outra = producao.get("/health", headers={"Origin": "https://malicioso.exemplo.com"})
    localhost = producao.get("/health", headers={"Origin": "http://localhost:5173"})

    assert liberada.headers["access-control-allow-origin"] == _ORIGEM_LIBERADA
    assert "access-control-allow-origin" not in outra.headers
    assert "access-control-allow-origin" not in localhost.headers


def test_cors_preflight_de_origem_nao_liberada_e_recusado(producao: TestClient) -> None:
    resposta = producao.options(
        "/agenda/chat",
        headers={
            "Origin": "https://malicioso.exemplo.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert resposta.status_code == 400
    assert "access-control-allow-origin" not in resposta.headers


def test_cors_aceita_lista_separada_por_virgula() -> None:
    cliente = TestClient(
        _app(
            app_env="production",
            cors_allowed_origins="https://a.exemplo.com, https://b.exemplo.com",
        )
    )

    for origem in ("https://a.exemplo.com", "https://b.exemplo.com"):
        resposta = cliente.get("/health", headers={"Origin": origem})
        assert resposta.headers["access-control-allow-origin"] == origem


def test_rastreamento_do_langchain_nao_liga_pelo_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    import langsmith.utils
    from langchain_core.globals import get_debug, get_verbose, set_debug

    for nome in VARIAVEIS_DE_RASTREAMENTO:
        monkeypatch.setenv(nome, "true")
    langsmith.utils.get_env_var.cache_clear()  # type: ignore[attr-defined]
    set_debug(True)

    desligar_rastreamento_externo()

    assert not any(nome in os.environ for nome in VARIAVEIS_DE_RASTREAMENTO)
    assert langsmith.utils.tracing_is_enabled() is False
    assert get_debug() is False
    assert get_verbose() is False
