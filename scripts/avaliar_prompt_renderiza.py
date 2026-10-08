"""Script manual: avalia a variante com renderização do prompt com o modelo real.

Ferramenta pontual, como `scripts/chat_manual.py` — **não** faz parte do
pacote `app`, não roda em CI e não é chamada pela suíte de testes
(`uv run pytest`). Exige o provedor de IA configurado no `.env` (`AI_PROVIDER`
e a chave/modelo correspondentes) e as credenciais do Google Sheets. Cada
execução faz chamadas pagas ao provedor (ver o custo aproximado no README).

Uso:
    uv run python scripts/avaliar_prompt_renderiza.py [AAAA-MM-DD] [--repeticoes N]

Repete cada pergunta de `PERGUNTAS` N vezes (padrão 3), sempre numa conversa
nova, com `renderiza_relatorios=True`. Para cada execução imprime só métricas
(frases, tools, blocos, fidelidade dos números, oferta de e-mail) — nunca o texto da resposta
nem o resultado das tools, que contêm nomes de pacientes. Nada é gravado em
arquivo. O envio de e-mail é substituído por um registro local: se o agente
chamar `enviar_relatorio`, isso aparece como tool extra e nenhum e-mail sai.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Mesmo ajuste de `scripts/chat_manual.py`: a raiz do projeto entra no path.
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
from app.ai.tools import EnviarRelatorio  # noqa: E402
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource  # noqa: E402
from app.data_sources.google_sheets import GoogleSheetsDataSource  # noqa: E402
from app.domain import ScheduleDataSource  # noqa: E402
from scripts.chat_manual import reconfigurar_para_utf8  # noqa: E402

#: Pergunta -> tools esperadas. A de exportação não chama nenhuma.
PERGUNTAS: dict[str, frozenset[str]] = {
    "Qual a ocupação da Rossana?": frozenset({"consultar_ocupacao_profissional"}),
    "Quantos pacientes cada profissional atende por dia?": frozenset(
        {"consultar_pacientes_por_profissional"}
    ),
    "Ocupação por especialidade hoje": frozenset({"consultar_ocupacao"}),
    "Exporta isso em Excel": frozenset(),
}

MAXIMO_DE_FRASES = 3

_FIM_DE_FRASE = re.compile(r"(?<=[.!?…])\s+|\n+")
_DECIMAL_COM_VIRGULA = re.compile(r"\d+,\d+")
#: Qualquer hífen ou nenhum entre "e" e "mail" (o modelo já usou U+2011).
_EMAIL = re.compile(r"e\W?mail", re.IGNORECASE)


@dataclass(frozen=True)
class Execucao:
    frases: int
    tools: list[str]
    extras: list[str]
    blocos: list[str]
    numeros_infieis: list[str]
    oferece_email: bool

    @property
    def frases_ok(self) -> bool:
        return self.frases <= MAXIMO_DE_FRASES

    @property
    def tools_ok(self) -> bool:
        return not self.extras

    @property
    def numeros_ok(self) -> bool:
        return not self.numeros_infieis

    @property
    def email_ok(self) -> bool:
        return not self.oferece_email


def contar_frases(texto: str) -> int:
    """Frases por pontuação final ou quebra de linha (cada item de lista conta como uma)."""
    return len([trecho for trecho in _FIM_DE_FRASE.split(texto.strip()) if trecho.strip()])


def numeros_ausentes_das_tools(resposta: str, textos_das_tools: list[str]) -> list[str]:
    """Números com vírgula decimal citados na resposta que não aparecem no texto das tools."""
    fonte = "\n".join(textos_das_tools)
    return [n for n in _DECIMAL_COM_VIRGULA.findall(resposta) if n not in fonte]


def menciona_email(resposta: str) -> bool:
    """Nenhuma pergunta da avaliação pede e-mail: qualquer menção conta como oferta."""
    return _EMAIL.search(resposta) is not None


def avaliar_turno(
    resposta: str,
    mensagens_do_turno: list[BaseMessage],
    blocos: list[str],
    esperadas: frozenset[str],
) -> Execucao:
    tools = [
        chamada["name"]
        for mensagem in mensagens_do_turno
        if isinstance(mensagem, AIMessage)
        for chamada in mensagem.tool_calls
    ]
    textos = [
        m.content if isinstance(m.content, str) else str(m.content)
        for m in mensagens_do_turno
        if isinstance(m, ToolMessage)
    ]
    return Execucao(
        frases=contar_frases(resposta),
        tools=tools,
        extras=[nome for nome in tools if nome not in esperadas],
        blocos=blocos,
        numeros_infieis=numeros_ausentes_das_tools(resposta, textos),
        oferece_email=menciona_email(resposta),
    )


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Avalia a variante com renderização do prompt com o modelo real."
    )
    parser.add_argument(
        "data",
        nargs="?",
        type=date.fromisoformat,
        default=None,
        help="Data de referência das conversas (AAAA-MM-DD). Padrão: hoje.",
    )
    parser.add_argument(
        "--repeticoes",
        type=int,
        default=3,
        help="Quantas vezes cada pergunta é feita, sempre em conversa nova. Padrão: 3.",
    )
    return parser.parse_args(argv)


def _registrar_sem_enviar(registro: list[date]) -> EnviarRelatorio:
    def enviar(_fonte: ScheduleDataSource, data: date, _destinatarios: list[str] | None) -> None:
        registro.append(data)

    return enviar


def _sim_nao(valor: bool) -> str:
    return "sim" if valor else "NÃO"


def main() -> None:
    reconfigurar_para_utf8(sys.stdin, sys.stdout, sys.stderr)
    argumentos = _argumentos()
    data_referencia: date = argumentos.data or date.today()
    fonte = GoogleSheetsDataSource()
    continuidade = SemHistoricoContinuidadeDataSource()
    chat_model = criar_chat_model()
    emails_bloqueados: list[date] = []

    print(
        f"Avaliação da variante com renderização — referência "
        f"{data_referencia.strftime('%d/%m/%Y')}, {argumentos.repeticoes} repetição(ões)\n"
    )
    resultados: dict[str, list[Execucao]] = {}
    for pergunta, esperadas in PERGUNTAS.items():
        resultados[pergunta] = []
        print(f"== {pergunta}")
        for indice in range(1, argumentos.repeticoes + 1):
            agente = criar_agente(
                fonte,
                continuidade,
                chat_model,
                data_referencia,
                _registrar_sem_enviar(emails_bloqueados),
                renderiza_relatorios=True,
            )
            historico: list[BaseMessage] = [HumanMessage(pergunta)]
            resposta, mensagens, blocos = perguntar_com_mensagens(agente, historico)
            execucao = avaliar_turno(
                resposta,
                mensagens[len(historico) :],
                [bloco.tipo for bloco in blocos],
                esperadas,
            )
            resultados[pergunta].append(execucao)
            print(
                f"  #{indice}: frases={execucao.frases} "
                f"tools={execucao.tools or '-'} "
                f"extras={execucao.extras or '-'} "
                f"blocos={execucao.blocos or '-'} "
                f"números fiéis={_sim_nao(execucao.numeros_ok)} "
                f"oferece e-mail={'SIM' if execucao.oferece_email else 'não'}"
                + (
                    f" (fora da tool: {execucao.numeros_infieis})"
                    if execucao.numeros_infieis
                    else ""
                )
            )
        print()

    print("== Resumo (execuções que cumpriram / total)")
    for pergunta, execucoes in resultados.items():
        total = len(execucoes)
        print(
            f"  {pergunta}\n"
            f"    até {MAXIMO_DE_FRASES} frases: {sum(e.frases_ok for e in execucoes)}/{total}"
            f" | só as tools esperadas: {sum(e.tools_ok for e in execucoes)}/{total}"
            f" | números fiéis: {sum(e.numeros_ok for e in execucoes)}/{total}"
            f" | sem oferta de e-mail: {sum(e.email_ok for e in execucoes)}/{total}"
        )
    if emails_bloqueados:
        print(
            f"\nenviar_relatorio chamado {len(emails_bloqueados)} vez(es); nenhum e-mail enviado."
        )


if __name__ == "__main__":
    main()
