"""Interpretador estruturado (atrás de INTERPRETADOR_ATIVO) e configuração dos modelos."""

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.ai.agente import Agente, criar_agente, criar_chat_model, perguntar_com_mensagens
from app.ai.interpretador import (
    Intencao,
    PedidoAgenda,
    criar_interpretador,
    criar_interpretador_configurado,
)
from app.config import Settings
from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from tests.support.fake_chat_model import FakeToolCallingChatModel
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

HOJE = date(2026, 9, 28)


def _pedido(**campos: Any) -> PedidoAgenda:
    base: dict[str, Any] = {
        "intencao": Intencao.DISPONIBILIDADE,
        "dias": ["2026-09-29"],
        "janela": {"inicio": "10:00", "fim": "12:00"},
        "turno": None,
        "especialidades": ["fono"],
        "profissionais": [],
        "duracao_min": 30,
        "recorrente": False,
        "autoriza_mover": False,
        "paciente": None,
        "lacunas": [],
    }
    return PedidoAgenda.model_validate({**base, **campos})


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "ai_provider": "openai",
        "openai_api_key": "chave-de-teste",
        "openai_model": "modelo-base",
        "openai_model_resposta": None,
        "openai_model_interpretador": None,
        "openai_reasoning_effort_resposta": None,
        "openai_reasoning_effort_interpretador": None,
        "interpretador_ativo": False,
        "google_api_key": None,
        "google_model": None,
    }
    return Settings(**{**base, **overrides})


def test_schema_estrito_tem_todos_os_campos_obrigatorios() -> None:
    """O modo estrito da OpenAI exige `required` com todas as propriedades."""
    esquema = PedidoAgenda.model_json_schema()
    assert set(esquema["required"]) == set(esquema["properties"])
    assert esquema["additionalProperties"] is False
    janela = esquema["$defs"]["Janela"]
    assert set(janela["required"]) == set(janela["properties"])


class _ModeloEstruturado:
    """Só o que `criar_interpretador` usa: `with_structured_output(...).invoke(...)`."""

    def __init__(self, resultado: object) -> None:
        self.resultado = resultado
        self.argumentos: dict[str, Any] = {}
        self.mensagens: Sequence[BaseMessage] = []

    def with_structured_output(self, schema: object, **kwargs: Any) -> _ModeloEstruturado:
        self.argumentos = {"schema": schema, **kwargs}
        return self

    def invoke(self, mensagens: Sequence[BaseMessage]) -> object:
        self.mensagens = mensagens
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


def test_interpretador_usa_json_schema_estrito() -> None:
    modelo = _ModeloEstruturado(_pedido())
    interpretar = criar_interpretador(modelo, HOJE)  # type: ignore[arg-type]

    pedido = interpretar([HumanMessage("vagas de fono na terça entre 10h e 12h")])

    assert pedido == _pedido()
    assert modelo.argumentos == {"schema": PedidoAgenda, "method": "json_schema", "strict": True}
    assert isinstance(modelo.mensagens[0], SystemMessage)
    assert "2026-09-28" in str(modelo.mensagens[0].content)


def test_falha_do_interpretador_nao_derruba(caplog: pytest.LogCaptureFixture) -> None:
    interpretar = criar_interpretador(_ModeloEstruturado(RuntimeError("x")), HOJE)  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING):
        assert interpretar([HumanMessage("oi")]) is None
    assert "Interpretador falhou (RuntimeError)" in caplog.text


def _agente(interpretado: PedidoAgenda | None, respostas: list[AIMessage]) -> Agente:
    return criar_agente(
        FakeScheduleDataSource(),
        SemHistoricoContinuidadeDataSource(),
        FakeToolCallingChatModel(responses=respostas),
        HOJE,
        interpretador=lambda _conversa: interpretado,
    )


def test_pedido_com_tudo_informado_nao_gera_pergunta() -> None:
    agente = _agente(_pedido(), [AIMessage("Há vaga de fono na terça.")])

    resposta, mensagens, _ = perguntar_com_mensagens(
        agente, [HumanMessage("vagas de fono na terça entre 10h e 12h")]
    )

    assert resposta == "Há vaga de fono na terça."
    # A interpretação foi como contexto para o agente, mas não fica no histórico.
    assert not any(isinstance(m, SystemMessage) for m in mensagens)


def test_lacunas_viram_uma_pergunta_so_sem_chamar_o_agente() -> None:
    agente = _agente(_pedido(lacunas=["o dia", "a especialidade"]), [])

    resposta, mensagens, blocos = perguntar_com_mensagens(agente, [HumanMessage("tem vaga?")])

    assert resposta == "Para eu sugerir, só preciso saber o dia e a especialidade."
    assert resposta.count("?") == 0 and blocos == []
    assert isinstance(mensagens[-1], AIMessage)


def test_interpretador_desligado_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.ai.interpretador.get_settings", lambda: _settings())
    assert criar_interpretador_configurado(HOJE) is None


def test_interpretador_so_com_openai(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "app.ai.interpretador.get_settings",
        lambda: _settings(interpretador_ativo=True, ai_provider="google"),
    )
    with caplog.at_level(logging.WARNING):
        assert criar_interpretador_configurado(HOJE) is None
    assert "só é suportado com AI_PROVIDER=openai" in caplog.text


def test_interpretador_ligado_usa_o_modelo_dele(monkeypatch: pytest.MonkeyPatch) -> None:
    criados: list[ChatOpenAI] = []
    original = criar_interpretador

    def capturar(modelo: ChatOpenAI, hoje: date) -> Any:
        criados.append(modelo)
        return original(modelo, hoje)

    monkeypatch.setattr("app.ai.interpretador.criar_interpretador", capturar)
    monkeypatch.setattr(
        "app.ai.interpretador.get_settings",
        lambda: _settings(
            interpretador_ativo=True,
            openai_model_interpretador="modelo-interpretador",
            openai_reasoning_effort_interpretador="low",
        ),
    )
    assert criar_interpretador_configurado(HOJE) is not None
    (modelo,) = criados
    assert modelo.model_name == "modelo-interpretador"
    assert modelo.reasoning_effort == "low"


def test_modelo_da_resposta_e_esforco_opcionais(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.ai.agente.get_settings", lambda: _settings())
    padrao = criar_chat_model()
    assert isinstance(padrao, ChatOpenAI)
    assert padrao.model_name == "modelo-base" and padrao.reasoning_effort is None

    monkeypatch.setattr(
        "app.ai.agente.get_settings",
        lambda: _settings(
            openai_model_resposta="modelo-resposta", openai_reasoning_effort_resposta="medium"
        ),
    )
    configurado = criar_chat_model()
    assert isinstance(configurado, ChatOpenAI)
    assert configurado.model_name == "modelo-resposta"
    assert configurado.reasoning_effort == "medium"
