"""Agrupamento de pacientes por plano (nomes fictícios)."""

from app.domain import NomeColorido, TrechoColorido
from app.engine.pacientes_por_plano import agrupar_pacientes_por_plano

AZUL, VERMELHO, LILAS, LARANJA = "#0000FF", "#FF0000", "#9900FF", "#FF9900"
VERDE, PRETO, MAGENTA = "#38761D", "#000000", "#FF00FF"


def _nome(atendimento: str, *trechos: tuple[str, str]) -> NomeColorido:
    return NomeColorido(
        atendimento_id=atendimento,
        trechos=tuple(TrechoColorido(texto=texto, cor=cor) for texto, cor in trechos),
    )


def _tabela(nomes: list[NomeColorido]) -> dict[str, list[tuple[str, int]]]:
    resultado = agrupar_pacientes_por_plano(nomes)
    return {g.plano: [(p.nome, p.atendimentos) for p in g.pacientes] for g in resultado.grupos}


def test_um_grupo_por_plano_na_ordem_mesmo_vazio() -> None:
    resultado = agrupar_pacientes_por_plano([_nome("a1", ("Ana Ficticia", AZUL))])

    assert [g.plano for g in resultado.grupos] == [
        "SulAmérica",
        "Klini",
        "Particulares",
        "Care Plus",
        "Unimed",
    ]
    assert resultado.pacientes == 1
    assert [len(g.pacientes) for g in resultado.grupos] == [1, 0, 0, 0, 0]


def test_homonimos_se_fundem_e_somam_atendimentos() -> None:
    tabela = _tabela([_nome("a1", ("Ana Ficticia", AZUL)), _nome("a2", ("ANA  FICTÍCIA", AZUL))])

    assert tabela["SulAmérica"] == [("Ana Ficticia", 2)]


def test_mesma_sessao_conta_uma_vez() -> None:
    tabela = _tabela([_nome("a1", ("Ana Ficticia", AZUL)), _nome("a1", ("Ana Ficticia", AZUL))])

    assert tabela["SulAmérica"] == [("Ana Ficticia", 1)]


def test_preto_fica_fora_inclusive_dos_atendimentos() -> None:
    resultado = agrupar_pacientes_por_plano(
        [_nome("a1", ("Bia Ficticia", LILAS)), _nome("a2", ("Bia Ficticia", PRETO))]
    )

    assert _tabela([_nome("a1", ("Bia Ficticia", LILAS))])["Particulares"] == [("Bia Ficticia", 1)]
    assert resultado.nomes_nao_reconhecidos == 0
    assert resultado.pacientes == 1


def test_cor_nao_reconhecida_conta_nomes_distintos() -> None:
    resultado = agrupar_pacientes_por_plano(
        [
            _nome("a1", ("Caio Ficticio", MAGENTA)),
            _nome("a2", ("Caio Ficticio", MAGENTA)),
            _nome("a3", ("Duda Ficticia", MAGENTA)),
        ]
    )

    assert resultado.nomes_nao_reconhecidos == 2
    assert resultado.pacientes == 0


def test_paciente_com_duas_cores_aparece_em_cada_plano() -> None:
    resultado = agrupar_pacientes_por_plano(
        [
            _nome("a1", ("Eva Ficticia", AZUL)),
            _nome("a2", ("Eva Ficticia", VERMELHO)),
            _nome("a3", ("Eva Ficticia", VERMELHO)),
        ]
    )
    tabela = {g.plano: [(p.nome, p.atendimentos) for p in g.pacientes] for g in resultado.grupos}

    assert tabela["SulAmérica"] == [("Eva Ficticia", 1)]
    assert tabela["Klini"] == [("Eva Ficticia", 2)]
    assert resultado.pacientes == 1
    assert resultado.pacientes_em_mais_de_um_plano == 1


def test_observacao_preta_sai_do_nome_e_nao_cria_outro_paciente() -> None:
    tabela = _tabela(
        [
            _nome("a1", ("Fabi Ficticia ", LARANJA), ("(falta)", PRETO)),
            _nome("a2", ("Fabi Ficticia", LARANJA)),
        ]
    )

    assert tabela["Care Plus"] == [("Fabi Ficticia", 2)]


def test_ordem_alfabetica_sem_acento_dentro_do_plano() -> None:
    tabela = _tabela(
        [
            _nome("a1", ("Úrsula Ficticia", VERDE)),
            _nome("a2", ("bruno Ficticio", VERDE)),
            _nome("a3", ("Álvaro Ficticio", VERDE)),
        ]
    )

    assert [nome for nome, _ in tabela["Unimed"]] == [
        "Álvaro Ficticio",
        "bruno Ficticio",
        "Úrsula Ficticia",
    ]
