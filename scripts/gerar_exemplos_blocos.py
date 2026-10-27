"""Gera `docs/exemplos-blocos/`: um JSON de cada variante de bloco e o JSON Schema.

Os exemplos saem das tools reais rodando sobre as agendas fictícias dos testes
(`tests/support/relatorios.py` e, para `gerar_relatorio`,
`tests/support/agenda_clinica.py`) — nenhum dado da planilha, nenhum nome real.
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

from tests.support.agenda_clinica import fonte_clinica  # noqa: E402
from tests.support.agenda_semanal import SEGUNDA, SEXTA  # noqa: E402
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

#: Arquivo -> argumentos de `gerar_relatorio` (sobre `fonte_clinica`).
VARIANTES_GERAIS: dict[str, dict[str, Any]] = {
    "disponibilidade_vagas.json": {
        "relatorio": "vagas_livres",
        "inicio": SEGUNDA.isoformat(),
        "fim": SEXTA.isoformat(),
    },
    "disponibilidade_janelas.json": {"relatorio": "janelas_continuas", "duracao_minutos": 90},
    "convenio.json": {"relatorio": "convenios"},
    "agenda_paciente.json": {
        "relatorio": "agenda_paciente",
        "paciente": "Paciente Ficticio Alfa",
    },
    "qualidade_agenda.json": {"relatorio": "qualidade_agenda"},
    "tabela_generica_ranking.json": {"relatorio": "ranking_ocupacao", "limite": 3},
    "tabela_generica_capacidade.json": {"relatorio": "capacidade_especialidade"},
    "tabela_generica_personalizado.json": {
        "relatorio": "personalizado",
        "dimensoes": ["dia", "especialidade"],
        "metricas": ["ocupacao", "vagas_livres", "pacientes_distintos"],
        "periodo_do_dia": "tarde",
    },
}
#: Arquivo -> argumentos de `planejar_semana` (sobre `fonte_clinica`).
VARIANTES_PLANO: dict[str, dict[str, Any]] = {
    "tabela_generica_plano_semanal.json": {
        "dias_semana": ["quarta", "quinta"],
        "especialidades": ["psicologia", "fonoaudiologia", "terapia_ocupacional"],
        "hora_inicio": "10:00",
        "hora_fim": "12:00",
    },
}
ARQUIVO_SCHEMA = "bloco_relatorio.schema.json"


def _serializar(conteudo: Any) -> str:
    return json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n"


def gerar_exemplos() -> dict[str, str]:
    """Nome do arquivo -> conteúdo JSON, validado contra `BlocoRelatorio`."""
    tools = {
        t.name: t
        for t in criar_tools(fonte_relatorios(), SemHistoricoContinuidadeDataSource(), SEGUNDA)
    }
    gerais = {
        t.name: t
        for t in criar_tools(fonte_clinica(), SemHistoricoContinuidadeDataSource(), SEGUNDA)
    }
    chamadas = [(arquivo, tools, nome, args) for arquivo, (nome, args) in VARIANTES.items()]
    chamadas += [
        (arquivo, gerais, "gerar_relatorio", args) for arquivo, args in VARIANTES_GERAIS.items()
    ]
    chamadas += [
        (arquivo, gerais, "planejar_semana", args) for arquivo, args in VARIANTES_PLANO.items()
    ]
    arquivos: dict[str, str] = {}
    for arquivo, disponiveis, nome, args in chamadas:
        mensagem = disponiveis[nome].invoke(
            {"name": nome, "args": args, "id": "c1", "type": "tool_call"}
        )
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
