"""`GET /relatorios/catalogo` e `POST /relatorios/gerar` sobre a semana de `agenda_clinica`.

O contrato é o das tools: o mesmo texto e o mesmo bloco que `gerar_relatorio`
e `consultar_catalogo` devolvem ao agente, para os mesmos parâmetros.
"""

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from app.ai.relatorios import BlocoRelatorio
from app.ai.tools_relatorios import RELATORIOS, criar_tools_relatorios
from app.api.dependencies import obter_data_de_hoje, obter_fonte
from app.main import app
from tests.api.conftest import HEADERS_AUTENTICADOS
from tests.support.agenda_clinica import NOMES_PACIENTES, fonte_clinica
from tests.support.agenda_semanal import DOMINGO, QUARTA, SEGUNDA, SEMANA, SEXTA
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

HOJE = QUARTA
PACIENTE = NOMES_PACIENTES[0]


@pytest.fixture
def fonte() -> FakeScheduleDataSource:
    return fonte_clinica()


@pytest.fixture
def cliente(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[obter_data_de_hoje] = lambda: HOJE
    yield client


def _gerar(cliente: TestClient, corpo: dict[str, Any]) -> Any:
    return cliente.post("/relatorios/gerar", json=corpo, headers=HEADERS_AUTENTICADOS)


def _da_tool(fonte: FakeScheduleDataSource, args: dict[str, Any]) -> dict[str, Any]:
    tool = next(t for t in criar_tools_relatorios(fonte, HOJE) if t.name == "gerar_relatorio")
    mensagem = tool.invoke(
        {"type": "tool_call", "id": "t", "name": "gerar_relatorio", "args": args}
    )
    assert isinstance(mensagem, ToolMessage)
    return {"texto": mensagem.content, "bloco": mensagem.artifact}


# --- autenticação -------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "caminho"), [("get", "/relatorios/catalogo"), ("post", "/relatorios/gerar")]
)
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "errada"}], ids=["sem-chave", "errada"])
def test_sem_chave_valida_devolve_401(
    cliente: TestClient, metodo: str, caminho: str, headers: dict[str, str]
) -> None:
    corpo = {"json": {"relatorio": "vagas_livres"}} if metodo == "post" else {}
    resposta = getattr(cliente, metodo)(caminho, headers=headers, **corpo)

    assert resposta.status_code == 401


# --- POST /relatorios/gerar ---------------------------------------------------

CASOS_IGUAIS_A_TOOL: list[dict[str, Any]] = [
    {"relatorio": "vagas_livres"},
    {"relatorio": "vagas_livres", "inicio": SEGUNDA.isoformat(), "fim": SEXTA.isoformat()},
    {"relatorio": "janelas_continuas", "duracao_minutos": 90},
    {"relatorio": "ranking_ocupacao", "limite": 2},
    {"relatorio": "comparativo_dias"},
    {"relatorio": "convenios"},
    {"relatorio": "qualidade_agenda"},
    {"relatorio": "salas_ociosas"},
    {"relatorio": "capacidade_especialidade", "especialidades": ["psico"]},
    {"relatorio": "agenda_paciente", "paciente": PACIENTE},
    {
        "relatorio": "personalizado",
        "dimensoes": ["dia", "especialidade"],
        "metricas": ["ocupacao", "vagas_livres"],
        "periodo_do_dia": "tarde",
    },
]


@pytest.mark.parametrize("corpo", CASOS_IGUAIS_A_TOOL, ids=lambda c: c["relatorio"])
def test_devolve_o_mesmo_texto_e_bloco_da_tool(
    cliente: TestClient, fonte: FakeScheduleDataSource, corpo: dict[str, Any]
) -> None:
    resposta = _gerar(cliente, corpo)

    assert resposta.status_code == 200
    esperado = _da_tool(fonte, corpo)
    assert esperado["bloco"] is not None
    assert resposta.json()["texto"] == esperado["texto"]
    assert resposta.json()["bloco"] == BlocoRelatorio.model_validate(esperado["bloco"]).model_dump(
        mode="json"
    )


def test_todos_os_relatorios_prontos_estao_cobertos() -> None:
    assert {c["relatorio"] for c in CASOS_IGUAIS_A_TOOL} | {"janelas_continuas"} == set(RELATORIOS)


def test_domingo_sem_agenda_devolve_bloco_nulo(cliente: TestClient) -> None:
    resposta = _gerar(cliente, {"relatorio": "vagas_livres", "inicio": DOMINGO.isoformat()})

    assert resposta.status_code == 200
    assert resposta.json()["bloco"] is None
    assert "domingo" in resposta.json()["texto"]


def test_dia_ilegivel_deixa_o_bloco_parcial_com_aviso(client: TestClient) -> None:
    app.dependency_overrides[obter_data_de_hoje] = lambda: HOJE
    parcial = fonte_clinica({QUARTA})
    app.dependency_overrides[obter_fonte] = lambda: parcial

    resposta = _gerar(client, {"relatorio": "ranking_ocupacao"})

    assert resposta.status_code == 200
    assert any(
        "Não foi possível ler: 30/09" in aviso for aviso in resposta.json()["bloco"]["avisos"]
    )


def test_falha_de_leitura_em_todos_os_dias_devolve_502_sem_detalhe(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    ilegivel = fonte_clinica(set(SEMANA))
    app.dependency_overrides[obter_fonte] = lambda: ilegivel
    app.dependency_overrides[obter_data_de_hoje] = lambda: HOJE

    with caplog.at_level(logging.WARNING):
        resposta = _gerar(client, {"relatorio": "convenios"})

    assert resposta.status_code == 502
    assert "falha simulada" not in resposta.text
    assert "falha simulada" not in caplog.text


@pytest.mark.parametrize(
    ("corpo", "trecho"),
    [
        ({"relatorio": "vagas_livres", "inicio": "2026-09-01", "fim": "2026-11-30"}, "62 dias"),
        ({"relatorio": "vagas_livres", "inicio": "2026-10-02", "fim": "2026-09-28"}, "anterior"),
        ({"relatorio": "vagas_livres", "profissionais": ["Zé Ninguém"]}, "Nenhuma profissional"),
        ({"relatorio": "vagas_livres", "salas": ["99"]}, "Sala '99'"),
        ({"relatorio": "personalizado"}, "exige 'metricas'"),
        ({"relatorio": "convenios", "paciente": PACIENTE}, "só vale com"),
        ({"relatorio": "agenda_paciente"}, "exige o nome"),
        (
            {"relatorio": "personalizado", "metricas": ["vagas_livres"], "convenios": ["unimed"]},
            "Convênio só existe em slot ocupado",
        ),
    ],
)
def test_parametro_sem_sentido_devolve_422_com_mensagem_util(
    cliente: TestClient, corpo: dict[str, Any], trecho: str
) -> None:
    resposta = _gerar(cliente, corpo)

    assert resposta.status_code == 422
    assert trecho in resposta.json()["detail"]


@pytest.mark.parametrize(
    "corpo",
    [
        {"relatorio": "ranking_ocupacao", "limite": 201},
        {"relatorio": "ranking_ocupacao", "limite": 0},
        {"relatorio": "nao_existe"},
        {"relatorio": "vagas_livres", "campo_que_nao_existe": 1},
        {"relatorio": "agenda_paciente", "paciente": "x" * 121},
    ],
    ids=["limite-alto", "limite-zero", "relatorio", "campo-extra", "paciente-longo"],
)
def test_schema_invalido_devolve_422(cliente: TestClient, corpo: dict[str, Any]) -> None:
    resposta = _gerar(cliente, corpo)

    assert resposta.status_code == 422
    assert resposta.json()["detail"][0]["loc"][0] == "body"


# --- agenda de paciente -------------------------------------------------------


def test_agenda_de_paciente_so_pelo_corpo(cliente: TestClient) -> None:
    """Nome na query string não é lido: sem ele no corpo, o pedido é recusado."""
    resposta = cliente.post(
        "/relatorios/gerar",
        params={"paciente": PACIENTE},
        json={"relatorio": "agenda_paciente"},
        headers=HEADERS_AUTENTICADOS,
    )

    assert resposta.status_code == 422
    assert PACIENTE not in resposta.text


def test_agenda_de_paciente_nao_tem_rota_get(cliente: TestClient) -> None:
    resposta = cliente.get(
        "/relatorios/gerar", params={"relatorio": "agenda_paciente"}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 405


def test_nome_exato_sem_sugestao_de_parecidos(cliente: TestClient) -> None:
    pedido = PACIENTE[:-2]

    resposta = _gerar(cliente, {"relatorio": "agenda_paciente", "paciente": pedido})

    assert resposta.status_code == 200
    assert resposta.json()["bloco"] is None
    assert "nome exato" in resposta.json()["texto"]
    assert not [nome for nome in NOMES_PACIENTES if nome in resposta.text]


def test_nome_do_paciente_nunca_vai_para_log(
    cliente: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        encontrado = _gerar(cliente, {"relatorio": "agenda_paciente", "paciente": PACIENTE})
        _gerar(cliente, {"relatorio": "agenda_paciente", "paciente": PACIENTE[:-2]})

    assert encontrado.status_code == 200
    assert encontrado.json()["bloco"]["tipo"] == "agenda_paciente"
    texto_dos_logs = caplog.text.lower()
    assert not [n for n in NOMES_PACIENTES if n.lower() in texto_dos_logs]
    assert "ficticio" not in texto_dos_logs


def test_relatorios_de_contagem_nunca_trazem_nome_de_paciente(cliente: TestClient) -> None:
    for corpo in CASOS_IGUAIS_A_TOOL:
        if corpo["relatorio"] == "agenda_paciente":
            continue
        resposta = _gerar(cliente, corpo)
        assert not [nome for nome in NOMES_PACIENTES if nome in resposta.text], corpo


# --- GET /relatorios/catalogo -------------------------------------------------


def test_catalogo_traz_o_texto_da_tool_e_os_campos(
    cliente: TestClient, fonte: FakeScheduleDataSource
) -> None:
    resposta = cliente.get("/relatorios/catalogo", headers=HEADERS_AUTENTICADOS)

    assert resposta.status_code == 200
    corpo = resposta.json()
    tool = next(t for t in criar_tools_relatorios(fonte, HOJE) if t.name == "consultar_catalogo")
    assert corpo["texto"] == tool.invoke({})
    assert (corpo["semana_inicio"], corpo["semana_fim"]) == ("2026-09-28", "2026-10-03")
    assert [item["nome"] for item in corpo["relatorios"]] == list(RELATORIOS)
    assert "ocupacao" in {item["nome"] for item in corpo["metricas"]}
    assert "especialidade" in corpo["dimensoes"]
    assert corpo["parcial"] is False
    assert not [nome for nome in NOMES_PACIENTES if nome in resposta.text]


def test_catalogo_de_outra_semana(cliente: TestClient) -> None:
    resposta = cliente.get(
        "/relatorios/catalogo", params={"data": "2026-10-07"}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 200
    assert resposta.json()["semana_inicio"] == "2026-10-05"


def test_catalogo_com_data_invalida_devolve_422(cliente: TestClient) -> None:
    resposta = cliente.get(
        "/relatorios/catalogo", params={"data": "ontem"}, headers=HEADERS_AUTENTICADOS
    )

    assert resposta.status_code == 422
    assert "ontem" not in resposta.text


def test_pacientes_por_plano_nao_e_exposto_na_api(cliente: TestClient) -> None:
    """Decisão da clínica: o relatório com nomes por plano sai só pelo chat."""
    resposta = _gerar(cliente, {"relatorio": "pacientes_por_plano"})

    assert resposta.status_code == 422
    assert "pacientes_por_plano" not in RELATORIOS
