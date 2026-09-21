"""Construção do agente LangChain do RealocAI (Fase 5b).

API do LangChain usada (inspecionada em `langchain==1.4.x`/`langchain-openai==1.6.2`,
as versões instaladas — não assumida de memória): `langchain.agents.create_agent`,
a fábrica de agente da v1 do LangChain (construída sobre LangGraph, substitui o
antigo `AgentExecutor`/`initialize_agent` de versões anteriores da biblioteca).
Ela devolve um grafo compilado que roda o loop de chamada de tools sozinho;
invoca-se com `.invoke({"messages": [...]})` e o resultado é
`{"messages": [...]}` — a mesma lista de entrada mais as mensagens que o
agente gerou (incluindo as chamadas de tool e os retornos delas). A resposta
final do turno é sempre a última mensagem dessa lista.
"""

from datetime import date
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from app.ai.prompts import PROMPT_SISTEMA
from app.ai.tools import criar_tools
from app.config import exigir, get_settings
from app.data_sources.base import ScheduleDataSource
from app.data_sources.continuidade import ContinuidadeDataSource

#: `create_agent` devolve um `CompiledStateGraph` parametrizado com tipos
#: internos do LangGraph que não precisamos nomear aqui: usamos `Any` nos
#: quatro parâmetros genéricos (estado, contexto, entrada, saída) porque só
#: chamamos `.invoke` sobre ele, nunca inspecionamos esses tipos.
Agente = CompiledStateGraph[Any, Any, Any, Any]

_DIAS_DA_SEMANA = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


def criar_chat_model() -> BaseChatModel:
    """Chat model real da OpenAI, a partir de `OPENAI_API_KEY`/`OPENAI_MODEL`.

    Nunca é chamada pela suíte de testes automatizados: exige credencial e
    modelo reais configurados (ver `.env.example`). Quem precisa testar o
    agente sem rede injeta outro `BaseChatModel` direto em `criar_agente`.
    """
    settings = get_settings()
    # `openai_api_key`/`openai_model` são `None` em `Settings` até aqui — validados
    # só neste ponto de uso, não na classe, para `Settings()` continuar construível
    # sem `.env` nenhum (ver `exigir`).
    api_key = exigir(settings.openai_api_key, "OPENAI_API_KEY")
    model = exigir(settings.openai_model, "OPENAI_MODEL")
    return ChatOpenAI(model=model, api_key=api_key)


def criar_agente(
    fonte: ScheduleDataSource,
    continuidade: ContinuidadeDataSource,
    chat_model: BaseChatModel,
    data_referencia: date,
) -> Agente:
    """Monta o agente RealocAI: tools da Parte B + prompt de sistema da Parte C.

    `chat_model` é sempre recebido de fora, nunca construído aqui — em
    produção é o resultado de `criar_chat_model()`; em teste, um chat model
    falso que não faz nenhuma chamada de rede (ver `tests/ai/test_agente.py`).
    """
    tools = criar_tools(fonte, continuidade)
    prompt = PROMPT_SISTEMA.format(
        data_referencia=data_referencia.strftime("%d/%m/%Y"),
        dia_da_semana=_DIAS_DA_SEMANA[data_referencia.weekday()],
    )
    return create_agent(model=chat_model, tools=tools, system_prompt=prompt)


def perguntar(agente: Agente, historico_mensagens: list[BaseMessage]) -> str:
    """Roda `agente` sobre o histórico completo e devolve o texto da resposta final.

    Sem gerenciamento de sessão aqui: quem chama já manda o histórico
    completo da conversa a cada turno (decisão já tomada nesta fase).
    """
    resultado = agente.invoke({"messages": historico_mensagens})
    return str(resultado["messages"][-1].content)
