"""Script manual: avalia a variante com renderização do prompt com o modelo real.

Ferramenta pontual, como `scripts/chat_manual.py` — **não** faz parte do
pacote `app`, não roda em CI e não é chamada pela suíte de testes
(`uv run pytest`). Exige o provedor de IA configurado no `.env` (`AI_PROVIDER`
e a chave/modelo correspondentes) e as credenciais do Google Sheets. Cada
execução faz chamadas pagas ao provedor.

Uso:
    uv run python scripts/avaliar_prompt_renderiza.py [AAAA-MM-DD] [--repeticoes N]
        [--preco-entrada US$/M] [--preco-saida US$/M] [--confirmar]

Sem `--confirmar`, só mostra a estimativa de custo e o comando para rodar:
nenhuma chamada ao modelo é feita. Com `--confirmar`, repete cada pergunta de
`PERGUNTAS` N vezes (padrão 1), sempre numa conversa nova, com
`renderiza_relatorios=True`.

Para cada execução imprime só métricas: tools e relatório escolhidos, número de
chamadas ao modelo e às tools, tempo, frases (1 a 3), fidelidade dos números,
e nomes de pacientes na resposta (só a contagem). Nunca o texto da resposta
nem o resultado das tools, que contêm nomes de pacientes. Nada é gravado em
arquivo. A pergunta de agenda de paciente roda sobre a semana fictícia de
`tests/support/agenda_clinica.py`, com um nome fictício: nenhum nome real é
escrito aqui.
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from collections.abc import Iterable
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
from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: E402
from tests.support.agenda_clinica import NOMES_PACIENTES, fonte_clinica  # noqa: E402
from tests.support.agenda_semanal import QUARTA  # noqa: E402

from app.ai.agente import criar_agente, criar_chat_model, perguntar_com_mensagens  # noqa: E402
from app.ai.prompts import prompt_de_sistema  # noqa: E402
from app.ai.tools import criar_tools  # noqa: E402
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource  # noqa: E402
from app.data_sources.google_sheets import GoogleSheetsDataSource  # noqa: E402
from app.domain import ScheduleDataSource, dias_da_semana_de  # noqa: E402
from app.engine.fotografia import fotografar_periodo  # noqa: E402
from scripts.chat_manual import reconfigurar_para_utf8  # noqa: E402


@dataclass(frozen=True)
class Pergunta:
    texto: str
    #: Tools aceitas; qualquer outra conta como extra. Vazio: nenhuma (exportação).
    esperadas: frozenset[str]
    #: O `relatorio` esperado de `gerar_relatorio`, quando é ele a tool.
    relatorio: str | None = None
    #: Roda sobre a semana fictícia (nunca nomes reais).
    ficticia: bool = False
    #: Nome fictício pedido pelo usuário: é o único que pode aparecer na resposta.
    paciente_pedido: str | None = None


_GERAR = frozenset({"gerar_relatorio"})

#: O nome fictício usado na pergunta de agenda de paciente.
PACIENTE_FICTICIO = NOMES_PACIENTES[0]

PERGUNTAS: list[Pergunta] = [
    Pergunta("Qual a ocupação da Rossana?", frozenset({"consultar_ocupacao_profissional"})),
    Pergunta(
        "Quantos pacientes cada profissional atende por dia?",
        frozenset({"consultar_pacientes_por_profissional"}),
    ),
    Pergunta("Ocupação por especialidade hoje", frozenset({"consultar_ocupacao"})),
    Pergunta("Exporta isso em Excel", frozenset()),
    Pergunta("Quais as vagas livres de segunda a sexta?", _GERAR, "vagas_livres"),
    Pergunta("Onde cabe um atendimento de 1h30 nesta semana?", _GERAR, "janelas_continuas"),
    Pergunta("Qual o ranking de ocupação das profissionais na semana?", _GERAR, "ranking_ocupacao"),
    Pergunta(
        "Como estão os convênios e o que está aguardando autorização nesta semana?",
        _GERAR,
        "convenios",
    ),
    Pergunta(
        "Onde estão os buracos e a fragmentação da agenda nesta semana?",
        _GERAR,
        "qualidade_agenda",
    ),
    Pergunta(
        f"Qual a agenda do {PACIENTE_FICTICIO} nesta semana?",
        _GERAR,
        "agenda_paciente",
        ficticia=True,
        paciente_pedido=PACIENTE_FICTICIO,
    ),
]

MAXIMO_DE_FRASES = 3

#: Estimativa de custo: chamadas ao modelo por conversa (pergunta, tool, resposta),
#: tokens do resultado das tools e da resposta. Conservadora de propósito.
CHAMADAS_POR_CONVERSA = 2
TOKENS_RESULTADO_TOOLS = 1_500
TOKENS_SAIDA_POR_CONVERSA = 400
#: Aproximação comum de caracteres por token em português.
CARACTERES_POR_TOKEN = 4
PRECO_ENTRADA_PADRAO = 2.50
PRECO_SAIDA_PADRAO = 10.00

_FIM_DE_FRASE = re.compile(r"(?<=[.!?…])\s+|\n+")
_DECIMAL_COM_VIRGULA = re.compile(r"\d+,\d+")


@dataclass(frozen=True)
class Execucao:
    frases: int
    tools: list[str]
    extras: list[str]
    blocos: list[str]
    numeros_infieis: list[str]
    #: `relatorio` de cada chamada a `gerar_relatorio`.
    relatorios: tuple[str, ...] = ()
    relatorio_esperado: str | None = None
    chamadas_modelo: int = 0
    segundos: float = 0.0
    #: Quantos nomes de paciente (fora o pedido) aparecem na resposta.
    nomes_de_pacientes: int = 0

    @property
    def frases_ok(self) -> bool:
        return 1 <= self.frases <= MAXIMO_DE_FRASES

    @property
    def tools_ok(self) -> bool:
        return not self.extras

    @property
    def relatorio_ok(self) -> bool:
        return self.relatorio_esperado is None or self.relatorio_esperado in self.relatorios

    @property
    def numeros_ok(self) -> bool:
        return not self.numeros_infieis

    @property
    def nomes_ok(self) -> bool:
        return self.nomes_de_pacientes == 0


def contar_frases(texto: str) -> int:
    """Frases por pontuação final ou quebra de linha (cada item de lista conta como uma)."""
    return len([trecho for trecho in _FIM_DE_FRASE.split(texto.strip()) if trecho.strip()])


def numeros_ausentes_das_tools(resposta: str, textos_das_tools: list[str]) -> list[str]:
    """Números com vírgula decimal citados na resposta que não aparecem no texto das tools."""
    fonte = "\n".join(textos_das_tools)
    return [n for n in _DECIMAL_COM_VIRGULA.findall(resposta) if n not in fonte]


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.casefold().split())


def contar_nomes_de_pacientes(
    resposta: str,
    pacientes: Iterable[str],
    *,
    permitidos: Iterable[str] = (),
    profissionais: Iterable[str] = (),
) -> int:
    """Quantos nomes de `pacientes` aparecem (palavra inteira) na resposta.

    Ignora o nome pedido pelo usuário (`permitidos`) e os que também são nome
    de profissional ou parte dele, para não confundir "Ana" paciente com "Ana
    Lima" profissional. Devolve só a contagem: o nome nunca é impresso.
    """
    texto = _normalizar(resposta)
    ignorar = {_normalizar(nome) for nome in permitidos}
    palavras_de_profissionais = {
        parte for nome in profissionais for parte in [_normalizar(nome), *_normalizar(nome).split()]
    }
    encontrados = 0
    for nome in {_normalizar(nome) for nome in pacientes}:
        if len(nome) < 3 or nome in ignorar or nome in palavras_de_profissionais:
            continue
        if re.search(rf"(?<!\w){re.escape(nome)}(?!\w)", texto):
            encontrados += 1
    return encontrados


def avaliar_turno(
    resposta: str,
    mensagens_do_turno: list[BaseMessage],
    blocos: list[str],
    esperadas: frozenset[str],
    *,
    relatorio_esperado: str | None = None,
    segundos: float = 0.0,
    nomes_de_pacientes: int = 0,
) -> Execucao:
    chamadas = [
        chamada
        for mensagem in mensagens_do_turno
        if isinstance(mensagem, AIMessage)
        for chamada in mensagem.tool_calls
    ]
    tools = [chamada["name"] for chamada in chamadas]
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
        relatorios=tuple(
            str(chamada["args"].get("relatorio"))
            for chamada in chamadas
            if chamada["name"] == "gerar_relatorio"
        ),
        relatorio_esperado=relatorio_esperado,
        chamadas_modelo=sum(1 for m in mensagens_do_turno if isinstance(m, AIMessage)),
        segundos=segundos,
        nomes_de_pacientes=nomes_de_pacientes,
    )


@dataclass(frozen=True)
class Estimativa:
    conversas: int
    tokens_prompt: int
    tokens_entrada: int
    tokens_saida: int
    dolares: float


def tokens_do_prompt(fonte: ScheduleDataSource, data_referencia: date) -> int:
    """Tokens aproximados do prompt de sistema e dos schemas das tools, sem chamar o modelo."""
    tools = criar_tools(fonte, SemHistoricoContinuidadeDataSource(), data_referencia)
    prompt = prompt_de_sistema(renderiza_relatorios=True)
    schemas = json.dumps([convert_to_openai_tool(tool) for tool in tools], ensure_ascii=False)
    return (len(prompt) + len(schemas)) // CARACTERES_POR_TOKEN


def estimar_custo(
    tokens_prompt: int,
    perguntas: int,
    repeticoes: int,
    preco_entrada: float = PRECO_ENTRADA_PADRAO,
    preco_saida: float = PRECO_SAIDA_PADRAO,
) -> Estimativa:
    """Custo aproximado em US$ (preços por milhão de tokens)."""
    conversas = perguntas * repeticoes
    entrada = conversas * (CHAMADAS_POR_CONVERSA * tokens_prompt + TOKENS_RESULTADO_TOOLS)
    saida = conversas * TOKENS_SAIDA_POR_CONVERSA
    dolares = (entrada * preco_entrada + saida * preco_saida) / 1_000_000
    return Estimativa(conversas, tokens_prompt, entrada, saida, dolares)


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
        default=1,
        help="Quantas vezes cada pergunta é feita, sempre em conversa nova. Padrão: 1.",
    )
    parser.add_argument(
        "--preco-entrada",
        type=float,
        default=PRECO_ENTRADA_PADRAO,
        help="US$ por milhão de tokens de entrada, para a estimativa. Padrão: 2,50.",
    )
    parser.add_argument(
        "--preco-saida",
        type=float,
        default=PRECO_SAIDA_PADRAO,
        help="US$ por milhão de tokens de saída, para a estimativa. Padrão: 10,00.",
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="Roda a avaliação (paga). Sem isto, só mostra a estimativa de custo.",
    )
    return parser.parse_args(argv)


def _sim_nao(valor: bool) -> str:
    return "sim" if valor else "NÃO"


def _br(valor: float, casas: int = 0) -> str:
    """`137420` -> `137.420`; `0.38` -> `0,38`."""
    return f"{valor:,.{casas}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _imprimir_estimativa(estimativa: Estimativa, argumentos: argparse.Namespace) -> None:
    print(
        f"Estimativa: {estimativa.conversas} conversa(s) ({len(PERGUNTAS)} pergunta(s) x "
        f"{argumentos.repeticoes} repetição(ões)), ~{CHAMADAS_POR_CONVERSA} chamadas ao modelo "
        f"cada; prompt + schemas das tools ~{_br(estimativa.tokens_prompt)} tokens por chamada."
    )
    print(
        f"  ~{_br(estimativa.tokens_entrada)} tokens de entrada e "
        f"~{_br(estimativa.tokens_saida)} de saída -> ~US$ {_br(estimativa.dolares, 2)} "
        f"a US$ {_br(argumentos.preco_entrada, 2)}/M "
        f"(entrada) e US$ {_br(argumentos.preco_saida, 2)}/M (saída). Ajuste os preços com "
        "--preco-entrada/--preco-saida."
    )


def main(argv: list[str] | None = None) -> None:
    reconfigurar_para_utf8(sys.stdin, sys.stdout, sys.stderr)
    argumentos = _argumentos(argv)
    data_referencia: date = argumentos.data or date.today()
    fonte = GoogleSheetsDataSource()

    # Medida do prompt real, sem ler a planilha nem chamar o modelo.
    estimativa = estimar_custo(
        tokens_do_prompt(fonte, data_referencia),
        len(PERGUNTAS),
        argumentos.repeticoes,
        argumentos.preco_entrada,
        argumentos.preco_saida,
    )
    _imprimir_estimativa(estimativa, argumentos)
    if not argumentos.confirmar:
        comando = "uv run python scripts/avaliar_prompt_renderiza.py"
        if argumentos.data:
            comando += f" {argumentos.data.isoformat()}"
        if argumentos.repeticoes != 1:
            comando += f" --repeticoes {argumentos.repeticoes}"
        print(f"\nNada foi chamado. Para rodar (pago): {comando} --confirmar")
        return

    continuidade = SemHistoricoContinuidadeDataSource()
    chat_model = criar_chat_model()
    semana = dias_da_semana_de(data_referencia)
    foto_real = fotografar_periodo(fonte, semana[0], semana[-1])
    # Os nomes ficam só em memória, para contar ocorrências; nunca são impressos.
    pacientes_reais = [paciente.nome for paciente in foto_real.pacientes.values()]
    profissionais_reais = [p.nome for p in foto_real.profissionais.values()]
    fonte_ficticia = fonte_clinica()
    profissionais_ficticios = [
        p.nome for p in fotografar_periodo(fonte_ficticia, *_semana(QUARTA)).profissionais.values()
    ]

    print(
        f"\nAvaliação da variante com renderização — referência "
        f"{data_referencia.strftime('%d/%m/%Y')}, {argumentos.repeticoes} repetição(ões)\n"
    )
    resultados: dict[str, list[Execucao]] = {}
    for pergunta in PERGUNTAS:
        resultados[pergunta.texto] = []
        print(f"== {pergunta.texto}" + (" (fonte fictícia)" if pergunta.ficticia else ""))
        for indice in range(1, argumentos.repeticoes + 1):
            agente = criar_agente(
                fonte_ficticia if pergunta.ficticia else fonte,
                continuidade,
                chat_model,
                QUARTA if pergunta.ficticia else data_referencia,
                renderiza_relatorios=True,
            )
            historico: list[BaseMessage] = [HumanMessage(pergunta.texto)]
            inicio = time.perf_counter()
            resposta, mensagens, blocos = perguntar_com_mensagens(agente, historico)
            segundos = time.perf_counter() - inicio
            nomes = contar_nomes_de_pacientes(
                resposta,
                NOMES_PACIENTES if pergunta.ficticia else pacientes_reais,
                permitidos=[pergunta.paciente_pedido] if pergunta.paciente_pedido else [],
                profissionais=profissionais_ficticios if pergunta.ficticia else profissionais_reais,
            )
            execucao = avaliar_turno(
                resposta,
                mensagens[len(historico) :],
                [bloco.tipo for bloco in blocos],
                pergunta.esperadas,
                relatorio_esperado=pergunta.relatorio,
                segundos=segundos,
                nomes_de_pacientes=nomes,
            )
            resultados[pergunta.texto].append(execucao)
            print(
                f"  #{indice}: tools={execucao.tools or '-'} "
                f"relatório={list(execucao.relatorios) or '-'} "
                f"chamadas modelo={execucao.chamadas_modelo} tools={len(execucao.tools)} "
                f"tempo={execucao.segundos:.1f}s frases={execucao.frases} "
                f"extras={execucao.extras or '-'} blocos={execucao.blocos or '-'} "
                f"números fiéis={_sim_nao(execucao.numeros_ok)} "
                f"nomes de pacientes={execucao.nomes_de_pacientes}"
                + (
                    f" (fora da tool: {execucao.numeros_infieis})"
                    if execucao.numeros_infieis
                    else ""
                )
            )
        print()

    print("== Resumo (execuções que cumpriram / total)")
    for texto, execucoes in resultados.items():
        total = len(execucoes)
        tempo = sum(e.segundos for e in execucoes) / total
        print(
            f"  {texto}\n"
            f"    1 a {MAXIMO_DE_FRASES} frases: {sum(e.frases_ok for e in execucoes)}/{total}"
            f" | só as tools esperadas: {sum(e.tools_ok for e in execucoes)}/{total}"
            f" | relatório certo: {sum(e.relatorio_ok for e in execucoes)}/{total}"
            f" | números fiéis: {sum(e.numeros_ok for e in execucoes)}/{total}"
            f" | sem nome de paciente: {sum(e.nomes_ok for e in execucoes)}/{total}"
            f" | tempo médio: {tempo:.1f}s"
        )


def _semana(data: date) -> tuple[date, date]:
    semana = dias_da_semana_de(data)
    return semana[0], semana[-1]


if __name__ == "__main__":
    main()
