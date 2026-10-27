"""Testes de `scripts/avaliar_casos_reais.py` sem modelo nem planilha real."""

import json
from pathlib import Path

import pytest

import scripts.avaliar_casos_reais as avaliacao
from scripts.avaliar_casos_reais import _argumentos, avaliar_deterministico, selecionar_casos
from tests.casos_reais.casos import carregar_casos
from tests.casos_reais.planilha import payload_da_semana


def test_exige_um_modo() -> None:
    with pytest.raises(SystemExit):
        _argumentos([])
    with pytest.raises(SystemExit):
        _argumentos(["--deterministico", "--llm"])


def test_llm_exige_os_dois_precos() -> None:
    with pytest.raises(SystemExit):
        _argumentos(["--llm"])
    with pytest.raises(SystemExit):
        _argumentos(["--llm", "--preco-entrada", "1"])
    argumentos = _argumentos(["--llm", "--preco-entrada", "1", "--preco-saida", "4"])
    assert argumentos.repeticoes == 1 and argumentos.confirmar is False


@pytest.mark.parametrize(
    "extra", [["--confirmar"], ["--repeticoes", "2"], ["--preco-entrada", "1"]]
)
def test_opcoes_pagas_so_com_llm(extra: list[str]) -> None:
    with pytest.raises(SystemExit):
        _argumentos(["--deterministico", *extra])


def test_selecionar_casos() -> None:
    casos = carregar_casos()
    assert [c.id for c in selecionar_casos(casos, "c07, c01")] == ["c01", "c07"]
    with pytest.raises(SystemExit, match="c99"):
        selecionar_casos(casos, "c01,c99")


def test_llm_sem_confirmar_so_estima(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def proibido() -> None:
        raise AssertionError("não pode criar o modelo sem --confirmar")

    monkeypatch.setattr(avaliacao, "criar_chat_model", proibido)
    codigo = avaliacao.main(
        ["--llm", "--preco-entrada", "2", "--preco-saida", "8", "--saida", str(tmp_path)]
    )
    saida = capsys.readouterr().out
    assert codigo == 0
    assert "Estimativa: 8 caso(s), 15 turno(s)" in saida
    assert "Nada foi chamado" in saida
    assert list(tmp_path.iterdir()) == []


def test_deterministico_ponta_a_ponta_sem_nomes_de_pacientes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    codigo = avaliacao.main(["--deterministico", "--saida", str(tmp_path)])
    saida = capsys.readouterr().out
    (arquivo,) = tmp_path.iterdir()
    detalhe = json.loads(arquivo.read_text(encoding="utf-8"))
    assert codigo == 0
    assert {caso["id"]: caso["status"] for caso in detalhe["casos"]} == {
        "c01": "passou",
        "c02": "passou",
        "c03": "passou",
        "c04": "passou",
        "c05": "passou",
        "c06": "xfail",
        "c07": "passou",
        "c08": "passou",
    }
    for texto in (saida, arquivo.read_text(encoding="utf-8")):
        assert "Paciente Ficticio" not in texto


def test_planilha_de_testes_pula_casos_so_da_ficticia() -> None:
    casos = [caso for caso in carregar_casos() if caso.id in ("c01", "c05")]
    resultados = avaliar_deterministico(casos, payload=payload_da_semana())
    assert [r.status for r in resultados] == ["passou", "pulado"]


def test_xpass_e_falha_inesperada_contam_como_falha() -> None:
    (c01,) = avaliar_deterministico([c for c in carregar_casos() if c.id == "c01"])
    assert not avaliacao.houve_falha([c01])
    c01.execucoes[0].status = "xpass"
    assert avaliacao.houve_falha([c01])


def test_leitura_do_catalogo_conta_no_primeiro_turno() -> None:
    """Criar as tools lê o catálogo: a régua do c07 (1 chamada à planilha) precisa vê-la."""
    (c07,) = avaliar_deterministico([c for c in carregar_casos() if c.id == "c07"])
    (turno,) = c07.execucoes[0].turnos
    assert turno.chamadas_planilha == 1
