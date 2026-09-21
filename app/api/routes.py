"""Rotas HTTP de consulta direta à agenda (Fase 6a).

Só leitura: nenhum endpoint aqui altera a agenda. O endpoint de conversa com
o agente é a Fase 6b, ainda não implementada. Todas as rotas deste router
exigem `X-API-Key` válida (ver `validar_api_key`); a única rota pública da
aplicação é `/health`, definida em `app.main`.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends

from app.ai.servico_agenda import consultar_disponibilidade_do_dia, consultar_ocupacao_do_dia
from app.api.dependencies import obter_fonte, validar_api_key
from app.api.schemas import OcupacaoItemResponse, OcupacaoResponse, SlotDisponivelResponse
from app.data_sources.base import ScheduleDataSource
from app.domain import Especialidade
from app.engine.ocupacao import OcupacaoAgregada, RelatorioOcupacaoDoDia

router = APIRouter(dependencies=[Depends(validar_api_key)])


def _rotulo_especialidade(especialidade: Especialidade) -> str:
    """`terapia_ocupacional` -> `Terapia Ocupacional`."""
    return especialidade.value.replace("_", " ").title()


def _item_ocupacao(rotulo: str, agregada: OcupacaoAgregada) -> OcupacaoItemResponse:
    return OcupacaoItemResponse(
        rotulo=rotulo,
        slots_escalados=agregada.slots_escalados,
        slots_ocupados=agregada.slots_ocupados,
        percentual=agregada.percentual,
        abaixo_da_meta=agregada.abaixo_da_meta,
    )


def _construir_ocupacao_response(
    fonte: ScheduleDataSource, data: date, relatorio: RelatorioOcupacaoDoDia
) -> OcupacaoResponse:
    """Resolve sala e especialidade para rótulos legíveis, ordenados de forma estável."""
    nomes_das_salas = {sala.id: sala.nome for sala in fonte.listar_salas(data)}
    por_sala = [
        _item_ocupacao(nomes_das_salas.get(sala_id, sala_id), agregada)
        for sala_id, agregada in sorted(relatorio.por_sala().items())
    ]
    por_especialidade = [
        _item_ocupacao(_rotulo_especialidade(especialidade), agregada)
        for especialidade, agregada in sorted(
            relatorio.por_especialidade().items(), key=lambda par: par[0].value
        )
    ]
    return OcupacaoResponse(
        data=data.isoformat(), por_sala=por_sala, por_especialidade=por_especialidade
    )


@router.get("/agenda/disponibilidade", response_model=list[SlotDisponivelResponse])
def obter_disponibilidade(
    data: date,
    fonte: Annotated[ScheduleDataSource, Depends(obter_fonte)],
    especialidade: Especialidade | None = None,
    profissional_id: str | None = None,
    sala_id: str | None = None,
) -> list[SlotDisponivelResponse]:
    """Slots livres do dia, com filtros opcionais de especialidade, profissional e sala."""
    slots = consultar_disponibilidade_do_dia(
        fonte,
        data,
        especialidade=especialidade,
        profissional_id=profissional_id,
        sala_id=sala_id,
    )
    nomes_das_salas = {sala.id: sala.nome for sala in fonte.listar_salas(data)}
    nomes_dos_profissionais = {
        profissional.id: profissional.nome for profissional in fonte.listar_profissionais(data)
    }
    return [
        SlotDisponivelResponse(
            horario=disponivel.slot.hora_inicio.strftime("%H:%M"),
            sala=nomes_das_salas.get(disponivel.sala_id, disponivel.sala_id),
            profissional=nomes_dos_profissionais.get(
                disponivel.profissional_id, disponivel.profissional_id
            ),
            especialidade=_rotulo_especialidade(disponivel.especialidade),
        )
        for disponivel in slots
    ]


@router.get("/agenda/ocupacao", response_model=OcupacaoResponse)
def obter_ocupacao(
    data: date, fonte: Annotated[ScheduleDataSource, Depends(obter_fonte)]
) -> OcupacaoResponse:
    """Ocupação do dia, agregada por sala e por especialidade."""
    relatorio = consultar_ocupacao_do_dia(fonte, data)
    return _construir_ocupacao_response(fonte, data, relatorio)
