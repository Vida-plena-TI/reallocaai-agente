"""Testes das partes puras de `scripts/chat_manual.py` (sem modelo nem planilha)."""

import io

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scripts.chat_manual import (
    _argumentos,
    formatar_rastro_de_tools,
    reconfigurar_para_utf8,
)


def test_reconfigurar_para_utf8_troca_cp1252_por_utf8() -> None:
    entrada = io.TextIOWrapper(io.BytesIO("João".encode()), encoding="cp1252")
    saida = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")

    reconfigurar_para_utf8(entrada, saida)

    assert entrada.encoding == "utf-8"
    assert saida.encoding == "utf-8"
    assert saida.errors == "replace"
    assert entrada.read() == "João"


def test_rastro_de_tools_mostra_nome_argumentos_e_resultado_truncado() -> None:
    mensagens = [
        HumanMessage("Encaixa fono na sexta"),
        AIMessage(
            "",
            tool_calls=[
                {"name": "buscar_encaixe", "args": {"paciente": "Théo"}, "id": "c1"},
            ],
        ),
        ToolMessage("x" * 500, tool_call_id="c1"),
        AIMessage("Pronto."),
    ]

    rastro = formatar_rastro_de_tools(mensagens)

    assert 'buscar_encaixe({"paciente": "Théo"})' in rastro
    assert "x" * 300 in rastro
    assert "x" * 301 not in rastro


def test_rastro_de_tools_sem_tools() -> None:
    assert formatar_rastro_de_tools([AIMessage("Oi")]) == "  (nenhuma tool chamada)"


def test_flag_renderiza_e_opcional_e_independente_de_verbose() -> None:
    padrao = _argumentos([])
    assert not padrao.renderiza and not padrao.verbose
    assert _argumentos(["--renderiza"]).renderiza
    combinado = _argumentos(["2026-10-05", "--renderiza", "--verbose"])
    assert combinado.renderiza and combinado.verbose
    assert str(combinado.data) == "2026-10-05"
