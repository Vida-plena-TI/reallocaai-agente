"""Tool `planejar_enxugamento` sobre a semana fictícia: texto, bloco e privacidade.

O panorama leva nomes de pacientes E é sensível de RH: nomes de pacientes e de
profissionais nunca podem aparecer em log, no tracing, no `content` que o
modelo lê nem no texto final (inclusive os nomes passados nos argumentos
`profissionais_foco` e `profissionais_preservados`); vão só no bloco da tela.
Nomes fictícios.
"""

import json
import logging
import re
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
from app.ai.blocos_enxugamento import (
    ABA_PASSOS,
    ABA_RESUMO,
    AVISOS_PANORAMA,
    nomes_de_profissionais_no_bloco,
)
from app.ai.blocos_gerais import nomes_de_pacientes_no_bloco
from app.ai.fundamentacao import nomes_de_profissional_no_panorama, termos_proibidos
from app.ai.rastreamento import desligar_rastreamento_externo
from app.ai.relatorios import (
    RELATORIO_PANORAMA_ENXUGAMENTO,
    RELATORIOS_COM_NOMES_DE_PACIENTES,
    BlocoRelatorio,
)
from app.ai.tools import criar_tools
from app.api.sessoes import ArmazenamentoConversas
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import ScheduleDataSource
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana
from tests.support.fake_chat_model import FakeToolCallingChatModel

HOJE = date(2026, 9, 28)
TOOL = "planejar_enxugamento"
CENARIO = "enxugamento"
#: Limites da exportação do chatbot (Excel/PDF): linhas por tabela e nome da aba.
MAX_LINHAS_EXPORTACAO = 5_000
MAX_CARACTERES_ABA = 31
_CODIGO = re.compile(r"\bE-[0-9A-F]{4}\b")


def _fonte(cenario: str | None = CENARIO) -> GoogleSheetsDataSource:
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
    """Nenhum nome de paciente (todos começam assim) nem de profissional da semana."""
    baixo = texto.lower()
    return all(
        nome not in baixo
        for nome in ("paciente ficticio", "ficticia", "acacia", "bromelia", "camelia", "zuleide")
    )


def test_texto_so_com_contagens_e_bloco_com_os_nomes() -> None:
    mensagem = _invocar({})

    texto = str(mensagem.content)
    assert _sem_nomes(texto)
    assert texto.startswith("Panorama de enxugamento (grade semanal vigente): 5 cenários")
    assert "sem abrir turno novo" in texto
    assert len(set(_CODIGO.findall(texto))) == 5
    assert "recomendado para o objetivo pedido" in texto
    assert "não escreva nenhum nome" in texto
    assert termos_proibidos(texto) == []

    bloco = mensagem.artifact
    assert bloco is not None
    BlocoRelatorio.model_validate(bloco)
    dados = bloco["dados"]
    assert bloco["tipo"] == "tabela_generica"
    assert dados["relatorio"] == RELATORIO_PANORAMA_ENXUGAMENTO
    assert bloco["contem_dados_pacientes"] is True
    assert bloco["periodo"] is None
    assert bloco["meta"] is None
    assert dados["agrupar_por"] == ["cenario", "turno_liberado"]
    assert dados["unidade_grupo"] == {"singular": "passo", "plural": "passos"}
    assert [c["chave"] for c in dados["colunas"]] == [
        "cenario", "turno_liberado", "passo", "paciente", "terapia", "de", "para", "alavanca",
        "observacao",
    ]  # fmt: skip
    primeira = dados["linhas"][0]
    assert re.fullmatch(
        r"Cenário 1 \(E-[0-9A-F]{4}\) · equilibrado · libera \d turnos?", primeira["cenario"]
    )
    assert _sem_nomes(primeira["cenario"])
    assert re.fullmatch(
        r"(Seg|Ter|Qua|Qui|Sex|Sáb) (manhã|tarde) · .+ · .+", primeira["turno_liberado"]
    )
    assert primeira["paciente"].startswith("Paciente Ficticio ")
    assert primeira["terapia"] == "Terapia Alimentar"
    assert re.fullmatch(r"\w{3} \d\d:\d\d às \d\d:\d\d · Ficticia \w+ · Sala \d", primeira["de"])
    assert primeira["alavanca"].startswith("L")
    # Resumo e avisos (que o resumo determinístico e o modelo podem ler): sem nomes.
    assert all(_sem_nomes(json.dumps(item, ensure_ascii=False)) for item in bloco["resumo"])
    assert _sem_nomes(" ".join(bloco["avisos"]))
    assert set(AVISOS_PANORAMA) <= set(bloco["avisos"])
    validado = BlocoRelatorio.model_validate(bloco)
    assert "Paciente Ficticio Aroeira" in nomes_de_pacientes_no_bloco(validado)
    assert "Ficticia Acacia" in nomes_de_profissionais_no_bloco(validado)


def test_exportacao_dentro_dos_limites_do_chatbot() -> None:
    bloco = _invocar({}).artifact
    assert bloco is not None
    assert [t["nome"] for t in bloco["tabelas"]] == [ABA_PASSOS, ABA_RESUMO]
    for aba in bloco["tabelas"]:
        assert len(aba["nome"]) <= MAX_CARACTERES_ABA
        assert len(aba["linhas"]) <= MAX_LINHAS_EXPORTACAO
    resumo = bloco["tabelas"][1]
    assert len(resumo["linhas"]) == 5
    assert {linha["turnos_novos"] for linha in resumo["linhas"]} == {0}
    assert all(_sem_nomes(json.dumps(linha, ensure_ascii=False)) for linha in resumo["linhas"])


def test_cenarios_ja_vistos_pela_tool() -> None:
    primeiro = _invocar({"max_cenarios": 2})
    vistos = _CODIGO.findall(str(primeiro.content))
    assert len(set(vistos)) == 2

    segundo = _invocar({"max_cenarios": 2, "cenarios_ja_vistos": vistos})
    novos = set(_CODIGO.findall(str(segundo.content)))
    assert novos and novos.isdisjoint(vistos)


def test_aviso_de_quem_fica_sem_atendimento_na_semana() -> None:
    mensagem = _invocar({}, _fonte("enxugamento_tres"))
    bloco = mensagem.artifact
    assert bloco is not None
    assert any("sem atendimento na semana" in aviso for aviso in bloco["avisos"])
    assert any(
        "esta profissional fica sem atendimento na semana" in linha["observacao"]
        for linha in bloco["dados"]["linhas"]
    )
    assert _sem_nomes(str(mensagem.content))


def test_turnos_presos_so_como_contagem() -> None:
    mensagem = _invocar({}, _fonte("enxugamento_presos"))
    texto = str(mensagem.content)
    assert "presos por regra: 2 (1 atendimento aguardando autorização, 1 sessão em grupo)" in texto
    assert _sem_nomes(texto)


def test_sem_cenario_nao_manda_bloco() -> None:
    mensagem = _invocar({"dias_semana": ["sexta"]})

    assert mensagem.artifact is None
    assert "não encontrei movimentação que libere turno" in str(mensagem.content)


def test_foco_ambiguo_pergunta_por_papel_sem_bloco() -> None:
    mensagem = _invocar({"profissionais_foco": ["Ficticia"], "liberar_turno": "manha"})

    texto = str(mensagem.content)
    assert mensagem.artifact is None
    assert texto.startswith("Mais de uma profissional corresponde a um dos nomes citados")
    assert "2 candidatas: uma de Terapia Alimentar, uma de Terapia Alimentar" in texto
    assert _sem_nomes(texto)


def test_profissional_nao_encontrada_nao_repete_o_nome() -> None:
    mensagem = _invocar({"profissionais_preservados": ["Zuleide"]})

    assert mensagem.artifact is None
    assert "não está na grade desta semana" in str(mensagem.content)
    assert _sem_nomes(str(mensagem.content))


def test_foco_resolvido_libera_so_o_turno_pedido() -> None:
    mensagem = _invocar({"profissionais_foco": ["ficticia acacia"], "liberar_turno": "tarde"})

    bloco = mensagem.artifact
    assert bloco is not None
    turnos = {linha["turno_liberado"] for linha in bloco["dados"]["linhas"]}
    assert turnos == {"Ter tarde · Ficticia Acacia · Terapia Alimentar"}
    assert _sem_nomes(str(mensagem.content))


def test_valor_fora_do_catalogo_e_recusado_sem_repetir_o_valor() -> None:
    mensagem = _invocar({"dias_semana": ["domingo"]})

    assert "Parâmetros inválidos (dias_semana" in str(mensagem.content)
    assert "domingo" not in str(mensagem.content)


def test_descricao_da_tool() -> None:
    tools = {t.name: t for t in criar_tools(_fonte(), SemHistoricoContinuidadeDataSource(), HOJE)}
    descricao = " ".join(tools[TOOL].description.split())

    assert "e se a X ficar só de tarde?" in descricao
    assert "NUNCA escreva nomes de pacientes nem de profissionais" in descricao
    assert "nunca fale em valores, salário ou dinheiro" in descricao
    assert "nunca diga que moveu, liberou ou economizou" in descricao


def test_registrado_entre_os_relatorios_com_nomes() -> None:
    assert RELATORIO_PANORAMA_ENXUGAMENTO in RELATORIOS_COM_NOMES_DE_PACIENTES


@pytest.mark.parametrize(
    "frase",
    [
        "Dá para cortar a manhã da terapeuta.",
        "Sugiro o corte de dois turnos.",
        "Você pode demitir uma profissional.",
        "Dá para dispensar a colega na quarta.",
        "Hoje há prejuízo nesse turno.",
        "O custo da profissional é alto.",
        "Isso reduz o salário.",
        "A economia é de R$300 por semana.",
        "Economizei três turnos.",
        "Liberei a manhã de quarta.",
        "Reduzi a folha.",
    ],
)
def test_vocabulario_sensivel_de_rh_e_recusado(frase: str) -> None:
    assert termos_proibidos(frase)


def test_sugerir_liberar_turno_continua_permitido() -> None:
    texto = "Sugiro liberar o turno de quarta: o cenário E-3F2A libera 3 turnos por semana."
    assert termos_proibidos(texto) == []


def test_guarda_de_nome_de_profissional_so_na_rodada_do_panorama() -> None:
    bloco = BlocoRelatorio.model_validate(_invocar({}).artifact)
    assert nomes_de_profissional_no_panorama("Sugiro mudar a Ficticia Acacia.", [bloco]) == 1
    assert nomes_de_profissional_no_panorama("Sugiro o cenário E-3F2A.", [bloco]) == 0
    # Nome da equipe que não está no bloco também conta.
    assert nomes_de_profissional_no_panorama("A Carol absorve.", [bloco], ["Carol"]) == 1
    # Fora da rodada do panorama, a guarda não age.
    assert nomes_de_profissional_no_panorama("Sugiro mudar a Ficticia Acacia.", []) == 0


class _FonteQuebrada:
    def __getattr__(self, _nome: str) -> Any:
        def falhar(*_args: Any) -> Any:
            raise RuntimeError("falha simulada com Ficticia Acacia")

        return falhar


def _registros(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.name} {r.getMessage()} {r.args!r}" for r in caplog.records)


def test_erro_so_pelo_tipo(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        mensagem = _invocar(
            {"profissionais_foco": ["Ficticia Acacia"]}, cast(Any, _FonteQuebrada())
        )

    assert str(mensagem.content).startswith("Erro ao montar o panorama (RuntimeError)")
    assert _sem_nomes(str(mensagem.content))
    assert _sem_nomes(_registros(caplog))


def test_log_sem_os_nomes_dos_argumentos(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        _invocar({"profissionais_foco": ["Ficticia Acacia"], "liberar_turno": "tarde"})
        _invocar({"profissionais_preservados": ["Ficticia Bromelia"]})

    registros = _registros(caplog)
    assert "planejar_enxugamento" in registros
    assert _sem_nomes(registros)


# --- agente: nomes não vazam para log, tracing, content nem texto final -------


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


@pytest.fixture
def tracing_vigiado(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Liga o tracing pelo ambiente e registra qualquer cliente do LangSmith criado."""
    for variavel in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        monkeypatch.setenv(variavel, "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "chave-falsa")
    criados: list[str] = []
    original = langsmith.Client.__init__

    def vigiar(self: Any, *args: Any, **kwargs: Any) -> None:
        criados.append("langsmith.Client")
        original(self, *args, **kwargs)

    monkeypatch.setattr(langsmith.Client, "__init__", vigiar)
    desligar_rastreamento_externo()
    yield criados
    langsmith.configure(enabled=None)


def _conversar(
    args: dict[str, Any], resposta: str, pergunta: str
) -> tuple[str, list[BaseMessage], list[BlocoRelatorio], _ModeloComCaptura]:
    chamada = AIMessage("", tool_calls=[{"name": TOOL, "args": args, "id": "e1"}])
    modelo = _ModeloComCaptura(responses=[chamada, AIMessage(resposta)])
    agente = criar_agente(
        _fonte(), SemHistoricoContinuidadeDataSource(), modelo, HOJE, renderiza_relatorios=True
    )
    texto, mensagens, blocos = perguntar_com_mensagens(agente, [HumanMessage(pergunta)])
    return texto, mensagens, blocos, modelo


@pytest.mark.parametrize(
    "resposta",
    [
        "Sugiro o primeiro cenário: a Ficticia Acacia fica só de manhã na terça.",
        "Sugiro passar o Paciente Ficticio Figueira para a manhã.",
    ],
    ids=["nome-de-profissional", "nome-de-paciente"],
)
def test_nomes_nunca_vazam(
    resposta: str, tracing_vigiado: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    pergunta = "e se a Ficticia Acacia ficar só de manhã? que movimentações eu posso fazer?"
    args = {"profissionais_foco": ["Ficticia Acacia"], "liberar_turno": "tarde"}
    with caplog.at_level(logging.DEBUG):
        texto, mensagens, blocos, modelo = _conversar(args, resposta, pergunta)

    resultados = [m for m in mensagens if isinstance(m, ToolMessage)]
    assert len(resultados) == 1 and resultados[0].name == TOOL
    assert blocos and blocos[0].dados.tipo == "tabela_generica"
    # content: o que o modelo lê da tool.
    for entrada in modelo.entradas:
        for mensagem in entrada:
            if isinstance(mensagem, ToolMessage):
                assert _sem_nomes(str(mensagem.content))
    # Texto final: a resposta com nome vira o resumo determinístico (sem nomes).
    assert _sem_nomes(texto)
    assert texto.startswith("Panorama de enxugamento (grade semanal vigente)")
    # Log: nenhum nome, nem os dos argumentos.
    registros = _registros(caplog)
    assert _sem_nomes(registros)
    assert "resumo determinístico" in registros
    # Tracing: desligado mesmo com o ambiente ligando, e nenhum cliente criado.
    assert langsmith.utils.tracing_is_enabled() is False
    assert tracing_vigiado == []
    # Persistência: o histórico guarda a resposta do RealocAI sem nomes.
    conversas = ArmazenamentoConversas()
    conversa = conversas.criar_conversa()
    conversas.registrar_troca(conversa, pergunta, texto, blocos)
    historico = conversas.obter_historico(conversa)
    assert historico is not None
    assert _sem_nomes(str(historico[-1].content))


def test_resposta_so_com_contagens_passa(caplog: pytest.LogCaptureFixture) -> None:
    mensagem = _invocar({})
    turnos = re.search(r"o de mais turnos libera (\d+) turnos", str(mensagem.content))
    assert turnos is not None
    resposta = (
        f"Encontrei 5 cenários; o de mais turnos libera {turnos.group(1)} turnos por semana. "
        "Sugiro conferir o detalhe no relatório; cada mudança precisa ser confirmada com a "
        "família e feita manualmente na planilha."
    )
    with caplog.at_level(logging.DEBUG):
        texto, _, _, _ = _conversar({}, resposta, "que movimentações eu posso fazer?")

    assert texto == resposta
    assert "resumo determinístico" not in _registros(caplog)
