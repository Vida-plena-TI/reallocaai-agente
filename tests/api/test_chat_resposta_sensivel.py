"""`resposta_sensivel` na resposta HTTP de `/agenda/chat` (nomes fictícios).

Os cenários de cada tool restrita estão em `tests/ai/test_resposta_sensivel.py`.
"""

from datetime import date
from typing import Any, cast

import gspread
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage

from app.ai.relatorios import BlocoRelatorio
from app.ai.tools import criar_tools
from app.api.dependencies import obter_chat_model, obter_fonte
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import ScheduleDataSource
from app.main import app
from tests.api.conftest import HEADERS_AUTENTICADOS
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana
from tests.support.agenda_clinica import fonte_clinica
from tests.support.fake_chat_model import FakeToolCallingChatModel

RESPOSTA = "Pronto, veja o resultado na tela."


def _planilha(cenario: str | None = None) -> ScheduleDataSource:
    planilha = PlanilhaContada(lambda _params: payload_da_semana(cenario))
    return GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha))


def _post(client: TestClient, fonte: Any, nome: str | None, args: dict[str, Any]) -> Any:
    respostas: list[BaseMessage] = [AIMessage(RESPOSTA)]
    if nome is not None:
        respostas.insert(0, AIMessage("", tool_calls=[{"name": nome, "args": args, "id": "h1"}]))
    modelo = FakeToolCallingChatModel(responses=respostas)
    app.dependency_overrides[obter_chat_model] = lambda: modelo
    app.dependency_overrides[obter_fonte] = lambda: fonte()
    resposta = client.post(
        "/agenda/chat",
        headers=HEADERS_AUTENTICADOS,
        json={"mensagem": "Pergunta fictícia.", "renderiza_relatorios": True},
    )
    assert resposta.status_code == 200
    return resposta.json()


def test_http_true_sem_bloco_e_ausente_nas_demais(client: TestClient) -> None:
    sem_cenario = _post(
        client, lambda: _planilha("enxugamento"), "planejar_enxugamento", {"dias_semana": ["sexta"]}
    )
    assert sem_cenario["resposta_sensivel"] is True
    assert sem_cenario["blocos"] == []

    disponibilidade = _post(
        client, fonte_clinica, "consultar_disponibilidade", {"data": date.today().isoformat()}
    )
    assert "resposta_sensivel" not in disponibilidade

    conversa = _post(client, fonte_clinica, None, {})
    assert "resposta_sensivel" not in conversa


def test_http_bloco_identico_ao_da_tool(client: TestClient) -> None:
    """A flag não muda o corpo dos blocos: os blocos do chat são o artifact da tool (o
    panorama manda dois: o quadro comparativo e os passos, ambos com nomes protegidos)."""
    tools = {
        t.name: t
        for t in criar_tools(
            _planilha("enxugamento"), SemHistoricoContinuidadeDataSource(), date.today()
        )
    }
    direto = tools["planejar_enxugamento"].invoke(
        {"name": "planejar_enxugamento", "args": {}, "id": "d", "type": "tool_call"}
    )
    corpo = _post(client, lambda: _planilha("enxugamento"), "planejar_enxugamento", {})

    assert corpo["resposta_sensivel"] is True
    assert len(corpo["blocos"]) == 2
    assert all("resposta_sensivel" not in bloco for bloco in corpo["blocos"])
    assert all(bloco["contem_dados_pacientes"] is True for bloco in corpo["blocos"])
    assert all(bloco["dados"]["relatorio"] == "panorama_enxugamento" for bloco in corpo["blocos"])
    assert corpo["blocos"] == [
        BlocoRelatorio.model_validate(artefato).model_dump(mode="json")
        for artefato in direto.artifact
    ]


def test_openapi_declara_o_campo_opcional_so_true(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    campo = schemas["ChatResponse"]["properties"]["resposta_sensivel"]

    assert campo["const"] is True and campo["type"] == "boolean"
    assert "resposta_sensivel" not in schemas["ChatResponse"].get("required", [])
    assert "resposta_sensivel" not in schemas["BlocoRelatorio"]["properties"]
