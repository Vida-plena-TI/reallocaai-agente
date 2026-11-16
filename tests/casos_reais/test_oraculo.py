"""O oráculo e a semana fictícia: independência do motor e contagens conferidas à mão."""

import ast
from datetime import date, time
from pathlib import Path

import pytest

from tests.casos_reais import oraculo
from tests.casos_reais.planilha import PlanilhaContada, payload_da_semana

SEGUNDA = date(2026, 9, 28)
TERCA = date(2026, 9, 29)
QUARTA = date(2026, 9, 30)
QUINTA = date(2026, 10, 1)
SABADO = date(2026, 10, 3)
DOMINGO = date(2026, 10, 4)
SEMANA = [SEGUNDA, TERCA, QUARTA, QUINTA, date(2026, 10, 2), SABADO]

PASTA = Path(__file__).resolve().parent


@pytest.mark.parametrize("arquivo", ["oraculo.py", "planilha.py"])
def test_nao_importa_nada_do_motor(arquivo: str) -> None:
    """O esperado não pode sair do código que está sendo testado."""
    arvore = ast.parse((PASTA / arquivo).read_text(encoding="utf-8"))
    modulos = [
        nome
        for no in ast.walk(arvore)
        for nome in (
            [alias.name for alias in no.names]
            if isinstance(no, ast.Import)
            else [no.module or ""]
            if isinstance(no, ast.ImportFrom)
            else []
        )
    ]
    assert not [m for m in modulos if m == "app" or m.startswith(("app.", "scripts"))]


def _horas(vagas: list[oraculo.Vaga], sala: int) -> list[str]:
    return [vaga.hora.strftime("%H:%M") for vaga in vagas if vaga.sala == sala]


def test_vagas_conferidas_a_mao_na_terca() -> None:
    vagas = oraculo.vagas(payload_da_semana(), TERCA)
    # Sala 1 (Bia): "AA..BBCCDD ..EEFF..GG".
    assert _horas(vagas, 1) == ["08:00", "08:30", "13:00", "13:30", "16:00", "16:30"]
    assert {vaga.profissional for vaga in vagas if vaga.sala == 1} == {"Bia"}


def test_fechado_nao_e_vaga_e_meio_dia_nao_e_expediente() -> None:
    vagas = oraculo.vagas(payload_da_semana(), SABADO)
    assert "07:00" not in _horas(vagas, 1)
    assert "07:30" not in _horas(vagas, 1)
    assert all(vaga.hora != time(12) for vaga in vagas)


def test_total_de_vagas_da_segunda() -> None:
    # Pontos de cada sala em `AGENDA["Segunda"]`, contados à mão.
    vagas = oraculo.vagas(payload_da_semana(), SEGUNDA)
    assert [len(_horas(vagas, sala)) for sala in range(1, 6)] == [8, 8, 11, 6, 12]


def test_sala_sem_coluna_e_domingo_nao_tem_vaga() -> None:
    assert _horas(oraculo.vagas(payload_da_semana(), QUINTA), 5) == []
    assert oraculo.vagas(payload_da_semana(), DOMINGO) == []


@pytest.mark.parametrize(
    ("nome", "dia", "esperado"),
    [
        (None, QUINTA, "nao_existe"),
        ("Zuleide", QUINTA, "nao_existe"),
        ("Elo", QUINTA, "nao_atende_no_dia"),
        ("Carol", QUINTA, "sem_vaga_na_janela"),
        ("Bia", QUINTA, "com_vaga"),
        ("ASSIZELY", QUARTA, "sem_vaga_na_janela"),
    ],
)
def test_estado_do_profissional_na_janela_das_10_as_12(
    nome: str | None, dia: date, esperado: str
) -> None:
    estado = oraculo.estado_profissional(payload_da_semana(), nome, dia, SEMANA, time(10), time(12))
    assert estado == esperado


def test_planilha_contada_busca_uma_vez_e_conta_todas() -> None:
    buscas: list[object] = []

    def buscar(params: object) -> dict[str, object]:
        buscas.append(params)
        return {"sheets": []}

    planilha = PlanilhaContada(buscar)
    planilha.fetch_sheet_metadata({"a": "1"})
    planilha.fetch_sheet_metadata()
    assert planilha.chamadas == 2
    assert buscas == [{"a": "1"}]


def test_rotulo_da_especialidade_vem_do_cabecalho() -> None:
    assert oraculo.rotulo_do_profissional("Bia (Fono)") == "Fono"
    assert oraculo.rotulo_do_profissional("Bia") == ""
    vagas = oraculo.vagas(payload_da_semana(), TERCA)
    assert {vaga.especialidade for vaga in vagas if vaga.sala == 2} == {"TO"}


def test_maior_sequencia_conferida_a_mao() -> None:
    """10h às 12h (posições 6 a 9 da manhã). Quarta: Elo (Fono) 10:00, Duda
    (Psicomotricidade) 10:30 e 11:00, Carol (Psicologia) 11:00; TO lotada -> 3.
    Quinta: Bia (Fono) 10:00 e 10:30, Duda 10:30, 11:00 e 11:30; Carol e TO
    lotadas -> 2 (fono e psicomotricidade)."""
    quatro = ["Fono", "Psicomotricidade", "Psicologia", "TO"]
    payload = payload_da_semana()

    assert oraculo.maior_sequencia(payload, QUARTA, quatro, time(10), time(12)) == 3
    assert oraculo.maior_sequencia(payload, QUINTA, quatro, time(10), time(12)) == 2
    assert oraculo.maior_sequencia(payload, QUARTA, ["TO"], time(10), time(12)) == 0
    assert oraculo.maior_sequencia(payload, DOMINGO, quatro, time(10), time(12)) == 0


def test_cenario_poucas_vagas_conferido_a_mao() -> None:
    """c09: TO com 1 vaga na quarta (14:00) e 4 na quinta, todas fora das 10h às 12h."""
    payload = payload_da_semana("poucas_vagas")

    assert _horas(oraculo.vagas(payload, QUARTA), 2) == ["14:00"]
    assert _horas(oraculo.vagas(payload, QUINTA), 2) == ["07:00", "07:30", "08:30", "09:00"]
    assert _horas(oraculo.vagas(payload, QUARTA), 3) == []
    assert "10:00" in _horas(oraculo.vagas(payload, TERCA), 2)
    # A semana padrão não mudou.
    assert payload_da_semana() == payload_da_semana(None)


def test_cenario_planos_por_cor_conferido_a_mao() -> None:
    """c11: ver `planilha.PECAS_DOS_CENARIOS` (preto fora, magenta não reconhecida,
    Azul em azul e em vermelho, observação preta fora do nome)."""
    esperado = oraculo.pacientes_por_plano(payload_da_semana("planos_por_cor"), SEMANA)

    assert esperado.atendimentos == {
        "SulAmérica": {"paciente ficticio azul": 3, "paciente ficticio obs": 1},
        "Klini": {"paciente ficticio azul": 1, "paciente ficticio vermelho": 2},
        "Particulares": {"paciente ficticio lilas": 1},
        "Care Plus": {"paciente ficticio laranja": 2},
        "Unimed": {"paciente ficticio verde": 2, "paciente ficticio musgo": 1},
    }
    assert esperado.nomes_nao_reconhecidos == 1
    assert esperado.pacientes_em_mais_de_um_plano == 1
    # A semana padrão é toda azul: nenhum aviso numérico.
    padrao = oraculo.pacientes_por_plano(payload_da_semana(), SEMANA)
    assert set(padrao.atendimentos) == {"SulAmérica"}
    assert padrao.nomes_nao_reconhecidos == padrao.pacientes_em_mais_de_um_plano == 0


def test_pacientes_por_profissional_conferidos_a_mao() -> None:
    """Cenário `nomes_por_profissional`: homônimo fundido, grupo e duas profissionais."""
    contagem = oraculo.pacientes_por_profissional(
        payload_da_semana("nomes_por_profissional"), SEMANA
    )

    resumo = {
        profissional: {nome: (p.dias, p.atendimentos) for nome, p in por_nome.items()}
        for profissional, por_nome in contagem.items()
    }
    assert resumo == {
        "Bia": {
            "paciente ficticio ametista": ("Seg, Ter, Qua", 3),
            "paciente ficticio basalto": ("Seg", 1),
            "paciente ficticio omega": ("Seg, Qua", 2),
        },
        "Assizely": {
            "paciente ficticio basalto": ("Seg, Qua", 2),
            "paciente ficticio esmeralda": ("Seg, Qua", 2),
            "paciente ficticio feldspato": ("Seg", 1),
        },
        "Carol": {"paciente ficticio ametista": ("Seg", 1)},
        "Elo": {"paciente ficticio feldspato": ("Ter", 1)},
    }


def _lugar(dia: date, inicio: str, fim: str, profissional: str, sala: int) -> oraculo.LugarDoPlano:
    return oraculo.LugarDoPlano(
        dia, time.fromisoformat(inicio), time.fromisoformat(fim), profissional, sala
    )


#: O c06 conferido à mão: na quarta, a Carol (Sala 3, "AA....BB.C") tem o
#: paciente B das 10:00 às 11:00 e vagas das 08:00 às 10:00.
_B_DA_SALA_3 = "Paciente Ficticio Bravo 3"
_C06 = [
    oraculo.PassoDoPlano(
        _B_DA_SALA_3,
        _lugar(QUARTA, "10:00", "11:00", "Carol", 3),
        _lugar(QUARTA, "09:00", "10:00", "Carol", 3),
    ),
    oraculo.PassoDoPlano(None, None, _lugar(QUARTA, "10:00", "11:00", "Carol", 3)),
]


def test_plano_valido_conferido_a_mao() -> None:
    assert oraculo.validar_plano(payload_da_semana(), SEMANA, _C06) == []


def test_plano_na_ordem_errada_ocupa_lugar_ainda_ocupado() -> None:
    problemas = oraculo.validar_plano(payload_da_semana(), SEMANA, list(reversed(_C06)))

    assert "passo 1: destino ocupado nesse momento" in problemas


@pytest.mark.parametrize(
    ("para", "problema"),
    [
        # Outra profissional (Elo, fono) e outra terapia.
        (_lugar(QUARTA, "08:00", "09:00", "Elo", 5), "passo 1: muda profissional ou duração"),
        (_lugar(QUARTA, "08:00", "09:30", "Carol", 3), "passo 1: muda profissional ou duração"),
        # 07:00 está ocupado por A.
        (_lugar(QUARTA, "07:00", "08:00", "Carol", 3), "passo 1: destino ocupado nesse momento"),
        # 12:00 é a pausa.
        (
            _lugar(QUARTA, "11:30", "12:30", "Carol", 3),
            "passo 1: destino fora da grade ou do expediente",
        ),
    ],
)
def test_plano_que_quebra_uma_regra(para: oraculo.LugarDoPlano, problema: str) -> None:
    passos = [oraculo.PassoDoPlano(_B_DA_SALA_3, _C06[0].de, para), _C06[1]]

    assert problema in oraculo.validar_plano(payload_da_semana(), SEMANA, passos)


def test_origem_que_nao_e_do_paciente_ou_nao_e_a_sessao_inteira() -> None:
    errado = oraculo.PassoDoPlano("Paciente Ficticio Alfa 3", _C06[0].de, _C06[0].para)
    assert "passo 1: a origem não traz esse paciente" in oraculo.validar_plano(
        payload_da_semana(), SEMANA, [errado]
    )
    metade = oraculo.PassoDoPlano(
        _B_DA_SALA_3,
        _lugar(QUARTA, "10:00", "10:30", "Carol", 3),
        _lugar(QUARTA, "09:00", "09:30", "Carol", 3),
    )
    assert "passo 1: a origem não é a sessão inteira" in oraculo.validar_plano(
        payload_da_semana(), SEMANA, [metade]
    )


def test_plano_que_poe_paciente_em_dois_lugares_ou_repete_a_terapia_no_dia() -> None:
    # Cenário `realocacao`: Berilo faz psicologia às 10:00 e TO das 16:00 às 18:00.
    payload = payload_da_semana("realocacao")
    berilo = "Paciente Ficticio Berilo"
    dois_lugares = oraculo.PassoDoPlano(
        berilo,
        _lugar(QUARTA, "10:00", "11:00", "Carol", 3),
        _lugar(QUARTA, "16:00", "17:00", "Carol", 3),
    )
    assert "um paciente ficaria em dois lugares ao mesmo tempo" in oraculo.validar_plano(
        payload, SEMANA, [dois_lugares]
    )
    novo_do_berilo = oraculo.PassoDoPlano(
        berilo, None, _lugar(QUARTA, "16:00", "17:00", "Carol", 3)
    )
    problemas = oraculo.validar_plano(payload, SEMANA, [novo_do_berilo])
    assert "um paciente ficaria com duas sessões da mesma terapia no dia" in problemas
