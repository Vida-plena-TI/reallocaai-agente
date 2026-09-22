"""Testes de `app.reports.template` (Fase 7).

Não comparamos a string inteira do HTML/texto — só que os trechos relevantes
(percentuais, rótulos de sala/especialidade, aviso de abaixo da meta)
aparecem nas duas versões geradas.
"""

from datetime import date, time

from app.data_sources.base import EntradaGrade
from app.domain import Atendimento, Especialidade, Profissional, Slot
from app.engine.ocupacao import RelatorioOcupacaoDoDia, construir_relatorio_ocupacao_do_dia
from app.reports.template import renderizar_relatorio
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
    assert "50%" in corpo_html
    assert "50%" in corpo_texto
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
