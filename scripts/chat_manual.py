"""Script manual: conversa interativa com o agente RealocAI no terminal.

Ferramenta pontual para testar o agente de verdade, com o modelo real da
OpenAI — **não** faz parte do pacote `app`, não roda em CI e não é chamado
pela suíte de testes (`uv run pytest`). Exige `OPENAI_API_KEY` e
`OPENAI_MODEL` configurados no `.env` (ver `.env.example`), além das
credenciais do Google Sheets já usadas por `scripts/inspect_sheet.py`.

Uso:
    uv run python scripts/chat_manual.py [AAAA-MM-DD] [--verbose]

Sem data, usa a data de hoje como referência da conversa. Com `--verbose`,
imprime no terminal, após cada resposta, as tools chamadas no turno (nome,
argumentos e o começo do resultado) — só na tela, nunca em arquivo ou log,
porque o rastro contém nomes de pacientes. Digite `sair` (ou Ctrl+C) para
encerrar.
"""

import argparse
import io
import json
import sys
from datetime import date
from pathlib import Path
from typing import TextIO

# Este script vive fora do pacote `app` e é executado direto
# (`python scripts/chat_manual.py`), então o `sys.path[0]` é `scripts/`. A
# raiz do projeto precisa entrar no path para `import app`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)

from app.ai.agente import criar_agente, criar_chat_model, perguntar_com_mensagens  # noqa: E402
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource  # noqa: E402
from app.data_sources.google_sheets import GoogleSheetsDataSource  # noqa: E402
from app.reports.envio import enviar_relatorio_por_email  # noqa: E402

#: Quantos caracteres do resultado de cada tool aparecem no rastro `--verbose`.
LIMITE_RESULTADO_TOOL = 300


def reconfigurar_para_utf8(entrada: TextIO, *saidas: TextIO) -> None:
    """Força UTF-8 na entrada e nas saídas do terminal (saídas com `errors="replace"`).

    Em terminais que não são o console do Windows (Git Bash/mintty, por
    exemplo), o Python não detecta o console e usa a codificação ANSI do
    sistema: o stdin chega decodificado como cp1252, e um nome como "João",
    digitado em UTF-8, vira "JoÃ£o"; as saídas também mostram "�". Fluxos que
    não são `TextIOWrapper` (ex.: substituídos em teste) ficam como estão.
    """
    if isinstance(entrada, io.TextIOWrapper):
        entrada.reconfigure(encoding="utf-8")
    for saida in saidas:
        if isinstance(saida, io.TextIOWrapper):
            saida.reconfigure(encoding="utf-8", errors="replace")


def _argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Conversa manual com o agente RealocAI.")
    parser.add_argument(
        "data",
        nargs="?",
        type=date.fromisoformat,
        default=None,
        help="Data de referência da conversa (AAAA-MM-DD). Padrão: hoje.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Mostra, após cada resposta, as tools chamadas no turno.",
    )
    return parser.parse_args()


def formatar_rastro_de_tools(mensagens_do_turno: list[BaseMessage]) -> str:
    """Sequência de tools do turno: nome, argumentos e o começo de cada resultado."""
    linhas: list[str] = []
    for mensagem in mensagens_do_turno:
        if isinstance(mensagem, AIMessage):
            for chamada in mensagem.tool_calls:
                argumentos = json.dumps(chamada["args"], ensure_ascii=False, default=str)
                linhas.append(f"  -> {chamada['name']}({argumentos})")
        elif isinstance(mensagem, ToolMessage):
            conteudo = mensagem.content
            texto = conteudo if isinstance(conteudo, str) else str(conteudo)
            linhas.append(f"     <- {texto[:LIMITE_RESULTADO_TOOL]}")
    return "\n".join(linhas) if linhas else "  (nenhuma tool chamada)"


def main() -> None:
    reconfigurar_para_utf8(sys.stdin, sys.stdout, sys.stderr)
    argumentos = _argumentos()
    data_referencia: date = argumentos.data or date.today()
    fonte = GoogleSheetsDataSource()
    continuidade = SemHistoricoContinuidadeDataSource()
    agente = criar_agente(
        fonte, continuidade, criar_chat_model(), data_referencia, enviar_relatorio_por_email
    )

    print(f"RealocAI — conversa manual (referência: {data_referencia.strftime('%d/%m/%Y')})")
    print("Digite 'sair' para encerrar.\n")

    historico: list[BaseMessage] = []
    while True:
        try:
            pergunta = input("Você: ").strip()
        except EOFError, KeyboardInterrupt:
            print()
            break

        if not pergunta:
            continue
        if pergunta.lower() in {"sair", "exit", "quit"}:
            break

        historico.append(HumanMessage(pergunta))
        resposta, mensagens, blocos = perguntar_com_mensagens(agente, historico)
        mensagens_do_turno = mensagens[len(historico) :]
        historico.append(AIMessage(resposta))
        print(f"RealocAI: {resposta}\n")
        if argumentos.verbose:
            print(f"[tools do turno]\n{formatar_rastro_de_tools(mensagens_do_turno)}\n")
            for bloco in blocos:
                print(f"[relatório] {bloco.tipo}: {bloco.titulo}")


if __name__ == "__main__":
    main()
