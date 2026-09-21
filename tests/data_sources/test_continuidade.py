"""Testes de `SemHistoricoContinuidadeDataSource` (Fase 5a)."""

from app.data_sources.continuidade import SemHistoricoContinuidadeDataSource
from app.domain import Especialidade


def test_sem_historico_sempre_retorna_none() -> None:
    fonte = SemHistoricoContinuidadeDataSource()

    assert fonte.profissional_habitual("qualquer-paciente", Especialidade.FONOAUDIOLOGIA) is None
    assert fonte.profissional_habitual("outro-paciente", Especialidade.PSICOLOGIA) is None
