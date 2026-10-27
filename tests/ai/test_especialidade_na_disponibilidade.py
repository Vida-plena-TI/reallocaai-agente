"""O bloco disponibilidade nunca sai com especialidade vazia.

Quem está na grade mas fora de `listar_profissionais` (Cau Melo, na fonte
"borda" do golden: entradas 015 e 017) não tem cadastro: vale a especialidade
da própria grade e, se nem ela existir, "Não informada".
"""

from datetime import date
from typing import Any

import pytest

from app.ai.blocos_gerais import ESPECIALIDADE_NAO_INFORMADA, _profissional_da_faixa
from app.ai.tools_relatorios import vagas_de_um_dia
from app.engine.fotografia import fotografar_periodo
from tests.support.fake_schedule_data_source import FakeScheduleDataSource
from tests.support.golden_tools import CASOS, executar_caso, fonte_borda

SEXTA = date(2026, 10, 2)


def _especialidades(bloco: dict[str, Any]) -> list[object]:
    return [faixa["profissional"]["especialidade"] for faixa in bloco["dados"]["faixas"]]


def test_vaga_de_quem_nao_tem_cadastro_usa_a_especialidade_da_grade() -> None:
    _, bloco, _ = vagas_de_um_dia(fonte_borda(), SEXTA)

    assert bloco is not None
    especialidades = _especialidades(bloco)
    assert None not in especialidades
    cau = [
        faixa for faixa in bloco["dados"]["faixas"] if faixa["profissional"]["nome"] == "Cau Melo"
    ]
    assert cau and {f["profissional"]["especialidade"] for f in cau} == {"Terapia Ocupacional"}
    # As tabelas da tela também.
    colunas = [
        linha["especialidade"]
        for tabela in bloco["tabelas"]
        for linha in tabela["linhas"]
        if "especialidade" in linha
    ]
    assert colunas and "—" not in colunas and None not in colunas


@pytest.mark.parametrize("indice", [15, 17])
def test_entradas_015_e_017_do_golden(indice: int) -> None:
    saida = executar_caso(*CASOS[indice])
    assert "- 13:30 às 18:00: Terapia Ocupacional com Cau Melo na Sala 2." in saida["texto"]
    assert "— com" not in saida["texto"]


def test_sem_especialidade_no_cadastro_nem_na_grade() -> None:
    foto = fotografar_periodo(FakeScheduleDataSource(), SEXTA, SEXTA)
    profissional = _profissional_da_faixa(foto, "sem-cadastro", None)
    assert profissional.especialidade == ESPECIALIDADE_NAO_INFORMADA == "Não informada"
