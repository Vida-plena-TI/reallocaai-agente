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
    assert [caso.id for caso in casos] == [f"c{n:02d}" for n in range(1, 12)]
    assert {caso.id: caso.cenario for caso in casos if caso.cenario} == {
        "c09": "poucas_vagas",
        "c11": "planos_por_cor",
    }
    assert {caso.id for caso in casos if caso.xfail} == {"c06"}


def test_criterio_desconhecido_e_recusado(tmp_path: Path) -> None:
    arquivo = tmp_path / "casos.yaml"
    arquivo.write_text(
        "- {id: x, descricao: d, hoje: 2026-09-28, turnos: [{pergunta: p}],"
        " criterios: [{tipo: inventado}]}",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inventado"):
        carregar_casos(arquivo)


def test_cenario_desconhecido_e_recusado(tmp_path: Path) -> None:
    arquivo = tmp_path / "casos.yaml"
    arquivo.write_text(
        "- {id: x, descricao: d, hoje: 2026-09-28, cenario: inventado,"
        " turnos: [{pergunta: p}], criterios: []}",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cenário desconhecido"):
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
        "slot",
        "encaixei",
        "registrar",
    ]
    assert termos_proibidos("Parâmetros inválidos: o bloco foi agendado.") == [
        "parâmetros inválidos",
        "bloco",
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


def _bloco_do_plano(*linhas: tuple[str, str, str, str, str]) -> dict[str, object]:
    return {
        "tipo": "tabela_generica",
        "dados": {
            "relatorio": "plano_semanal",
            "linhas": [
                {"dia": d, "situacao": s, "especialidade": e, "horario": h, "sala": sala}
                for d, s, e, h, sala in linhas
            ],
        },
    }


def test_criterios_do_plano_olham_so_as_linhas_plano() -> None:
    """Quarta fictícia: Sala 5 (Elo) livre às 10:00; Sala 2 (TO) livre às 09:30."""
    bloco = _bloco_do_plano(
        ("quarta-feira", "Plano", "Fonoaudiologia", "10:00 às 10:30", "Sala 5"),
        ("quarta-feira", "Plano", "Psicomotricidade", "10:30 às 11:00", "Sala 4"),
        ("quarta-feira", "Não coube", "Terapia Ocupacional", "—", "—"),
        ("quarta-feira", "Alternativa", "Terapia Ocupacional", "09:30 às 10:00", "Sala 2"),
    )
    turno = TurnoObservado(textos_das_tools=["Plano; não coube TO."], blocos=[bloco])

    assert _rodar("vagas_dentro_da_janela", turno, inicio="10:00", fim="12:00").ok
    assert _rodar("sem_sobreposicao_no_dia", turno).ok
    assert _rodar("plano_em_vagas_reais", turno).ok
    assert _rodar("nao_coube_com_alternativas", turno, especialidade="Terapia Ocupacional").ok
    assert _rodar("nao_coube_com_alternativas", turno, especialidade="Psicologia").ok is False


def test_criterios_do_plano_acusam_cruzamento_e_vaga_inexistente() -> None:
    bloco = _bloco_do_plano(
        ("quarta-feira", "Plano", "Fonoaudiologia", "10:00 às 10:30", "Sala 5"),
        ("quarta-feira", "Plano", "Terapia Ocupacional", "10:00 às 10:30", "Sala 2"),
    )
    turno = TurnoObservado(textos_das_tools=["Plano."], blocos=[bloco])

    assert _rodar("sem_sobreposicao_no_dia", turno).ok is False
    resultado = _rodar("plano_em_vagas_reais", turno)
    assert resultado.ok is False and "quarta Sala 2 10:00" in resultado.detalhe


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


def test_alternativas_fora_dos_dias_pedidos_repetidas_e_fora_do_grupo() -> None:
    """O defeito que c09 cobre: alternativas de segunda num plano de quarta e
    quinta, as mesmas em dois grupos e sob o "Não coube" de outro dia."""
    certo = _bloco_do_plano(
        ("quarta-feira", "Não coube", "Terapia Ocupacional", "—", "—"),
        ("quarta-feira", "Alternativa", "Terapia Ocupacional", "14:00 às 14:30", "Sala 2"),
        ("quinta-feira", "Plano", "Fonoaudiologia", "10:30 às 11:00", "Sala 1"),
        ("quinta-feira", "Não coube", "Terapia Ocupacional", "—", "—"),
        ("quinta-feira", "Alternativa", "Terapia Ocupacional", "09:00 às 09:30", "Sala 2"),
    )
    errado = _bloco_do_plano(
        ("quarta-feira", "Não coube", "Psicologia", "—", "—"),
        ("segunda-feira", "Alternativa", "Psicologia", "10:00 às 10:30", "Sala 3"),
        ("quinta-feira", "Não coube", "Psicologia", "—", "—"),
        ("segunda-feira", "Alternativa", "Psicologia", "10:00 às 10:30", "Sala 3"),
    )
    bom = TurnoObservado(textos_das_tools=["Plano."], blocos=[certo])
    ruim = TurnoObservado(textos_das_tools=["Plano."], blocos=[errado])

    for tipo, params in (
        ("alternativas_nos_dias_pedidos", {"dias": ["quarta", "quinta"]}),
        ("alternativas_sem_repeticao", {}),
        ("alternativa_no_grupo_do_dia", {}),
    ):
        assert _rodar(tipo, bom, **params).ok, tipo
        assert _rodar(tipo, ruim, **params).ok is False, tipo
    assert _rodar("alternativas_sem_repeticao", TurnoObservado()).ok is False


def test_texto_contem_e_sem_datas() -> None:
    turno = TurnoObservado(textos_das_tools=["Na quinta, 3 SEGUIDAS das 10:00 às 11:30."])
    com_data = TurnoObservado(textos_das_tools=["Na quinta (29/10), 3 seguidas."])
    com_data_iso = TurnoObservado(blocos=[_bloco_do_plano(("2026-10-29", "Plano", "", "", ""))])

    assert _rodar("texto_contem", turno, frases=["seguidas", "quinta"]).ok
    assert _rodar("texto_contem", turno, frases=["ampliar"]).ok is False
    assert _rodar("sem_datas", turno).ok
    assert _rodar("sem_datas", com_data).ok is False
    assert _rodar("sem_datas", com_data_iso).ok is False


def test_sequencia_maxima_contra_o_oraculo() -> None:
    """Quarta fictícia, 10h às 12h: fono, psicomotricidade e psicologia seguidas (3)."""
    quatro = ["Fono", "Psicomotricidade", "Psicologia", "TO"]
    params = {"dias": ["quarta"], "especialidades": quatro, "inicio": "10:00", "fim": "12:00"}
    seguidas = _bloco_do_plano(
        ("quarta-feira", "Plano", "Fonoaudiologia", "10:00 às 10:30", "Sala 5"),
        ("quarta-feira", "Plano", "Psicomotricidade", "10:30 às 11:00", "Sala 4"),
        ("quarta-feira", "Plano", "Psicologia", "11:00 às 11:30", "Sala 3"),
    )
    com_buraco = _bloco_do_plano(
        ("quarta-feira", "Plano", "Fonoaudiologia", "10:00 às 10:30", "Sala 5"),
        ("quarta-feira", "Plano", "Psicomotricidade", "10:30 às 11:00", "Sala 4"),
        ("quarta-feira", "Plano", "Psicologia", "11:30 às 12:00", "Sala 3"),
    )
    curta = _bloco_do_plano(
        ("quarta-feira", "Plano", "Fonoaudiologia", "10:00 às 10:30", "Sala 5"),
        ("quarta-feira", "Plano", "Psicomotricidade", "10:30 às 11:00", "Sala 4"),
    )

    assert _rodar("sequencia_maxima", TurnoObservado(blocos=[seguidas]), **params).ok
    resultado = _rodar("sequencia_maxima", TurnoObservado(blocos=[com_buraco]), **params)
    assert resultado.ok is False and "não seguidas" in resultado.detalhe
    resultado = _rodar("sequencia_maxima", TurnoObservado(blocos=[curta]), **params)
    assert resultado.ok is False and "2 no plano, 3 no oráculo" in resultado.detalhe


def test_tools_chamadas() -> None:
    turno = TurnoObservado(tools=["planejar_semana"])
    params = {"tools": ["planejar_semana"], "nunca": ["buscar_encaixe"]}

    assert _rodar("tools_chamadas", turno, **params).ok
    assert _rodar("tools_chamadas", TurnoObservado(tools=["buscar_encaixe"]), **params).ok is False


def test_alternativas_em_vagas_reais() -> None:
    """Quarta fictícia: Sala 2 (TO) livre às 09:30, ocupada às 10:00."""
    real = _bloco_do_plano(
        ("quarta-feira", "Alternativa", "Terapia Ocupacional", "09:30 às 10:00", "Sala 2"),
    )
    falsa = _bloco_do_plano(
        ("quarta-feira", "Alternativa", "Terapia Ocupacional", "10:00 às 10:30", "Sala 2"),
    )

    assert _rodar("plano_em_vagas_reais", TurnoObservado(blocos=[real]), situacao="Alternativa").ok
    resultado = _rodar(
        "plano_em_vagas_reais", TurnoObservado(blocos=[falsa]), situacao="Alternativa"
    )
    assert resultado.ok is False


CONTEXTO_PLANOS = Contexto(payload_da_semana("planos_por_cor"), CASO.semana)


def _bloco_por_plano(
    *linhas: tuple[str, str, int], avisos: tuple[str, ...] = ()
) -> dict[str, object]:
    return {
        "tipo": "tabela_generica",
        "avisos": list(avisos),
        "dados": {
            "relatorio": "pacientes_por_plano",
            "linhas": [
                {"plano": plano, "paciente": paciente, "atendimentos_semana": atendimentos}
                for plano, paciente, atendimentos in linhas
            ],
        },
    }


#: O que o cenário `planos_por_cor` deve dar (nomes fictícios).
LINHAS_PLANOS = (
    ("SulAmérica", "Paciente Ficticio Azul", 3),
    ("SulAmérica", "Paciente Ficticio Obs", 1),
    ("Klini", "Paciente Ficticio Azul", 1),
    ("Klini", "Paciente Ficticio Vermelho", 2),
    ("Particulares", "Paciente Ficticio Lilas", 1),
    ("Care Plus", "Paciente Ficticio Laranja", 2),
    ("Unimed", "Paciente Ficticio Musgo", 1),
    ("Unimed", "Paciente Ficticio Verde", 2),
)
AVISOS_PLANOS = (
    "1 nome com cor não reconhecida foi ignorado.",
    "1 paciente aparece com cores de mais de um plano na semana e está em cada um deles.",
)


def _rodar_planos(tipo: str, turno: TurnoObservado) -> ResultadoCriterio:
    return CRITERIOS[tipo](Criterio(tipo, {}), [turno], CONTEXTO_PLANOS)


def test_pacientes_por_plano_igual_ao_oraculo() -> None:
    certo = _bloco_por_plano(*LINHAS_PLANOS, avisos=AVISOS_PLANOS)
    a_mais = _bloco_por_plano(*LINHAS_PLANOS[:-1], ("Unimed", "Paciente Ficticio Verde", 3))
    sem_aviso = _bloco_por_plano(*LINHAS_PLANOS, avisos=AVISOS_PLANOS[:1])
    fora_de_ordem = _bloco_por_plano(*reversed(LINHAS_PLANOS), avisos=AVISOS_PLANOS)
    com_total = _bloco_por_plano(*LINHAS_PLANOS, ("Unimed", "Total", 3), avisos=AVISOS_PLANOS)

    assert _rodar_planos("pacientes_por_plano_igual_ao_oraculo", TurnoObservado(blocos=[certo])).ok
    for errado, motivo in (
        (a_mais, "atendimentos"),
        (sem_aviso, "mais de um plano"),
        (fora_de_ordem, "ordem"),
        (com_total, "atendimentos"),
    ):
        resultado = _rodar_planos(
            "pacientes_por_plano_igual_ao_oraculo", TurnoObservado(blocos=[errado])
        )
        assert resultado.ok is False and motivo in resultado.detalhe
        # O detalhe vai para o JSON da régua: só contagens.
        assert "Ficticio" not in resultado.detalhe


def test_sem_nomes_de_paciente_no_content_nem_na_resposta() -> None:
    bloco = _bloco_por_plano(*LINHAS_PLANOS)
    limpo = TurnoObservado(textos_das_tools=["SulAmérica: 2 pacientes."], blocos=[bloco])
    no_content = TurnoObservado(textos_das_tools=["Inclui paciente ficticio AZUL."], blocos=[bloco])
    na_resposta = TurnoObservado(
        textos_das_tools=["SulAmérica: 2 pacientes."],
        blocos=[bloco],
        resposta="Um deles é Paciente Fictício Obs.",
    )

    assert _rodar_planos("sem_nomes_de_paciente", limpo).ok
    for turno in (no_content, na_resposta):
        resultado = _rodar_planos("sem_nomes_de_paciente", turno)
        assert resultado.ok is False and resultado.detalhe == "1 nome(s) de paciente no texto"
    assert _rodar_planos("sem_nomes_de_paciente", TurnoObservado()).ok is False
