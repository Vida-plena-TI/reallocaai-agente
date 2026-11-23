"""Motor do panorama de enxugamento (`app.engine.enxugamento`).

As semanas são as da planilha fictícia (`tests/casos_reais/planilha.py`),
lidas pelo caminho de produção (Sheets -> parser -> fotografia). Todo cenário
de todos os testes passa por `_conferir`, que aplica os passos à grade crua
com o oráculo independente (`tests/casos_reais/oraculo.py`, sem importar
`app`): saldo >= 0 e nenhum turno novo, terapias e durações de cada paciente
iguais, nenhum posto com dois pacientes, nenhum paciente em dois lugares.
Nomes de pacientes e de profissionais são fictícios.
"""

import logging
import time as relogio
from collections.abc import Mapping
from datetime import date, time
from typing import Any, cast

import gspread
import pytest

from app.data_sources.google_sheets import GoogleSheetsDataSource
from app.domain import dias_da_semana_de
from app.domain.constants import TURNO_CORTE
from app.domain.normalizacao import normalizar_id
from app.engine.enxugamento import (
    Alavanca,
    Cenario,
    MotivoPreso,
    Objetivo,
    PanoramaEnxugamento,
    PedidoEnxugamento,
    RegrasEnxugamento,
    Turno,
    custo_do_passo,
    planejar_enxugamento,
    turno_do_inicio,
)
from app.engine.fotografia import FotografiaPeriodo, fotografar_periodo
from app.engine.grade_simulada import Lugar
from tests.casos_reais import oraculo
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana, payload_de

HOJE = date(2026, 9, 28)
SEMANA = dias_da_semana_de(HOJE)
SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA = SEMANA[:5]
ACACIA, BROMELIA, CAMELIA = "ficticia-acacia", "ficticia-bromelia", "ficticia-camelia"


def _foto(payload: dict[str, Any]) -> FotografiaPeriodo:
    planilha = PlanilhaContada(lambda _params: payload)
    fonte = GoogleSheetsDataSource(cast(gspread.Spreadsheet, planilha))
    return fotografar_periodo(fonte, SEMANA[0], SEMANA[-1])


def _lugar(foto: FotografiaPeriodo, lugar: Lugar) -> oraculo.LugarDoPlano:
    return oraculo.LugarDoPlano(
        dia=lugar.data,
        inicio=lugar.hora_inicio,
        fim=lugar.hora_fim,
        profissional=foto.nome_profissional(lugar.profissional_id),
        sala=int(foto.nome_sala(lugar.sala_id).split()[-1]),
    )


def _conferir(payload: dict[str, Any], foto: FotografiaPeriodo, cenario: Cenario) -> None:
    passos = [
        oraculo.PassoDoCenario(
            paciente=passo.paciente,
            de=_lugar(foto, passo.de),
            para=_lugar(foto, passo.para),
            abre_espaco=passo.abre_espaco,
        )
        for passo in cenario.passos
    ]
    resultado = oraculo.validar_cenario(payload, SEMANA, passos)
    assert resultado.problemas == [], cenario.codigo
    assert resultado.novos == 0 == cenario.resumo.turnos_novos
    assert resultado.liberados == cenario.resumo.turnos_liberados >= len(cenario.turnos_liberados)
    assert resultado.liberados - resultado.novos >= 0
    # As colunas de cobertura do quadro, recontadas na grade crua.
    assert resultado.profissionais_sem_atendimento == len(cenario.resumo.profissionais_sem_presenca)
    assert resultado.turnos_sem_profissional == len(cenario.resumo.sem_cobertura)
    for passo in cenario.passos:
        assert passo.para.blocos == passo.de.blocos
        assert passo.alavanca is not Alavanca.L4 or passo.outro_dia
        if passo.abre_espaco:
            assert not passo.outro_profissional


def panorama(
    cenario: str,
    pedido: PedidoEnxugamento | None = None,
    regras: RegrasEnxugamento | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[FotografiaPeriodo, PanoramaEnxugamento]:
    payload = payload if payload is not None else payload_da_semana(cenario)
    foto = _foto(payload)
    resultado = planejar_enxugamento(foto, pedido or PedidoEnxugamento(), regras)
    assert resultado.estatistica.descartados_na_validacao == 0
    for item in resultado.cenarios:
        _conferir(payload, foto, item)
    assert len({frozenset(c.turnos_liberados) for c in resultado.cenarios}) == len(
        resultado.cenarios
    ), "dois cenários com os mesmos turnos liberados"
    return foto, resultado


def _turnos(cenario: Cenario) -> set[tuple[str, date, Turno]]:
    return {(t.profissional_id, t.data, t.turno) for t in cenario.turnos_liberados}


def _todos_os_passos(resultado: PanoramaEnxugamento) -> list[Any]:
    return [passo for cenario in resultado.cenarios for passo in cenario.passos]


# ----------------------------------------------------------------- regras


def test_turno_pelo_horario_de_inicio() -> None:
    assert time(12) == TURNO_CORTE
    assert turno_do_inicio(11 * 60 + 30) is Turno.MANHA
    assert turno_do_inicio(12 * 60) is Turno.TARDE
    assert turno_do_inicio(13 * 60) is Turno.TARDE


def test_custo_do_passo_na_ordem_das_alavancas() -> None:
    de = Lugar(SEGUNDA, 8 * 60, "sala-7", 0, ACACIA, 2)
    l1 = custo_do_passo(Alavanca.L1, de, Lugar(SEGUNDA, 13 * 60, "sala-7", 0, ACACIA, 2), False)
    l2 = custo_do_passo(Alavanca.L2, de, Lugar(SEGUNDA, 8 * 60, "sala-8", 0, BROMELIA, 2), False)
    l3 = custo_do_passo(Alavanca.L3, de, Lugar(SEGUNDA, 14 * 60, "sala-8", 0, BROMELIA, 2), False)
    l4 = custo_do_passo(Alavanca.L4, de, Lugar(TERCA, 8 * 60, "sala-7", 0, ACACIA, 2), False)
    assert l1 < l2 < l3 < l4
    assert l2 == (2, 1, 0, 0, 0, 0)
    assert l3 == (3, 1, 1, 0, 0, 360)
    # No mesmo nível, o posto simultâneo e a distância desempatam.
    extra = custo_do_passo(Alavanca.L2, de, Lugar(SEGUNDA, 8 * 60, "sala-8", 1, BROMELIA, 2), True)
    longe = custo_do_passo(Alavanca.L2, de, Lugar(SEGUNDA, 9 * 60, "sala-8", 0, BROMELIA, 2), False)
    assert l2 < longe < extra


# -------------------------------------------------------------- alavancas


def test_l2_colega_da_mesma_terapia_no_mesmo_turno() -> None:
    _, resultado = panorama("enxugamento")
    passos = [
        p
        for p in _todos_os_passos(resultado)
        if (p.turno_liberado.profissional_id, p.turno_liberado.data) == (ACACIA, SEGUNDA)
    ]
    assert passos
    assert all(p.alavanca is Alavanca.L2 and p.outro_profissional for p in passos)
    assert all(not p.outro_turno and p.minutos_deslocados == 0 for p in passos)


def test_l1_mesmo_profissional_outro_turno() -> None:
    _, resultado = panorama("enxugamento")
    terca = [p for p in _todos_os_passos(resultado) if p.turno_liberado.data == TERCA]
    assert terca
    assert all(p.alavanca is Alavanca.L1 for p in terca)
    assert all(not p.outro_profissional and p.outro_turno for p in terca)


def test_l3_colega_outro_turno() -> None:
    _, resultado = panorama("enxugamento")
    quarta = [p for p in _todos_os_passos(resultado) if p.turno_liberado.data == QUARTA]
    assert quarta
    assert all(p.alavanca is Alavanca.L3 for p in quarta)
    assert all(p.outro_profissional and p.outro_turno for p in quarta)


def test_l4_so_com_permitir_trocar_dia() -> None:
    _, sem = panorama("enxugamento")
    assert all(p.alavanca is not Alavanca.L4 for p in _todos_os_passos(sem))
    assert all(t.data not in (QUINTA, SEXTA) for c in sem.cenarios for t in c.turnos_liberados)
    assert sem.candidatos_sem_plano == 2

    _, com = panorama("enxugamento", PedidoEnxugamento(permitir_trocar_dia=True))
    l4 = [p for p in _todos_os_passos(com) if p.alavanca is Alavanca.L4]
    assert l4
    assert all(p.outro_dia and not p.outro_profissional for p in l4)


def test_cadeia_quando_nao_ha_destino_direto() -> None:
    _, resultado = panorama("enxugamento_cadeia")
    (cenario,) = [c for c in resultado.cenarios if (ACACIA, SEGUNDA, Turno.MANHA) in _turnos(c)]
    assert {p.alavanca for p in cenario.passos} == {Alavanca.L5}
    abrem = [p for p in cenario.passos if p.abre_espaco]
    assert len(abrem) == 1
    assert abrem[0].de.profissional_id == abrem[0].para.profissional_id == BROMELIA
    (absorvido,) = [p for p in cenario.passos if not p.abre_espaco]
    assert absorvido.para.profissional_id == BROMELIA
    # Quem abre espaço sai antes de o lugar ser ocupado.
    assert cenario.passos.index(abrem[0]) < cenario.passos.index(absorvido)


def test_cadeia_respeita_o_limite_de_deslocados() -> None:
    _, resultado = panorama("enxugamento_cadeia", regras=RegrasEnxugamento(max_deslocados_cadeia=0))
    assert all((ACACIA, SEGUNDA, Turno.MANHA) not in _turnos(c) for c in resultado.cenarios)


def test_sem_solucao() -> None:
    _, resultado = panorama("enxugamento", PedidoEnxugamento(dias=(QUINTA,)))
    assert resultado.cenarios == ()
    assert resultado.candidatos == 1
    assert resultado.candidatos_sem_plano == 1


def test_nunca_abre_turno_novo() -> None:
    """A Bromélia está escalada na quinta à tarde, sem ninguém: não recebe paciente."""
    _, resultado = panorama("enxugamento", PedidoEnxugamento(permitir_trocar_dia=True))
    assert resultado.cenarios
    for passo in _todos_os_passos(resultado):
        assert not (passo.para.profissional_id == BROMELIA and passo.para.data == QUINTA)
    assert all(c.resumo.turnos_novos == 0 for c in resultado.cenarios)


# ---------------------------------------------------------------- pedido


def test_objetivo_muda_o_cenario_recomendado() -> None:
    primeiros = {}
    for objetivo in Objetivo:
        _, resultado = panorama("enxugamento", PedidoEnxugamento(objetivo=objetivo))
        assert resultado.cenarios[0].perfil is objetivo
        primeiros[objetivo] = resultado.cenarios[0]
    mais = primeiros[Objetivo.MAIS_TURNOS]
    menos = primeiros[Objetivo.MENOS_MUDANCA]
    assert menos.codigo != mais.codigo
    assert mais.resumo.turnos_liberados > menos.resumo.turnos_liberados
    assert menos.custo / menos.resumo.turnos_liberados < mais.custo / mais.resumo.turnos_liberados
    assert all(p.alavanca in (Alavanca.L1, Alavanca.L2) for p in menos.passos)


def test_foco_com_liberar_turno() -> None:
    pedido = PedidoEnxugamento(profissionais_foco=(ACACIA,), liberar_turno=Turno.TARDE)
    _, resultado = panorama("enxugamento", pedido)
    assert resultado.cenarios
    for cenario in resultado.cenarios:
        assert all(t.profissional_id == ACACIA for t in cenario.turnos_liberados)
        assert all(t.turno is Turno.TARDE for t in cenario.turnos_liberados)


def test_foco_ignora_o_limite_de_pacientes() -> None:
    pedido = PedidoEnxugamento(
        profissionais_foco=(ACACIA,), liberar_turno=Turno.MANHA, max_pacientes_por_turno=1
    )
    _, resultado = panorama("enxugamento", pedido)
    assert any((ACACIA, SEGUNDA, Turno.MANHA) in _turnos(c) for c in resultado.cenarios)
    _, sem_foco = panorama("enxugamento", PedidoEnxugamento(max_pacientes_por_turno=1))
    assert all((ACACIA, SEGUNDA, Turno.MANHA) not in _turnos(c) for c in sem_foco.cenarios)


def test_preservadas_nao_liberam_turno_nem_cedem_pacientes() -> None:
    pedido = PedidoEnxugamento(profissionais_preservados=(BROMELIA,))
    _, resultado = panorama("enxugamento", pedido)
    assert resultado.cenarios
    for cenario in resultado.cenarios:
        assert all(t.profissional_id != BROMELIA for t in cenario.turnos_liberados)
        assert all(p.de.profissional_id != BROMELIA for p in cenario.passos)


def test_permitir_zerar_semana() -> None:
    """Na semana de três, cada profissional só atende na segunda de manhã."""
    _, com = panorama("enxugamento_tres")
    assert com.cenarios
    assert all(c.resumo.profissionais_sem_presenca for c in com.cenarios)
    _, sem = panorama("enxugamento_tres", PedidoEnxugamento(permitir_zerar_semana=False))
    assert sem.cenarios == ()
    _, outra = panorama(
        "enxugamento", PedidoEnxugamento(permitir_zerar_semana=False, permitir_trocar_dia=True)
    )
    assert outra.cenarios
    assert all(not c.resumo.profissionais_sem_presenca for c in outra.cenarios)


def test_cenarios_ja_vistos_devolvem_outros() -> None:
    _, primeiro = panorama("enxugamento", PedidoEnxugamento(max_cenarios=2))
    vistos = tuple(c.codigo for c in primeiro.cenarios)
    assert len(vistos) == 2
    _, segundo = panorama(
        "enxugamento", PedidoEnxugamento(max_cenarios=2, cenarios_ja_vistos=vistos)
    )
    assert segundo.cenarios
    conjuntos = {frozenset(c.turnos_liberados) for c in primeiro.cenarios}
    for cenario in segundo.cenarios:
        assert cenario.codigo not in vistos
        assert frozenset(cenario.turnos_liberados) not in conjuntos


def test_de_tres_a_cinco_cenarios() -> None:
    _, resultado = panorama("enxugamento")
    assert 3 <= len(resultado.cenarios) <= 5
    _, um = panorama("enxugamento", PedidoEnxugamento(max_cenarios=1))
    assert len(um.cenarios) == 1


def test_determinismo() -> None:
    def assinatura(resultado: PanoramaEnxugamento) -> list[object]:
        return [
            (c.codigo, c.perfil, c.turnos_liberados, [(p.de, p.para, p.alavanca) for p in c.passos])
            for c in resultado.cenarios
        ]

    _, um = panorama("enxugamento")
    _, outro = panorama("enxugamento")
    assert assinatura(um) == assinatura(outro)


# ----------------------------------------------------------------- presos


def test_turnos_presos_contam_e_nao_saem() -> None:
    fixo = normalizar_id("Paciente Ficticio Figueira")
    _, resultado = panorama(
        "enxugamento_presos", regras=RegrasEnxugamento(pacientes_fixos=frozenset({fixo}))
    )
    assert resultado.presos == {
        MotivoPreso.SESSAO_EM_GRUPO: 1,
        MotivoPreso.AGUARDANDO_AUTORIZACAO: 1,
        MotivoPreso.PACIENTE_FIXO: 1,
    }
    assert resultado.cenarios
    for cenario in resultado.cenarios:
        assert all(t.profissional_id != ACACIA for t in cenario.turnos_liberados)
    # Sem a regra do paciente fixo, a quarta da Acácia pode sair.
    _, livre = panorama("enxugamento_presos")
    assert MotivoPreso.PACIENTE_FIXO not in livre.presos
    assert any((ACACIA, QUARTA, Turno.MANHA) in _turnos(c) for c in livre.cenarios)


def test_profissional_em_dois_postos() -> None:
    payload = payload_da_semana("enxugamento_tres")
    # O oráculo lê as duas colunas da Camélia na segunda (as vagas são só da Sala
    # 9) e conta um turno só.
    assert any(p.endswith("Camelia") for p in oraculo.profissionais_do_dia(payload, SEGUNDA))
    camelia = [v for v in oraculo.vagas(payload, SEGUNDA) if v.profissional.endswith("Camelia")]
    assert {v.sala for v in camelia} == {9}
    assert sum(1 for t in oraculo.turnos_pagos(payload, SEMANA) if t[0].endswith("camelia")) == 1

    foto, resultado = panorama("enxugamento_tres")
    salas = {s.sala_id for s in foto.slots if s.profissional_id == CAMELIA}
    assert len(salas) == 2
    extras = [p for p in _todos_os_passos(resultado) if p.posto_extra]
    assert extras
    assert all(p.para.profissional_id == CAMELIA for p in extras)

    _, sem_extras = panorama("enxugamento_tres", regras=RegrasEnxugamento(usar_postos_extras=False))
    assert sem_extras.cenarios == ()


def test_log_so_com_contagens(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="app.engine")
    panorama("enxugamento")
    texto = caplog.text.lower()
    assert "planejar_enxugamento" in texto
    assert "paciente ficticio" not in texto
    assert "ficticia" not in texto


# ------------------------------------------------------------------ escala

#: 8 profissionais (4 terapias x 2), cada uma em 2 postos (2 salas), 6 dias.
_TERAPIAS = ("Fono", "TO", "Psicologia", "Nutri")
_DESENHOS = (
    "AA..BB..CC DD..EE..FF",
    "..AA...... ....BB....",
    "AABBCC..DD ..EEFF..GG",
    "....AA.... ##########",
    "AA........ BB..CC....",
    "..AABB..CC ..DD......",
    "########## AA..BB..CC",
    "AA..BB.... ..CC......",
)
_DIAS_DA_PLANILHA = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado")


def _semana_de_escala() -> tuple[dict[str, dict[str, str]], Mapping[str, str]]:
    semana: dict[str, dict[str, str]] = {}
    profissionais: dict[str, str] = {}
    for d, dia in enumerate(_DIAS_DA_PLANILHA):
        semana[dia] = {}
        for p in range(8):
            nome = f"Ficticia Escala{p + 1} ({_TERAPIAS[p % 4]})"
            for posto in range(2):
                sala = f"Sala {10 + 2 * p + posto}"
                profissionais[sala] = nome
                semana[dia][sala] = _DESENHOS[(p + d + 3 * posto) % len(_DESENHOS)]
    return semana, profissionais


def test_escala_real_em_menos_de_tres_segundos() -> None:
    semana, profissionais = _semana_de_escala()
    payload = payload_de(semana, profissionais)
    foto = _foto(payload)
    assert len({s.profissional_id for s in foto.slots if s.profissional_id}) == 8

    comeco = relogio.perf_counter()
    resultado = planejar_enxugamento(foto, PedidoEnxugamento())
    segundos = relogio.perf_counter() - comeco

    assert segundos < 3.0, f"{segundos:.2f}s"
    assert resultado.candidatos >= 20
    assert len(resultado.cenarios) >= 3
    assert resultado.estatistica.descartados_na_validacao == 0
    for cenario in resultado.cenarios:
        _conferir(payload, foto, cenario)


def test_escala_com_teto_de_busca_avisa_busca_limitada() -> None:
    semana, profissionais = _semana_de_escala()
    payload = payload_de(semana, profissionais)
    foto = _foto(payload)
    resultado = planejar_enxugamento(
        foto, PedidoEnxugamento(), RegrasEnxugamento(max_avaliacoes=2_000)
    )
    assert resultado.estatistica.busca_limitada
    assert resultado.estatistica.avaliacoes <= 2_000
    for cenario in resultado.cenarios:
        _conferir(payload, foto, cenario)
