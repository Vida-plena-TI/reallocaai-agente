"""Confere que `app.main` é importável e funciona sem nenhum segredo configurado.

`import app.main` chama `get_settings()` no nível do módulo para montar o
CORS (`app/main.py`). Antes da Fase 6a-fix, `Settings()` tinha campos
obrigatórios sem default (`openai_model`, `internal_api_key`, ...), então
importar `app.main` exigia um `.env` completo — quebrando em qualquer
ambiente sem ele (CI, clone limpo). Agora esses campos são opcionais na
classe e só validados no ponto de uso (ver `exigir` em `app/config.py`).

Roda num subprocesso com um diretório de trabalho sem `.env` e sem as
variáveis de ambiente relevantes: `monkeypatch.delenv` sozinho não bastaria
aqui, porque `pydantic-settings` também lê o arquivo `.env` do diretório
atual, não só `os.environ` — e o `.env` deste projeto tem `INTERNAL_API_KEY`
preenchido para uso manual (`uvicorn`).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_RAIZ_DO_PROJETO = Path(__file__).resolve().parent.parent

#: Todas as variáveis que `Settings` lê — removidas do ambiente do subprocesso
#: para simular um clone limpo/CI sem `.env` nenhum.
_VARIAVEIS_DE_SETTINGS = (
    "APP_ENV",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "GOOGLE_SHEETS_CREDENTIALS_PATH",
    "GOOGLE_SHEETS_SPREADSHEET_ID",
    "RESEND_API_KEY",
    "REPORT_EMAIL_FROM",
    "REPORT_EMAIL_TO",
    "INTERNAL_API_KEY",
    "CORS_ALLOWED_ORIGINS",
    "CACHE_TTL_SEGUNDOS",
)

#: Roda dentro do subprocesso: importa `app.main`, chama `/health` (pública) e
#: `/agenda/ocupacao` (autenticada) sem `X-API-Key`, e imprime tudo como JSON
#: numa única linha — mais simples de recuperar do que fazer o teste principal
#: entender saída multi-linha do subprocesso.
_SCRIPT = """
import json
from fastapi.testclient import TestClient

import app.main as modulo  # não deve levantar exceção

# Sem "with": o lifespan (que exigiria credencial real do Google) não roda.
# Isso é intencional aqui: só queremos confirmar que a IMPORTAÇÃO do módulo e
# as rotas que não dependem da fonte de dados real continuam funcionando.
cliente = TestClient(modulo.app)

saude = cliente.get("/health")
ocupacao = cliente.get("/agenda/ocupacao", params={"data": "2026-09-08"})

print(json.dumps({
    "health_status": saude.status_code,
    "health_body": saude.json(),
    "ocupacao_status": ocupacao.status_code,
    "ocupacao_body": ocupacao.json(),
}))
"""


def test_app_e_importavel_e_health_funciona_sem_nenhuma_variavel_de_ambiente(
    tmp_path: Path,
) -> None:
    ambiente = {
        chave: valor for chave, valor in os.environ.items() if chave not in _VARIAVEIS_DE_SETTINGS
    }
    # "app" precisa ser importável a partir do diretório de trabalho do
    # subprocesso, que é `tmp_path` (sem `.env`) só para o pydantic-settings
    # não encontrar o `.env` real do projeto.
    ambiente["PYTHONPATH"] = str(_RAIZ_DO_PROJETO)

    resultado = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=tmp_path,
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert resultado.returncode == 0, (
        f"import app.main falhou num ambiente sem nenhuma variável configurada:\n{resultado.stderr}"
    )

    saida = json.loads(resultado.stdout.strip().splitlines()[-1])

    assert saida["health_status"] == 200
    assert saida["health_body"] == {"status": "ok", "service": "realocai"}

    # 500 (problema operacional: chave não configurada), nunca 401 (que
    # sugeriria, enganosamente, que a chave enviada está só errada).
    assert saida["ocupacao_status"] == 500
    assert "INTERNAL_API_KEY" in saida["ocupacao_body"]["detail"]
