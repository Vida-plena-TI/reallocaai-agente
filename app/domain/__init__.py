"""Domínio do RealocAI: entidades, regras estáticas e exceções do negócio."""

from app.domain.constants import (
    DURACAO_SLOT_MINUTOS,
    FIM_PAUSA,
    HORARIO_ABERTURA,
    HORARIO_FECHAMENTO,
    INICIO_PAUSA,
    META_OCUPACAO_POR_SALA,
)
from app.domain.entities import (
    Atendimento,
    ItemSolicitacao,
    Paciente,
    Profissional,
    Sala,
    SolicitacaoAtendimento,
)
from app.domain.enums import Especialidade
from app.domain.exceptions import (
    AtendimentoComBuracoError,
    AtendimentoEmDiasDiferentesError,
    AtendimentoInvalidoError,
    RealocAIDomainError,
    SlotForaDoExpedienteError,
    SlotForaDoGridError,
    SlotInvalidoError,
    SlotNaPausaError,
)
from app.domain.slot import Slot

__all__ = [
    "DURACAO_SLOT_MINUTOS",
    "FIM_PAUSA",
    "HORARIO_ABERTURA",
    "HORARIO_FECHAMENTO",
    "INICIO_PAUSA",
    "META_OCUPACAO_POR_SALA",
    "Atendimento",
    "AtendimentoComBuracoError",
    "AtendimentoEmDiasDiferentesError",
    "AtendimentoInvalidoError",
    "Especialidade",
    "ItemSolicitacao",
    "Paciente",
    "Profissional",
    "RealocAIDomainError",
    "Sala",
    "Slot",
    "SlotForaDoExpedienteError",
    "SlotForaDoGridError",
    "SlotInvalidoError",
    "SlotNaPausaError",
    "SolicitacaoAtendimento",
]
