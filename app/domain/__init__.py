"""Domínio do RealocAI: entidades, regras estáticas e exceções do negócio."""

from app.domain.constants import (
    COR_AGUARDANDO_AUTORIZACAO,
    CORES_CONVENIO,
    DURACAO_SLOT_MINUTOS,
    FIM_PAUSA,
    HORARIO_ABERTURA,
    HORARIO_FECHAMENTO,
    HORARIO_PREFERENCIAL_PADRAO,
    INICIO_PAUSA,
    MAPA_ALIAS_PROFISSIONAL,
    MAPA_ESPECIALIDADE_FALLBACK,
    MAPA_SALA_FALLBACK,
    META_OCUPACAO_POR_SALA,
    PROFISSIONAIS_IGNORAR,
)
from app.domain.entities import (
    Atendimento,
    ItemSolicitacao,
    Paciente,
    Profissional,
    Sala,
    SolicitacaoAtendimento,
)
from app.domain.enums import Convenio, Especialidade
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
from app.domain.normalizacao import normalizar_id
from app.domain.slot import Slot

__all__ = [
    "CORES_CONVENIO",
    "COR_AGUARDANDO_AUTORIZACAO",
    "DURACAO_SLOT_MINUTOS",
    "FIM_PAUSA",
    "HORARIO_ABERTURA",
    "HORARIO_FECHAMENTO",
    "HORARIO_PREFERENCIAL_PADRAO",
    "INICIO_PAUSA",
    "MAPA_ALIAS_PROFISSIONAL",
    "MAPA_ESPECIALIDADE_FALLBACK",
    "MAPA_SALA_FALLBACK",
    "META_OCUPACAO_POR_SALA",
    "PROFISSIONAIS_IGNORAR",
    "Atendimento",
    "AtendimentoComBuracoError",
    "AtendimentoEmDiasDiferentesError",
    "AtendimentoInvalidoError",
    "Convenio",
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
    "normalizar_id",
]
