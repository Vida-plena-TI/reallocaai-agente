"""Testes de `app.reports.template` (Fase 7).

Não comparamos a string inteira do HTML/texto — só que os trechos relevantes
(percentuais, rótulos de sala/especialidade, cabeçalhos, situação frente à
meta, linha de meta e resumo) aparecem nas duas versões geradas.
"""

from datetime import date, time

from app.domain import Atendimento, EntradaGrade, Especialidade, Profissional, Slot
from app.engine.ocupacao import RelatorioOcupacaoDoDia, construir_relatorio_ocupacao_do_dia
from app.reports.template import ESTILO_DESTAQUE, renderizar_relatorio
from tests.support.agenda_semanal import (
    LUCIANA,
    atendimentos,
    grade,
    horas_da_manha,
    horas_da_tarde,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

DIA = date(2026, 9, 8)


def _relatorio_com_sala_abaixo_da_meta() -> RelatorioOcupacaoDoDia:
    fonte = FakeScheduleDataSource(
        grade={
            DIA: [
                EntradaGrade(
                    sala_id="sala-1",
                    profissional_id="prof-1",
                    especialidade=Especialidade.PSICOLOGIA,
                    slot=Slot(data=DIA, hora_inicio=hora),
                    indice_posto=0,
                )
                for hora in (time(8, 0), time(8, 30))
            ]
        },
        profissionais={
            DIA: [Profissional(id="prof-1", nome="Ana", especialidade=Especialidade.PSICOLOGIA)]
        },
        atendimentos={
            DIA: [
                Atendimento(
                    id="at-1",
                    paciente_ids=["pac-1"],
                    profissional_id="prof-1",
                    sala_id="sala-1",
                    especialidade=Especialidade.PSICOLOGIA,
                    slots=[Slot(data=DIA, hora_inicio=time(8, 0))],
                )
            ]
        },
    )
    return construir_relatorio_ocupacao_do_dia(fonte, DIA)


def test_renderizar_relatorio_inclui_percentuais_e_rotulos_no_html_e_no_texto() -> None:
    relatorio = _relatorio_com_sala_abaixo_da_meta()

    assunto, corpo_html, corpo_texto = renderizar_relatorio(relatorio)

    assert "8 de setembro de 2026" in assunto
    assert "Psicologia" in corpo_html
    assert "Psicologia" in corpo_texto
    assert "Sala 1" in corpo_html
    assert "Sala 1" in corpo_texto
    assert "50,0%" in corpo_html
    assert "50,0%" in corpo_texto
    assert "abaixo da meta" in corpo_html.lower()
    assert "abaixo da meta" in corpo_texto.lower()


def test_renderizar_relatorio_sem_escala_no_dia_nao_quebra() -> None:
    relatorio = construir_relatorio_ocupacao_do_dia(FakeScheduleDataSource(), DIA)

    assunto, corpo_html, corpo_texto = renderizar_relatorio(relatorio)

    assert assunto
    assert corpo_html
    assert corpo_texto
    assert "nenhum dado" in corpo_html.lower()
    assert "nenhum dado" in corpo_texto.lower()


def test_renderizar_relatorio_acima_de_cem_por_cento_mostra_o_valor_real() -> None:
    # 20 slots escalados e 21 ocupados: um atendimento fora da grade (outro posto).
    horas = horas_da_manha(DIA) + horas_da_tarde(DIA)
    fonte = FakeScheduleDataSource(
        profissionais={DIA: [LUCIANA]},
        grade={DIA: grade(DIA, horas, sala_id="sala-1")},
        atendimentos={
            DIA: atendimentos(DIA, horas, sala_id="sala-1")
            + atendimentos(DIA, horas[:1], sala_id="sala-1", indice_posto=1)
        },
    )
    relatorio = construir_relatorio_ocupacao_do_dia(fonte, DIA)

    _, corpo_html, corpo_texto = renderizar_relatorio(relatorio)

    assert "105,0%" in corpo_html
    assert "105,0%" in corpo_texto
    # Acima da meta: a situação é "Na meta", sem destaque; o resumo conta zero abaixo dela.
    assert "Abaixo da meta" not in corpo_html
    assert "Sala 1: 105,0% — Na meta" in corpo_texto
    assert ESTILO_DESTAQUE not in corpo_html
    assert "0 de 1 especialidade e 0 de 1 sala abaixo da meta" in corpo_texto


def _relatorio_com_salas(*salas: tuple[str, int]) -> RelatorioOcupacaoDoDia:
    """Uma profissional por sala, com 10 slots escalados e `ocupados` atendimentos."""
    horas = horas_da_manha(DIA)
    grade_do_dia: list[EntradaGrade] = []
    atendimentos_do_dia: list[Atendimento] = []
    profissionais: list[Profissional] = []
    for sala_id, ocupados in salas:
        profissional = Profissional(
            id=f"prof-{sala_id}", nome=f"Prof {sala_id}", especialidade=Especialidade.PSICOLOGIA
        )
        profissionais.append(profissional)
        grade_do_dia += grade(DIA, horas, profissional, sala_id=sala_id)
        atendimentos_do_dia += atendimentos(DIA, horas[:ocupados], profissional, sala_id=sala_id)
    fonte = FakeScheduleDataSource(
        profissionais={DIA: profissionais},
        grade={DIA: grade_do_dia},
        atendimentos={DIA: atendimentos_do_dia},
    )
    return construir_relatorio_ocupacao_do_dia(fonte, DIA)


def test_salas_em_ordem_natural_no_html_e_no_texto() -> None:
    relatorio = _relatorio_com_salas(("sala-10", 9), ("sala-2", 9), ("sala-1", 9), ("sala-12", 9))

    _, corpo_html, corpo_texto = renderizar_relatorio(relatorio)

    ordem = ["Sala 1<", "Sala 2<", "Sala 10<", "Sala 12<"]
    assert sorted(ordem, key=corpo_html.index) == ordem
    ordem_texto = ["- Sala 1:", "- Sala 2:", "- Sala 10:", "- Sala 12:"]
    assert sorted(ordem_texto, key=corpo_texto.index) == ordem_texto


def test_cabecalhos_situacao_meta_e_resumo() -> None:
    relatorio = _relatorio_com_salas(("sala-1", 9), ("sala-2", 5), ("sala-3", 2))

    _, corpo_html, corpo_texto = renderizar_relatorio(relatorio)

    for cabecalho in ("Especialidade", "Sala", "Ocupação", "Situação"):
        assert f">{cabecalho}</th>" in corpo_html
    assert ">Nome</th>" not in corpo_html
    assert "Meta de ocupação: 80%" in corpo_html
    assert "Meta de ocupação: 80%" in corpo_texto
    resumo = "1 de 1 especialidade e 2 de 3 salas abaixo da meta"
    assert resumo in corpo_html
    assert resumo in corpo_texto
    assert "Sala 1: 90,0% — Na meta" in corpo_texto
    assert "Sala 2: 50,0% — Abaixo da meta" in corpo_texto
    assert "Psicologia: 53,3% — Abaixo da meta" in corpo_texto


def test_so_a_linha_abaixo_da_meta_fica_destacada_no_html() -> None:
    relatorio = _relatorio_com_salas(("sala-1", 9), ("sala-2", 5))

    _, corpo_html, _ = renderizar_relatorio(relatorio)

    linhas = corpo_html.split("<tr")
    destacadas = [linha for linha in linhas if ESTILO_DESTAQUE in linha]
    assert [">Sala 2<" in linha for linha in destacadas if ">Sala" in linha] == [True]
    assert any(">Na meta<" in linha for linha in linhas if ">Sala 1<" in linha)
