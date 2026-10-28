"""Tool `pacientes_por_plano`: pacientes de cada plano de saúde, pela cor do texto.

Relatório gerencial sobre a grade semanal vigente (sem datas): quantos
pacientes e atendimentos cada plano tem, com a lista de nomes. A conta é de
`app.engine.pacientes_por_plano`; aqui ficam a leitura da semana (uma chamada à
planilha, o resto vem do cache) e o texto/bloco.

Privacidade: os nomes de pacientes vão só no artifact (o bloco para a tela).
O `content`, que é o que o modelo da OpenAI lê, leva só as contagens, e o log
só o tamanho do resultado.
"""

import logging
import time
from datetime import date
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.ai import blocos_gerais
from app.domain import (
    PLANO_POR_COR,
    FonteNomesColoridos,
    NomeColorido,
    ScheduleDataSource,
    dias_da_semana_de,
)
from app.engine.pacientes_por_plano import agrupar_pacientes_por_plano

logger = logging.getLogger(__name__)


def executar_pacientes_por_plano(
    fonte: ScheduleDataSource, data_referencia: date
) -> tuple[str, dict[str, Any] | None]:
    """Texto (só contagens) e bloco (já em JSON, com os nomes) da semana de hoje."""
    if not isinstance(fonte, FonteNomesColoridos):
        return "Este relatório precisa da cor do texto da planilha, que a fonte atual não lê.", None

    comeco = time.perf_counter()
    nomes: list[NomeColorido] = []
    nao_lidos: list[date] = []
    ultimo_erro: Exception | None = None
    semana = dias_da_semana_de(data_referencia)
    for dia in semana:
        try:
            nomes.extend(fonte.listar_nomes_coloridos(dia))
        except Exception as erro:
            ultimo_erro = erro
            nao_lidos.append(dia)
            # Só o tipo do erro: a mensagem pode repetir dados da planilha.
            logger.warning(
                "Falha ao ler as cores de %s para pacientes_por_plano: %s",
                dia.isoformat(),
                type(erro).__name__,
            )
    if ultimo_erro is not None and len(nao_lidos) == len(semana):
        raise ultimo_erro

    resultado = agrupar_pacientes_por_plano(nomes)
    avisos = blocos_gerais.avisos_pacientes_por_plano(resultado)
    texto, bloco = blocos_gerais.pacientes_por_plano(resultado, avisos, nao_lidos)
    # Só contagens: nenhum nome vai para o log.
    logger.info(
        "pacientes_por_plano: %d paciente(s) em %d plano(s), %d dia(s) não lido(s), em %.2fs",
        resultado.pacientes,
        len(resultado.grupos),
        len(nao_lidos),
        time.perf_counter() - comeco,
    )
    return texto, bloco.model_dump(mode="json")


def criar_tool_pacientes_por_plano(fonte: ScheduleDataSource, data_referencia: date) -> BaseTool:
    """`pacientes_por_plano`, fechada sobre `fonte` e a data de hoje."""
    planos = ", ".join(faixa.rotulo for faixa in PLANO_POR_COR)

    @tool("pacientes_por_plano", response_format="content_and_artifact")
    def pacientes_por_plano_tool() -> tuple[str, dict[str, Any] | None]:
        """Pacientes por plano de saúde."""
        try:
            return executar_pacientes_por_plano(fonte, data_referencia)
        except Exception as erro:
            # Só o tipo: a mensagem pode repetir dados da planilha.
            logger.warning("Falha ao gerar pacientes_por_plano: %s", type(erro).__name__)
            return (
                f"Erro ao gerar o relatório ({type(erro).__name__}). Tente de novo em instantes."
            ), None

    # A descrição cita os planos de `PLANO_POR_COR`, para acompanhar a configuração.
    pacientes_por_plano_tool.description = (
        f"Relatório gerencial de pacientes por plano de saúde ({planos}), lido pela cor do "
        "texto do nome na grade semanal vigente: quantos pacientes e atendimentos na semana "
        "cada plano tem, com a lista de nomes na tela.\n\n"
        "Use para 'quantos pacientes de cada plano/convênio', 'pacientes da SulAmérica', "
        "'lista de pacientes por plano'. Você recebe só as contagens: NUNCA escreva nomes de "
        "pacientes na resposta. Cobre só quem tem sessão na grade desta semana (sem datas). "
        "Sessões por convênio e autorizações pendentes -> gerar_relatorio (convenios)."
    )
    return pacientes_por_plano_tool
