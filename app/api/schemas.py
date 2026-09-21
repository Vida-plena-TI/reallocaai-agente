"""Modelos de resposta da API HTTP (Fase 6a).

Contrato próprio da API: nunca reexporta as entidades de `app.domain`
diretamente como resposta HTTP, para a API não quebrar toda vez que um
detalhe interno do domínio mudar.
"""

from pydantic import BaseModel, ConfigDict, Field


class SlotDisponivelResponse(BaseModel):
    """Um horário livre, já com nomes resolvidos (não ids) para exibição."""

    model_config = ConfigDict(frozen=True)

    horario: str
    sala: str
    profissional: str
    especialidade: str


class OcupacaoItemResponse(BaseModel):
    """Ocupação agregada de uma sala ou de uma especialidade."""

    model_config = ConfigDict(frozen=True)

    rotulo: str
    slots_escalados: int
    slots_ocupados: int
    percentual: float = Field(ge=0.0, le=1.0)
    abaixo_da_meta: bool


class OcupacaoResponse(BaseModel):
    """Ocupação consolidada de um dia, por sala e por especialidade."""

    model_config = ConfigDict(frozen=True)

    data: str
    por_sala: list[OcupacaoItemResponse]
    por_especialidade: list[OcupacaoItemResponse]
