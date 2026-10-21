"""Testes do motor de relatório genérico sobre a semana fictícia de `agenda_clinica`."""

from datetime import time

import pytest

from app.domain import Especialidade
from app.engine import (
    ConsultaInvalidaError,
    ConsultaRelatorio,
    Dimensao,
    FiltrosRelatorio,
    FotografiaPeriodo,
    Metrica,
    Periodo,
    fotografar_periodo,
    gerar_relatorio,
)
from app.engine.relatorio_generico import AVISO_ACIMA_DE_CEM, rotulo_convenio
from tests.support.agenda_clinica import ANA, BETO, DIVA, fonte_clinica
from tests.support.agenda_semanal import (
    LUCIANA,
    QUARTA,
    QUINTA,
    SABADO,
    SEGUNDA,
    TERCA,
    atendimentos,
    grade,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource


@pytest.fixture(scope="module")
def foto() -> FotografiaPeriodo:
    return fotografar_periodo(fonte_clinica(), SEGUNDA, SABADO)


def _filtro(**campos: object) -> FiltrosRelatorio:
    return FiltrosRelatorio.model_validate(campos)


def test_ocupacao_do_periodo_e_ponderada_e_nao_media_dos_dias(foto: FotografiaPeriodo) -> None:
    """Diva: quinta 4/10 (40%), sábado 2/8 (25%, duas janelas FECHADO)."""
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.DIA,),
            metricas=(Metrica.OCUPACAO, Metrica.SLOTS_ESCALADOS, Metrica.SLOTS_OCUPADOS),
            filtros=_filtro(profissional_ids={DIVA.id}),
        ),
    )

    assert [linha.valores[Metrica.OCUPACAO] for linha in resultado.linhas] == [0.4, 0.25]
    assert resultado.total[Metrica.OCUPACAO] == 6 / 18
    assert resultado.total[Metrica.OCUPACAO] != (0.4 + 0.25) / 2
    assert resultado.meta == 0.8 and resultado.total_abaixo_da_meta is True


def test_janelas_continuas_nao_atravessam_a_pausa(foto: FotografiaPeriodo) -> None:
    """Beto na quarta: livre de manhã (10), 13:00-14:00 (2) e 15:00-18:00 (6)."""
    filtro = _filtro(profissional_ids={BETO.id}, dias_semana={QUARTA.weekday()})

    def janelas(slots: int) -> int | float:
        return gerar_relatorio(
            foto,
            ConsultaRelatorio(
                metricas=(Metrica.JANELAS_LIVRES,), filtros=filtro, slots_por_janela=slots
            ),
        ).total[Metrica.JANELAS_LIVRES]

    assert janelas(1) == 18
    assert janelas(2) == 5 + 1 + 3
    # Com a pausa quebrando a sequência: 3 + 0 + 2 (sem ela seriam 6).
    assert janelas(3) == 5


def test_filtros_por_especialidade_dia_da_semana_e_periodo(foto: FotografiaPeriodo) -> None:
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.PROFISSIONAL,),
            metricas=(Metrica.SLOTS_ESCALADOS, Metrica.SLOTS_OCUPADOS),
            filtros=_filtro(
                especialidades={Especialidade.PSICOLOGIA},
                dias_semana={QUINTA.weekday()},
                periodos={Periodo.TARDE},
            ),
        ),
    )

    # Ana só atende de manhã: na quinta à tarde de psicologia só há a Diva.
    assert [linha.chaves[Dimensao.PROFISSIONAL].rotulo for linha in resultado.linhas] == [
        "Diva Rocha"
    ]
    assert resultado.total == {Metrica.SLOTS_ESCALADOS: 10, Metrica.SLOTS_OCUPADOS: 4}


def test_ordenacao_por_metrica_e_limite_preservam_o_total(foto: FotografiaPeriodo) -> None:
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.PROFISSIONAL,),
            metricas=(Metrica.OCUPACAO, Metrica.SLOTS_OCUPADOS, Metrica.SLOTS_ESCALADOS),
            ordenar_por=Metrica.OCUPACAO,
            limite=2,
        ),
    )
    todos = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.PROFISSIONAL,),
            metricas=(Metrica.OCUPACAO, Metrica.SLOTS_OCUPADOS, Metrica.SLOTS_ESCALADOS),
        ),
    )

    ocupacoes = sorted(
        (float(linha.valores[Metrica.OCUPACAO]) for linha in todos.linhas), reverse=True
    )
    assert [linha.valores[Metrica.OCUPACAO] for linha in resultado.linhas] == ocupacoes[:2]
    assert resultado.linhas_omitidas == 2
    assert resultado.total == todos.total


def test_ordenacao_crescente_e_por_dimensao(foto: FotografiaPeriodo) -> None:
    crescente = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.SALA,),
            metricas=(Metrica.OCUPACAO,),
            ordenar_por=Metrica.OCUPACAO,
            decrescente=False,
        ),
    )
    valores = [float(linha.valores[Metrica.OCUPACAO]) for linha in crescente.linhas]
    assert valores == sorted(valores)

    por_sala = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.SALA,),
            metricas=(Metrica.SLOTS_ESCALADOS,),
            ordenar_por=Dimensao.SALA,
            decrescente=True,
        ),
    )
    # Ordem natural: Sala 10 depois de Sala 2 (aqui invertida).
    assert [linha.chaves[Dimensao.SALA].rotulo for linha in por_sala.linhas] == [
        "Sala 10",
        "Sala 2",
        "Sala 1",
    ]


def test_pacientes_distintos_do_total_nao_sao_a_soma_das_linhas(foto: FotografiaPeriodo) -> None:
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(dimensoes=(Dimensao.DIA,), metricas=(Metrica.PACIENTES_DISTINTOS,)),
    )

    soma = sum(int(linha.valores[Metrica.PACIENTES_DISTINTOS]) for linha in resultado.linhas)
    assert resultado.total[Metrica.PACIENTES_DISTINTOS] == 10
    assert soma > 10


def test_sessao_em_grupo_conta_um_slot_e_dois_pacientes(foto: FotografiaPeriodo) -> None:
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            metricas=(Metrica.SLOTS_OCUPADOS, Metrica.SESSOES, Metrica.PACIENTES_DISTINTOS),
            filtros=_filtro(profissional_ids={ANA.id}, dias_semana={TERCA.weekday()}),
        ),
    )

    assert resultado.total == {
        Metrica.SLOTS_OCUPADOS: 4,
        Metrica.SESSOES: 2,
        Metrica.PACIENTES_DISTINTOS: 3,
    }


def test_horas_e_aguardando_autorizacao(foto: FotografiaPeriodo) -> None:
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            metricas=(
                Metrica.HORAS_OCUPADAS,
                Metrica.SLOTS_OCUPADOS,
                Metrica.AGUARDANDO_AUTORIZACAO,
            ),
        ),
    )

    assert resultado.total[Metrica.HORAS_OCUPADAS] == resultado.total[Metrica.SLOTS_OCUPADOS] / 2
    assert resultado.total[Metrica.AGUARDANDO_AUTORIZACAO] == 2


def test_convenio_agrupa_so_slots_ocupados(foto: FotografiaPeriodo) -> None:
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.CONVENIO,), metricas=(Metrica.SESSOES, Metrica.SLOTS_OCUPADOS)
        ),
    )
    rotulos = [linha.chaves[Dimensao.CONVENIO].rotulo for linha in resultado.linhas]

    assert "Sem paciente" not in rotulos
    assert set(rotulos) == {
        "Klini Saude",
        "Klini Saude + Unimed",
        "Não informado",
        "Particular",
        "Sulamerica",
        "Unimed",
    }
    assert rotulo_convenio("unimed+nao_informado") == "Não informado + Unimed"


def test_slots_fechados_contam_postos_sem_escala(foto: FotografiaPeriodo) -> None:
    resultado = gerar_relatorio(
        foto,
        ConsultaRelatorio(
            dimensoes=(Dimensao.SALA,),
            metricas=(Metrica.SLOTS_FECHADOS,),
            filtros=_filtro(dias_semana={SABADO.weekday()}),
        ),
    )

    # Sábado: Sala 10 só de manhã, sem 07:00 e 07:30 (2) e a tarde inteira (10).
    assert resultado.total[Metrica.SLOTS_FECHADOS] == 12


@pytest.mark.parametrize(
    ("consulta", "trecho"),
    [
        (
            ConsultaRelatorio(dimensoes=(Dimensao.CONVENIO,), metricas=(Metrica.OCUPACAO,)),
            "não combina com métricas de oferta",
        ),
        (
            ConsultaRelatorio(
                metricas=(Metrica.VAGAS_LIVRES,),
                filtros=FiltrosRelatorio(convenios=frozenset({"unimed"})),
            ),
            "não combina com métricas de oferta",
        ),
        (
            ConsultaRelatorio(metricas=(Metrica.SESSOES,), ordenar_por=Metrica.OCUPACAO),
            "precisa estar entre as métricas",
        ),
        (
            ConsultaRelatorio(metricas=(Metrica.SESSOES,), ordenar_por=Dimensao.DIA),
            "precisa estar entre as dimensões",
        ),
        (
            ConsultaRelatorio(metricas=(Metrica.JANELAS_LIVRES,), slots_por_janela=11),
            "longa demais",
        ),
        (
            ConsultaRelatorio(metricas=(Metrica.SESSOES, Metrica.SESSOES)),
            "repetida",
        ),
        (ConsultaRelatorio(metricas=()), "ao menos uma métrica"),
    ],
)
def test_consulta_invalida_explica_o_problema(
    foto: FotografiaPeriodo, consulta: ConsultaRelatorio, trecho: str
) -> None:
    with pytest.raises(ConsultaInvalidaError, match=trecho):
        gerar_relatorio(foto, consulta)


def test_sem_meta_quando_nao_ha_metrica_de_ocupacao(foto: FotografiaPeriodo) -> None:
    resultado = gerar_relatorio(foto, ConsultaRelatorio(metricas=(Metrica.SESSOES,)))

    assert resultado.meta is None and resultado.total_abaixo_da_meta is None
    assert Metrica.SLOTS_PARA_META not in resultado.total


def test_quebra_por_sala_deixa_de_fora_o_ocupado_fora_da_grade() -> None:
    fonte = FakeScheduleDataSource(
        profissionais={SEGUNDA: [LUCIANA]},
        grade={SEGUNDA: grade(SEGUNDA, [time(8, 0)])},
        atendimentos={SEGUNDA: atendimentos(SEGUNDA, [time(8, 0), time(9, 0)])},
    )
    foto = fotografar_periodo(fonte, SEGUNDA, SEGUNDA)

    por_sala = gerar_relatorio(
        foto, ConsultaRelatorio(dimensoes=(Dimensao.SALA,), metricas=(Metrica.SLOTS_OCUPADOS,))
    )
    por_profissional = gerar_relatorio(
        foto,
        ConsultaRelatorio(dimensoes=(Dimensao.PROFISSIONAL,), metricas=(Metrica.OCUPACAO,)),
    )

    assert por_sala.total[Metrica.SLOTS_OCUPADOS] == 1
    assert "fora da grade" in por_sala.avisos[0]
    # Por profissional, conta (como na engine antiga) e passa de 100%.
    assert por_profissional.total[Metrica.OCUPACAO] == 2.0
    assert AVISO_ACIMA_DE_CEM in por_profissional.avisos
    assert por_profissional.linhas[0].abaixo_da_meta is False


def test_resultado_parcial_herda_os_dias_nao_lidos() -> None:
    foto = fotografar_periodo(fonte_clinica({TERCA}), SEGUNDA, SABADO)

    resultado = gerar_relatorio(foto, ConsultaRelatorio(metricas=(Metrica.VAGAS_LIVRES,)))

    assert resultado.parcial and resultado.dias_nao_lidos == [TERCA]
