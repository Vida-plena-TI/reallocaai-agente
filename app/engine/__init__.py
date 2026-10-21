"""Motor de alocação do RealocAI: disponibilidade, ocupação do dia e por
profissional na semana (Fase 4a), pacientes por profissional e encaixe casado
com sugestão de realocação (Fase 4b), e a fotografia do período com o motor de
relatório genérico.
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
from app.engine.fotografia import (
    EstadoSlot,
    FotografiaPeriodo,
    Periodo,
    SlotFotografado,
    fotografar_periodo,
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
from app.engine.relatorio_generico import (
    ConsultaInvalidaError,
    ConsultaRelatorio,
    Dimensao,
    FiltrosRelatorio,
    Metrica,
    ResultadoRelatorio,
    gerar_relatorio,
)

__all__ = [
    "CargaDiaProfissional",
    "CargaDoDia",
    "CargaProfissionais",
    "CargaProfissional",
    "CenarioSugestao",
    "ConsultaInvalidaError",
    "ConsultaRelatorio",
    "Dimensao",
    "EstadoSlot",
    "FiltrosRelatorio",
    "FotografiaPeriodo",
    "ItemEncaixeResolvido",
    "Metrica",
    "OcupacaoAgregada",
    "OcupacaoDiaProfissional",
    "OcupacaoProfissional",
    "OcupacaoSalaPosto",
    "OcupacaoSemanalProfissional",
    "OpcaoComCenario",
    "OpcaoEncaixe",
    "Periodo",
    "RelatorioOcupacaoDoDia",
    "ResultadoRelatorio",
    "SlotDisponivel",
    "SlotFotografado",
    "buscar_alternativas",
    "buscar_melhor_encaixe",
    "construir_carga_profissionais",
    "construir_ocupacao_semanal_profissional",
    "construir_relatorio_ocupacao_do_dia",
    "fotografar_periodo",
    "gerar_relatorio",
    "listar_disponibilidade",
    "sugerir_realocacao",
]
