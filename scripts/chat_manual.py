"""Script manual: conversa interativa com o agente RealocAI no terminal.

Ferramenta pontual para testar o agente de verdade, com o modelo real da
OpenAI — **não** faz parte do pacote `app`, não roda em CI e não é chamado
pela suíte de testes (`uv run pytest`). Exige `OPENAI_API_KEY` e
`OPENAI_MODEL` configurados no `.env` (ver `.env.example`), além das
credenciais do Google Sheets já usadas por `scripts/inspect_sheet.py`.

Uso:
    uv run python scripts/chat_manual.py [AAAA-MM-DD]

Sem argumento, usa a data de hoje como referência da conversa. Digite `sair`
(ou Ctrl+C) para encerrar.
"""

import sys
from datetime import date
from pathlib import Path

# Este script vive fora do pacote `app` e é executado direto
# (`python scripts/chat_manual.py`), então o `sys.path[0]` é `scripts/`. A
# raiz do projeto precisa entrar no path para `import app`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage  # noqa: E402

from app.ai.agente import criar_agente, criar_chat_model, perguntar  # noqa: E402
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource  # noqa: E402
from app.data_sources.google_sheets import GoogleSheetsDataSource  # noqa: E402


def _data_referencia() -> date:
    if len(sys.argv) > 1:
        return date.fromisoformat(sys.argv[1])
    return date.today()


def main() -> None:
    data_referencia = _data_referencia()
    fonte = GoogleSheetsDataSource()
    continuidade = SemHistoricoContinuidadeDataSource()
    agente = criar_agente(fonte, continuidade, criar_chat_model(), data_referencia)

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
        resposta = perguntar(agente, historico)
        historico.append(AIMessage(resposta))
        print(f"RealocAI: {resposta}\n")


if __name__ == "__main__":
    main()
