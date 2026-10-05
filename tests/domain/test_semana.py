"""Testes de `dias_da_semana_de`: a janela segunda-sábado de uma data."""

from datetime import date, timedelta

import pytest

from app.domain import dias_da_semana_de

SEMANA = [date(2026, 9, 28) + timedelta(days=n) for n in range(6)]


@pytest.mark.parametrize(
    "data",
    [date(2026, 9, 28), date(2026, 10, 1), date(2026, 10, 3), date(2026, 10, 4)],
)
def test_segunda_a_sabado_da_semana_da_data(data: date) -> None:
    """Domingo pertence à semana que começa na segunda anterior, e nunca entra na janela."""
    assert dias_da_semana_de(data) == SEMANA
