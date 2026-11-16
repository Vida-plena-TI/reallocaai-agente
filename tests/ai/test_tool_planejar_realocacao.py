"""Tool `planejar_realocacao` sobre a semana fictícia: texto, bloco e privacidade.

O argumento `paciente` carrega um nome dito na conversa. Ele nunca pode
aparecer em log, no tracing, no `content` que o modelo lê nem no texto final;
os nomes exibidos vão só no bloco da tela. Nomes fictícios.
"""

import logging
from collections.abc import Iterator
from datetime import date
from typing import Any, cast

import gspread
import langsmith
import langsmith.utils
import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult
from pydantic import Field

from app.ai.agente import criar_agente, perguntar_com_mensagens
from app.ai.blocos_gerais import nomes_de_pacientes_no_bloco
from app.ai.blocos_realocacao import AVISOS_PLANO_REALOCACAO
from app.ai.fundamentacao import nomes_de_paciente_na_resposta, termos_proibidos
from app.ai.rastreamento import desligar_rastreamento_externo
from app.ai.relatorios import (
    RELATORIO_PLANO_REALOCACAO,
    RELATORIOS_COM_NOMES_DE_PACIENTES,
    BlocoRelatorio,
)
from app.ai.tools import criar_tools
from app.api.sessoes import ArmazenamentoConversas
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import ScheduleDataSource
from app.engine.realocacao import ROTULO_PACIENTE_NOVO
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana
from tests.support.fake_chat_model import FakeToolCallingChatModel

HOJE = date(2026, 9, 28)
TOOL = "planejar_realocacao"
#: Nome dito na conversa, fora da grade (paciente novo com nome).
NOME = "Paciente Ficticio Zirconia"

PEDIDO_C06: dict[str, Any] = {
    "dias_semana": ["quarta"],
    "especialidade": "psico",
    "duracao_minutos": 60,
    "horario_desejado": "10:00",
    "horario_minimo": "10:00",
    "horario_maximo": "11:00",
}


def _fonte(cenario: str | None = None) -> GoogleSheetsDataSource:
    planilha = PlanilhaContada(lambda _params: payload_da_semana(cenario))
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha))


def _invocar(args: dict[str, Any], fonte: ScheduleDataSource | None = None) -> ToolMessage:
    tools = {
        t.name: t
        for t in criar_tools(fonte or _fonte(), SemHistoricoContinuidadeDataSource(), HOJE)
    }
    mensagem = tools[TOOL].invoke({"name": TOOL, "args": args, "id": "t", "type": "tool_call"})
    assert isinstance(mensagem, ToolMessage)
    return mensagem


def _sem_nomes(texto: str) -> bool:
    """Nenhum nome de paciente da semana fictícia (todos começam assim) nem o pedido."""
    baixo = texto.lower()
    return "paciente ficticio" not in baixo and "zirconia" not in baixo


def test_c06_texto_por_papeis_e_bloco_com_os_nomes() -> None:
    mensagem = _invocar(PEDIDO_C06)

    texto = str(mensagem.content)
    assert _sem_nomes(texto)
    assert texto.startswith("Plano de realocação (grade semanal vigente): 3 opções")
    assert "quem está na quarta das 10:00 às 11:00 com Carol (Sala 3) vai para quarta das " in texto
    assert "não escreva nomes de pacientes" in texto
    assert termos_proibidos(texto) == []

    bloco = mensagem.artifact
    assert bloco is not None
    dados = bloco["dados"]
    assert dados["relatorio"] == RELATORIO_PLANO_REALOCACAO
    assert bloco["contem_dados_pacientes"] is True
    assert bloco["periodo"] is None
    assert dados["agrupar_por"] == ["opcao"]
    assert dados["unidade_grupo"] == {"singular": "passo", "plural": "passos"}
    assert [c["chave"] for c in dados["colunas"]] == [
        "opcao", "passo", "paciente", "terapia", "profissional", "de", "para", "observacao",
    ]  # fmt: skip
    assert [c["rotulo"] for c in dados["colunas"]][2] == "Quem"
    primeira = dados["linhas"][:2]
    assert primeira[0] == {
        "opcao": "Opção 1",
        "passo": 1,
        "paciente": "Paciente Ficticio Bravo 3",
        "terapia": "Psicologia",
        "profissional": "Carol",
        "de": "Qua 10:00 às 11:00 · Carol · Sala 3",
        "para": "Qua 09:00 às 10:00 · Carol · Sala 3",
        "observacao": "mesma terapia e profissional",
    }
    assert primeira[1]["paciente"] == ROTULO_PACIENTE_NOVO
    assert primeira[1]["de"] == "— (paciente novo)"
    assert set(AVISOS_PLANO_REALOCACAO) <= set(bloco["avisos"])
    assert (
        "Vagas livres na grade podem estar reservadas (reuniões, supervisão); confira antes de "
        "mover." in bloco["avisos"]
    )
    assert [t["nome"] for t in bloco["tabelas"]] == ["Passos", "Resumo por opção"]
    # O rótulo do paciente novo não é nome de paciente para a proteção do texto.
    nomes = nomes_de_pacientes_no_bloco(BlocoRelatorio.model_validate(bloco))
    assert ROTULO_PACIENTE_NOVO not in nomes and "Paciente Ficticio Bravo 3" in nomes


def test_duas_cadeias_e_sem_solucao_no_cenario_realocacao() -> None:
    fonte = _fonte("realocacao")
    plano = _invocar(PEDIDO_C06, fonte)
    linhas = plano.artifact["dados"]["linhas"] if plano.artifact else []
    assert {linha["opcao"] for linha in linhas} == {"Opção 1", "Opção 2", "Opção 3"}
    assert all(sum(1 for x in linhas if x["opcao"] == o) == 3 for o in ("Opção 1", "Opção 2"))
    assert _sem_nomes(str(plano.content))

    sem = _invocar({**PEDIDO_C06, "dias_semana": ["quinta"], "especialidade": "TO"}, fonte)
    assert "não encontrei opção para Terapia Ocupacional de 60 minutos" in str(sem.content)
    assert sem.artifact is not None and sem.artifact["dados"]["linhas"] == []


def test_nome_ambiguo_pergunta_sem_nomes_e_mostra_as_candidatas() -> None:
    mensagem = _invocar(
        {
            "paciente": "Paciente Ficticio Bravo",
            "dias_semana": ["quarta"],
            "horario_minimo": "08:00",
        }
    )

    texto = str(mensagem.content)
    assert texto.startswith("Há mais de um paciente na grade com esse nome. Qual deles?")
    assert "candidata 1: Fonoaudiologia com Bia" in texto
    assert "Bravo" not in texto and _sem_nomes(texto)
    bloco = mensagem.artifact
    assert bloco is not None
    assert bloco["dados"]["relatorio"] == RELATORIO_PLANO_REALOCACAO
    assert bloco["contem_dados_pacientes"] is True
    assert "agrupar_por" not in bloco["dados"]
    assert [c["rotulo"] for c in bloco["dados"]["colunas"]] == [
        "Candidata", "Nome", "Terapia", "Profissional", "Dias",
    ]  # fmt: skip
    assert bloco["dados"]["linhas"][0]["paciente"].startswith("Paciente Ficticio Bravo")


def test_o_que_falta_vira_pergunta_sem_bloco() -> None:
    mensagem = _invocar({"dias_semana": ["quarta"], "especialidade": "psico"})

    assert str(mensagem.content).startswith("Qual a duração do atendimento?")
    assert mensagem.artifact is None


def test_valor_fora_do_catalogo_e_recusado_sem_repetir_o_valor() -> None:
    mensagem = _invocar({**PEDIDO_C06, "dias_semana": ["domingo"]})

    assert "Parâmetros inválidos (dias_semana" in str(mensagem.content)
    assert "domingo" not in str(mensagem.content)


def test_descricao_da_tool_diz_quando_usar_e_que_nunca_se_pede_id() -> None:
    tools = {t.name: t for t in criar_tools(_fonte(), SemHistoricoContinuidadeDataSource(), HOJE)}
    descricao = " ".join(tools[TOOL].description.split())

    assert "pode mover o que for necessário" in descricao
    assert "NUNCA peça atendimento_id" in descricao
    assert "mesma terapia, o mesmo profissional e a mesma duração" in descricao


def test_registrado_entre_os_relatorios_com_nomes() -> None:
    assert RELATORIO_PLANO_REALOCACAO in RELATORIOS_COM_NOMES_DE_PACIENTES


@pytest.mark.parametrize(
    "frase",
    [
        "Movi o paciente das 10h para as 9h.",
        "Mudei a sessão de quarta.",
        "Troquei os dois horários.",
        "Realoquei a psicologia.",
        "O paciente foi movido para as 9h.",
        "A sessão foi trocada.",
    ],
)
def test_vocabulario_de_mudanca_feita_e_recusado(frase: str) -> None:
    assert termos_proibidos(frase)


def test_sugerir_mover_continua_permitido() -> None:
    assert termos_proibidos("Sugiro mover quem está às 10:00 para as 09:00.") == []


def test_protecao_cobre_o_nome_passado_no_argumento() -> None:
    chamada = AIMessage("", tool_calls=[{"name": TOOL, "args": {"paciente": NOME}, "id": "x"}])

    assert nomes_de_paciente_na_resposta(f"Sugiro mover {NOME.upper()}.", [], [chamada]) == 1
    assert nomes_de_paciente_na_resposta("Sugiro mover o pedido.", [], [chamada]) == 0
    generico = AIMessage(
        "", tool_calls=[{"name": TOOL, "args": {"paciente": "paciente novo"}, "id": "x"}]
    )
    assert nomes_de_paciente_na_resposta("O paciente novo fica às 10h.", [], [generico]) == 0


class _FonteQuebrada:
    def __getattr__(self, _nome: str) -> Any:
        def falhar(*_args: Any) -> Any:
            raise RuntimeError(f"falha simulada com {NOME}")

        return falhar


def test_erro_so_pelo_tipo(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        mensagem = _invocar({**PEDIDO_C06, "paciente": NOME}, cast(Any, _FonteQuebrada()))

    assert str(mensagem.content).startswith("Erro ao planejar a realocação (RuntimeError)")
    assert _sem_nomes(str(mensagem.content))
    assert _sem_nomes(_registros(caplog))


# --- agente: o nome do argumento não vaza para log, tracing, content nem texto ----


class _ModeloComCaptura(FakeToolCallingChatModel):
    entradas: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.entradas.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _registros(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records)


@pytest.fixture
def tracing_vigiado(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Liga o tracing pelo ambiente (como num deploy esquecido) e registra qualquer
    cliente do LangSmith ou tracer do LangChain criado depois do desligamento."""
    for variavel in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        monkeypatch.setenv(variavel, "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "chave-falsa")
    criados: list[str] = []
    original = langsmith.Client.__init__

    def vigiar(self: Any, *args: Any, **kwargs: Any) -> None:
        criados.append("langsmith.Client")
        original(self, *args, **kwargs)

    monkeypatch.setattr(langsmith.Client, "__init__", vigiar)
    # O que `criar_chat_model` faz antes de existir o modelo.
    desligar_rastreamento_externo()
    yield criados
    langsmith.configure(enabled=None)


def _conversar(
    args: dict[str, Any], resposta: str, pergunta: str
) -> tuple[str, list[BaseMessage], list[BlocoRelatorio], _ModeloComCaptura]:
    chamada = AIMessage("", tool_calls=[{"name": TOOL, "args": args, "id": "r1"}])
    modelo = _ModeloComCaptura(responses=[chamada, AIMessage(resposta)])
    agente = criar_agente(
        _fonte(), SemHistoricoContinuidadeDataSource(), modelo, HOJE, renderiza_relatorios=True
    )
    texto, mensagens, blocos = perguntar_com_mensagens(agente, [HumanMessage(pergunta)])
    return texto, mensagens, blocos, modelo


@pytest.mark.parametrize(
    ("args", "inicio_do_resumo"),
    [
        # Plano: o nome dito vai no bloco (rótulo do pedido).
        ({**PEDIDO_C06, "paciente": NOME, "situacao": "novo"}, "Plano de realocação"),
        # Pergunta sem bloco: só o argumento protege o nome.
        (
            {
                "paciente": NOME,
                "situacao": "novo",
                "dias_semana": ["quarta"],
                "especialidade": "psico",
            },
            "Qual a duração do atendimento?",
        ),
    ],
    ids=["plano", "pergunta-sem-bloco"],
)
def test_nome_do_argumento_nunca_vaza(
    args: dict[str, Any],
    inicio_do_resumo: str,
    tracing_vigiado: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    pergunta = f"preciso colocar psicologia de 1h na quarta às 10h para {NOME}"
    with caplog.at_level(logging.DEBUG):
        texto, mensagens, blocos, modelo = _conversar(
            args, f"Sugiro mover quem está às 10:00; {NOME} fica às 10:00.", pergunta
        )

    # A tool rodou de verdade.
    resultados = [m for m in mensagens if isinstance(m, ToolMessage)]
    assert len(resultados) == 1 and resultados[0].name == TOOL
    # content: o que o modelo lê da tool.
    assert _sem_nomes(str(resultados[0].content))
    for entrada in modelo.entradas:
        for mensagem in entrada:
            if isinstance(mensagem, ToolMessage):
                assert _sem_nomes(str(mensagem.content))
    # Texto final: a resposta que repetiu o nome vira o resumo determinístico.
    assert _sem_nomes(texto)
    assert texto.startswith(inicio_do_resumo)
    # Log: nem o nome, nem pedaço dele.
    registros = _registros(caplog)
    assert _sem_nomes(registros)
    assert "repetiu 1 nome(s) de paciente" in registros
    # Tracing: desligado mesmo com o ambiente ligando, e nenhum cliente criado.
    assert langsmith.utils.tracing_is_enabled() is False
    assert tracing_vigiado == []
    # Persistência: o histórico guarda a resposta do RealocAI sem o nome.
    conversas = ArmazenamentoConversas()
    conversa = conversas.criar_conversa()
    conversas.registrar_troca(conversa, pergunta, texto, blocos)
    historico = conversas.obter_historico(conversa)
    assert historico is not None
    assert _sem_nomes(str(historico[-1].content))
