"""Exceções específicas do domínio do RealocAI.

Elas não herdam de `ValueError` de propósito: o pydantic converte `ValueError`
em `ValidationError`, e aqui queremos que a violação de regra do negócio chegue
a quem chamou com o seu próprio tipo, mesmo quando levantada dentro de um
validator.
"""


class RealocAIDomainError(Exception):
    """Base de todas as violações de regra do domínio."""


class SlotInvalidoError(RealocAIDomainError):
    """Base para janelas de 30 minutos que não existem na agenda da clínica."""


class SlotForaDoExpedienteError(SlotInvalidoError):
    """O slot começa antes da abertura ou termina depois do fechamento."""


class SlotNaPausaError(SlotInvalidoError):
    """O slot cai (total ou parcialmente) dentro da pausa geral."""


class SlotForaDoGridError(SlotInvalidoError):
    """O slot não começa em um múltiplo exato da granularidade da agenda."""


class AtendimentoInvalidoError(RealocAIDomainError):
    """Base para atendimentos cujos slots não formam um bloco válido."""


class AtendimentoComBuracoError(AtendimentoInvalidoError):
    """Os slots do atendimento não são contíguos entre si."""


class AtendimentoEmDiasDiferentesError(AtendimentoInvalidoError):
    """Os slots do atendimento não pertencem todos ao mesmo dia."""
