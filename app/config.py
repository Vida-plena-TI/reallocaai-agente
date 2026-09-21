"""Configuração central da aplicação, carregada a partir de variáveis de ambiente."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def exigir(valor: str | None, nome_da_variavel: str) -> str:
    """Devolve `valor`, ou levanta `ValueError` claro se ele for `None`.

    Os segredos e credenciais de `Settings` são opcionais na classe (`None`
    por padrão) de propósito: só quem realmente vai usá-los — `criar_chat_model`,
    `validar_api_key`, o `lifespan` de `app.main` — chama `exigir` no momento
    do uso. Isso mantém `Settings()` (e portanto `import app.main`) sempre
    construível, mesmo num ambiente sem nenhuma variável configurada (CI,
    clone limpo, testes automatizados).
    """
    if valor is None:
        raise ValueError(f"Variável de ambiente {nome_da_variavel} não configurada.")
    return valor


class Settings(BaseSettings):
    """Variáveis de ambiente do RealocAI.

    Os valores são lidos do ambiente do processo e, se existir, do arquivo `.env`
    na raiz do projeto. Veja `.env.example` para a lista de chaves esperadas.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Ambiente de execução
    app_env: Literal["development", "staging", "production"] = "development"

    # Integração com IA (app/ai)
    #: `None` por padrão — sem valor chutado no código — em vez de campo
    #: obrigatório: assim `Settings()` continua construível sem `.env`
    #: nenhum, e `import app.main` nunca exige segredo. Validado por `exigir`
    #: dentro de `criar_chat_model` (Fase 5b), o único lugar que precisa dele
    #: de verdade; a suíte de testes automatizados nunca chama essa função.
    openai_api_key: str | None = None
    openai_model: str | None = None

    # Fonte de dados: Google Sheets (app/data_sources)
    #: Mesma lógica de `openai_model`: `None` por padrão, validado por `exigir`
    #: só no `lifespan` de `app.main`, onde a fonte de dados real é construída.
    google_sheets_credentials_path: str | None = None
    google_sheets_spreadsheet_id: str | None = None

    # Envio de relatórios por e-mail (app/reports)
    resend_api_key: str | None = None
    report_email_from: str | None = None
    # `NoDecode` desliga o parsing JSON que o pydantic-settings aplicaria a campos de lista
    # antes dos validadores: sem isso, `REPORT_EMAIL_TO=a@b.com,c@d.com` quebraria a leitura
    # do `.env` e o validador abaixo nunca seria chamado.
    report_email_to: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # API HTTP interna (app/api)
    #: Mesma lógica de `openai_model`: `None` por padrão, validado por `exigir`
    #: dentro de `validar_api_key`, no momento em que uma rota autenticada é
    #: chamada — não em `Settings`, para `import app.main` nunca exigir isso.
    internal_api_key: str | None = None
    #: Vazio por padrão (nenhuma origem liberada em produção até o chat/widget
    #: da Fase 6b existir). Em desenvolvimento, `app/main.py` libera `localhost`
    #: automaticamente, independente desta variável. Já tem um default seguro:
    #: é um dos únicos campos necessários para `app.main` construir o app.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: TTL do cache de leitura da agenda (`CacheadoScheduleDataSource`). Já tem
    #: um default seguro, pelo mesmo motivo de `cors_allowed_origins` acima.
    cache_ttl_segundos: int = 60

    @field_validator("report_email_to", "cors_allowed_origins", mode="before")
    @classmethod
    def _split_lista_separada_por_virgula(cls, value: object) -> object:
        """Aceita `REPORT_EMAIL_TO`/`CORS_ALLOWED_ORIGINS` como lista separada por vírgulas.

        Sem isso, o pydantic-settings exigiria JSON (`["a@b.com"]`) no `.env`.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância única de `Settings` (cacheada por processo)."""
    return Settings()
