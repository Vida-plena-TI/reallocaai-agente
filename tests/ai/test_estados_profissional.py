"""Os três estados da busca por profissional, e o resolvedor em todas as tools.

Semana fictícia de `tests/casos_reais/planilha.py`, lida pelo caminho de
produção. Quinta-feira, das 10h às 12h:
- Bia (Fono, Sala 1): livre às 10:00 e 10:30;
- Carol (Psicologia, Sala 3): lotada;
- Elo (Fono, Sala 5): não atende (a Sala 5 não tem coluna na quinta);
- Zuleide: não existe.
"""

import json
from datetime import date
from typing import Any, cast

import gspread
import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.ai.tools import criar_tools
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import Especialidade, Profissional
from tests.casos_reais.planilha import PlanilhaContada
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

QUARTA = date(2026, 9, 30)
QUINTA = date(2026, 10, 1)


def _tools(fonte: Any = None) -> dict[str, BaseTool]:
    if fonte is None:
        fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, PlanilhaContada()))
    return {t.name: t for t in criar_tools(fonte, SemHistoricoContinuidadeDataSource(), QUARTA)}


def _disponibilidade(profissional: str, **extra: str) -> str:
    args = {"data": QUINTA.isoformat(), "profissional": profissional, **extra}
    return str(_tools()["consultar_disponibilidade"].invoke(args))


JANELA = {"horario_inicio": "10:00", "horario_fim": "12:00"}


def test_inexistente_na_grade() -> None:
    texto = _disponibilidade("Zuleide", **JANELA)
    assert texto.startswith("'Zuleide' não existe na grade da semana de 28/09 a 03/10/2026.")


def test_nao_atende_no_dia() -> None:
    texto = _disponibilidade("elo", **JANELA)
    assert texto == (
        "Elo não atende na quinta-feira (01/10). Nesta semana, atende: segunda-feira, "
        "terça-feira, quarta-feira, sexta-feira."
    )


def test_atende_sem_vaga_na_janela() -> None:
    assert _disponibilidade("CAROL", **JANELA) == (
        "Carol atende na quinta-feira (01/10), mas está sem vaga entre 10:00 e 12:00."
    )


def test_apelido_com_vaga_na_janela() -> None:
    assert _disponibilidade("beatriz", **JANELA) == (
        "Horários livres em 01/10/2026 (10:00 às 12:00):\n"
        "- 10:00 às 11:00: Fonoaudiologia com Bia na Sala 1."
    )


def test_janela_invertida() -> None:
    texto = _disponibilidade("Bia", horario_inicio="12:00", horario_fim="10:00")
    assert "depois do inicial" in texto


def test_ambiguo_pede_escolha() -> None:
    profissionais = [
        Profissional(id="ana-paula", nome="Ana Paula", especialidade=Especialidade.PSICOLOGIA),
        Profissional(
            id="ana-carolina", nome="Ana Carolina", especialidade=Especialidade.PSICOLOGIA
        ),
    ]
    tools = _tools(FakeScheduleDataSource(profissionais={QUINTA: profissionais}))
    texto = str(
        tools["consultar_disponibilidade"].invoke(
            {"data": QUINTA.isoformat(), "profissional": "ana"}
        )
    )
    assert texto.startswith(
        "Mais de uma profissional corresponde a 'ana': Ana Carolina (Psicologia), "
        "Ana Paula (Psicologia)."
    )
    assert "não escolha por conta própria" in texto


def test_encaixe_com_profissional_que_nao_atende_no_dia() -> None:
    texto = _tools()["buscar_encaixe"].invoke(
        {
            "data": QUINTA.isoformat(),
            "itens": [
                {"especialidade": "fonoaudiologia", "duracao_minutos": 30, "profissional_id": "Elo"}
            ],
        }
    )
    assert str(texto).startswith("Elo não atende na quinta-feira (01/10).")


# ---- toda tool que recebe nome de profissional usa o resolvedor ----


def _campos_de_profissional(tool: BaseTool) -> set[str]:
    """Campos (inclusive em listas de itens) cujo nome fala de profissional."""
    esquema = json.dumps(cast(type[BaseModel], tool.tool_call_schema).model_json_schema())
    propriedades: set[str] = set()

    def visitar(no: Any) -> None:
        if isinstance(no, dict):
            for chave in no.get("properties", {}):
                if "profissiona" in chave or chave == "nomes":
                    propriedades.add(chave)
            for valor in no.values():
                visitar(valor)
        elif isinstance(no, list):
            for valor in no:
                visitar(valor)

    visitar(json.loads(esquema))
    return propriedades


#: Para cada tool com campo de profissional: argumentos com o APELIDO "beatriz".
#: Uma tool nova com campo de profissional faz o teste falhar até entrar aqui.
CHAMADAS_COM_APELIDO: dict[str, dict[str, Any]] = {
    "buscar_encaixe": {
        "data": QUINTA.isoformat(),
        "itens": [
            {"especialidade": "fonoaudiologia", "duracao_minutos": 30, "profissional_id": "beatriz"}
        ],
        "horario_minimo": "10:00",
    },
    "consultar_disponibilidade": {"data": QUINTA.isoformat(), "profissional": "beatriz"},
    "consultar_ocupacao_profissional": {"profissional": "beatriz"},
    "consultar_pacientes_por_profissional": {"profissional": "beatriz"},
    "consultar_profissionais": {"nomes": ["beatriz"]},
    "gerar_relatorio": {"relatorio": "vagas_livres", "profissionais": ["beatriz"]},
    "planejar_semana": {
        "dias_semana": ["quinta"],
        "especialidades": ["fonoaudiologia"],
        "profissionais_preferidos": ["beatriz"],
    },
}


def test_toda_tool_com_profissional_esta_coberta() -> None:
    com_profissional = {nome for nome, tool in _tools().items() if _campos_de_profissional(tool)}
    assert com_profissional == set(CHAMADAS_COM_APELIDO)


@pytest.mark.parametrize("nome", sorted(CHAMADAS_COM_APELIDO))
def test_tool_resolve_o_apelido(nome: str) -> None:
    resposta = _tools()[nome].invoke(
        {"type": "tool_call", "name": nome, "id": "t", "args": CHAMADAS_COM_APELIDO[nome]}
    )
    texto = str(resposta.content)
    assert "Bia" in texto
    for falha in ("não existe", "não encontrad", "Nenhuma profissional", "Não há profissional"):
        assert falha not in texto
