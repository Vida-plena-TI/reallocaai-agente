"""Ponto de entrada da API do RealocAI."""

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.rastreamento import desligar_rastreamento_externo
from app.api.routes import router
from app.api.sessoes import ArmazenamentoConversas
from app.config import Settings, get_settings, validar_configuracao
from app.data_sources.cache import CacheadoScheduleDataSource
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource

#: Em desenvolvimento, o front-end local (chat/widget, Fase 6b) é liberado
#: automaticamente, sem exigir configuração manual em `CORS_ALLOWED_ORIGINS`.
_ORIGENS_LOCAIS_DE_DESENVOLVIMENTO = ("http://localhost:3000", "http://localhost:5173")

_FORMATO_DOS_LOGS = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configurar_logs(nivel: str) -> None:
    """Logs da aplicação só em stdout, no nível de `LOG_LEVEL`.

    Os loggers do uvicorn têm handlers próprios (e `propagate=False`), então
    não duplicam aqui.
    """
    logging.basicConfig(level=nivel, format=_FORMATO_DOS_LOGS, stream=sys.stdout, force=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Valida a configuração e constrói as fontes de dados uma única vez, no startup.

    Nunca em import-time. Guardadas em `app.state` para `obter_fonte`/
    `obter_continuidade` (`app/api/dependencies.py`) lerem; nos testes, essas
    dependencies são substituídas via `app.dependency_overrides`, então este
    `lifespan` nunca chega a rodar com uma credencial real do Google.
    """
    settings_atual = get_settings()
    configurar_logs(settings_atual.log_level)
    desligar_rastreamento_externo()
    # Falha aqui, listando tudo o que falta, em vez de na primeira requisição.
    validar_configuracao(settings_atual)

    fonte_real = GoogleSheetsDataSource()
    app.state.fonte_agenda = CacheadoScheduleDataSource(
        fonte_real, ttl_segundos=settings_atual.cache_ttl_segundos
    )
    app.state.continuidade = SemHistoricoContinuidadeDataSource()
    app.state.conversas = ArmazenamentoConversas()
    yield


def criar_app(settings: Settings) -> FastAPI:
    """Monta a aplicação a partir de `settings` (CORS e docs dependem do ambiente).

    Só lê campos com default seguro (`app_env`, `cors_allowed_origins`,
    `docs_enabled`), por isso montar o app nunca exige segredo configurado.
    """
    docs = settings.docs_habilitados
    aplicacao = FastAPI(
        title="RealocAI",
        description="Agente de IA para otimização de agenda de clínica multidisciplinar.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    origens_liberadas = list(settings.cors_allowed_origins)
    if settings.app_env == "development":
        origens_liberadas += [
            origem
            for origem in _ORIGENS_LOCAIS_DE_DESENVOLVIMENTO
            if origem not in origens_liberadas
        ]

    aplicacao.add_middleware(
        CORSMiddleware,
        allow_origins=origens_liberadas,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    aplicacao.include_router(router)

    @aplicacao.get("/health")
    def health() -> dict[str, str]:
        """Verificação de disponibilidade. Pública, sem X-API-Key e sem tocar em
        Sheets, IA ou Resend."""
        return {"status": "ok"}

    return aplicacao


app = criar_app(get_settings())
