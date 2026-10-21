"""`POST /relatorio/enviar` com o campo opcional `relatorios`.

Sem o campo, o comportamento é o de sempre (os testes de `test_relatorio.py`
continuam valendo e este arquivo prova que o caminho novo nem é tocado). Com
o campo, o e-mail traz exatamente os relatórios pedidos. Nenhum teste envia
e-mail de verdade: `resend.Emails.send` é sempre substituído.
"""

import logging
from datetime import date
from typing import Any

import pytest
import resend
from fastapi.testclient import TestClient

from app.api.dependencies import obter_fonte
from app.config import Settings, get_settings
from app.main import app
from tests.api.conftest import API_KEY, HEADERS_AUTENTICADOS
from tests.support.agenda_clinica import NOMES_PACIENTES, fonte_clinica
from tests.support.agenda_semanal import QUARTA, SEGUNDA, SEMANA, SEXTA
from tests.support.fake_schedule_data_source import FakeScheduleDataSource


@pytest.fixture
def fonte() -> FakeScheduleDataSource:
    return fonte_clinica()


@pytest.fixture
def enviados(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Os e-mails que iriam para o Resend, com credenciais falsas."""
    chamadas: list[dict[str, Any]] = []
    monkeypatch.setattr(resend.Emails, "send", lambda params: chamadas.append(params))
    monkeypatch.setattr(
        "app.reports.envio.get_settings",
        lambda: Settings(
            resend_api_key="re_test_key",
            report_email_from="relatorios@exemplo.com.br",
            report_email_to=["equipe@exemplo.com.br"],
        ),
    )
    return chamadas


def _enviar(client: TestClient, corpo: dict[str, Any]) -> Any:
    return client.post("/relatorio/enviar", json=corpo, headers=HEADERS_AUTENTICADOS)


def test_sem_o_campo_o_caminho_novo_nem_e_tocado(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[Any] = []
    monkeypatch.setattr(
        "app.api.routes.enviar_relatorio_por_email",
        lambda fonte, data, destinatarios: chamadas.append((data, destinatarios)),
    )

    def nao_chamar(*_: Any) -> None:
        raise AssertionError("sem 'relatorios', o envio novo não pode ser usado")

    monkeypatch.setattr("app.api.routes.enviar_relatorios_gerais_por_email", nao_chamar)

    resposta = _enviar(client, {"data": QUARTA.isoformat()})

    assert resposta.status_code == 200
    assert resposta.json()["enviado"] is True
    assert chamadas == [(QUARTA, None)]


def test_sem_o_campo_o_email_e_o_de_ocupacao_de_sempre(
    client: TestClient, enviados: list[dict[str, Any]]
) -> None:
    resposta = _enviar(client, {"data": QUARTA.isoformat()})

    assert resposta.status_code == 200
    assert len(enviados) == 1
    assert enviados[0]["subject"] == "RealocAI — Relatório de ocupação de 30 de setembro de 2026"


def test_com_o_campo_envia_exatamente_os_relatorios_pedidos(
    client: TestClient, enviados: list[dict[str, Any]]
) -> None:
    resposta = _enviar(
        client,
        {
            "relatorios": ["salas_ociosas", "convenios"],
            "inicio": SEGUNDA.isoformat(),
            "fim": SEXTA.isoformat(),
            "destinatarios": ["coordenacao@exemplo.com.br"],
        },
    )

    assert resposta.status_code == 200
    assert resposta.json() == {"enviado": True, "destinatarios": ["coordenacao@exemplo.com.br"]}
    (email,) = enviados
    assert email["to"] == ["coordenacao@exemplo.com.br"]
    assert email["subject"] == "RealocAI — Relatórios de 28/09/2026 a 02/10/2026"
    texto = email["text"]
    assert texto.index("SALAS OCIOSAS") < texto.index("CONVÊNIOS E AUTORIZAÇÕES PENDENTES")
    assert "OCUPAÇÃO DO DIA" not in texto
    assert "VAGAS" not in texto


def test_ocupacao_so_entra_quando_pedida(
    client: TestClient, enviados: list[dict[str, Any]]
) -> None:
    _enviar(client, {"relatorios": ["ocupacao"], "data": QUARTA.isoformat()})
    _enviar(client, {"relatorios": ["vagas_livres"], "data": QUARTA.isoformat()})

    assert "OCUPAÇÃO DO DIA" in enviados[0]["text"]
    assert "OCUPAÇÃO DO DIA" not in enviados[1]["text"]


def test_email_nunca_tem_nome_de_paciente_nem_nos_logs(
    client: TestClient, enviados: list[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    todos = ["vagas_livres", "ranking_ocupacao", "convenios", "qualidade_agenda", "salas_ociosas"]

    with caplog.at_level(logging.DEBUG):
        resposta = _enviar(
            client, {"relatorios": todos, "inicio": SEGUNDA.isoformat(), "fim": SEXTA.isoformat()}
        )

    assert resposta.status_code == 200
    conteudo = (enviados[0]["html"] + enviados[0]["text"] + caplog.text).lower()
    assert not [nome for nome in NOMES_PACIENTES if nome.lower() in conteudo]
    assert "ficticio" not in conteudo


@pytest.mark.parametrize(
    ("corpo", "trecho"),
    [
        ({"relatorios": ["agenda_paciente"]}, "nunca contém nome de paciente"),
        ({"relatorios": ["vagas_livres", "agenda_paciente"]}, "nunca contém nome de paciente"),
        ({"relatorios": []}, "ao menos um"),
        ({"relatorios": ["vagas"]}, "não vão por e-mail: vagas"),
        (
            {"relatorios": ["convenios"], "data": "2026-09-30", "inicio": "2026-09-28"},
            "não os dois",
        ),
        (
            {"relatorios": ["ocupacao"], "inicio": "2026-09-28", "fim": "2026-09-30"},
            "ocupacao é de um dia só",
        ),
        (
            {"relatorios": ["convenios"], "inicio": "2026-09-01", "fim": "2026-09-30"},
            "o máximo é 14 dias",
        ),
        (
            {"relatorios": ["convenios"], "inicio": "2026-09-30", "fim": "2026-09-28"},
            "anterior ao início",
        ),
        ({"inicio": "2026-09-28"}, "só valem com 'relatorios'"),
    ],
)
def test_pedido_invalido_devolve_422_com_mensagem_clara(
    client: TestClient, enviados: list[dict[str, Any]], corpo: dict[str, Any], trecho: str
) -> None:
    resposta = _enviar(client, corpo)

    assert resposta.status_code == 422
    assert trecho in resposta.json()["detail"][0]["msg"]
    assert enviados == []


def test_agenda_de_paciente_nao_ecoa_nada_do_corpo(
    client: TestClient, enviados: list[dict[str, Any]]
) -> None:
    resposta = _enviar(
        client, {"relatorios": ["agenda_paciente"], "destinatarios": [NOMES_PACIENTES[0]]}
    )

    assert resposta.status_code == 422
    assert NOMES_PACIENTES[0] not in resposta.text


def test_sem_resend_devolve_503_tambem_com_relatorios(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        openai_model="gpt-4o-mini",
        internal_api_key=API_KEY,
        resend_api_key=None,
        report_email_from=None,
    )

    resposta = _enviar(client, {"relatorios": ["convenios"]})

    assert resposta.status_code == 503


def test_falha_de_leitura_devolve_502_sem_detalhe(
    client: TestClient, enviados: list[dict[str, Any]]
) -> None:
    ilegivel = fonte_clinica(set(SEMANA))
    app.dependency_overrides[obter_fonte] = lambda: ilegivel

    resposta = _enviar(
        client, {"relatorios": ["convenios"], "inicio": "2026-09-28", "fim": "2026-10-03"}
    )

    assert resposta.status_code == 502
    assert "falha simulada" not in resposta.text
    assert enviados == []


def test_data_padrao_e_hoje(
    client: TestClient, enviados: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[tuple[date, date]] = []
    monkeypatch.setattr(
        "app.api.routes.enviar_relatorios_gerais_por_email",
        lambda fonte, tipos, inicio, fim, destinatarios: chamadas.append((inicio, fim)),
    )

    _enviar(client, {"relatorios": ["convenios"]})

    assert chamadas == [(date.today(), date.today())]
