"""Configuração central da aplicação, carregada a partir de variáveis de ambiente."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    openai_api_key: str = ""

    # Fonte de dados: Google Sheets (app/data_sources)
    google_sheets_credentials_path: str = ""
    google_sheets_spreadsheet_id: str = ""

    # Envio de relatórios por e-mail (app/reports)
    resend_api_key: str = ""
    report_email_from: str = ""
    report_email_to: list[str] = Field(default_factory=list)

    @field_validator("report_email_to", mode="before")
    @classmethod
    def _split_email_list(cls, value: object) -> object:
        """Aceita `REPORT_EMAIL_TO` como lista separada por vírgulas.

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
