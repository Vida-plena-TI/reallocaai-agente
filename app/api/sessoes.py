"""Armazenamento em memória das conversas com o agente (Fase 6b).

`app.ai.agente.perguntar` não tem estado próprio: cada chamada recebe o
histórico completo da conversa. Como a API é feita de requisições HTTP sem
estado entre si, alguém precisa guardar esse histórico entre um turno e o
próximo — é o papel de `ArmazenamentoConversas`.

Em memória de propósito, sem processo/thread de limpeza separado: a faxina
de conversas expiradas roda oportunisticamente a cada chamada pública (ver
`_remover_expiradas`). Não há requisito de sobreviver a um restart do
processo nesta fase. Protegido por `threading.Lock` porque o Starlette roda
endpoints síncronos em threads de um pool, então requisições concorrentes
podem chamar os métodos públicos ao mesmo tempo.

Uma única instância vive em `app.state`, construída no `lifespan` de
`app.main`; `app.api.dependencies.obter_armazenamento_conversas` só lê de lá.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.ai.relatorios import BlocoRelatorio


@dataclass
class _Conversa:
    historico: list[BaseMessage] = field(default_factory=list)
    blocos_por_mensagem: list[list[BlocoRelatorio]] = field(default_factory=list)
    # `lambda: time.monotonic()`, não `time.monotonic` direto: o segundo capturaria a
    # função original já no momento em que este módulo é importado, o que impediria os
    # testes de simular o avanço do tempo via `monkeypatch.setattr(..., "time.monotonic", ...)`.
    ultimo_uso: float = field(default_factory=lambda: time.monotonic())


class ArmazenamentoConversas:
    """Histórico de conversas em memória, indexado por `conversa_id`, com expiração por TTL."""

    def __init__(self, ttl_horas: float = 4) -> None:
        self._ttl_segundos = ttl_horas * 3600
        self._conversas: dict[str, _Conversa] = {}
        self._lock = threading.Lock()

    def _remover_expiradas(self) -> None:
        """Chamada só de dentro de um bloco já protegido por `self._lock`."""
        agora = time.monotonic()
        expiradas = [
            conversa_id
            for conversa_id, conversa in self._conversas.items()
            if agora - conversa.ultimo_uso > self._ttl_segundos
        ]
        for conversa_id in expiradas:
            del self._conversas[conversa_id]

    def criar_conversa(self) -> str:
        """Registra uma nova conversa, com histórico vazio, e devolve seu id."""
        with self._lock:
            self._remover_expiradas()
            conversa_id = str(uuid.uuid4())
            self._conversas[conversa_id] = _Conversa()
            return conversa_id

    def obter_historico(self, conversa_id: str) -> list[BaseMessage] | None:
        """Histórico de `conversa_id`, ou `None` se ele não existir ou tiver expirado.

        Formato compatível com o que `app.ai.agente.perguntar` espera: uma
        lista de `BaseMessage` (`HumanMessage`/`AIMessage`), pronta para
        completar com a mensagem nova do turno atual.
        """
        with self._lock:
            self._remover_expiradas()
            conversa = self._conversas.get(conversa_id)
            if conversa is None:
                return None
            return list(conversa.historico)

    def registrar_troca(
        self,
        conversa_id: str,
        mensagem_usuario: str,
        resposta_agente: str,
        blocos: list[BlocoRelatorio] | None = None,
    ) -> None:
        """Adiciona a pergunta e a resposta ao histórico e atualiza o instante de último uso.

        Não-op silencioso se `conversa_id` não existir (ou tiver expirado
        entre o início do turno e este registro): quem chama já garantiu a
        existência da conversa antes de rodar o agente.
        """
        with self._lock:
            self._remover_expiradas()
            conversa = self._conversas.get(conversa_id)
            if conversa is None:
                return
            conversa.historico.append(HumanMessage(mensagem_usuario))
            conversa.historico.append(AIMessage(resposta_agente))
            conversa.blocos_por_mensagem.extend(
                [
                    [],
                    [bloco.model_copy(deep=True) for bloco in blocos or []],
                ]
            )
            conversa.ultimo_uso = time.monotonic()

    def obter_historico_com_blocos(
        self,
        conversa_id: str,
    ) -> list[tuple[BaseMessage, list[BlocoRelatorio]]] | None:
        """Snapshot para a API; obter_historico continua trazendo só texto ao modelo."""
        with self._lock:
            self._remover_expiradas()
            conversa = self._conversas.get(conversa_id)
            if conversa is None:
                return None
            return [
                (mensagem, [b.model_copy(deep=True) for b in blocos])
                for mensagem, blocos in zip(
                    conversa.historico,
                    conversa.blocos_por_mensagem,
                    strict=True,
                )
            ]
