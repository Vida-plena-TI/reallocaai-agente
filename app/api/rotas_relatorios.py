"""Rotas HTTP dos relatórios gerais: o mesmo motor e o mesmo bloco das tools do agente.

`POST /relatorios/gerar` recebe no corpo os parâmetros da tool
`gerar_relatorio` e devolve o mesmo `{texto, bloco}` que ela devolve ao
agente — é a mesma função (`executar_relatorio`). `GET /relatorios/catalogo`
devolve o que `consultar_catalogo` sabe da semana, em texto e em campos.

O nome de paciente (`agenda_paciente`) só entra pelo corpo do POST: nunca pela
URL, que vai para o access log. Nada do corpo é registrado em log — só o tipo
do relatório e, numa falha, o tipo do erro. Exportação em Excel continua no
backend do chatbot: aqui só há o bloco estruturado.
"""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.ai.relatorios import BlocoRelatorio
from app.ai.tools_relatorios import (
    DESCRICAO_RELATORIOS,
    ArgsGerarRelatorio,
    ParametroInvalidoError,
    executar_relatorio,
    texto_catalogo,
)
from app.api.dependencies import obter_data_de_hoje, obter_fonte, validar_api_key
from app.api.schemas import (
    CatalogoResponse,
    GerarRelatorioRequest,
    ItemCatalogoResponse,
    RelatorioResponse,
)
from app.domain import ScheduleDataSource, dias_da_semana_de
from app.engine.fotografia import fotografar_periodo
from app.engine.relatorio_generico import (
    DEFINICOES_METRICAS,
    ConsultaInvalidaError,
    Dimensao,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/relatorios", dependencies=[Depends(validar_api_key)])

_MENSAGEM_FALHA_LEITURA = "erro ao ler a agenda, tente novamente"


@router.get(
    "/catalogo",
    response_model=CatalogoResponse,
    summary="Mapa da clínica na semana e o que cada relatório e métrica significa",
    description=(
        "Profissionais por especialidade (dias e salas), salas e postos, convênios por cor, "
        "dias com agenda, relatórios prontos e métricas. Não traz pacientes. Omita `data` "
        "para a semana de hoje (segunda a sábado)."
    ),
)
def obter_catalogo(
    fonte: Annotated[ScheduleDataSource, Depends(obter_fonte)],
    hoje: Annotated[date, Depends(obter_data_de_hoje)],
    data: Annotated[date | None, Query(description="Qualquer dia da semana desejada.")] = None,
) -> CatalogoResponse:
    semana = dias_da_semana_de(data or hoje)
    try:
        foto = fotografar_periodo(fonte, semana[0], semana[-1])
    except Exception as erro:
        logger.warning("Falha ao montar o catálogo: %s", type(erro).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=_MENSAGEM_FALHA_LEITURA
        ) from erro
    return CatalogoResponse(
        semana_inicio=semana[0],
        semana_fim=semana[-1],
        texto=texto_catalogo(foto),
        relatorios=[
            ItemCatalogoResponse(nome=nome, descricao=descricao)
            for nome, descricao in DESCRICAO_RELATORIOS.items()
        ],
        metricas=[
            ItemCatalogoResponse(nome=metrica.value, descricao=descricao)
            for metrica, descricao in DEFINICOES_METRICAS.items()
        ],
        dimensoes=[dimensao.value for dimensao in Dimensao],
        parcial=foto.parcial,
    )


@router.post(
    "/gerar",
    response_model=RelatorioResponse,
    summary="Gera um relatório geral (o mesmo bloco da tool gerar_relatorio)",
    description=(
        "Corpo com os parâmetros da tool `gerar_relatorio`: `relatorio` (um relatório pronto ou "
        "`personalizado`), período (`inicio`/`fim`; sem os dois, a semana de hoje; só `inicio`, "
        "um dia; até 62 dias), filtros e, só em `agenda_paciente`, o nome exato do `paciente`. "
        "Domingo não tem agenda e um dia que não pôde ser lido deixa o resultado parcial, com "
        "aviso no bloco. Parâmetros sem sentido devolvem 422 com a explicação."
    ),
)
def gerar_relatorio(
    corpo: GerarRelatorioRequest,
    fonte: Annotated[ScheduleDataSource, Depends(obter_fonte)],
    hoje: Annotated[date, Depends(obter_data_de_hoje)],
) -> RelatorioResponse:
    args = ArgsGerarRelatorio.model_validate(corpo.model_dump())
    try:
        texto, bloco = executar_relatorio(fonte, hoje, args)
    except (ParametroInvalidoError, ConsultaInvalidaError) as erro:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(erro)
        ) from erro
    except Exception as erro:
        # Só o tipo do relatório e do erro: nada do corpo nem da planilha.
        logger.warning("Falha ao gerar relatório %s: %s", args.relatorio, type(erro).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=_MENSAGEM_FALHA_LEITURA
        ) from erro
    return RelatorioResponse(
        texto=texto, bloco=BlocoRelatorio.model_validate(bloco) if bloco is not None else None
    )
