"""Testes de `app.reports.relatorios_gerais`: validação do pedido e o e-mail renderizado.

Tudo sobre a semana fictícia de `agenda_clinica`; nenhum e-mail é enviado.
"""

from datetime import date, time

import pytest

from app.domain import EntradaGrade, Especialidade, Profissional, Slot
from app.engine.fotografia import FotografiaPeriodo, fotografar_periodo
from app.reports.relatorios_gerais import (
    MAX_DIAS_EMAIL,
    MAX_LINHAS_EMAIL,
    TIPOS_RELATORIO_EMAIL,
    renderizar_relatorios_gerais,
    resolver_periodo,
    validar_tipos,
)
from app.reports.template import ESTILO_DESTAQUE
from tests.support.agenda_clinica import NOMES_PACIENTES, fonte_clinica
from tests.support.agenda_semanal import QUARTA, SABADO, SEGUNDA, SEXTA, TERCA
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

HOJE = QUARTA
DE_PERIODO = [tipo for tipo in TIPOS_RELATORIO_EMAIL if tipo != "ocupacao"]


def _semana() -> FotografiaPeriodo:
    return fotografar_periodo(fonte_clinica(), SEGUNDA, SABADO)


# --- validação ----------------------------------------------------------------


def test_agenda_de_paciente_nunca_vai_por_email() -> None:
    with pytest.raises(ValueError, match="nunca contém nome de paciente"):
        validar_tipos(["vagas_livres", "agenda_paciente"])


@pytest.mark.parametrize(
    ("tipos", "trecho"),
    [([], "ao menos um"), (["vagas"], "Relatório(s) que não vão por e-mail: vagas")],
)
def test_tipos_invalidos(tipos: list[str], trecho: str) -> None:
    with pytest.raises(ValueError, match=trecho.replace("(", r"\(").replace(")", r"\)")):
        validar_tipos(tipos)


def test_tipos_repetidos_ficam_uma_vez_na_ordem_pedida() -> None:
    assert validar_tipos(["salas_ociosas", "ocupacao", "salas_ociosas"]) == [
        "salas_ociosas",
        "ocupacao",
    ]


def test_periodo_padrao_e_o_dia_informado_ou_hoje() -> None:
    assert resolver_periodo(["ocupacao"], None, None, None, HOJE) == (HOJE, HOJE)
    assert resolver_periodo(["ocupacao"], TERCA, None, None, HOJE) == (TERCA, TERCA)
    assert resolver_periodo(["convenios"], None, SEGUNDA, SEXTA, HOJE) == (SEGUNDA, SEXTA)
    assert resolver_periodo(["convenios"], None, SEGUNDA, None, HOJE) == (SEGUNDA, SEGUNDA)


@pytest.mark.parametrize(
    ("tipos", "data", "inicio", "fim", "trecho"),
    [
        (["convenios"], TERCA, SEGUNDA, SEXTA, "não os dois"),
        (["ocupacao", "convenios"], None, SEGUNDA, SEXTA, "ocupacao é de um dia só"),
        (["convenios"], None, SEXTA, SEGUNDA, "anterior ao início"),
        (["convenios"], None, date(2026, 9, 1), date(2026, 9, 15), f"máximo é {MAX_DIAS_EMAIL}"),
    ],
)
def test_periodo_invalido(
    tipos: list[str], data: date | None, inicio: date, fim: date, trecho: str
) -> None:
    with pytest.raises(ValueError, match=trecho):
        resolver_periodo(tipos, data, inicio, fim, HOJE)


def test_quatorze_dias_cabem() -> None:
    assert resolver_periodo(["convenios"], None, date(2026, 9, 1), date(2026, 9, 14), HOJE)


# --- renderização -------------------------------------------------------------


def test_secoes_na_ordem_pedida_e_so_as_pedidas() -> None:
    _, html, texto = renderizar_relatorios_gerais(_semana(), ["salas_ociosas", "vagas_livres"])

    assert texto.index("SALAS OCIOSAS") < texto.index("VAGAS POR PERÍODO E ESPECIALIDADE")
    assert "RANKING" not in texto
    assert "Ranking" not in html
    assert html.index("Salas ociosas") < html.index("Vagas por período e especialidade")


def test_email_nunca_tem_nome_de_paciente() -> None:
    _, html, texto = renderizar_relatorios_gerais(_semana(), DE_PERIODO)

    conteudo = (html + texto).lower()
    assert not [nome for nome in NOMES_PACIENTES if nome.lower() in conteudo]
    assert "ficticio" not in conteudo


def test_assunto_e_titulo_do_periodo() -> None:
    assunto, html, texto = renderizar_relatorios_gerais(_semana(), ["convenios"])

    assert assunto == "RealocAI — Relatórios de 28/09/2026 a 03/10/2026"
    assert texto.startswith("Relatórios — 28/09/2026 a 03/10/2026")
    assert "<h2>Relatórios — 28/09/2026 a 03/10/2026</h2>" in html


def test_ocupacao_de_um_dia_tem_meta_resumo_situacao_e_ordem_natural() -> None:
    foto = fotografar_periodo(fonte_clinica(), QUARTA, QUARTA)

    assunto, html, texto = renderizar_relatorios_gerais(foto, ["ocupacao"])

    assert assunto == "RealocAI — Relatórios de 30 de setembro de 2026"
    assert "Meta de ocupação: 80%" in texto
    assert "especialidades e" in texto
    assert "salas abaixo da meta" in texto
    for cabecalho in ("Especialidade", "Sala", "Situação"):
        assert f">{cabecalho}</th>" in html
    assert texto.index("- Sala 1:") < texto.index("- Sala 2:")


def test_vagas_por_especialidade_e_periodo() -> None:
    _, html, texto = renderizar_relatorios_gerais(_semana(), ["vagas_livres"])

    for cabecalho in ("Especialidade", "Manhã", "Tarde", "Total"):
        assert f">{cabecalho}</th>" in html
    assert "136 vagas de 30 min (68 h) no período" in texto
    assert "Psicologia: Manhã 43 · Tarde 6 · Total 49" in texto


def test_ranking_com_meta_situacao_e_resumo() -> None:
    _, html, texto = renderizar_relatorios_gerais(_semana(), ["ranking_ocupacao"])

    assert "Meta de ocupação: 80%" in texto
    assert "4 de 4 profissionais abaixo da meta" in texto
    assert "1. Diva Rocha: Especialidade Psicologia · Ocupação 33,3%" in texto
    assert ">Situação</th>" in html
    assert ESTILO_DESTAQUE in html


def test_convenios_so_com_contagens() -> None:
    _, _, texto = renderizar_relatorios_gerais(_semana(), ["convenios"])

    assert "22 sessões no período, 2 aguardando autorização" in texto
    assert "Unimed: Sessões 8 · Pacientes 3 · Aguardando autorização 0" in texto
    assert "30/09/2026: Profissional Beto Reis · Sessões aguardando 1" in texto


def test_buracos_e_fragmentacao() -> None:
    _, _, texto = renderizar_relatorios_gerais(_semana(), ["qualidade_agenda"])

    assert "6 buracos entre atendimentos (4 h presas) e 5 vagas isoladas" in texto
    assert "Ana Lima: Especialidade Psicologia · Buracos 3 · Tempo em buracos 2 h" in texto


def test_salas_ociosas_com_cabecalho_sala_meta_e_resumo() -> None:
    _, html, texto = renderizar_relatorios_gerais(_semana(), ["salas_ociosas"])

    assert ">Sala</th>" in html
    assert "3 de 3 salas abaixo da meta" in texto
    assert texto.index("- Sala 2:") < texto.index("- Sala 1:") < texto.index("- Sala 10:")


def _fonte_com_profissionais(quantidade: int) -> FakeScheduleDataSource:
    profissionais = [
        Profissional(
            id=f"prof-{i:02d}", nome=f"Prof {i:02d}", especialidade=Especialidade.PSICOLOGIA
        )
        for i in range(quantidade)
    ]
    return FakeScheduleDataSource(
        profissionais={SEGUNDA: profissionais},
        grade={
            SEGUNDA: [
                EntradaGrade(
                    sala_id=f"sala-{i}",
                    profissional_id=p.id,
                    especialidade=p.especialidade,
                    slot=Slot(data=SEGUNDA, hora_inicio=time(8, 0)),
                    indice_posto=0,
                )
                for i, p in enumerate(profissionais)
            ]
        },
    )


def test_tabela_longa_mostra_mais_n_linhas() -> None:
    foto = fotografar_periodo(_fonte_com_profissionais(MAX_LINHAS_EMAIL + 3), SEGUNDA, SEGUNDA)

    _, html, texto = renderizar_relatorios_gerais(foto, ["ranking_ocupacao", "salas_ociosas"])

    assert texto.count("+3 linhas") == 2
    assert html.count("+3 linhas") == 2
    assert f"{MAX_LINHAS_EMAIL}. Prof" in texto
    assert f"{MAX_LINHAS_EMAIL + 1}. Prof" not in texto


def test_dia_ilegivel_avisa_que_e_parcial() -> None:
    foto = fotografar_periodo(fonte_clinica({QUARTA}), SEGUNDA, SEXTA)

    _, html, texto = renderizar_relatorios_gerais(foto, ["convenios"])

    assert "Não foi possível ler: 30/09; os números são parciais." in texto
    assert "Não foi possível ler: 30/09" in html


def test_so_domingo_avisa_que_nao_ha_agenda() -> None:
    domingo = date(2026, 10, 4)
    foto = fotografar_periodo(fonte_clinica(), domingo, domingo)

    _, _, texto = renderizar_relatorios_gerais(foto, ["vagas_livres"])

    assert "Não há agenda no período" in texto
