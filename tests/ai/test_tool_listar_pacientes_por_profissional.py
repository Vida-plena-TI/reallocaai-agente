"""Tool `listar_pacientes_por_profissional` sobre a semana fictícia `nomes_por_profissional`.

Nomes fictícios. O foco é a privacidade: os nomes só saem no artifact (bloco
para a tela), nunca no `content` que o modelo lê, no texto da resposta nem em
log. As contagens são conferidas contra o oráculo independente da régua e
contra a contagem de `consultar_pacientes_por_profissional`.
"""

import logging
import re
from datetime import date, timedelta
from typing import Any, cast

import gspread
import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult
from pydantic import Field

from app.ai.agente import criar_agente, perguntar_com_mensagens
from app.ai.blocos_gerais import (
    AVISOS_PACIENTES_POR_PROFISSIONAL_NOMES,
    RELATORIO_PACIENTES_POR_PROFISSIONAL_NOMES,
)
from app.ai.fundamentacao import afirma_nomes_na_tela, nomes_de_paciente_na_resposta
from app.ai.prompts import PROMPT_SISTEMA_COM_RELATORIOS
from app.ai.relatorios import BlocoRelatorio
from app.ai.tools import criar_tools
from app.ai.tools_pacientes_profissional import executar_listar_pacientes_por_profissional
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import Especialidade, dias_da_semana_de
from app.engine import visoes_fotografia
from app.engine.fotografia import fotografar_periodo
from app.engine.pacientes_por_profissional_nomes import listar_pacientes_por_profissional
from tests.casos_reais import oraculo
from tests.casos_reais.planilha import PECAS_DOS_CENARIOS, PlanilhaContada, payload_da_semana
from tests.support.agenda_clinica import NOMES_PACIENTES, fonte_clinica
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.golden_tools import fonte_borda

HOJE = date(2026, 9, 28)
SEMANA = [HOJE + timedelta(days=i) for i in range(6)]
CENARIO = "nomes_por_profissional"
TOOL = "listar_pacientes_por_profissional"

#: Todo texto de paciente do cenário, nas grafias da planilha.
NOMES_DO_CENARIO = sorted(
    {
        nome.strip()
        for pedacos in PECAS_DOS_CENARIOS[CENARIO].values()
        for pedaco, _ in pedacos
        for nome in pedaco.split("/")
    }
)


def _fonte(cenario: str | None = CENARIO) -> GoogleSheetsDataSource:
    planilha = PlanilhaContada(lambda _params: payload_da_semana(cenario))
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha))


def _executar(**filtros: Any) -> tuple[str, BlocoRelatorio]:
    texto, bloco = executar_listar_pacientes_por_profissional(_fonte(), HOJE, **filtros)
    assert bloco is not None, texto
    return texto, BlocoRelatorio.model_validate(bloco)


def _linhas(bloco: BlocoRelatorio) -> list[dict[str, Any]]:
    linhas: list[dict[str, Any]] = bloco.model_dump(mode="json")["dados"]["linhas"]
    return linhas


def _cita(texto: str, nome: str) -> bool:
    return (
        re.search(
            rf"(?<!\w){re.escape(oraculo.nome_normalizado(nome))}(?!\w)",
            (oraculo.nome_normalizado(texto)),
        )
        is not None
    )


def _citados(texto: str) -> list[str]:
    return [nome for nome in NOMES_DO_CENARIO if _cita(texto, nome)]


def _por_profissional(bloco: BlocoRelatorio) -> dict[str, dict[str, tuple[str, int]]]:
    resultado: dict[str, dict[str, tuple[str, int]]] = {}
    for linha in _linhas(bloco):
        resultado.setdefault(linha["profissional"], {})[
            oraculo.nome_normalizado(linha["paciente"])
        ] = (linha["dias"], linha["atendimentos_semana"])
    return resultado


def _do_oraculo(dias: list[date]) -> dict[str, dict[str, tuple[str, int]]]:
    contagem = oraculo.pacientes_por_profissional(payload_da_semana(CENARIO), dias)
    return {
        profissional: {nome: (p.dias, p.atendimentos) for nome, p in por_nome.items()}
        for profissional, por_nome in contagem.items()
    }


# --- contagens ----------------------------------------------------------------


def test_linhas_iguais_ao_oraculo() -> None:
    _, bloco = _executar()

    assert _por_profissional(bloco) == _do_oraculo(SEMANA)
    assert len(_linhas(bloco)) == 8


def test_distintos_batem_com_a_tool_de_contagem() -> None:
    """O resumo por profissional = `pacientes_distintos_semana` de `carga_profissionais`."""
    fonte = fonte_clinica()
    semana = dias_da_semana_de(HOJE)
    foto = fotografar_periodo(fonte, semana[0], semana[-1])
    carga = visoes_fotografia.carga_profissionais(foto, semana)
    lista = listar_pacientes_por_profissional(foto, semana)

    assert {g.profissional_id: len(g.pacientes) for g in lista.grupos} == {
        c.profissional_id: c.pacientes_distintos_semana
        for c in carga.profissionais
        if c.pacientes_distintos_semana
    }
    assert {g.profissional_id: g.atendimentos for g in lista.grupos} == {
        c.profissional_id: sum(d.sessoes for d in c.dias)
        for c in carga.profissionais
        if c.pacientes_distintos_semana
    }


def test_paciente_com_duas_profissionais_aparece_em_cada_uma() -> None:
    texto, bloco = _executar()
    alfa = [
        (linha["profissional"], linha["dias"])
        for linha in _linhas(bloco)
        if linha["paciente"] == "Paciente Ficticio Ametista"
    ]

    assert alfa == [("Bia", "Seg, Ter, Qua"), ("Carol", "Seg")]
    assert "3 pacientes são atendidos por mais de uma profissional" in texto
    assert next(i for i in bloco.resumo if i.rotulo == "Com mais de uma profissional").valor == 3


def test_homonimos_fundem_pelo_nome_normalizado() -> None:
    _, bloco = _executar()
    omega = [linha for linha in _linhas(bloco) if "mega" in linha["paciente"]]

    # "Paciente Fictício Ômega" (segunda) e "paciente ficticio omega" (quarta).
    assert len(omega) == 1
    assert omega[0]["paciente"] == "Paciente Fictício Ômega"
    assert (omega[0]["dias"], omega[0]["atendimentos_semana"]) == ("Seg, Qua", 2)


def test_sessao_em_grupo_conta_para_cada_paciente_e_uma_vez_para_a_profissional() -> None:
    _, bloco = _executar()
    assizely = _por_profissional(bloco)["Assizely"]
    resumo = bloco.model_dump(mode="json")["tabelas"][1]["linhas"]

    assert assizely["paciente ficticio esmeralda"] == ("Seg, Qua", 2)
    assert assizely["paciente ficticio basalto"] == ("Seg, Qua", 2)
    assert next(r for r in resumo if r["profissional"] == "Assizely")["atendimentos_semana"] == 3


# --- filtros ------------------------------------------------------------------


def test_filtro_de_profissionais_aceita_apelido_e_varias() -> None:
    _, bloco = _executar(profissionais=["bia", "CAROL"])

    assert {linha["profissional"] for linha in _linhas(bloco)} == {"Bia", "Carol"}
    esperado = _do_oraculo(SEMANA)
    assert _por_profissional(bloco) == {"Bia": esperado["Bia"], "Carol": esperado["Carol"]}


def test_filtro_de_especialidade() -> None:
    _, bloco = _executar(especialidade=Especialidade.FONOAUDIOLOGIA)

    assert [r["profissional"] for r in bloco.model_dump()["tabelas"][1]["linhas"]] == [
        "Bia",
        "Elo",
    ]


def test_filtro_de_dias_restringe_linhas_e_contagens() -> None:
    texto, bloco = _executar(profissionais=["Bia"], dias_semana=["quarta"])

    assert _por_profissional(bloco) == {"Bia": _do_oraculo([SEMANA[2]])["Bia"]}
    assert "Só os dias pedidos: Qua." in bloco.avisos
    assert "Bia (Fonoaudiologia): 2 pacientes, 2 atendimentos na semana." in texto


def test_filtros_sem_resultado_respondem_sem_bloco() -> None:
    texto, bloco = executar_listar_pacientes_por_profissional(
        _fonte(), HOJE, profissionais=["Elo"], dias_semana=["quarta"]
    )

    assert bloco is None
    assert texto.startswith("Nenhuma profissional tem paciente na grade desta semana")


def test_profissional_inexistente_lista_so_profissionais() -> None:
    texto, bloco = executar_listar_pacientes_por_profissional(
        _fonte(), HOJE, profissionais=["Zuleide"]
    )

    assert bloco is None
    assert texto == (
        "Não há profissional com o nome 'Zuleide' na grade desta semana. "
        "Profissionais da semana: Assizely, Bia, Carol, Elo."
    )


def test_profissional_ambiguo_pede_escolha() -> None:
    texto, bloco = executar_listar_pacientes_por_profissional(
        fonte_borda(), date(2026, 9, 30), profissionais=["Ana"]
    )

    assert bloco is None
    assert texto.startswith("Mais de uma profissional corresponde a 'Ana'")
    assert [n for n in NOMES_PACIENTES if n.casefold() in texto.casefold()] == []


# --- bloco ----------------------------------------------------------------------


def test_bloco_tabela_generica_sem_periodo_e_totais_so_no_resumo() -> None:
    _, bloco = _executar()
    dump = bloco.model_dump(mode="json")

    assert bloco.tipo == "tabela_generica"
    assert dump["periodo"] is None and dump["meta"] is None
    assert dump["dados"]["relatorio"] == RELATORIO_PACIENTES_POR_PROFISSIONAL_NOMES
    assert [c["chave"] for c in dump["dados"]["colunas"]] == [
        "especialidade",
        "profissional",
        "paciente",
        "dias",
        "atendimentos_semana",
    ]
    assert dump["dados"]["total"] == {} and dump["dados"]["linhas_omitidas"] == 0
    assert not any(str(linha["paciente"]).casefold() == "total" for linha in _linhas(bloco))
    # Agrupado por especialidade e profissional; pacientes em ordem alfabética.
    ordem = [(linha["especialidade"], linha["profissional"]) for linha in _linhas(bloco)]
    assert list(dict.fromkeys(ordem)) == [
        ("Fonoaudiologia", "Bia"),
        ("Fonoaudiologia", "Elo"),
        ("Psicologia", "Carol"),
        ("Terapia Ocupacional", "Assizely"),
    ]

    pacientes, resumo = dump["tabelas"]
    assert pacientes["nome"] == "Pacientes" and pacientes["linhas"] == _linhas(bloco)
    assert resumo["nome"] == "Resumo por profissional"
    assert [(r["profissional"], r["pacientes"]) for r in resumo["linhas"]] == [
        ("Bia", 3),
        ("Elo", 1),
        ("Carol", 1),
        ("Assizely", 3),
    ]
    assert [i.rotulo for i in bloco.resumo] == [
        "Profissionais",
        "Pacientes distintos",
        "Com mais de uma profissional",
    ]
    assert [i.valor for i in bloco.resumo] == [4, 5, 3]


def test_avisos_fixos_de_escopo() -> None:
    _, bloco = _executar()

    assert bloco.avisos[: len(AVISOS_PACIENTES_POR_PROFISSIONAL_NOMES)] == list(
        AVISOS_PACIENTES_POR_PROFISSIONAL_NOMES
    )
    assert any("grade semanal vigente" in a for a in bloco.avisos)
    assert any("identificado só pelo nome" in a for a in bloco.avisos)


def test_dia_ilegivel_deixa_o_relatorio_parcial(monkeypatch: pytest.MonkeyPatch) -> None:
    fonte = _fonte()
    original = fonte.listar_atendimentos

    def com_falha(dia: date) -> list[Any]:
        if dia == SEMANA[1]:
            raise RuntimeError("aba ilegível")
        return original(dia)

    monkeypatch.setattr(fonte, "listar_atendimentos", com_falha)
    texto, bloco = executar_listar_pacientes_por_profissional(fonte, HOJE)

    assert bloco is not None
    assert bloco["parcial"] is True and bloco["dias_nao_lidos"] == [SEMANA[1].isoformat()]
    assert bloco["periodo"] is None
    assert "Não foi possível ler: Ter; os números são parciais." in texto


# --- privacidade ----------------------------------------------------------------


def test_nomes_so_no_bloco() -> None:
    texto, bloco = _executar()

    assert {oraculo.nome_normalizado(linha["paciente"]) for linha in _linhas(bloco)} == {
        oraculo.nome_normalizado(nome) for nome in NOMES_DO_CENARIO
    }
    assert _citados(texto) == []
    assert _citados(bloco.titulo) == []
    assert _citados(" ".join(bloco.avisos)) == []
    assert _citados(" ".join(i.rotulo + " " + i.exibicao for i in bloco.resumo)) == []
    assert _citados(str(bloco.model_dump()["tabelas"][1])) == []


def test_content_so_tem_contagens() -> None:
    texto, _ = _executar()

    assert "4 profissionais, 5 pacientes distintos na grade." in texto
    assert "Bia (Fonoaudiologia): 3 pacientes, 6 atendimentos na semana." in texto
    assert "não escreva nomes de pacientes" in texto


def test_nenhum_nome_vai_para_o_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        _executar()
        _executar(profissionais=["Bia"], dias_semana=["quarta"])

    registros = "\n".join(r.getMessage() for r in caplog.records)
    assert f"{TOOL}: 5 paciente(s) distinto(s) em 4 profissional(is), 8 linha(s)" in registros
    assert _citados(registros) == []


def test_erro_na_leitura_so_pelo_tipo(caplog: pytest.LogCaptureFixture) -> None:
    class Quebrada(GoogleSheetsDataSource):
        def listar_atendimentos(self, dia: date) -> list[Any]:
            raise RuntimeError("Paciente Ficticio Ametista na célula B6")

    planilha = PlanilhaContada(lambda _params: payload_da_semana(CENARIO))
    tools = {
        t.name: t
        for t in criar_tools(
            Quebrada(cast(gspread.Spreadsheet, planilha)),
            SemHistoricoContinuidadeDataSource(),
            HOJE,
        )
    }
    with caplog.at_level(logging.DEBUG):
        mensagem = tools[TOOL].invoke({"name": TOOL, "args": {}, "id": "t", "type": "tool_call"})

    assert isinstance(mensagem, ToolMessage) and mensagem.artifact is None
    assert "RuntimeError" in str(mensagem.content)
    registros = "\n".join(r.getMessage() for r in caplog.records)
    assert _citados(str(mensagem.content) + registros) == []


def test_registrada_entre_as_tools_com_filtros() -> None:
    tools = {t.name: t for t in criar_tools(_fonte(), SemHistoricoContinuidadeDataSource(), HOJE)}
    mensagem = tools[TOOL].invoke(
        {
            "name": TOOL,
            "args": {"profissionais": ["Bia"], "especialidade": "fono", "dias_semana": ["quarta"]},
            "id": "t",
            "type": "tool_call",
        }
    )

    assert isinstance(mensagem, ToolMessage) and mensagem.artifact is not None
    assert len(mensagem.artifact["dados"]["linhas"]) == 2
    assert _citados(str(mensagem.content)) == []


def test_dia_fora_do_catalogo_e_recusado_sem_repetir_o_valor() -> None:
    tools = {t.name: t for t in criar_tools(_fonte(), SemHistoricoContinuidadeDataSource(), HOJE)}
    mensagem = tools[TOOL].invoke(
        {"name": TOOL, "args": {"dias_semana": ["domingo"]}, "id": "t", "type": "tool_call"}
    )

    assert "Parâmetros inválidos (dias_semana" in str(mensagem.content)
    assert "domingo" not in str(mensagem.content)


# --- agente: o que o modelo recebe e o texto que sai ---------------------------


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


def _perguntar(
    *respostas: str, tool: str = TOOL
) -> tuple[str, _ModeloComCaptura, list[BlocoRelatorio]]:
    chamada = AIMessage("", tool_calls=[{"name": tool, "args": {}, "id": "p1"}])
    modelo = _ModeloComCaptura(responses=[chamada, *(AIMessage(r) for r in respostas)])
    agente = criar_agente(
        _fonte(), SemHistoricoContinuidadeDataSource(), modelo, HOJE, renderiza_relatorios=True
    )
    texto, _, blocos = perguntar_com_mensagens(
        agente, [HumanMessage("quais pacientes cada profissional atende?")]
    )
    return texto, modelo, blocos


def test_modelo_nunca_recebe_os_nomes() -> None:
    _, modelo, blocos = _perguntar("A Bia atende 3 pacientes e a Assizely, 3.")

    assert blocos and blocos[0].model_dump()["dados"]["relatorio"] == (
        RELATORIO_PACIENTES_POR_PROFISSIONAL_NOMES
    )
    enviado = "\n".join(str(m.content) for entrada in modelo.entradas for m in entrada)
    assert _citados(enviado) == []


def test_resposta_sem_nomes_passa_como_veio() -> None:
    texto, _, _ = _perguntar("A Bia atende 3 pacientes e a Assizely, 3.")

    assert texto == "A Bia atende 3 pacientes e a Assizely, 3."


def test_resposta_com_nome_vira_resumo_deterministico_sem_nomes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        texto, modelo, _ = _perguntar("A Bia atende paciente ficticio omega.")

    assert _citados(texto) == []
    assert texto.startswith("Pacientes por profissional (grade semanal vigente)")
    assert "O detalhe está no relatório." in texto
    # Sem nova geração: o aviso teria de citar o nome para o modelo.
    assert len(modelo.entradas) == 2
    registros = "\n".join(r.getMessage() for r in caplog.records)
    assert "repetiu 1 nome(s) de paciente" in registros
    assert _citados(registros) == []


def test_protecao_compara_nome_inteiro_sem_acento_nem_caixa() -> None:
    _, bloco = _executar()

    assert nomes_de_paciente_na_resposta("PACIENTE FICTICIO OMEGA tem sessão", [bloco]) == 1
    assert nomes_de_paciente_na_resposta("Paciente Ficticio Ametistas", [bloco]) == 0
    assert nomes_de_paciente_na_resposta("Bia: 3 pacientes.", [bloco]) == 0


def test_com_a_lista_na_rodada_pode_dizer_que_a_tela_traz_os_nomes() -> None:
    texto, modelo, _ = _perguntar("A tela traz os nomes; a Bia atende 3 pacientes.")

    assert texto == "A tela traz os nomes; a Bia atende 3 pacientes."
    assert len(modelo.entradas) == 2


def test_relatorio_de_contagem_nao_pode_dizer_que_traz_os_nomes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        texto, modelo, _ = _perguntar(
            "A Bia atende 3 pacientes; a tela já traz os nomes dos pacientes.",
            "A Bia atende 3 pacientes; a tela traz as quantidades.",
            tool="consultar_pacientes_por_profissional",
        )

    assert texto == "A Bia atende 3 pacientes; a tela traz as quantidades."
    aviso = str(modelo.entradas[-1][-1].content)
    assert "o relatório desta rodada traz as quantidades" in aviso
    assert "nova geração" in "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("resposta", "afirma"),
    [
        ("A tela já traz os nomes dos pacientes.", True),
        ("O relatório mostra a lista com os nomes.", True),
        ("A tela traz as quantidades, não traz os nomes.", False),
        ("A tela mostra os nomes das profissionais.", False),
        ("A Bia atende 3 pacientes.", False),
    ],
)
def test_afirma_nomes_na_tela(resposta: str, afirma: bool) -> None:
    assert afirma_nomes_na_tela(resposta) is afirma


def test_prompt_com_tela_diz_que_contagem_traz_as_quantidades() -> None:
    prompt = " ".join(PROMPT_SISTEMA_COM_RELATORIOS.split())

    assert "Nos relatórios de contagem, a tela traz as quantidades (não traz os nomes" in prompt
    assert "Pedido de nomes de pacientes por profissional chama" in prompt
