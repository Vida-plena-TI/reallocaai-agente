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

`ChatGoogleGenerativeAI` (inspecionada em `langchain-google-genai==4.4.0`, via
`help(langchain_google_genai.ChatGoogleGenerativeAI)`): mesmo padrão de
`ChatOpenAI` — construtor recebe `model` e `api_key` (a chave também pode vir
da variável de ambiente `GOOGLE_API_KEY`, mas aqui é sempre passada
explicitamente, junto com `model`, para manter a mesma validação de
configuração dos dois provedores).
"""

import logging
from datetime import date
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from app.ai.prompts import prompt_de_sistema
from app.ai.rastreamento import desligar_rastreamento_externo
from app.ai.relatorios import BlocoRelatorio
from app.ai.tools import DIAS_DA_SEMANA, EnviarRelatorio, criar_tools
from app.config import exigir, get_settings
from app.data_sources.continuidade import ContinuidadeDataSource
from app.domain import ScheduleDataSource

#: `create_agent` devolve um `CompiledStateGraph` parametrizado com tipos
#: internos do LangGraph que não precisamos nomear aqui: usamos `Any` nos
#: quatro parâmetros genéricos (estado, contexto, entrada, saída) porque só
#: chamamos `.invoke` sobre ele, nunca inspecionamos esses tipos.
Agente = CompiledStateGraph[Any, Any, Any, Any]
logger = logging.getLogger(__name__)


def criar_chat_model() -> BaseChatModel:
    """Chat model real, escolhido por `AI_PROVIDER` ("openai" ou "google").

    Dois provedores suportados, sem padrão hardcoded — `AI_PROVIDER` precisa
    estar configurado explicitamente (ver `.env.example`). "google" existe
    para testar o agente de ponta a ponta sem custo, no tier gratuito do
    Google AI Studio, antes de decidir o modelo definitivo da OpenAI para
    produção.

    Nunca é chamada pela suíte de testes automatizados: exige credencial e
    modelo reais configurados. Quem precisa testar o agente sem rede injeta
    outro `BaseChatModel` direto em `criar_agente`.
    """
    # Antes de existir o modelo: nenhuma conversa pode sair por tracing/debug.
    desligar_rastreamento_externo()
    settings = get_settings()
    # Os campos de credencial/modelo são `None` em `Settings` até aqui —
    # validados só neste ponto de uso, não na classe, para `Settings()`
    # continuar construível sem `.env` nenhum (ver `exigir`).
    provider = exigir(settings.ai_provider, 'AI_PROVIDER ("openai" ou "google")')

    if provider == "openai":
        api_key = exigir(settings.openai_api_key, "OPENAI_API_KEY")
        model = exigir(settings.openai_model, "OPENAI_MODEL")
        return ChatOpenAI(model=model, api_key=api_key)

    if provider == "google":
        google_api_key = exigir(settings.google_api_key, "GOOGLE_API_KEY")
        google_model = exigir(settings.google_model, "GOOGLE_MODEL")
        return ChatGoogleGenerativeAI(model=google_model, api_key=google_api_key)

    raise ValueError(
        f'AI_PROVIDER="{provider}" não reconhecido. Valores aceitos: "openai" ou "google".'
    )


def criar_agente(
    fonte: ScheduleDataSource,
    continuidade: ContinuidadeDataSource,
    chat_model: BaseChatModel,
    data_referencia: date,
    enviar_relatorio: EnviarRelatorio,
    renderiza_relatorios: bool = False,
) -> Agente:
    """Monta o agente RealocAI: tools da Parte B + prompt de sistema da Parte C.

    `chat_model` é sempre recebido de fora, nunca construído aqui — em
    produção é o resultado de `criar_chat_model()`; em teste, um chat model
    falso que não faz nenhuma chamada de rede (ver `tests/ai/test_agente.py`).
    `enviar_relatorio` é repassado direto para `criar_tools` (ver lá o porquê
    de não ser importado direto de `app.reports`).
    """
    tools = criar_tools(fonte, continuidade, data_referencia, enviar_relatorio)
    # O prompt só cita o envio por e-mail quando a tool foi registrada.
    variante = prompt_de_sistema(
        renderiza_relatorios=renderiza_relatorios,
        envio_email=any(item.name == "enviar_relatorio" for item in tools),
    )
    prompt = variante.format(
        data_referencia=data_referencia.strftime("%d/%m/%Y"),
        dia_da_semana=DIAS_DA_SEMANA[data_referencia.weekday()],
    )
    return create_agent(model=chat_model, tools=tools, system_prompt=prompt)


def _extrair_texto_da_resposta(mensagem: BaseMessage) -> str:
    """Normaliza `mensagem.content` para o texto que vai para o usuário.

    `content` pode ser uma `str` (caso comum) ou uma lista de blocos
    estruturados — formato usado por alguns provedores/modelos (visto com
    `ChatGoogleGenerativeAI`) para carregar, junto do texto, metadados
    internos (ex: blocos de assinatura) que não fazem sentido devolver ao
    usuário. Só os blocos com `"type": "text"` viram texto; qualquer outro
    bloco é ignorado silenciosamente.
    """
    content = mensagem.content
    if isinstance(content, str):
        return content

    textos = [
        bloco["text"]
        for bloco in content
        if isinstance(bloco, dict) and bloco.get("type") == "text"
    ]
    return "\n".join(textos)


def perguntar(agente: Agente, historico_mensagens: list[BaseMessage]) -> str:
    """Roda `agente` sobre o histórico completo e devolve o texto da resposta final.

    Sem gerenciamento de sessão aqui: quem chama já manda o histórico
    completo da conversa a cada turno (decisão já tomada nesta fase).
    """
    resposta, _, _ = perguntar_com_mensagens(agente, historico_mensagens)
    return resposta


def perguntar_com_mensagens(
    agente: Agente, historico_mensagens: list[BaseMessage]
) -> tuple[str, list[BaseMessage], list[BlocoRelatorio]]:
    """Como `perguntar`, com mensagens do grafo e blocos apenas do turno atual.

    A lista inclui o histórico de entrada seguido das mensagens geradas no
    turno (chamadas de tool e seus retornos) — útil para inspecionar quais
    tools o agente usou (ver `scripts/chat_manual.py --verbose`).
    """
    resultado = agente.invoke({"messages": historico_mensagens})
    mensagens: list[BaseMessage] = resultado["messages"]
    return _extrair_texto_da_resposta(mensagens[-1]), mensagens, extrair_blocos_do_turno(mensagens)


def extrair_blocos_do_turno(mensagens: list[BaseMessage]) -> list[BlocoRelatorio]:
    """Valida artifacts depois da última pergunta, em ordem, com limite de dez."""
    inicio = next(
        (
            i + 1
            for i in range(len(mensagens) - 1, -1, -1)
            if isinstance(mensagens[i], HumanMessage)
        ),
        len(mensagens),
    )
    blocos: list[BlocoRelatorio] = []
    for mensagem in mensagens[inicio:]:
        if not isinstance(mensagem, ToolMessage) or mensagem.artifact is None:
            continue
        try:
            bloco = BlocoRelatorio.model_validate(mensagem.artifact)
        except ValidationError:
            # Não imprime o artifact nem os valores da falha de validação.
            logger.warning("Artefato inválido de relatório ignorado.")
            continue
        if len(blocos) >= 10:
            logger.warning("Limite de 10 blocos por turno excedido; bloco ignorado.")
            continue
        blocos.append(bloco)
    return blocos
