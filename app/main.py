"""Ponto de entrada da API do RealocAI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import exigir, get_settings
from app.data_sources.cache import CacheadoScheduleDataSource
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource

#: Em desenvolvimento, o front-end local (chat/widget, Fase 6b) é liberado
#: automaticamente, sem exigir configuração manual em `CORS_ALLOWED_ORIGINS`.
_ORIGENS_LOCAIS_DE_DESENVOLVIMENTO = ("http://localhost:3000", "http://localhost:5173")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Constrói as fontes de dados uma única vez, no startup — nunca em import-time.

    Guardadas em `app.state` para `obter_fonte`/`obter_continuidade`
    (`app/api/dependencies.py`) lerem; nos testes, essas dependencies são
    substituídas via `app.dependency_overrides`, então este `lifespan` nunca
    chega a rodar com uma credencial real do Google.
    """
    settings_atual = get_settings()
    # `google_sheets_credentials_path`/`google_sheets_spreadsheet_id` são `None` em
    # `Settings` até aqui — validados só neste ponto de uso (o startup real da
    # aplicação), não na classe, para `import app.main` continuar funcionando sem
    # nenhum segredo configurado (ver `exigir`).
    exigir(settings_atual.google_sheets_credentials_path, "GOOGLE_SHEETS_CREDENTIALS_PATH")
    exigir(settings_atual.google_sheets_spreadsheet_id, "GOOGLE_SHEETS_SPREADSHEET_ID")

    fonte_real = GoogleSheetsDataSource()
    app.state.fonte_agenda = CacheadoScheduleDataSource(
        fonte_real, ttl_segundos=settings_atual.cache_ttl_segundos
    )
    app.state.continuidade = SemHistoricoContinuidadeDataSource()
    yield


app = FastAPI(
    title="RealocAI",
    description="Agente de IA para otimização de agenda de clínica multidisciplinar.",
    version="0.1.0",
    lifespan=lifespan,
)

# Só `cors_allowed_origins` e `app_env` são necessários para montar o CORS — os
# dois únicos campos de `Settings` com um default seguro (ver `app/config.py`) —
# por isso a linha de import deste módulo nunca exige nenhum segredo configurado.
settings = get_settings()
_origens_liberadas = list(settings.cors_allowed_origins)
if settings.app_env == "development":
    _origens_liberadas += [
        origem for origem in _ORIGENS_LOCAIS_DE_DESENVOLVIMENTO if origem not in _origens_liberadas
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origens_liberadas,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    """Verificação de disponibilidade do serviço. Pública, sem exigir X-API-Key."""
    return {"status": "ok", "service": "realocai"}
