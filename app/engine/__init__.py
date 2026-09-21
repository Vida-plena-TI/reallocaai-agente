"""Motor de alocação do RealocAI: disponibilidade, ocupação (Fase 4a) e
encaixe casado com sugestão de realocação (Fase 4b).
"""

from app.engine.disponibilidade import SlotDisponivel, listar_disponibilidade
from app.engine.encaixe import (
    CenarioSugestao,
    ItemEncaixeResolvido,
    OpcaoComCenario,
    OpcaoEncaixe,
    buscar_alternativas,
    buscar_melhor_encaixe,
    sugerir_realocacao,
)
from app.engine.ocupacao import (
    OcupacaoAgregada,
    OcupacaoProfissional,
    RelatorioOcupacaoDoDia,
    construir_relatorio_ocupacao_do_dia,
)

__all__ = [
    "CenarioSugestao",
    "ItemEncaixeResolvido",
    "OcupacaoAgregada",
    "OcupacaoProfissional",
    "OpcaoComCenario",
    "OpcaoEncaixe",
    "RelatorioOcupacaoDoDia",
    "SlotDisponivel",
    "buscar_alternativas",
    "buscar_melhor_encaixe",
    "construir_relatorio_ocupacao_do_dia",
    "listar_disponibilidade",
    "sugerir_realocacao",
]
