"""Regras estáticas da clínica.

São valores fixos do negócio (expediente, pausa, granularidade da agenda e meta
de ocupação). Dados que variam por unidade — como a capacidade de uma sala —
não moram aqui: são atributos das entidades.
"""

from datetime import time
from typing import Final

#: Início do expediente da clínica.
HORARIO_ABERTURA: Final[time] = time(7, 0)

#: Fim do expediente da clínica (nenhum atendimento pode terminar depois disso).
HORARIO_FECHAMENTO: Final[time] = time(18, 0)

#: Início da pausa geral: nenhum atendimento acontece nesta janela.
INICIO_PAUSA: Final[time] = time(12, 0)

#: Fim da pausa geral.
FIM_PAUSA: Final[time] = time(13, 0)

#: Granularidade da agenda: toda janela de atendimento tem 30 minutos.
DURACAO_SLOT_MINUTOS: Final[int] = 30

#: Meta de ocupação por sala (80% do expediente útil).
META_OCUPACAO_POR_SALA: Final[float] = 0.8
