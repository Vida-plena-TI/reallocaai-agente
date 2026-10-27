"""Avalia o RealocAI com casos reais da coordenação (`tests/casos_reais/casos.yaml`).

Mede se a resposta está CERTA, não só se é curta: cada caso traz perguntas em
sequência, uma data "hoje" fixa e critérios verificáveis, e o esperado vem de
um oráculo que conta direto na grade crua da planilha
(`tests/casos_reais/oraculo.py`), nunca do motor testado.

Uso:
    uv run python scripts/avaliar_casos_reais.py --deterministico [--casos c01,c07]
    uv run python scripts/avaliar_casos_reais.py --llm --preco-entrada US$/M
        --preco-saida US$/M [--repeticoes N] [--confirmar]

Modos:
- `--deterministico`: não chama modelo nenhum. Executa, no lugar da LLM, a
  tool indicada em cada turno do caso e aplica os critérios ao que ela
  devolve. Rápido e gratuito; é o que roda no pytest
  (`tests/casos_reais/test_casos_reais.py`).
- `--llm`: conversa com o agente real (provedor do `.env`), uma conversa nova
  por caso e repetição. Exige `--preco-entrada` e `--preco-saida`; sem
  `--confirmar` só mostra a estimativa de custo, sem chamar nada.

Fonte: por padrão, a semana fictícia de `tests/casos_reais/planilha.py`, lida
pelo caminho de produção (`GoogleSheetsDataSource` e parser). Com
`--planilha-id`, a planilha de testes no Google, lida uma vez e mantida só em
memória; os casos `so_ficticia` são pulados.

Saída: uma tabela por caso (status, tempo, chamadas de tool e à planilha,
tokens), sem texto de resposta nem nomes de pacientes. O detalhe de cada
critério vai para `scripts/output/` (ignorado pelo git), também sem eles.
Código de saída 1 quando algum caso falhou sem ser esperado (ou passou
marcado como xfail).
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

# Mesmo ajuste de `scripts/chat_manual.py`: a raiz do projeto entra no path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import gspread  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage  # noqa: E402
from tests.casos_reais.casos import (  # noqa: E402
    Caso,
    Contexto,
    ResultadoCriterio,
    Status,
    TurnoObservado,
    avaliar,
    carregar_casos,
    resolver_datas,
    status_do_caso,
)
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana  # noqa: E402

from app.ai.agente import (  # noqa: E402
    criar_agente,
    criar_chat_model,
    extrair_blocos_do_turno,
    perguntar_com_mensagens,
)
from app.ai.tools import criar_tools  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource  # noqa: E402
from app.data_sources.google_sheets import GoogleSheetsDataSource, abrir_planilha  # noqa: E402
from scripts.avaliar_prompt_renderiza import (  # noqa: E402
    CHAMADAS_POR_CONVERSA,
    Estimativa,
    _br,
    estimar_custo,
    tokens_do_prompt,
)
from scripts.chat_manual import reconfigurar_para_utf8  # noqa: E402

PASTA_DE_SAIDA = _PROJECT_ROOT / "scripts" / "output"

#: Uma planilha nova (contador zerado) a cada conversa.
FabricaDePlanilha = Callable[[], PlanilhaContada]


@dataclass
class Execucao:
    repeticao: int
    status: Status
    criterios: list[ResultadoCriterio]
    turnos: list[TurnoObservado]


@dataclass
class ResultadoCaso:
    caso: Caso
    execucoes: list[Execucao]

    @property
    def status(self) -> Status:
        """Todas as repetições passaram, falharam ou não se aplicam; mistura = falhou."""
        estados = {execucao.status for execucao in self.execucoes}
        if len(estados) == 1:
            return estados.pop()
        # Repetições divergentes: o caso não é confiável, conta como falha (ou
        # xfail, se já era esperado falhar).
        return "xfail" if self.caso.xfail else "falhou"

    @property
    def aprovadas(self) -> int:
        return sum(execucao.status in ("passou", "xpass") for execucao in self.execucoes)


def _fonte(planilha: PlanilhaContada) -> GoogleSheetsDataSource:
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha))


def executar_deterministico(caso: Caso, planilha: PlanilhaContada) -> list[TurnoObservado]:
    """Executa a `chamada` de cada turno direto na tool, como a LLM faria.

    Criar as tools lê o catálogo da semana: essa leitura (e o tempo dela) conta
    no primeiro turno executado, como contaria numa conversa de verdade.
    """
    criacao: tuple[int, float] | None = (planilha.chamadas, time.perf_counter())
    tools = {
        tool.name: tool
        for tool in criar_tools(_fonte(planilha), SemHistoricoContinuidadeDataSource(), caso.hoje)
    }
    observados: list[TurnoObservado] = []
    for indice, turno in enumerate(caso.turnos, start=1):
        observado = TurnoObservado()
        observados.append(observado)
        if turno.chamada is None:
            continue
        tool = tools.get(turno.chamada.tool)
        if tool is None:
            observado.erro = f"tool inexistente: {turno.chamada.tool}"
            continue
        antes, inicio = criacao or (planilha.chamadas, time.perf_counter())
        criacao = None
        try:
            mensagem = tool.invoke(
                {
                    "type": "tool_call",
                    "name": tool.name,
                    "args": resolver_datas(turno.chamada.args, caso.semana),
                    "id": f"{caso.id}-{indice}",
                }
            )
        except Exception as erro:
            # Só o tipo: a mensagem pode repetir dados da planilha.
            observado.erro = f"{tool.name} levantou {type(erro).__name__}"
        else:
            observado.tools = [tool.name]
            observado.textos_das_tools = [str(mensagem.content)]
            blocos = extrair_blocos_do_turno([HumanMessage(turno.pergunta), mensagem])
            observado.blocos = [bloco.model_dump(mode="json") for bloco in blocos]
        observado.segundos = time.perf_counter() - inicio
        observado.chamadas_planilha = planilha.chamadas - antes
    return observados


def executar_llm(
    caso: Caso, planilha: PlanilhaContada, chat_model: BaseChatModel
) -> list[TurnoObservado]:
    """Uma conversa nova com o agente real, turno a turno, com o histórico acumulado.

    Criar o agente lê o catálogo da semana: conta no primeiro turno.
    """
    criacao: tuple[int, float] | None = (planilha.chamadas, time.perf_counter())
    agente = criar_agente(
        _fonte(planilha),
        SemHistoricoContinuidadeDataSource(),
        chat_model,
        caso.hoje,
        renderiza_relatorios=True,
    )
    historico: list[BaseMessage] = []
    observados: list[TurnoObservado] = []
    for turno in caso.turnos:
        observado = TurnoObservado()
        observados.append(observado)
        if any(anterior.erro for anterior in observados[:-1]):
            observado.erro = "conversa interrompida no turno anterior"
            continue
        historico.append(HumanMessage(turno.pergunta))
        antes, inicio = criacao or (planilha.chamadas, time.perf_counter())
        criacao = None
        try:
            resposta, mensagens, blocos = perguntar_com_mensagens(agente, historico)
        except Exception as erro:
            observado.erro = f"agente levantou {type(erro).__name__}"
            continue
        finally:
            observado.segundos = time.perf_counter() - inicio
            observado.chamadas_planilha = planilha.chamadas - antes
        novas = mensagens[len(historico) :]
        observado.resposta = resposta
        observado.tools = [
            chamada["name"]
            for mensagem in novas
            if isinstance(mensagem, AIMessage)
            for chamada in mensagem.tool_calls
        ]
        observado.textos_das_tools = [
            str(mensagem.content) for mensagem in novas if isinstance(mensagem, ToolMessage)
        ]
        observado.blocos = [bloco.model_dump(mode="json") for bloco in blocos]
        for mensagem in novas:
            if isinstance(mensagem, AIMessage) and mensagem.usage_metadata:
                observado.tokens_entrada += mensagem.usage_metadata["input_tokens"]
                observado.tokens_saida += mensagem.usage_metadata["output_tokens"]
        historico = list(mensagens)
    return observados


def avaliar_casos(
    casos: Sequence[Caso],
    payload: dict[str, Any],
    executar: Callable[[Caso, PlanilhaContada], list[TurnoObservado]],
    *,
    llm: bool,
    repeticoes: int = 1,
    planilha_ficticia: bool = True,
) -> list[ResultadoCaso]:
    resultados: list[ResultadoCaso] = []
    for caso in casos:
        execucoes: list[Execucao] = []
        for repeticao in range(1, repeticoes + 1):
            if caso.so_ficticia and not planilha_ficticia:
                execucoes.append(Execucao(repeticao, "pulado", [], []))
                continue
            observados = executar(caso, PlanilhaContada(lambda _params: payload))
            criterios = avaliar(caso, observados, Contexto(payload, caso.semana), llm=llm)
            execucoes.append(
                Execucao(repeticao, status_do_caso(caso, criterios), criterios, observados)
            )
        resultados.append(ResultadoCaso(caso, execucoes))
    return resultados


def avaliar_deterministico(
    casos: Sequence[Caso], payload: dict[str, Any] | None = None
) -> list[ResultadoCaso]:
    """O modo `--deterministico` sobre a semana fictícia (ou o `payload` dado)."""
    return avaliar_casos(
        casos,
        payload if payload is not None else payload_da_semana(),
        executar_deterministico,
        llm=False,
        planilha_ficticia=payload is None,
    )


# ---------------------------------------------------------------- saída

_ROTULOS: dict[str, str] = {
    "passou": "passou",
    "falhou": "FALHOU",
    "xfail": "xfail",
    "xpass": "XPASS",
    "n/a": "n/a",
    "pulado": "pulado",
}


def houve_falha(resultados: Sequence[ResultadoCaso]) -> bool:
    return any(resultado.status in ("falhou", "xpass") for resultado in resultados)


def _media(valores: Sequence[float]) -> float:
    return sum(valores) / len(valores) if valores else 0.0


def linhas_da_tabela(resultados: Sequence[ResultadoCaso], *, llm: bool) -> list[str]:
    """Uma linha por caso: só status, métricas e o nome dos critérios que falharam."""
    cabecalho = (
        f"{'caso':<5} {'status':<13} {'tempo':>7} {'tools':>5} {'planilha':>8} "
        f"{'tokens (ent/saí)':>17}  critérios que falharam"
    )
    linhas = [cabecalho, "-" * len(cabecalho)]
    for resultado in resultados:
        execucoes = [e for e in resultado.execucoes if e.status != "pulado"]
        status = _ROTULOS[resultado.status]
        if len(resultado.execucoes) > 1:
            status += f" {resultado.aprovadas}/{len(resultado.execucoes)}"
        turnos = [e.turnos for e in execucoes]
        tempo = _media([sum(t.segundos for t in ts) for ts in turnos])
        tools = _media([sum(len(t.tools) for t in ts) for ts in turnos])
        planilha = _media([sum(t.chamadas_planilha for t in ts) for ts in turnos])
        tokens = "-"
        if llm and execucoes:
            entrada = _media([sum(t.tokens_entrada for t in ts) for ts in turnos])
            saida = _media([sum(t.tokens_saida for t in ts) for ts in turnos])
            tokens = f"{_br(entrada)}/{_br(saida)}"
        falhos = sorted({c.tipo for e in execucoes for c in e.criterios if c.ok is False})
        linhas.append(
            f"{resultado.caso.id:<5} {status:<13} {tempo:>6.2f}s {tools:>5.1f} {planilha:>8.1f} "
            f"{tokens:>17}  {', '.join(falhos) or '-'}"
        )
    return linhas


def detalhe_em_json(
    resultados: Sequence[ResultadoCaso], *, modo: str, fonte: str
) -> dict[str, Any]:
    """Detalhe por critério e por turno, sem texto de resposta nem de tool."""
    return {
        "modo": modo,
        "fonte": fonte,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "casos": [
            {
                "id": resultado.caso.id,
                "descricao": resultado.caso.descricao,
                "xfail": resultado.caso.xfail,
                "status": resultado.status,
                "execucoes": [
                    {
                        "repeticao": execucao.repeticao,
                        "status": execucao.status,
                        "criterios": [asdict(criterio) for criterio in execucao.criterios],
                        "turnos": [
                            {
                                "tools": turno.tools,
                                "blocos": [bloco.get("tipo") for bloco in turno.blocos],
                                "chamadas_planilha": turno.chamadas_planilha,
                                "segundos": round(turno.segundos, 3),
                                "tokens_entrada": turno.tokens_entrada,
                                "tokens_saida": turno.tokens_saida,
                                "erro": turno.erro,
                            }
                            for turno in execucao.turnos
                        ],
                    }
                    for execucao in resultado.execucoes
                ],
            }
            for resultado in resultados
        ],
    }


def gravar_detalhe(detalhe: dict[str, Any], pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    caminho = pasta / f"casos_reais_{detalhe['modo']}_{carimbo}.json"
    caminho.write_text(json.dumps(detalhe, ensure_ascii=False, indent=2), encoding="utf-8")
    return caminho


# ---------------------------------------------------------------- linha de comando


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avalia o RealocAI com os casos reais.")
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument(
        "--deterministico",
        action="store_true",
        help="Sem modelo: executa as tools dos casos e aplica os critérios (gratuito).",
    )
    modo.add_argument(
        "--llm", action="store_true", help="Conversa com o agente real (pago; ver --confirmar)."
    )
    parser.add_argument(
        "--casos", default=None, help="Só estes casos, separados por vírgula (ex.: c01,c07)."
    )
    parser.add_argument(
        "--planilha-id",
        default=None,
        help="Id da planilha de testes no Google, lida uma vez e só em memória. "
        "Padrão: a semana fictícia.",
    )
    parser.add_argument(
        "--repeticoes",
        type=int,
        default=1,
        help="--llm: quantas vezes cada caso roda, sempre em conversa nova. Padrão: 1.",
    )
    parser.add_argument(
        "--preco-entrada",
        type=float,
        default=None,
        help="--llm (obrigatório): US$ por milhão de tokens de entrada do modelo do .env.",
    )
    parser.add_argument(
        "--preco-saida",
        type=float,
        default=None,
        help="--llm (obrigatório): US$ por milhão de tokens de saída do modelo do .env.",
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="--llm: roda de verdade (pago). Sem isto, só mostra a estimativa.",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=PASTA_DE_SAIDA,
        help="Pasta do detalhe em JSON. Padrão: scripts/output/.",
    )
    argumentos = parser.parse_args(argv)
    if argumentos.repeticoes < 1:
        parser.error("--repeticoes precisa ser 1 ou mais.")
    if argumentos.llm:
        if argumentos.preco_entrada is None or argumentos.preco_saida is None:
            parser.error(
                "--llm exige --preco-entrada e --preco-saida (US$ por milhão de tokens), "
                "conferidos na tabela de preços do provedor para o modelo do .env."
            )
    else:
        usados = [
            nome
            for nome, valor in (
                ("--confirmar", argumentos.confirmar),
                ("--repeticoes", argumentos.repeticoes != 1),
                ("--preco-entrada", argumentos.preco_entrada is not None),
                ("--preco-saida", argumentos.preco_saida is not None),
            )
            if valor
        ]
        if usados:
            parser.error(f"{', '.join(usados)} só vale(m) com --llm.")
    return argumentos


def selecionar_casos(casos: list[Caso], filtro: str | None) -> list[Caso]:
    if not filtro:
        return casos
    pedidos = [item.strip() for item in filtro.split(",") if item.strip()]
    desconhecidos = sorted(set(pedidos) - {caso.id for caso in casos})
    if desconhecidos:
        raise SystemExit(f"Casos desconhecidos: {', '.join(desconhecidos)}")
    return [caso for caso in casos if caso.id in pedidos]


def estimar(casos: Sequence[Caso], argumentos: argparse.Namespace) -> Estimativa:
    """Cada turno conta como uma conversa de `estimar_custo` (o histórico cresce: é aproximado)."""
    planilha = PlanilhaContada()
    tokens_prompt = tokens_do_prompt(_fonte(planilha), casos[0].hoje)
    turnos = sum(len(caso.turnos) for caso in casos)
    return estimar_custo(
        tokens_prompt,
        turnos,
        argumentos.repeticoes,
        argumentos.preco_entrada,
        argumentos.preco_saida,
    )


def _imprimir_estimativa(
    estimativa: Estimativa, casos: Sequence[Caso], argumentos: argparse.Namespace
) -> None:
    print(
        f"Estimativa: {len(casos)} caso(s), {estimativa.conversas} turno(s) com as "
        f"{argumentos.repeticoes} repetição(ões), ~{CHAMADAS_POR_CONVERSA} chamadas ao modelo "
        f"por turno; prompt + schemas das tools ~{_br(estimativa.tokens_prompt)} tokens por "
        "chamada (o histórico dos turnos seguintes não entra: é um piso)."
    )
    print(
        f"  ~{_br(estimativa.tokens_entrada)} tokens de entrada e "
        f"~{_br(estimativa.tokens_saida)} de saída -> ~US$ {_br(estimativa.dolares, 2)} "
        f"a US$ {_br(argumentos.preco_entrada, 2)}/M (entrada) e "
        f"US$ {_br(argumentos.preco_saida, 2)}/M (saída)."
    )


def _ler_planilha_de_testes(planilha_id: str) -> dict[str, Any]:
    """Lê a planilha de testes uma vez; o payload (com nomes reais) fica só em memória."""
    os.environ["GOOGLE_SHEETS_SPREADSHEET_ID"] = planilha_id
    get_settings.cache_clear()
    real = abrir_planilha()
    contada = PlanilhaContada(
        lambda params: dict(real.fetch_sheet_metadata(params=dict(params or {})))
    )
    # Os parâmetros da leitura são os da produção: quem busca é a própria fonte.
    _fonte(contada).listar_salas(datetime.now().date())
    return contada.fetch_sheet_metadata()


def main(argv: list[str] | None = None) -> int:
    reconfigurar_para_utf8(sys.stdin, sys.stdout, sys.stderr)
    argumentos = _argumentos(argv)
    casos = selecionar_casos(carregar_casos(), argumentos.casos)
    modo = "llm" if argumentos.llm else "deterministico"

    if argumentos.llm:
        _imprimir_estimativa(estimar(casos, argumentos), casos, argumentos)
        if not argumentos.confirmar:
            print("\nNada foi chamado. Para rodar (pago), repita o comando com --confirmar.")
            return 0

    ficticia = argumentos.planilha_id is None
    payload = payload_da_semana() if ficticia else _ler_planilha_de_testes(argumentos.planilha_id)
    executar: Callable[[Caso, PlanilhaContada], list[TurnoObservado]] = executar_deterministico
    if argumentos.llm:
        executar = partial(executar_llm, chat_model=criar_chat_model())

    resultados = avaliar_casos(
        casos,
        payload,
        executar,
        llm=argumentos.llm,
        repeticoes=argumentos.repeticoes,
        planilha_ficticia=ficticia,
    )
    fonte = "semana fictícia" if ficticia else "planilha de testes (em memória)"
    print(f"\nCasos reais — modo {modo}, {fonte}\n")
    print("\n".join(linhas_da_tabela(resultados, llm=argumentos.llm)))
    caminho = gravar_detalhe(detalhe_em_json(resultados, modo=modo, fonte=fonte), argumentos.saida)
    print(f"\nDetalhe por critério: {caminho}")
    return 1 if houve_falha(resultados) else 0


if __name__ == "__main__":
    sys.exit(main())
