"""Contrato da fonte de dados da agenda.

A engine e os relatórios enxergam a agenda por aqui — nunca pela planilha. Hoje
existe uma implementação (`GoogleSheetsDataSource`), mas o `Protocol` mantém a
porta aberta para trocar a origem dos dados sem tocar no resto do sistema.
"""

from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain import Atendimento, Especialidade, Paciente, Profissional, Sala, Slot


class EntradaGrade(BaseModel):
    """Uma janela de atendimento que existe na agenda do dia.

    É a oferta, não a ocupação: a entrada existe porque aquele profissional está
    escalado naquela sala naquele slot, esteja ela livre ou já com paciente. O
    que **não** existe são os slots marcados como `FECHADO` na planilha.
    """

    model_config = ConfigDict(frozen=True)

    sala_id: str = Field(min_length=1)
    profissional_id: str = Field(min_length=1)
    especialidade: Especialidade
    slot: Slot


class ScheduleDataSource(Protocol):
    """De onde a agenda de um dia é lida."""

    def listar_salas(self, dia: date) -> list[Sala]:
        """Salas em uso no dia, com a capacidade simultânea de cada uma."""
        ...

    def listar_profissionais(self, dia: date) -> list[Profissional]:
        """Profissionais escalados no dia."""
        ...

    def listar_grade(self, dia: date) -> list[EntradaGrade]:
        """Todas as janelas (sala, profissional, slot) abertas no dia."""
        ...

    def listar_atendimentos(self, dia: date) -> list[Atendimento]:
        """Atendimentos já alocados no dia."""
        ...

    def listar_pacientes(self, dia: date) -> list[Paciente]:
        """Todos os pacientes que aparecem em algum `Atendimento` do dia, com o
        convênio já resolvido quando disponível.
        """
        ...
