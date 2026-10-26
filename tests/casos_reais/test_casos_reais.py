"""Os casos reais no modo determinístico: sem modelo, sobre a semana fictícia.

Caso com `xfail` no YAML é xfail estrito: quando a tool que falta chegar e o
caso passar, o pytest acusa XPASS e obriga a tirar a marca. Caso só com
critérios de resposta da LLM (c08) é pulado aqui; ele roda no `--llm`.
"""

import pytest

from scripts.avaliar_casos_reais import avaliar_deterministico
from tests.casos_reais.casos import Caso, carregar_casos


def _parametros() -> list[object]:
    return [
        pytest.param(
            caso,
            id=caso.id,
            marks=[pytest.mark.xfail(strict=True, reason=caso.xfail)] if caso.xfail else [],
        )
        for caso in carregar_casos()
    ]


@pytest.mark.parametrize("caso", _parametros())
def test_caso_real(caso: Caso) -> None:
    (resultado,) = avaliar_deterministico([caso])
    (execucao,) = resultado.execucoes
    if execucao.status == "n/a":
        pytest.skip("só critérios da resposta da LLM: roda com --llm")
    falhas = [f"{c.tipo}: {c.detalhe}" for c in execucao.criterios if c.ok is False]
    assert not falhas, "\n".join(falhas)
