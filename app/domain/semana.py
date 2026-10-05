"""Janela semanal da agenda: segunda a sábado (domingo não tem agenda).

Mora no domínio porque tanto a camada de IA (`localizar_paciente`,
`localizar_profissional`) quanto a engine (ocupação semanal por profissional)
precisam da mesma definição de "semana", e a engine não pode importar de
`app.ai`.
"""

from datetime import date, timedelta

#: Dias com agenda numa semana: segunda (0) a sábado (5).
DIAS_COM_AGENDA_POR_SEMANA = 6


def dias_da_semana_de(data: date) -> list[date]:
    """Segunda a sábado da semana que contém `data`."""
    segunda = data - timedelta(days=data.weekday())
    return [
        segunda + timedelta(days=deslocamento) for deslocamento in range(DIAS_COM_AGENDA_POR_SEMANA)
    ]
