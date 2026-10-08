"""Validação da configuração na inicialização (`validar_configuracao`) e credencial em JSON.

`Settings(_env_file=None, ...)` ignora o `.env` local: cada teste decide
exatamente o que está configurado.
"""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.config import (
    ConfiguracaoInvalidaError,
    Settings,
    carregar_credencial_google_json,
    validar_configuracao,
)

_RAIZ_DO_PROJETO = Path(__file__).resolve().parent.parent

#: Valores fictícios, distintos o bastante para conferir que nunca aparecem nas mensagens.
_CREDENCIAL_FALSA = {
    "type": "service_account",
    "client_email": "realocai@projeto-falso.iam.gserviceaccount.com",
    "private_key": "chave-privada-ficticia-123",
}

_VALIDA: dict[str, Any] = {
    "internal_api_key": "chave-interna-ficticia",
    "google_sheets_spreadsheet_id": "planilha-ficticia",
    "google_sheets_credentials_path": "./credentials/google-service-account.json",
    "ai_provider": "openai",
    "openai_api_key": "sk-ficticia",
    "openai_model": "modelo-ficticio",
}


def _settings(**valores: Any) -> Settings:
    return Settings(_env_file=None, **valores)


def _problemas(settings: Settings) -> list[str]:
    with pytest.raises(ConfiguracaoInvalidaError) as exc_info:
        validar_configuracao(settings)
    return exc_info.value.problemas


def test_configuracao_completa_passa() -> None:
    validar_configuracao(_settings(**_VALIDA))


def test_lista_todas_as_obrigatorias_que_faltam_de_uma_vez() -> None:
    problemas = _problemas(
        _settings(
            internal_api_key=None,
            google_sheets_spreadsheet_id=None,
            google_sheets_credentials_path=None,
            google_service_account_json=None,
            ai_provider=None,
        )
    )

    texto = "\n".join(problemas)
    assert len(problemas) == 4
    for nome in (
        "INTERNAL_API_KEY",
        "GOOGLE_SHEETS_SPREADSHEET_ID",
        "GOOGLE_SERVICE_ACCOUNT_JSON ou GOOGLE_SHEETS_CREDENTIALS_PATH",
        "AI_PROVIDER",
    ):
        assert nome in texto


def test_valor_em_branco_conta_como_ausente() -> None:
    problemas = _problemas(_settings(**{**_VALIDA, "internal_api_key": "   "}))

    assert [problema.split(":")[0] for problema in problemas] == ["INTERNAL_API_KEY"]


@pytest.mark.parametrize(
    ("provedor", "esperadas"),
    [
        ("openai", ["OPENAI_API_KEY", "OPENAI_MODEL"]),
        ("google", ["GOOGLE_API_KEY", "GOOGLE_MODEL"]),
    ],
)
def test_exige_as_variaveis_do_provedor_escolhido(provedor: str, esperadas: list[str]) -> None:
    problemas = _problemas(
        _settings(
            **{
                **_VALIDA,
                "ai_provider": provedor,
                "openai_api_key": None,
                "openai_model": None,
                "google_api_key": None,
                "google_model": None,
            }
        )
    )

    assert [problema.split(":")[0] for problema in problemas] == esperadas


def test_provedor_google_nao_exige_chave_da_openai() -> None:
    validar_configuracao(
        _settings(
            **{
                **_VALIDA,
                "ai_provider": "google",
                "openai_api_key": None,
                "google_api_key": "chave-google-ficticia",
                "google_model": "gemini-ficticio",
            }
        )
    )


def test_provedor_desconhecido() -> None:
    problemas = _problemas(_settings(**{**_VALIDA, "ai_provider": "anthropic"}))

    assert problemas == ['AI_PROVIDER: valor não reconhecido (aceitos: "openai" ou "google").']


def test_resend_exige_as_duas_variaveis_juntas() -> None:
    problemas = _problemas(_settings(**_VALIDA, resend_api_key="re_ficticia"))

    assert problemas[0].startswith("RESEND_API_KEY e REPORT_EMAIL_FROM")


def test_resend_e_opcional() -> None:
    validar_configuracao(_settings(**_VALIDA, resend_api_key=None, report_email_from=None))
    validar_configuracao(
        _settings(**_VALIDA, resend_api_key="re_ficticia", report_email_from="a@exemplo.com.br")
    )


def test_cors_obrigatorio_em_producao() -> None:
    problemas = _problemas(_settings(**_VALIDA, app_env="production", cors_allowed_origins=""))

    assert problemas[0].startswith("CORS_ALLOWED_ORIGINS: obrigatória em produção")
    validar_configuracao(
        _settings(**_VALIDA, app_env="production", cors_allowed_origins="https://app.exemplo.com")
    )


def test_cors_curinga_e_recusado() -> None:
    problemas = _problemas(_settings(**_VALIDA, cors_allowed_origins="*"))

    assert problemas[0].startswith('CORS_ALLOWED_ORIGINS: "*" não é aceito')


def test_mensagem_nunca_inclui_valores() -> None:
    settings = _settings(
        **{**_VALIDA, "ai_provider": "provedor-secreto-xyz", "google_service_account_json": "{x"}
    )

    with pytest.raises(ConfiguracaoInvalidaError) as exc_info:
        validar_configuracao(settings)

    mensagem = str(exc_info.value)
    for valor in ("provedor-secreto-xyz", "chave-interna-ficticia", "sk-ficticia", "{x"):
        assert valor not in mensagem


def test_log_level_aceita_minusculas() -> None:
    assert _settings(log_level="debug").log_level == "DEBUG"


# ---- Credencial do Google em variável de ambiente ----


def test_credencial_em_json_puro() -> None:
    assert carregar_credencial_google_json(json.dumps(_CREDENCIAL_FALSA)) == _CREDENCIAL_FALSA


def test_credencial_em_base64() -> None:
    codificada = base64.b64encode(json.dumps(_CREDENCIAL_FALSA).encode()).decode()

    assert carregar_credencial_google_json(codificada) == _CREDENCIAL_FALSA


def test_credencial_em_base64_com_quebras_de_linha() -> None:
    codificada = base64.encodebytes(json.dumps(_CREDENCIAL_FALSA).encode()).decode()
    assert "\n" in codificada

    assert carregar_credencial_google_json(codificada) == _CREDENCIAL_FALSA


@pytest.mark.parametrize(
    "valor",
    ["nem-json-nem-base64!", "{json quebrado", json.dumps({"type": "service_account"})],
)
def test_credencial_invalida_vira_problema_sem_vazar_conteudo(valor: str) -> None:
    problemas = _problemas(
        _settings(
            **{**_VALIDA, "google_sheets_credentials_path": None}, google_service_account_json=valor
        )
    )

    assert len(problemas) == 1
    assert problemas[0].startswith("GOOGLE_SERVICE_ACCOUNT_JSON")
    assert valor not in problemas[0]


def test_credencial_em_json_dispensa_o_arquivo() -> None:
    validar_configuracao(
        _settings(
            **{**_VALIDA, "google_sheets_credentials_path": None},
            google_service_account_json=json.dumps(_CREDENCIAL_FALSA),
        )
    )


def test_abrir_planilha_prefere_o_json_ao_arquivo(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.data_sources import google_sheets

    settings = _settings(
        google_sheets_spreadsheet_id="planilha-ficticia",
        google_sheets_credentials_path="/caminho/que/nao/existe.json",
        google_service_account_json=base64.b64encode(
            json.dumps(_CREDENCIAL_FALSA).encode()
        ).decode(),
    )
    recebido: dict[str, Any] = {}

    def from_service_account_info(info: dict[str, Any], scopes: list[str]) -> str:
        recebido["info"] = info
        return "credencial"

    def from_service_account_file(*_: Any, **__: Any) -> str:
        raise AssertionError("não deveria ler o arquivo")

    class _Cliente:
        def open_by_key(self, chave: str) -> str:
            return f"planilha:{chave}"

    modulo = "app.data_sources.google_sheets"
    monkeypatch.setattr(f"{modulo}.get_settings", lambda: settings)
    monkeypatch.setattr(
        f"{modulo}.Credentials.from_service_account_info", from_service_account_info
    )
    monkeypatch.setattr(
        f"{modulo}.Credentials.from_service_account_file", from_service_account_file
    )
    monkeypatch.setattr(f"{modulo}.gspread.authorize", lambda credencial: _Cliente())

    planilha: object = google_sheets.abrir_planilha()

    assert planilha == "planilha:planilha-ficticia"
    assert recebido["info"] == _CREDENCIAL_FALSA


# ---- Inicialização do servidor (`python -m app`) ----


def test_servidor_sai_com_codigo_1_e_lista_o_que_falta(tmp_path: Path) -> None:
    """Num diretório sem `.env` e sem variáveis: sai antes de subir o uvicorn."""
    ambiente = {
        nome: valor
        for nome, valor in os.environ.items()
        if nome.upper() not in {campo.upper() for campo in Settings.model_fields}
    }
    ambiente["PYTHONPATH"] = str(_RAIZ_DO_PROJETO)

    resultado = subprocess.run(
        [sys.executable, "-m", "app"],
        cwd=tmp_path,
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert resultado.returncode == 1
    assert "INTERNAL_API_KEY" in resultado.stderr
    assert "AI_PROVIDER" in resultado.stderr
    assert "Traceback" not in resultado.stderr
