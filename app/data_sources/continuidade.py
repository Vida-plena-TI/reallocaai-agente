"""Continuidade terapêutica: qual profissional atende habitualmente um paciente.

`ContinuidadeDataSource` existe como `Protocol` para a camada de serviço (Fase
5a) escolher a implementação sem instanciar nada internamente. Hoje só existe
`SemHistoricoContinuidadeDataSource`: a integração que traria o histórico real
depende do Agendador/API Gateway, fase futura fora deste escopo.
"""

from typing import Protocol

from app.domain import Especialidade


class ContinuidadeDataSource(Protocol):
    """De onde vem a informação de continuidade terapêutica de um paciente."""

    def profissional_habitual(self, paciente_id: str, especialidade: Especialidade) -> str | None:
        """Id do profissional que atende habitualmente esse paciente nessa
        especialidade, ou `None` quando essa informação não está disponível.
        """
        ...


class SemHistoricoContinuidadeDataSource:
    """`ContinuidadeDataSource` sem histórico algum: sempre devolve `None`.

    Implementação usada enquanto não houver integração com o Agendador/API
    Gateway.
    """

    def profissional_habitual(self, paciente_id: str, especialidade: Especialidade) -> str | None:
        """Sempre `None`: não há fonte de continuidade nesta fase."""
        return None
