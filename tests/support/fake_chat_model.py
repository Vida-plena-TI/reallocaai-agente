"""Chat model falso para testar o agente sem nenhuma chamada de rede.

Estende `FakeMessagesListChatModel`, do próprio `langchain_core`: ele já
roteiriza uma sequência de `BaseMessage` de resposta (ciclando por elas a
cada chamada, incluindo `AIMessage` com `tool_calls` simuladas) — só falta a
ele `bind_tools`, que `create_agent` (`app.ai.agente`) chama sempre que a
lista de tools não está vazia, e a implementação padrão de `BaseChatModel`
levanta `NotImplementedError`. Como o roteiro de respostas já vem pronto,
o bind não precisa inspecionar as tools nem montar nada: só devolve o
próprio model.
"""

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool


class FakeToolCallingChatModel(FakeMessagesListChatModel):
    """`FakeMessagesListChatModel` com `bind_tools` que devolve o próprio model."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self
