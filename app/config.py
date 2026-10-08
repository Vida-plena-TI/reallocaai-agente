"""Configuração central da aplicação, carregada a partir de variáveis de ambiente."""

import base64
import binascii
import json
from functools import lru_cache
from typing import Annotated, Any, Literal

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


class ConfiguracaoInvalidaError(ValueError):
    """Uma ou mais variáveis de ambiente faltam ou estão inválidas.

    `problemas` traz uma linha por variável, só com o nome e o motivo, nunca o valor.
    """

    def __init__(self, problemas: list[str]) -> None:
        self.problemas = problemas
        linhas = "\n".join(f"  - {problema}" for problema in problemas)
        super().__init__(f"Configuração inválida, corrija as variáveis de ambiente:\n{linhas}")


#: Provedores aceitos em `AI_PROVIDER`, com as variáveis que cada um exige.
_VARIAVEIS_POR_PROVEDOR: dict[str, tuple[str, str]] = {
    "openai": ("openai_api_key", "openai_model"),
    "google": ("google_api_key", "google_model"),
}


def carregar_credencial_google_json(valor: str) -> dict[str, Any]:
    """Decodifica `GOOGLE_SERVICE_ACCOUNT_JSON`: JSON puro ou JSON em base64.

    Tudo acontece em memória, nada é gravado em disco. As mensagens de erro
    nunca repetem o conteúdo da variável, que contém a chave privada.
    """
    texto = valor.strip()
    if not texto.startswith("{"):
        try:
            texto = base64.b64decode("".join(texto.split()), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as erro:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON não é JSON nem base64 de um JSON."
            ) from erro
    try:
        credencial = json.loads(texto)
    except json.JSONDecodeError as erro:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON não contém um JSON válido.") from erro
    if not isinstance(credencial, dict) or not {"client_email", "private_key"} <= credencial.keys():
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON não parece uma service account "
            "(faltam client_email/private_key)."
        )
    return credencial


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
    #: Nível dos logs (stdout). Aceita minúsculas (`info`).
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    #: `/docs`, `/redoc` e `/openapi.json`. Sem valor, ficam ligados só em
    #: `development` (ver `docs_habilitados`).
    docs_enabled: bool | None = None
    #: Porta do servidor de produção (`python -m app`).
    port: int = 8000

    # Integração com IA (app/ai)
    #: `None` por padrão — sem valor chutado no código — em vez de campo
    #: obrigatório: assim `Settings()` continua construível sem `.env`
    #: nenhum, e `import app.main` nunca exige segredo. Validado por `exigir`
    #: dentro de `criar_chat_model` (Fase 5b), o único lugar que precisa dele
    #: de verdade; a suíte de testes automatizados nunca chama essa função.
    #: `ai_provider` escolhe entre "openai" e "google" — sem padrão hardcoded
    #: aqui: `criar_chat_model` exige um valor explícito.
    ai_provider: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    google_api_key: str | None = None
    google_model: str | None = None

    # Fonte de dados: Google Sheets (app/data_sources)
    #: Mesma lógica de `openai_model`: `None` por padrão, validado por `exigir`
    #: só no `lifespan` de `app.main`, onde a fonte de dados real é construída.
    google_sheets_credentials_path: str | None = None
    google_sheets_spreadsheet_id: str | None = None
    #: Conteúdo da credencial (JSON puro ou base64), para produção, onde não há
    #: arquivo. Tem prioridade sobre `google_sheets_credentials_path`.
    google_service_account_json: str | None = None

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

    @field_validator(
        "ai_provider",
        "openai_api_key",
        "openai_model",
        "google_api_key",
        "google_model",
        "google_sheets_credentials_path",
        "google_sheets_spreadsheet_id",
        "google_service_account_json",
        "resend_api_key",
        "report_email_from",
        "internal_api_key",
        mode="before",
    )
    @classmethod
    def _vazio_vira_none(cls, value: object) -> object:
        """`OPENAI_MODEL=` no `.env` conta como não configurada, não como string vazia."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _log_level_em_maiusculas(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @property
    def docs_habilitados(self) -> bool:
        """`DOCS_ENABLED` explícito vence; sem ele, docs só em `development`."""
        if self.docs_enabled is not None:
            return self.docs_enabled
        return self.app_env == "development"

    @property
    def envio_de_email_configurado(self) -> bool:
        """`True` quando `RESEND_API_KEY` e `REPORT_EMAIL_FROM` estão preenchidas."""
        return bool(self.resend_api_key) and bool(self.report_email_from)

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


def validar_configuracao(settings: Settings) -> None:
    """Confere, de uma vez, tudo o que o servidor precisa para atender requisições.

    Chamada na inicialização (`python -m app` e o `lifespan` de `app.main`),
    nunca no import. Junta todos os problemas antes de levantar
    `ConfiguracaoInvalidaError`, para quem configura o deploy corrigir tudo
    numa rodada só. Nenhuma mensagem inclui o valor de uma variável.
    """
    problemas: list[str] = []

    def faltando(nome_do_campo: str) -> bool:
        return getattr(settings, nome_do_campo) is None

    if faltando("internal_api_key"):
        problemas.append("INTERNAL_API_KEY: obrigatória (chave exigida no header X-API-Key).")
    if faltando("google_sheets_spreadsheet_id"):
        problemas.append("GOOGLE_SHEETS_SPREADSHEET_ID: obrigatória.")

    if settings.google_service_account_json is not None:
        try:
            carregar_credencial_google_json(settings.google_service_account_json)
        except ValueError as erro:
            problemas.append(str(erro))
    elif faltando("google_sheets_credentials_path"):
        problemas.append(
            "GOOGLE_SERVICE_ACCOUNT_JSON ou GOOGLE_SHEETS_CREDENTIALS_PATH: "
            "uma das duas é obrigatória (credencial do Google Sheets)."
        )

    provedor = settings.ai_provider
    if provedor is None:
        problemas.append('AI_PROVIDER: obrigatória ("openai" ou "google").')
    elif provedor not in _VARIAVEIS_POR_PROVEDOR:
        problemas.append('AI_PROVIDER: valor não reconhecido (aceitos: "openai" ou "google").')
    else:
        for nome_do_campo in _VARIAVEIS_POR_PROVEDOR[provedor]:
            if faltando(nome_do_campo):
                problemas.append(
                    f"{nome_do_campo.upper()}: obrigatória quando AI_PROVIDER={provedor}."
                )

    if faltando("resend_api_key") != faltando("report_email_from"):
        problemas.append(
            "RESEND_API_KEY e REPORT_EMAIL_FROM: configure as duas juntas, ou nenhuma "
            "(sem elas, o envio de e-mail fica desligado)."
        )

    if "*" in settings.cors_allowed_origins:
        problemas.append('CORS_ALLOWED_ORIGINS: "*" não é aceito, liste as origens.')
    elif settings.app_env == "production" and not settings.cors_allowed_origins:
        problemas.append(
            "CORS_ALLOWED_ORIGINS: obrigatória em produção (origens separadas por vírgula)."
        )

    if problemas:
        raise ConfiguracaoInvalidaError(problemas)


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância única de `Settings` (cacheada por processo)."""
    return Settings()
