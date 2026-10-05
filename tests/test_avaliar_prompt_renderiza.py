"""Testes das partes puras de `scripts/avaliar_prompt_renderiza.py` (sem modelo nem planilha)."""

from langchain_core.messages import AIMessage, ToolMessage

from scripts.avaliar_prompt_renderiza import (
    _argumentos,
    avaliar_turno,
    contar_frases,
    numeros_ausentes_das_tools,
)


def test_contar_frases_por_pontuacao_e_por_linha() -> None:
    assert contar_frases("A Ana está com 72,5%. Faltam 6 slots! Ok?") == 3
    assert contar_frases("Resumo:\n- Segunda: 80,0%\n- Terça: 75,0%") == 3
    assert contar_frases("Uma frase só, com 05/10/2026 e 77,5%.") == 1


def test_numeros_com_virgula_precisam_estar_no_texto_da_tool() -> None:
    tool = ["Total da semana: 72,5% (29 de 40 slots)"]
    assert numeros_ausentes_das_tools("Está em 72,5%.", tool) == []
    assert numeros_ausentes_das_tools("Está em 72,5%, ou 73,0%.", tool) == ["73,0"]


def test_avaliar_turno_marca_tools_extras_e_blocos() -> None:
    mensagens = [
        AIMessage(
            "",
            tool_calls=[
                {"name": "consultar_ocupacao", "args": {}, "id": "c1"},
                {"name": "consultar_pacientes_por_profissional", "args": {}, "id": "c2"},
            ],
        ),
        ToolMessage("Clínica: 64,2%", tool_call_id="c1"),
        ToolMessage("Pacientes", tool_call_id="c2"),
        AIMessage("A clínica está em 64,2%."),
    ]
    execucao = avaliar_turno(
        "A clínica está em 64,2%.",
        mensagens,
        ["ocupacao_agregada", "pacientes_por_profissional"],
        frozenset({"consultar_ocupacao"}),
    )
    assert execucao.extras == ["consultar_pacientes_por_profissional"]
    assert not execucao.tools_ok and execucao.frases_ok and execucao.numeros_ok


def test_repeticoes_padrao_tres() -> None:
    assert _argumentos([]).repeticoes == 3
    assert _argumentos(["--repeticoes", "5"]).repeticoes == 5
