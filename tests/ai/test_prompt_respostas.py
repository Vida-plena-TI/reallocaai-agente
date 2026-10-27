"""Regras de resposta do prompt: rota do dia único, só sugerir, vocabulário e erros."""

import re

import pytest

from app.ai.prompts import PROMPT_SISTEMA, PROMPT_SISTEMA_COM_RELATORIOS

VARIANTES = [
    pytest.param(PROMPT_SISTEMA, id="texto"),
    pytest.param(PROMPT_SISTEMA_COM_RELATORIOS, id="tela"),
]


def _sem_a_lista_de_proibidos(prompt: str) -> str:
    """O prompt cita os termos proibidos uma vez, na regra que os proíbe."""
    inicio = prompt.index("- Nunca escreva para quem perguntou:")
    fim = prompt.index("## Estilo de resposta")
    return prompt[:inicio] + prompt[fim:]


@pytest.mark.parametrize("prompt", VARIANTES)
def test_dia_unico_vai_para_a_tool_com_relatorio(prompt: str) -> None:
    assert "Vagas de UM dia" in prompt and "`consultar_disponibilidade`" in prompt
    assert "Lista detalhada dos horários livres" not in prompt


@pytest.mark.parametrize("prompt", VARIANTES)
def test_frases_que_vazavam_sairam(prompt: str) -> None:
    resto = _sem_a_lista_de_proibidos(prompt)
    assert "exatamente como" not in resto
    assert "reproduza CADA" not in resto
    for termo in ("slot", "tool", "bloco", "atendimento_id"):
        assert not re.search(rf"(?i)\b{termo}s?\b", resto), termo


@pytest.mark.parametrize("prompt", VARIANTES)
def test_so_sugere_e_proibe_o_vocabulario(prompt: str) -> None:
    assert "Você só SUGERE" in prompt
    assert '"encaixar", "registrar" ou "agendar"' in prompt
    assert '"Parâmetros inválidos"' in prompt


@pytest.mark.parametrize("prompt", VARIANTES)
def test_erro_de_parametro_e_corrigido_e_nao_repassado(prompt: str) -> None:
    assert "repasse o problema" not in prompt
    assert "corrija-o conforme a mensagem e" in prompt
    assert "Nunca repita para quem perguntou a mensagem técnica" in prompt


def test_com_tela_lista_de_varias_linhas_vai_no_relatorio() -> None:
    assert "o texto NUNCA repete a\nlista" in PROMPT_SISTEMA_COM_RELATORIOS
    assert "consultar_disponibilidade (um dia)" in PROMPT_SISTEMA_COM_RELATORIOS


@pytest.mark.parametrize("prompt", VARIANTES)
def test_perguntas_agrupadas_e_padroes_declarados(prompt: str) -> None:
    assert "faça UMA pergunta curta que liste, de" in prompt
    assert "nunca pergunte em sequência" in prompt
    assert "Nunca pergunte nem peça confirmação do que já foi dito" in prompt
    assert "duração de 30 minutos; qualquer profissional" in prompt
    assert "não pergunte por quantas semanas" in prompt
    assert "feriados e exceções não aparecem na grade" in prompt


@pytest.mark.parametrize("prompt", VARIANTES)
def test_plano_semanal_sem_datas_e_seguidas_no_planejar_semana(prompt: str) -> None:
    assert "`planejar_semana` com `seguidas` verdadeiro, nunca `buscar_encaixe`" in prompt
    assert "O plano semanal não tem datas" in prompt
    assert "Exceção: no plano semanal (`planejar_semana`) não" in prompt
    assert "sem chamar a ferramenta de novo" in prompt
