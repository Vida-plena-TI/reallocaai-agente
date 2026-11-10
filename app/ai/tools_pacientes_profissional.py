"""Tool `listar_pacientes_por_profissional`: os NOMES dos pacientes de cada profissional.

Relatório gerencial sobre a grade semanal vigente (sem datas): para cada
profissional, quais pacientes têm sessão na semana, em que dias e quantas
sessões. A conta é de `app.engine.pacientes_por_profissional_nomes`; aqui ficam a
leitura da semana (uma fotografia, cada dia lido uma vez), os filtros e o
texto/bloco.

Privacidade, como em `pacientes_por_plano`: os nomes de pacientes vão só no
artifact (o bloco para a tela). O `content`, que é o que o modelo lê, leva só
as contagens, e o log só o tamanho do resultado. Erros só pelo tipo: a
mensagem pode repetir dados da planilha.
"""

import logging
import time
from datetime import date
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, create_model

from app.ai import blocos_gerais
from app.ai.parametros_catalogo import (
    como_especialidade,
    dias_validos,
    especialidades_validas,
    mensagem_de_parametro_invalido,
    tipo_dia,
    tipo_especialidade,
)
from app.ai.servico_agenda import (
    ProfissionalAmbiguo,
    ProfissionalNaoEncontrado,
    resolver_profissional,
)
from app.ai.textos_profissional import texto_ambiguo
from app.domain import Especialidade, ScheduleDataSource, dias_da_semana_de
from app.domain.catalogo_agenda import NOMES_DOS_DIAS, CatalogoAgenda
from app.domain.formatacao import rotulo_enum
from app.engine.fotografia import fotografar_periodo
from app.engine.pacientes_por_profissional_nomes import listar_pacientes_por_profissional

logger = logging.getLogger(__name__)

NOME_DA_TOOL = "listar_pacientes_por_profissional"


class ArgsListarPacientesPorProfissional(BaseModel):
    profissionais: list[str] | None = Field(
        default=None,
        description=(
            "Profissionais citadas, com o nome como foi dito (apelido e grafia diferente "
            "valem), só quando a pergunta citar alguma. Em branco: todas."
        ),
    )
    especialidade: str | None = Field(
        default=None,
        description=(
            "Só desta especialidade ('fono', 'psico', 'TO'), quando a pergunta citar uma."
        ),
    )
    dias_semana: list[str] | None = Field(
        default=None,
        description="Só estes dias da semana, quando a pergunta citar algum. Em branco: a semana.",
    )


def executar_listar_pacientes_por_profissional(
    fonte: ScheduleDataSource,
    data_referencia: date,
    profissionais: list[str] | None = None,
    especialidade: Especialidade | None = None,
    dias_semana: list[str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Texto (só contagens) e bloco (já em JSON, com os nomes) da semana de hoje."""
    comeco = time.perf_counter()
    semana = dias_da_semana_de(data_referencia)
    foto = fotografar_periodo(fonte, semana[0], semana[-1])

    ids: list[str] | None = None
    if profissionais:
        ids = []
        for citado in profissionais:
            localizacao = resolver_profissional(foto.profissionais, citado)
            if isinstance(localizacao, ProfissionalAmbiguo):
                return texto_ambiguo(citado, localizacao), None
            if isinstance(localizacao, ProfissionalNaoEncontrado):
                disponiveis = ", ".join(localizacao.nomes_disponiveis) or "nenhuma"
                return (
                    f"Não há profissional com o nome '{citado}' na grade desta semana. "
                    f"Profissionais da semana: {disponiveis}."
                ), None
            ids.append(localizacao.profissional_id)

    pedidos = set(dias_semana or [])
    dias = [dia for dia in semana if not pedidos or NOMES_DOS_DIAS[dia.weekday()] in pedidos]
    resultado = listar_pacientes_por_profissional(foto, dias, ids, especialidade)
    logger.info(
        "%s: %d paciente(s) distinto(s) em %d profissional(is), %d linha(s), "
        "%d dia(s) não lido(s), em %.2fs",
        NOME_DA_TOOL,
        resultado.pacientes,
        len(resultado.grupos),
        sum(len(grupo.pacientes) for grupo in resultado.grupos),
        len(resultado.dias_com_falha),
        time.perf_counter() - comeco,
    )
    if not resultado.grupos:
        filtro = f" de {rotulo_enum(especialidade.value)}" if especialidade else ""
        return (
            f"Nenhuma profissional{filtro} tem paciente na grade desta semana com esses filtros.",
            None,
        )
    avisos = blocos_gerais.avisos_pacientes_por_profissional_nomes(
        resultado, dias if pedidos else None
    )
    texto, bloco = blocos_gerais.pacientes_por_profissional_nomes(resultado, avisos)
    return texto, bloco.model_dump(mode="json")


def criar_tool_listar_pacientes_por_profissional(
    fonte: ScheduleDataSource, data_referencia: date, catalogo: CatalogoAgenda | None
) -> BaseTool:
    """`listar_pacientes_por_profissional`, com especialidade e dias do catálogo da semana."""
    validas = especialidades_validas(catalogo)
    dias = dias_validos(catalogo)
    campos = ArgsListarPacientesPorProfissional.model_fields
    args = create_model(
        "ArgsListarPacientesPorProfissional",
        __base__=ArgsListarPacientesPorProfissional,
        especialidade=(
            tipo_especialidade(validas) | None,
            Field(default=None, description=campos["especialidade"].description),
        ),
        dias_semana=(
            list[tipo_dia(dias)] | None,  # type: ignore[misc]
            Field(default=None, description=campos["dias_semana"].description),
        ),
    )

    @tool(NOME_DA_TOOL, args_schema=args, response_format="content_and_artifact")
    def listar_pacientes_por_profissional_tool(
        profissionais: list[str] | None = None,
        especialidade: Any = None,
        dias_semana: list[Any] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """NOMES dos pacientes que cada profissional atende na semana, com os dias e o
        número de atendimentos, na tela.

        Use SÓ quando pedirem os nomes ou a lista de pacientes por profissional
        ("quais pacientes a Bia atende?", "lista de pacientes de cada profissional",
        "quem a fono atende na terça?"). Para QUANTOS pacientes, use
        `consultar_pacientes_por_profissional`. Você recebe só as contagens: NUNCA
        escreva nomes de pacientes na resposta. Cobre só quem tem sessão na grade
        semanal vigente (sem datas: não cite datas).
        """
        try:
            return executar_listar_pacientes_por_profissional(
                fonte,
                data_referencia,
                profissionais,
                como_especialidade(especialidade) if especialidade is not None else None,
                [str(dia) for dia in dias_semana] if dias_semana else None,
            )
        except Exception as erro:
            # Só o tipo: a mensagem pode repetir dados da planilha.
            logger.warning("Falha ao gerar %s: %s", NOME_DA_TOOL, type(erro).__name__)
            return (
                f"Erro ao gerar o relatório ({type(erro).__name__}). Tente de novo em instantes."
            ), None

    listar_pacientes_por_profissional_tool.handle_validation_error = mensagem_de_parametro_invalido(
        validas, dias
    )
    return listar_pacientes_por_profissional_tool
