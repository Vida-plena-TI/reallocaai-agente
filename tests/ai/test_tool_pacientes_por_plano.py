"""Tool `pacientes_por_plano` sobre a semana fictícia colorida (`planos_por_cor`).

Nomes fictícios. O foco é a privacidade: os nomes só saem no artifact (bloco
para a tela), nunca no `content` que o modelo lê, no texto da resposta nem em
log. As contagens são conferidas contra o oráculo independente da régua.
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
from app.ai.blocos_gerais import AVISOS_PACIENTES_POR_PLANO, RELATORIO_PACIENTES_POR_PLANO
from app.ai.fundamentacao import nomes_de_paciente_na_resposta
from app.ai.relatorios import BlocoRelatorio
from app.ai.tools import criar_tools
from app.ai.tools_pacientes_plano import executar_pacientes_por_plano
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from tests.casos_reais import oraculo
from tests.casos_reais.planilha import PECAS_DOS_CENARIOS, PlanilhaContada, payload_da_semana
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

HOJE = date(2026, 9, 28)
SEMANA = [HOJE + timedelta(days=i) for i in range(6)]

#: Todo texto de paciente do cenário (inclusive os pretos e o não reconhecido).
NOMES_DO_CENARIO = sorted(
    {
        nome.strip()
        for pedacos in PECAS_DOS_CENARIOS["planos_por_cor"].values()
        for pedaco, _ in pedacos
        for nome in pedaco.split("/")
        if "Paciente" in nome
    }
)


def _planilha(cenario: str | None = "planos_por_cor") -> PlanilhaContada:
    return PlanilhaContada(lambda _params: payload_da_semana(cenario))


def _fonte(cenario: str | None = "planos_por_cor") -> GoogleSheetsDataSource:
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, _planilha(cenario)))


def _executar(cenario: str | None = "planos_por_cor") -> tuple[str, BlocoRelatorio]:
    texto, bloco = executar_pacientes_por_plano(_fonte(cenario), HOJE)
    assert bloco is not None
    return texto, BlocoRelatorio.model_validate(bloco)


def _linhas(bloco: BlocoRelatorio) -> list[dict[str, Any]]:
    linhas: list[dict[str, Any]] = bloco.model_dump(mode="json")["dados"]["linhas"]
    return linhas


def _cita(texto: str, nome: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(nome.casefold())}(?!\w)", texto.casefold()) is not None


def test_contagens_iguais_ao_oraculo() -> None:
    _, bloco = _executar()
    esperado = oraculo.pacientes_por_plano(payload_da_semana("planos_por_cor"), SEMANA)

    do_motor = {
        (linha["plano"], oraculo.nome_normalizado(linha["paciente"])): linha["atendimentos_semana"]
        for linha in _linhas(bloco)
    }
    do_oraculo = {
        (plano, nome): atendimentos
        for plano, por_nome in esperado.atendimentos.items()
        for nome, atendimentos in por_nome.items()
    }
    assert do_motor == do_oraculo
    assert len(do_motor) == len(_linhas(bloco))


def test_preto_nao_aparece_e_observacao_sai_do_nome() -> None:
    _, bloco = _executar()
    pacientes = {linha["paciente"] for linha in _linhas(bloco)}

    assert "Paciente Ficticio Preto" not in pacientes
    assert "Paciente Ficticio Magenta" not in pacientes
    assert "Paciente Ficticio Obs" in pacientes
    assert not any("falta" in nome for nome in pacientes)
    # Lilás em preto noutro horário não soma atendimento.
    assert [
        linha["atendimentos_semana"]
        for linha in _linhas(bloco)
        if linha["paciente"] == "Paciente Ficticio Lilas"
    ] == [1]


def test_bloco_tabela_generica_sem_periodo_e_totais_so_no_resumo() -> None:
    _, bloco = _executar()
    dump = bloco.model_dump(mode="json")

    assert bloco.tipo == "tabela_generica"
    assert dump["periodo"] is None and dump["meta"] is None
    assert dump["dados"]["relatorio"] == RELATORIO_PACIENTES_POR_PLANO
    assert [c["chave"] for c in dump["dados"]["colunas"]] == [
        "plano",
        "paciente",
        "atendimentos_semana",
    ]
    assert dump["dados"]["total"] == {} and dump["dados"]["linhas_omitidas"] == 0
    assert not any(str(linha["paciente"]).casefold() == "total" for linha in _linhas(bloco))
    planos = [linha["plano"] for linha in _linhas(bloco)]
    ordem = ["SulAmérica", "Klini", "Particulares", "Care Plus", "Unimed"]
    assert planos == sorted(planos, key=ordem.index)

    pacientes, resumo = dump["tabelas"]
    assert pacientes["nome"] == "Pacientes" and pacientes["linhas"] == _linhas(bloco)
    assert resumo["nome"] == "Resumo por plano"
    assert [linha["plano"] for linha in resumo["linhas"]] == ordem
    assert sum(linha["atendimentos_semana"] for linha in resumo["linhas"]) == sum(
        linha["atendimentos_semana"] for linha in _linhas(bloco)
    )
    assert [linha["pacientes"] for linha in resumo["linhas"]] == [2, 2, 1, 1, 2]


def test_avisos_fixos_e_numericos_quando_devidos() -> None:
    texto, bloco = _executar()

    assert bloco.avisos[: len(AVISOS_PACIENTES_POR_PLANO)] == list(AVISOS_PACIENTES_POR_PLANO)
    assert "1 nome com cor não reconhecida foi ignorado." in bloco.avisos
    assert any(
        a.startswith("1 paciente aparece com cores de mais de um plano") for a in bloco.avisos
    )
    assert "atendimentos contados apenas com cor de plano" in texto.casefold()


def test_sem_cor_estranha_nem_duas_cores_nao_ha_aviso_numerico() -> None:
    # A semana padrão da régua: todo paciente em azul.
    _, bloco = _executar(cenario=None)

    assert bloco.avisos == list(AVISOS_PACIENTES_POR_PLANO)
    assert [linha["plano"] for linha in _linhas(bloco)] == ["SulAmérica"] * len(_linhas(bloco))


def test_content_so_tem_contagens_e_nenhum_nome() -> None:
    texto, bloco = _executar()

    assert "SulAmérica: 2 pacientes, 4 atendimentos na semana." in texto
    assert "Unimed: 2 pacientes, 3 atendimentos na semana." in texto
    assert "7 pacientes com plano" in texto
    citados = [
        n
        for n in {*NOMES_DO_CENARIO, *(linha["paciente"] for linha in _linhas(bloco))}
        if _cita(texto, n)
    ]
    assert citados == []
    assert "Ficticio" not in bloco.titulo and "Ficticio" not in texto
    assert all("Ficticio" not in item.rotulo + item.exibicao for item in bloco.resumo)


def test_nenhum_nome_vai_para_o_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        _executar()

    registros = "\n".join(r.getMessage() for r in caplog.records)
    assert "pacientes_por_plano: 7 paciente(s)" in registros
    assert [n for n in NOMES_DO_CENARIO if _cita(registros, n)] == []


def test_registrada_entre_as_tools_e_sem_argumentos() -> None:
    tools = {t.name: t for t in criar_tools(_fonte(), SemHistoricoContinuidadeDataSource(), HOJE)}
    mensagem = tools["pacientes_por_plano"].invoke(
        {"name": "pacientes_por_plano", "args": {}, "id": "p1", "type": "tool_call"}
    )

    assert isinstance(mensagem, ToolMessage)
    assert mensagem.artifact is not None
    assert [n for n in NOMES_DO_CENARIO if _cita(str(mensagem.content), n)] == []


def test_fonte_sem_cor_responde_sem_bloco() -> None:
    texto, bloco = executar_pacientes_por_plano(FakeScheduleDataSource(), HOJE)

    assert bloco is None
    assert "cor do texto" in texto


def test_dia_ilegivel_deixa_o_relatorio_parcial(monkeypatch: pytest.MonkeyPatch) -> None:
    fonte = _fonte()
    original = fonte.listar_nomes_coloridos

    def com_falha(dia: date) -> list[Any]:
        if dia == SEMANA[1]:
            raise RuntimeError("aba ilegível")
        return original(dia)

    monkeypatch.setattr(fonte, "listar_nomes_coloridos", com_falha)
    texto, bloco = executar_pacientes_por_plano(fonte, HOJE)

    assert bloco is not None
    assert bloco["parcial"] is True and bloco["dias_nao_lidos"] == [SEMANA[1].isoformat()]
    assert "os números são parciais" in texto
    assert bloco["periodo"] is None


# --- agente: o que o modelo recebe e o texto que sai -----------------------


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


CHAMADA = AIMessage("", tool_calls=[{"name": "pacientes_por_plano", "args": {}, "id": "p1"}])


def _perguntar(resposta: str) -> tuple[str, _ModeloComCaptura, list[BlocoRelatorio]]:
    modelo = _ModeloComCaptura(responses=[CHAMADA, AIMessage(resposta), AIMessage(resposta)])
    agente = criar_agente(
        _fonte(), SemHistoricoContinuidadeDataSource(), modelo, HOJE, renderiza_relatorios=True
    )
    texto, _, blocos = perguntar_com_mensagens(
        agente, [HumanMessage("quantos pacientes de cada plano?")]
    )
    return texto, modelo, blocos


def test_modelo_nunca_recebe_os_nomes() -> None:
    _, modelo, blocos = _perguntar("A SulAmérica tem 2 pacientes e a Unimed, 2.")

    assert blocos and blocos[0].model_dump()["dados"]["relatorio"] == RELATORIO_PACIENTES_POR_PLANO
    enviado = "\n".join(str(m.content) for entrada in modelo.entradas for m in entrada)
    assert [n for n in NOMES_DO_CENARIO if _cita(enviado, n)] == []


def test_resposta_sem_nomes_passa_como_veio() -> None:
    texto, _, _ = _perguntar("A SulAmérica tem 2 pacientes e a Unimed, 2.")

    assert texto == "A SulAmérica tem 2 pacientes e a Unimed, 2."


def test_resposta_com_nome_vira_resumo_deterministico_sem_nomes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        texto, modelo, _ = _perguntar("A SulAmérica tem 2 pacientes, como Paciente Ficticio Azul.")

    assert [n for n in NOMES_DO_CENARIO if _cita(texto, n)] == []
    assert texto.startswith("Pacientes por plano de saúde")
    assert "O detalhe está no relatório." in texto
    # Sem nova geração: o aviso teria de citar o nome para o modelo.
    assert len(modelo.entradas) == 2
    registros = "\n".join(r.getMessage() for r in caplog.records)
    assert "repetiu 1 nome(s) de paciente" in registros
    assert [n for n in NOMES_DO_CENARIO if _cita(registros, n)] == []


def test_protecao_compara_nome_inteiro_sem_acento_nem_caixa() -> None:
    _, bloco = _executar()

    assert nomes_de_paciente_na_resposta("paciente fictício AZUL tem plano", [bloco]) == 1
    assert nomes_de_paciente_na_resposta("Paciente Ficticio Azulado", [bloco]) == 0
    assert nomes_de_paciente_na_resposta("SulAmérica: 2 pacientes.", [bloco]) == 0
