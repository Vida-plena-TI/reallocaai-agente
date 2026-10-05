"""Motor de alocação do RealocAI: disponibilidade, ocupação do dia e por
profissional na semana (Fase 4a), pacientes por profissional e encaixe casado
com sugestão de realocação (Fase 4b).
"""

from app.engine.carga_profissionais import (
    CargaDiaProfissional,
    CargaDoDia,
    CargaProfissionais,
    CargaProfissional,
    construir_carga_profissionais,
)
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
from app.engine.ocupacao_profissional import (
    OcupacaoDiaProfissional,
    OcupacaoSalaPosto,
    OcupacaoSemanalProfissional,
    construir_ocupacao_semanal_profissional,
)

__all__ = [
    "CargaDiaProfissional",
    "CargaDoDia",
    "CargaProfissionais",
    "CargaProfissional",
    "CenarioSugestao",
    "ItemEncaixeResolvido",
    "OcupacaoAgregada",
    "OcupacaoDiaProfissional",
    "OcupacaoProfissional",
    "OcupacaoSalaPosto",
    "OcupacaoSemanalProfissional",
    "OpcaoComCenario",
    "OpcaoEncaixe",
    "RelatorioOcupacaoDoDia",
    "SlotDisponivel",
    "buscar_alternativas",
    "buscar_melhor_encaixe",
    "construir_carga_profissionais",
    "construir_ocupacao_semanal_profissional",
    "construir_relatorio_ocupacao_do_dia",
    "listar_disponibilidade",
    "sugerir_realocacao",
]
