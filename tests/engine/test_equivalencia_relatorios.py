"""O motor genérico, no recorte das engines antigas, devolve exatamente os mesmos números."""

from datetime import date

import pytest

from app.engine import (
    ConsultaRelatorio,
    Dimensao,
    FiltrosRelatorio,
    FotografiaPeriodo,
    Metrica,
    construir_relatorio_ocupacao_do_dia,
    fotografar_periodo,
    gerar_relatorio,
    listar_disponibilidade,
)
from app.engine.relatorio_generico import ResultadoRelatorio
from tests.support.agenda_clinica import ANA, BETO, CAU, DIVA, fonte_clinica
from tests.support.agenda_semanal import QUARTA, SABADO, SEGUNDA, SEMANA
from tests.support.fake_schedule_data_source import FakeScheduleDataSource
from tests.support.visoes import construir_carga, ocupacao_semanal

PROFISSIONAIS = [ANA.id, BETO.id, CAU.id, DIVA.id]
ESCALADOS_OCUPADOS = (Metrica.SLOTS_ESCALADOS, Metrica.SLOTS_OCUPADOS)


@pytest.fixture(params=[set(), {QUARTA}], ids=["completa", "quarta-ilegivel"])
def fonte(request: pytest.FixtureRequest) -> FakeScheduleDataSource:
    dias_com_falha: set[date] = request.param
    return fonte_clinica(dias_com_falha)


@pytest.fixture
def foto(fonte: FakeScheduleDataSource) -> FotografiaPeriodo:
    return fotografar_periodo(fonte, SEGUNDA, SABADO)


def _por_chave(
    resultado: ResultadoRelatorio, dimensoes: tuple[Dimensao, ...]
) -> dict[tuple[str, ...], tuple[int | float, ...]]:
    return {
        tuple(linha.chaves[d].id for d in dimensoes): tuple(linha.valores.values())
        for linha in resultado.linhas
    }


def test_ocupacao_do_dia(foto: FotografiaPeriodo, fonte: FakeScheduleDataSource) -> None:
    for dia in foto.dias_lidos:
        relatorio = construir_relatorio_ocupacao_do_dia(fonte, dia)
        filtro = FiltrosRelatorio(dias_semana=frozenset({dia.weekday()}))

        for dimensao, esperado in [
            (
                Dimensao.SALA,
                {
                    (sala,): (a.slots_escalados, a.slots_ocupados)
                    for sala, a in relatorio.por_sala().items()
                },
            ),
            (
                Dimensao.ESPECIALIDADE,
                {
                    (e.value,): (a.slots_escalados, a.slots_ocupados)
                    for e, a in relatorio.por_especialidade().items()
                },
            ),
            (
                Dimensao.PROFISSIONAL,
                {
                    (p.profissional_id,): (p.slots_escalados, p.slots_ocupados)
                    for p in relatorio.por_profissional
                },
            ),
        ]:
            resultado = gerar_relatorio(
                foto,
                ConsultaRelatorio(
                    dimensoes=(dimensao,), metricas=ESCALADOS_OCUPADOS, filtros=filtro
                ),
            )
            assert _por_chave(resultado, (dimensao,)) == esperado


@pytest.mark.parametrize("profissional_id", PROFISSIONAIS)
def test_ocupacao_semanal_profissional(
    foto: FotografiaPeriodo, fonte: FakeScheduleDataSource, profissional_id: str
) -> None:
    semana = ocupacao_semanal(fonte, profissional_id, SEGUNDA)
    filtro = FiltrosRelatorio(profissional_ids=frozenset({profissional_id}))
    metricas = (
        Metrica.SLOTS_ESCALADOS,
        Metrica.SLOTS_OCUPADOS,
        Metrica.VAGAS_LIVRES,
        Metrica.SLOTS_PARA_META,
        Metrica.OCUPACAO,
    )

    por_dia = gerar_relatorio(
        foto, ConsultaRelatorio(dimensoes=(Dimensao.DIA,), metricas=metricas, filtros=filtro)
    )

    assert _por_chave(por_dia, (Dimensao.DIA,)) == {
        (d.data.isoformat(),): (
            d.slots_escalados,
            d.slots_ocupados,
            d.slots_livres,
            d.slots_para_meta,
            d.percentual,
        )
        for d in semana.dias
    }
    assert [linha.abaixo_da_meta for linha in por_dia.linhas] == [
        d.abaixo_da_meta for d in semana.dias
    ]
    # Total da semana: ponderado, exatamente o mesmo float.
    assert por_dia.total[Metrica.OCUPACAO] == semana.percentual
    assert por_dia.total[Metrica.SLOTS_PARA_META] == semana.slots_para_meta
    assert por_dia.total_abaixo_da_meta is semana.abaixo_da_meta
    assert por_dia.dias_nao_lidos == semana.dias_com_falha

    turnos = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.DIA, Dimensao.PERIODO), metricas=ESCALADOS_OCUPADOS, filtros=filtro
        ),
    )
    esperado_turnos: dict[tuple[str, ...], tuple[int | float, ...]] = {}
    for d in semana.dias:
        if d.manha_escalados or d.manha_ocupados:
            esperado_turnos[(d.data.isoformat(), "manha")] = (d.manha_escalados, d.manha_ocupados)
        if d.tarde_escalados or d.tarde_ocupados:
            esperado_turnos[(d.data.isoformat(), "tarde")] = (d.tarde_escalados, d.tarde_ocupados)
    assert _por_chave(turnos, (Dimensao.DIA, Dimensao.PERIODO)) == esperado_turnos

    postos = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.DIA, Dimensao.POSTO), metricas=ESCALADOS_OCUPADOS, filtros=filtro
        ),
    )
    assert _por_chave(postos, (Dimensao.DIA, Dimensao.POSTO)) == {
        (d.data.isoformat(), f"{s.sala_id}#{s.indice_posto + 1}"): (
            s.slots_escalados,
            s.slots_ocupados,
        )
        for d in semana.dias
        for s in d.por_sala_posto
    }


def test_pacientes_por_profissional(foto: FotografiaPeriodo, fonte: FakeScheduleDataSource) -> None:
    carga = construir_carga(fonte, SEMANA)

    por_dia = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.PROFISSIONAL, Dimensao.DIA),
            metricas=(Metrica.PACIENTES_DISTINTOS, Metrica.SESSOES, Metrica.SLOTS_OCUPADOS),
        ),
    )
    assert _por_chave(por_dia, (Dimensao.PROFISSIONAL, Dimensao.DIA)) == {
        (c.profissional_id, d.data.isoformat()): (d.pacientes, d.sessoes, d.slots_ocupados)
        for c in carga.profissionais
        for d in c.dias
    }

    semana = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.PROFISSIONAL,), metricas=(Metrica.PACIENTES_DISTINTOS,)
        ),
    )
    assert _por_chave(semana, (Dimensao.PROFISSIONAL,)) == {
        (c.profissional_id,): (c.pacientes_distintos_semana,) for c in carga.profissionais
    }

    clinica = gerar_relatorio(
        foto, ConsultaRelatorio(dimensoes=(Dimensao.DIA,), metricas=(Metrica.PACIENTES_DISTINTOS,))
    )
    assert [linha.valores[Metrica.PACIENTES_DISTINTOS] for linha in clinica.linhas] == [
        d.pacientes_distintos_clinica for d in carga.por_dia
    ]
    assert foto.dias_nao_lidos == carga.dias_com_falha


def test_disponibilidade(foto: FotografiaPeriodo, fonte: FakeScheduleDataSource) -> None:
    for dia in foto.dias_lidos:
        esperado = {
            (s.slot.hora_inicio, s.sala_id, s.indice_posto, s.profissional_id, s.especialidade)
            for s in listar_disponibilidade(fonte, dia)
        }
        livres = {
            (s.hora_inicio, s.sala_id, s.indice_posto, s.profissional_id, s.especialidade)
            for s in foto.slots
            if s.data == dia and s.livre
        }
        assert livres == esperado
        vagas = gerar_relatorio(
            foto,
            ConsultaRelatorio(
                metricas=(Metrica.VAGAS_LIVRES,),
                filtros=FiltrosRelatorio(dias_semana=frozenset({dia.weekday()})),
            ),
        )
        assert vagas.total[Metrica.VAGAS_LIVRES] == len(esperado)
