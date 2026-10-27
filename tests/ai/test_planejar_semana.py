"""Tool `planejar_semana` sobre a semana fictícia de `tests/casos_reais/planilha.py`."""

import json
import re
from datetime import date
from typing import Any, cast

import gspread
from langchain_core.messages import ToolMessage

from app.ai.blocos_gerais import (
    AVISO_PLANO_GRADE,
    RELATORIO_PLANO_SEMANAL,
    SITUACAO_ALTERNATIVA,
    SITUACAO_NAO_COUBE,
    SITUACAO_PLANO,
)
from app.ai.relatorios import BlocoRelatorio
from app.ai.tools import criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from tests.casos_reais.planilha import PlanilhaContada

HOJE = date(2026, 9, 28)
QUATRO = ["fonoaudiologia", "psicomotricidade", "psicologia", "terapia_ocupacional"]


def _planejar(
    planilha: PlanilhaContada | None = None, **args: Any
) -> tuple[str, BlocoRelatorio | None]:
    fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha or PlanilhaContada()))
    tools = {t.name: t for t in criar_tools(fonte, SemHistoricoContinuidadeDataSource(), HOJE)}
    mensagem = tools["planejar_semana"].invoke(
        {"name": "planejar_semana", "args": args, "id": "c1", "type": "tool_call"}
    )
    assert isinstance(mensagem, ToolMessage)
    bloco = BlocoRelatorio.model_validate(mensagem.artifact) if mensagem.artifact else None
    return str(mensagem.content), bloco


def _linhas(bloco: BlocoRelatorio) -> list[dict[str, Any]]:
    dados: list[dict[str, Any]] = bloco.model_dump(mode="json")["dados"]["linhas"]
    return dados


def test_caso_de_referencia_vira_tabela_generica_com_situacao() -> None:
    texto, bloco = _planejar(
        dias_semana=["quarta", "quinta"],
        especialidades=QUATRO,
        hora_inicio="10:00",
        hora_fim="12:00",
    )

    assert bloco is not None
    dados = bloco.model_dump(mode="json")["dados"]
    assert bloco.tipo == "tabela_generica"
    assert dados["relatorio"] == RELATORIO_PLANO_SEMANAL
    assert bloco.titulo == (
        "Plano semanal: quarta e quinta, das 10:00 às 12:00 (sessões de 30 min)"
    )
    assert [c["chave"] for c in dados["colunas"]] == [
        "dia",
        "situacao",
        "especialidade",
        "horario",
        "profissional",
        "sala",
        "posto",
    ]
    assert dados["dimensoes"] == [] and dados["metricas"] == [] and dados["total"] == {}

    linhas = _linhas(bloco)
    plano = [linha for linha in linhas if linha["situacao"] == SITUACAO_PLANO]
    assert plano and all(linha["horario"][:5] >= "10:00" for linha in plano)
    assert all(linha["horario"][-5:] <= "12:00" for linha in plano)
    nao_coube = [linha for linha in linhas if linha["situacao"] == SITUACAO_NAO_COUBE]
    assert [(n["dia"], n["especialidade"]) for n in nao_coube] == [
        ("quarta-feira", "Terapia Ocupacional"),
        ("quinta-feira", "Psicologia"),
        ("quinta-feira", "Terapia Ocupacional"),
    ]
    assert all(n["horario"] == "—" and n["posto"] is None for n in nao_coube)
    # Agrupado por dia: as linhas da quarta vêm todas antes das da quinta.
    indice = linhas.index(nao_coube[0])
    assert linhas[indice + 1]["situacao"] == SITUACAO_ALTERNATIVA
    assert linhas[indice + 1]["especialidade"] == "Terapia Ocupacional"
    assert sum(1 for linha in linhas if linha["situacao"] == SITUACAO_ALTERNATIVA) <= 3 * 3

    assert AVISO_PLANO_GRADE in bloco.avisos
    assert "não coube Terapia Ocupacional" in texto
    assert "bloco contínuo" not in texto
    assert "verificad" not in texto


def test_plano_semanal_tem_periodo_nulo_e_nenhuma_data() -> None:
    """A fonte é a grade semanal, sem datas: o bloco não inventa período."""
    fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))
    tools = {t.name: t for t in criar_tools(fonte, SemHistoricoContinuidadeDataSource(), HOJE)}
    mensagem = tools["planejar_semana"].invoke(
        {
            "name": "planejar_semana",
            "args": {"dias_semana": ["quarta", "quinta"], "especialidades": QUATRO},
            "id": "c1",
            "type": "tool_call",
        }
    )
    artefato = mensagem.artifact

    assert "periodo" in artefato and artefato["periodo"] is None
    assert BlocoRelatorio.model_validate(artefato).periodo is None
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}", json.dumps(artefato))
    assert {linha["dia"] for linha in artefato["dados"]["linhas"]} <= {
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
    }


def test_uma_unica_leitura_da_planilha() -> None:
    planilha = PlanilhaContada()
    _planejar(planilha, dias_semana=["quarta", "quinta"], especialidades=QUATRO)

    assert planilha.chamadas == 1


def test_duracao_padrao_e_janela_padrao() -> None:
    _, bloco = _planejar(dias_semana=["quarta"], especialidades=["fono"])

    assert bloco is not None
    assert bloco.titulo == "Plano semanal: quarta, das 07:00 às 18:00 (sessões de 30 min)"
    (linha,) = _linhas(bloco)
    assert linha["situacao"] == SITUACAO_PLANO and linha["horario"] == "07:00 às 07:30"


def test_evitar_sala() -> None:
    _, bloco = _planejar(
        dias_semana=["quarta"], especialidades=["fono"], evitar_salas=["1", "Sala 5"]
    )

    assert bloco is not None
    (linha,) = _linhas(bloco)
    assert linha["situacao"] == SITUACAO_NAO_COUBE
    assert "Salas evitadas: Sala 1, Sala 5." in bloco.avisos


def test_sala_inexistente_e_janela_invertida_viram_mensagem() -> None:
    sala, bloco_sala = _planejar(
        dias_semana=["quarta"], especialidades=["fono"], evitar_salas=["99"]
    )
    janela, bloco_janela = _planejar(
        dias_semana=["quarta"], especialidades=["fono"], hora_inicio="12:00", hora_fim="10:00"
    )

    assert bloco_sala is None and "Sala '99' não está na agenda" in sala
    assert bloco_janela is None and janela == "O fim da janela precisa ser depois do início."


def test_profissional_preferido_pelo_apelido() -> None:
    """Na quarta, as duas fonos têm 07:00 livre; a preferida (Elo) ganha."""
    _, sem = _planejar(dias_semana=["quarta"], especialidades=["fono"], hora_fim="09:00")
    texto, com = _planejar(
        dias_semana=["quarta"],
        especialidades=["fono"],
        hora_fim="09:00",
        profissionais_preferidos=["elo"],
    )

    assert sem is not None and com is not None
    assert _linhas(com)[0]["profissional"] == "Elo"
    assert "com Elo" in texto


def test_profissional_preferido_de_outra_especialidade() -> None:
    texto, bloco = _planejar(
        dias_semana=["quarta"], especialidades=["fono"], profissionais_preferidos=["Carol"]
    )

    assert bloco is None
    assert texto == "Carol é de Psicologia, que não está entre as especialidades pedidas."


def test_dia_fora_do_catalogo_e_recusado_pelo_schema() -> None:
    texto, bloco = _planejar(dias_semana=["domingo"], especialidades=["fono"])

    assert bloco is None
    assert "Parâmetros inválidos (dias_semana.0)" in texto
