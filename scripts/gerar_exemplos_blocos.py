"""Gera `docs/exemplos-blocos/`: um JSON de cada variante de bloco e o JSON Schema.

Os exemplos saem das tools reais rodando sobre a agenda fictícia dos testes
(`tests/support/relatorios.py`) — nenhum dado da planilha, nenhum nome real.
`tests/ai/test_exemplos_blocos.py` falha se os arquivos versionados ficarem
desatualizados em relação ao contrato.

Uso:
    uv run python scripts/gerar_exemplos_blocos.py
"""

import json
import sys
from pathlib import Path
from typing import Any

# Executado direto (`python scripts/...`), o `sys.path[0]` é `scripts/`: a raiz
# do projeto precisa entrar no path para `import app` e `import tests`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.support.agenda_semanal import SEGUNDA  # noqa: E402
from tests.support.relatorios import fonte_relatorios  # noqa: E402

from app.ai.relatorios import BlocoRelatorio  # noqa: E402
from app.ai.tools import criar_tools  # noqa: E402
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource  # noqa: E402

DIRETORIO = _PROJECT_ROOT / "docs" / "exemplos-blocos"

#: Arquivo -> (tool, argumentos) de cada variante do contrato.
VARIANTES: dict[str, tuple[str, dict[str, Any]]] = {
    "ocupacao_profissional.json": ("consultar_ocupacao_profissional", {"profissional": "Ana"}),
    "pacientes_por_profissional_semana.json": ("consultar_pacientes_por_profissional", {}),
    "pacientes_por_profissional_dia.json": (
        "consultar_pacientes_por_profissional",
        {"escopo": "dia"},
    ),
    "ocupacao_agregada.json": ("consultar_ocupacao", {"data": SEGUNDA.isoformat()}),
}
ARQUIVO_SCHEMA = "bloco_relatorio.schema.json"


def _serializar(conteudo: Any) -> str:
    return json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n"


def gerar_exemplos() -> dict[str, str]:
    """Nome do arquivo -> conteúdo JSON, validado contra `BlocoRelatorio`."""
    tools = {
        t.name: t
        for t in criar_tools(
            fonte_relatorios(), SemHistoricoContinuidadeDataSource(), SEGUNDA, lambda *_: None
        )
    }
    arquivos: dict[str, str] = {}
    for arquivo, (nome, args) in VARIANTES.items():
        mensagem = tools[nome].invoke({"name": nome, "args": args, "id": "c1", "type": "tool_call"})
        bloco = BlocoRelatorio.model_validate(mensagem.artifact)
        arquivos[arquivo] = _serializar(bloco.model_dump(mode="json"))
    arquivos[ARQUIVO_SCHEMA] = _serializar(BlocoRelatorio.model_json_schema())
    return arquivos


def main() -> None:
    DIRETORIO.mkdir(parents=True, exist_ok=True)
    for arquivo, conteudo in gerar_exemplos().items():
        (DIRETORIO / arquivo).write_text(conteudo, encoding="utf-8", newline="\n")
        print(f"Gerado: {DIRETORIO.relative_to(_PROJECT_ROOT) / arquivo}")


if __name__ == "__main__":
    main()
