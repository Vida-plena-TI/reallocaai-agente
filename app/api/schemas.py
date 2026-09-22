"""Modelos de resposta da API HTTP (Fase 6a).

Contrato próprio da API: nunca reexporta as entidades de `app.domain`
diretamente como resposta HTTP, para a API não quebrar toda vez que um
detalhe interno do domínio mudar.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class ChatRequest(BaseModel):
    """Corpo de `POST /agenda/chat`."""

    model_config = ConfigDict(frozen=True)

    conversa_id: str | None = Field(
        default=None, description="Id de uma conversa já existente. Omitido para iniciar uma nova."
    )
    mensagem: str = Field(min_length=1, description="Mensagem do usuário para o agente.")

    @field_validator("mensagem")
    @classmethod
    def _rejeitar_mensagem_em_branco(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("mensagem não pode conter só espaços em branco.")
        return valor


class ChatResponse(BaseModel):
    """Resposta de `POST /agenda/chat`."""

    model_config = ConfigDict(frozen=True)

    conversa_id: str
    resposta: str


class MensagemHistoricoResponse(BaseModel):
    """Uma mensagem do histórico bruto de uma conversa (`GET /agenda/chat/{conversa_id}`)."""

    model_config = ConfigDict(frozen=True)

    papel: str
    conteudo: str
