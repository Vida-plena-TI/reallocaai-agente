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
from app.domain.fonte_agenda import EntradaGrade, ScheduleDataSource
from app.domain.normalizacao import normalizar_id
from app.domain.semana import dias_da_semana_de
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
    "EntradaGrade",
    "Especialidade",
    "ItemSolicitacao",
    "Paciente",
    "Profissional",
    "RealocAIDomainError",
    "Sala",
    "ScheduleDataSource",
    "Slot",
    "SlotForaDoExpedienteError",
    "SlotForaDoGridError",
    "SlotInvalidoError",
    "SlotNaPausaError",
    "SolicitacaoAtendimento",
    "dias_da_semana_de",
    "normalizar_id",
]
