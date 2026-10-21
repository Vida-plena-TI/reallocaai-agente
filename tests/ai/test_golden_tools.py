"""As tools antigas devolvem exatamente o que devolviam antes de irem para o motor novo.

O golden (`tests/ai/golden/`) foi capturado com o código antigo; ver
`tests.support.golden_tools`. Texto e bloco são comparados por inteiro.
"""

import json
from typing import Any

import pytest

from app.ai import tools as modulo_tools
from tests.support.agenda_clinica import NOMES_PACIENTES, fonte_clinica
from tests.support.golden_tools import (
    ARQUIVO_CONTRATO,
    ARQUIVO_SAIDAS,
    CASOS,
    capturar_contrato,
    chave_caso,
    criar_tools_golden,
    executar_caso,
)

SAIDAS: dict[str, dict[str, Any]] = json.loads(ARQUIVO_SAIDAS.read_text(encoding="utf-8"))
CONTRATO: dict[str, dict[str, Any]] = json.loads(ARQUIVO_CONTRATO.read_text(encoding="utf-8"))


def test_golden_cobre_todos_os_casos() -> None:
    assert list(SAIDAS) == [chave_caso(indice) for indice in range(len(CASOS))]


@pytest.mark.parametrize("indice", range(len(CASOS)), ids=chave_caso)
def test_saida_igual_ao_golden(indice: int) -> None:
    assert executar_caso(*CASOS[indice]) == SAIDAS[chave_caso(indice)]


def test_contrato_das_tools_igual_ao_golden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nome, descrição e schema dos argumentos: o que o modelo vê de cada tool."""
    monkeypatch.setattr(modulo_tools, "envio_de_email_configurado", lambda: True)

    contrato = capturar_contrato(list(criar_tools_golden(fonte_clinica()).values()))

    assert json.loads(json.dumps(contrato)) == CONTRATO


def test_golden_nao_tem_nome_de_paciente() -> None:
    conteudo = ARQUIVO_SAIDAS.read_text(encoding="utf-8").lower()

    assert not [nome for nome in NOMES_PACIENTES if nome.lower() in conteudo]
