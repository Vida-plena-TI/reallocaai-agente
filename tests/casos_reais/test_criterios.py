"""Leitura de `casos.yaml` e os critérios, sobre turnos montados à mão (sem motor nem LLM)."""

from datetime import date
from pathlib import Path

import pytest

from tests.casos_reais.casos import (
    CRITERIOS,
    Caso,
    Contexto,
    Criterio,
    ResultadoCriterio,
    TurnoObservado,
    avaliar,
    carregar_casos,
    criterios_do_modo,
    resolver_datas,
    status_do_caso,
    termos_proibidos,
)
from tests.casos_reais.planilha import payload_da_semana

CASO = Caso(
    id="t",
    descricao="teste",
    hoje=date(2026, 9, 30),
    turnos=(),
    criterios=(),
)
CONTEXTO = Contexto(payload_da_semana(), CASO.semana)


def _rodar(tipo: str, turno: TurnoObservado, **params: object) -> ResultadoCriterio:
    return CRITERIOS[tipo](Criterio(tipo, dict(params)), [turno], CONTEXTO)


def test_casos_do_yaml_carregam() -> None:
    casos = carregar_casos()
    assert [caso.id for caso in casos] == [f"c0{n}" for n in range(1, 9)]
    assert {caso.id for caso in casos if caso.xfail} == {"c02", "c03", "c04", "c06"}


def test_criterio_desconhecido_e_recusado(tmp_path: Path) -> None:
    arquivo = tmp_path / "casos.yaml"
    arquivo.write_text(
        "- {id: x, descricao: d, hoje: 2026-09-28, turnos: [{pergunta: p}],"
        " criterios: [{tipo: inventado}]}",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inventado"):
        carregar_casos(arquivo)


def test_semana_e_datas_nos_argumentos() -> None:
    assert CASO.semana["segunda"] == date(2026, 9, 28)
    assert CASO.semana["sabado"] == date(2026, 10, 3)
    args = {"dias": ["{quarta}", "{quinta}"], "data": "{segunda}", "outro": "{x}", "n": 3}
    assert resolver_datas(args, CASO.semana) == {
        "dias": ["2026-09-30", "2026-10-01"],
        "data": "2026-09-28",
        "outro": "{x}",
        "n": 3,
    }


def test_vocabulario_proibido_por_palavra_inteira() -> None:
    assert termos_proibidos("Encaixei no Slot das 10h; vou REGISTRAR.") == [
        "registrar",
        "encaixei",
        "slot",
    ]
    assert termos_proibidos("3 slots livres; o atendimento_id é x") == ["atendimento_id", "slot"]
    assert termos_proibidos("Toolbox e slotting não contam; registro também não.") == []


def test_criterios_da_resposta_ficam_na_no_deterministico() -> None:
    sem_resposta = TurnoObservado(textos_das_tools=["- 10:00 às 10:30: Fono, slot livre"])
    for tipo in ("vocabulario_proibido", "maximo_de_frases", "nao_pede_atendimento_id"):
        assert _rodar(tipo, sem_resposta, maximo=3).ok is None
    assert _rodar("sem_linhas_hh_mm", sem_resposta).ok is None


def test_linhas_hh_mm_na_resposta() -> None:
    assert _rodar("sem_linhas_hh_mm", TurnoObservado(resposta="Há 3 vagas às 10:00.")).ok
    lista = TurnoObservado(resposta="Vagas:\n- 10:00 às 10:30: Fono\n- 11:00 às 11:30: TO")
    assert _rodar("sem_linhas_hh_mm", lista).ok is False


def test_vagas_dentro_da_janela_le_texto_e_blocos() -> None:
    dentro = TurnoObservado(resposta="Tem 10:00 às 10:30 e 11:30-12:00.")
    assert _rodar("vagas_dentro_da_janela", dentro, inicio="10:00", fim="12:00").ok
    bloco = {"tipo": "disponibilidade", "dados": {"faixas": [{"inicio": "07:00", "fim": "08:00"}]}}
    fora = TurnoObservado(resposta="Veja o quadro.", blocos=[bloco])
    resultado = _rodar("vagas_dentro_da_janela", fora, inicio="10:00", fim="12:00")
    assert resultado.ok is False and "07:00-08:00" in resultado.detalhe
    vazio = TurnoObservado(resposta="Não há vagas.")
    assert _rodar("vagas_dentro_da_janela", vazio, inicio="10:00", fim="12:00").ok is False


def test_turno_com_erro_falha_os_criterios_de_saida() -> None:
    erro = TurnoObservado(erro="tool inexistente: planejar_semana")
    resultado = _rodar("sem_sobreposicao_no_dia", erro)
    assert resultado.ok is False and "planejar_semana" in resultado.detalhe


def test_sobreposicao_so_conta_no_mesmo_dia() -> None:
    texto = "Quarta:\n- Fono: 10:00 às 10:30\n- TO: 10:30 às 11:00\nQuinta:\n- Fono: 10:00 às 10:30"
    assert _rodar("sem_sobreposicao_no_dia", TurnoObservado(resposta=texto)).ok
    cruzado = "Quarta 30/09:\n- Fono: 10:00 às 10:30\n- TO: 10:00 às 10:30"
    assert _rodar("sem_sobreposicao_no_dia", TurnoObservado(resposta=cruzado)).ok is False


def test_nao_coube_frase_proibida_e_planos() -> None:
    texto = "Plano 1: ...\nPlano 2: ...\nTO não coube; alternativas: 13:00."
    turno = TurnoObservado(resposta=texto)
    assert _rodar("nao_coube_com_alternativas", turno).ok
    assert _rodar("max_planos", turno, maximo=3).ok
    assert _rodar("max_planos", TurnoObservado(resposta="Nada."), maximo=3).ok is False
    proibida = TurnoObservado(resposta="Não existe bloco contínuo de 120 minutos.")
    assert _rodar("frase_proibida", proibida, frases=["bloco contínuo de 120 minutos"]).ok is False


def test_estado_profissional_contra_o_oraculo() -> None:
    params = {"dia": "quinta", "inicio": "10:00", "fim": "12:00"}
    sem_vaga = TurnoObservado(resposta="A Carol está sem vaga entre 10h e 12h na quinta.")
    assert _rodar("estado_profissional", sem_vaga, na_planilha="Carol", **params).ok
    errado = TurnoObservado(resposta="Profissional 'Elo' não encontrado.")
    resultado = _rodar("estado_profissional", errado, na_planilha="Elo", **params)
    assert resultado.ok is False
    assert resultado.detalhe == "esperado nao_atende_no_dia, dito ['nao_existe']"
    com_vaga = TurnoObservado(resposta="A Bia tem 10:00 às 10:30 e 10:30 às 11:00.")
    assert _rodar("estado_profissional", com_vaga, na_planilha="Bia", **params).ok


def test_vocabulario_vale_para_todos_os_casos_no_llm() -> None:
    caso = Caso("x", "d", date(2026, 9, 28), (), (Criterio("max_segundos", {"maximo": 1}),))
    assert [c.tipo for c in criterios_do_modo(caso, llm=False)] == ["max_segundos"]
    assert [c.tipo for c in criterios_do_modo(caso, llm=True)] == [
        "max_segundos",
        "vocabulario_proibido",
    ]
    resultados = avaliar(caso, [TurnoObservado(resposta="Ok, slot livre.")], CONTEXTO, llm=True)
    assert [r.ok for r in resultados] == [True, False]


@pytest.mark.parametrize(
    ("xfail", "oks", "esperado"),
    [
        (None, [True, None], "passou"),
        (None, [True, False], "falhou"),
        (None, [None], "n/a"),
        ("motivo", [False], "xfail"),
        ("motivo", [True], "xpass"),
    ],
)
def test_status_do_caso(xfail: str | None, oks: list[bool | None], esperado: str) -> None:
    caso = Caso("x", "d", date(2026, 9, 28), (), (), xfail=xfail)
    assert status_do_caso(caso, [ResultadoCriterio("t", ok) for ok in oks]) == esperado
