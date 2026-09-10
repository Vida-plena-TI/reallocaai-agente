"""Entidades do domínio do RealocAI.

Nenhuma delas sabe alocar: aqui só existem os dados do negócio e as regras que
impedem um estado inválido de ser construído.
"""

from datetime import date
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.constants import DURACAO_SLOT_MINUTOS
from app.domain.enums import Especialidade
from app.domain.exceptions import (
    AtendimentoComBuracoError,
    AtendimentoEmDiasDiferentesError,
)
from app.domain.slot import Slot


class Profissional(BaseModel):
    """Quem atende. Tem exatamente uma especialidade — nunca mais de uma."""

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    nome: str = Field(min_length=1)
    especialidade: Especialidade


class Sala(BaseModel):
    """Onde se atende.

    `capacidade_simultanea` é um dado da sala, não uma regra fixa: quantos
    atendimentos ela comporta ao mesmo tempo.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    nome: str = Field(min_length=1)
    capacidade_simultanea: int = Field(default=1, ge=1)


class Paciente(BaseModel):
    """Quem é atendido.

    `convenio` é apenas informativo: hoje não existe restrição de alocação por
    convênio.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    nome: str = Field(min_length=1)
    convenio: str | None = None


class Atendimento(BaseModel):
    """Um bloco contínuo de slots de um paciente com um profissional numa sala.

    Os slots são normalizados em ordem crescente e precisam formar um bloco sem
    buraco dentro do mesmo dia.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    paciente_id: str = Field(min_length=1)
    profissional_id: str = Field(min_length=1)
    sala_id: str = Field(min_length=1)
    especialidade: Especialidade
    slots: list[Slot] = Field(min_length=1)

    @field_validator("slots")
    @classmethod
    def _validar_bloco_continuo(cls, slots: list[Slot]) -> list[Slot]:
        dias = {slot.data for slot in slots}
        if len(dias) > 1:
            raise AtendimentoEmDiasDiferentesError(
                f"Um atendimento acontece em um único dia, mas recebeu {len(dias)}: "
                f"{', '.join(dia.isoformat() for dia in sorted(dias))}."
            )

        ordenados = sorted(slots)
        for anterior, seguinte in pairwise(ordenados):
            if not anterior.e_contiguo_a(seguinte):
                raise AtendimentoComBuracoError(
                    f"Há um buraco entre {anterior} e {seguinte}: "
                    "os slots de um atendimento precisam ser contíguos."
                )

        return ordenados

    @property
    def data(self) -> date:
        """Dia do atendimento (todos os slots são do mesmo dia)."""
        return self.slots[0].data

    @property
    def duracao_minutos(self) -> int:
        """Duração total do atendimento em minutos."""
        return len(self.slots) * DURACAO_SLOT_MINUTOS


class ItemSolicitacao(BaseModel):
    """Uma especialidade pedida e quantos slots ela precisa ocupar."""

    model_config = ConfigDict(validate_assignment=True)

    especialidade: Especialidade
    duracao_em_slots: int = Field(ge=1)

    @property
    def duracao_minutos(self) -> int:
        """Duração pedida em minutos."""
        return self.duracao_em_slots * DURACAO_SLOT_MINUTOS


class SolicitacaoAtendimento(BaseModel):
    """A necessidade de um paciente num dia — o "atendimento casado".

    É só a estrutura da demanda: encontrar o encaixe é trabalho da engine.
    """

    model_config = ConfigDict(validate_assignment=True)

    paciente_id: str = Field(min_length=1)
    data: date
    itens: list[ItemSolicitacao] = Field(min_length=1)

    @property
    def total_de_slots(self) -> int:
        """Quantos slots o conjunto de itens solicitados consome."""
        return sum(item.duracao_em_slots for item in self.itens)
