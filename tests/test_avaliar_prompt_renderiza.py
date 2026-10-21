"""Testes das partes puras de `scripts/avaliar_prompt_renderiza.py` (sem modelo nem planilha)."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import scripts.avaliar_prompt_renderiza as avaliacao
from scripts.avaliar_prompt_renderiza import (
    PERGUNTAS,
    _argumentos,
    avaliar_turno,
    contar_frases,
    contar_nomes_de_pacientes,
    estimar_custo,
    numeros_ausentes_das_tools,
)
from tests.support.agenda_clinica import NOMES_PACIENTES, fonte_clinica


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


def test_repeticoes_padrao_uma() -> None:
    """Padrão 1 (era 3): a avaliação é paga, e mais repetições são pedidas explicitamente."""
    assert _argumentos([]).repeticoes == 1
    assert _argumentos(["--repeticoes", "5"]).repeticoes == 5
    assert _argumentos([]).confirmar is False


def test_relatorio_esperado_e_chamadas_do_modelo() -> None:
    mensagens = [
        AIMessage(
            "",
            tool_calls=[
                {"name": "gerar_relatorio", "args": {"relatorio": "vagas_livres"}, "id": "c1"}
            ],
        ),
        ToolMessage("Vagas: 12", tool_call_id="c1"),
        AIMessage("Há 12 vagas."),
    ]
    certa = avaliar_turno(
        "Há 12 vagas.",
        mensagens,
        ["disponibilidade"],
        frozenset({"gerar_relatorio"}),
        relatorio_esperado="vagas_livres",
        segundos=1.5,
    )
    errada = avaliar_turno(
        "Há 12 vagas.",
        mensagens,
        ["disponibilidade"],
        frozenset({"gerar_relatorio"}),
        relatorio_esperado="janelas_continuas",
    )

    assert certa.relatorios == ("vagas_livres",)
    assert certa.relatorio_ok and not errada.relatorio_ok
    assert certa.chamadas_modelo == 2
    assert certa.segundos == 1.5
    assert certa.tools_ok and certa.frases_ok and certa.nomes_ok


def test_resposta_vazia_nao_conta_como_curta() -> None:
    assert not avaliar_turno("", [], [], frozenset()).frases_ok


def test_conta_nomes_de_pacientes_sem_falso_positivo() -> None:
    pacientes = ["Paciente Ficticio Alfa", "Paciente Ficticio Bravo", "Ana"]

    assert contar_nomes_de_pacientes("O paciente ficticio ALFA tem 3 sessões.", pacientes) == 1
    assert (
        contar_nomes_de_pacientes(
            "Paciente Ficticio Alfa tem 3 sessões.",
            pacientes,
            permitidos=["Paciente Ficticio Alfa"],
        )
        == 0
    )
    # "Ana" também é nome de profissional: não conta.
    assert (
        contar_nomes_de_pacientes("A Ana Lima está com 80%.", pacientes, profissionais=["Ana Lima"])
        == 0
    )
    assert contar_nomes_de_pacientes("Bananas.", pacientes) == 0


def test_estimativa_de_custo() -> None:
    estimativa = estimar_custo(5_000, perguntas=10, repeticoes=1, preco_entrada=2.5, preco_saida=10)

    assert estimativa.conversas == 10
    assert estimativa.tokens_entrada == 10 * (2 * 5_000 + 1_500)
    assert estimativa.tokens_saida == 10 * 400
    assert round(estimativa.dolares, 4) == round((115_000 * 2.5 + 4_000 * 10) / 1_000_000, 4)


def test_perguntas_cobrem_os_relatorios_novos_sem_nome_real() -> None:
    relatorios = {p.relatorio for p in PERGUNTAS if p.relatorio}
    assert {
        "vagas_livres",
        "janelas_continuas",
        "ranking_ocupacao",
        "convenios",
        "qualidade_agenda",
        "agenda_paciente",
    } <= relatorios
    (agenda,) = [p for p in PERGUNTAS if p.relatorio == "agenda_paciente"]
    assert agenda.ficticia
    assert agenda.paciente_pedido in NOMES_PACIENTES
    assert agenda.paciente_pedido in agenda.texto


def test_sem_confirmar_so_mostra_a_estimativa(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def nao_chamar() -> None:
        raise AssertionError("sem --confirmar, o modelo não pode ser criado")

    monkeypatch.setattr(avaliacao, "criar_chat_model", nao_chamar)
    monkeypatch.setattr(avaliacao, "GoogleSheetsDataSource", fonte_clinica)

    avaliacao.main(["--repeticoes", "2"])

    saida = capsys.readouterr().out
    assert "Estimativa: 20 conversa(s)" in saida
    assert "~US$ " in saida
    assert "avaliar_prompt_renderiza.py --repeticoes 2 --confirmar" in saida
