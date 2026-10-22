"""Ordem das linhas da tabela "Vagas" do relatório de disponibilidade."""

from datetime import date

from app.ai.blocos_gerais import ordem_vaga
from app.ai.relatorios import FaixaVaga, ProfissionalRelatorio

SEG = date(2026, 9, 28)
TER = date(2026, 9, 29)


def _faixa(
    especialidade: str | None = "Psicologia",
    nome: str = "Ana",
    sala: str = "Sala 1",
    posto: int = 1,
    inicio: str = "08:00",
    data: date = SEG,
) -> FaixaVaga:
    return FaixaVaga(
        data=data,
        dia_semana="segunda-feira",
        periodo="manha",
        profissional=ProfissionalRelatorio(id="x", nome=nome, especialidade=especialidade),
        sala_id=sala,
        sala_nome=sala,
        posto=posto,
        inicio=inicio,
        fim="09:00",
        slots=2,
        cabem=2,
    )


def _ordenar(faixas: list[FaixaVaga]) -> list[FaixaVaga]:
    return sorted(faixas, key=ordem_vaga)


def test_especialidade_em_ordem_alfabetica_sem_acento_nem_maiusculas() -> None:
    nomes = ["Psicologia", "Pedagogia", "Fonoaudiologia", "Fisioterapia", "ética"]

    ordem = _ordenar([_faixa(especialidade=e) for e in nomes])

    # Por código Unicode, "ética" iria para o fim e "Pedagogia" antes de "Fisioterapia" não.
    assert [f.profissional.especialidade for f in ordem] == [
        "ética",
        "Fisioterapia",
        "Fonoaudiologia",
        "Pedagogia",
        "Psicologia",
    ]


def test_data_vem_antes_da_especialidade_e_inicio_antes_do_profissional() -> None:
    ordem = _ordenar(
        [
            _faixa(data=TER, especialidade="Fisioterapia"),
            _faixa(especialidade="Psicologia", inicio="08:00", nome="Bia"),
            _faixa(especialidade="Psicologia", inicio="07:30", nome="Zeca"),
            _faixa(especialidade="Fonoaudiologia", inicio="10:00"),
        ]
    )

    assert [(f.data, f.profissional.especialidade, f.inicio) for f in ordem] == [
        (SEG, "Fonoaudiologia", "10:00"),
        (SEG, "Psicologia", "07:30"),
        (SEG, "Psicologia", "08:00"),
        (TER, "Fisioterapia", "08:00"),
    ]


def test_profissional_sem_acento_nem_maiusculas() -> None:
    ordem = _ordenar([_faixa(nome=n) for n in ["Bruno", "Álvaro", "carla", "Ana"]])

    assert [f.profissional.nome for f in ordem] == ["Álvaro", "Ana", "Bruno", "carla"]


def test_sala_em_ordem_natural_e_posto_numerico() -> None:
    ordem = _ordenar(
        [
            _faixa(sala="Sala 10", posto=1),
            _faixa(sala="Sala 2", posto=10),
            _faixa(sala="Sala 2", posto=2),
            _faixa(sala="Sala 1", posto=1),
        ]
    )

    assert [(f.sala_nome, f.posto) for f in ordem] == [
        ("Sala 1", 1),
        ("Sala 2", 2),
        ("Sala 2", 10),
        ("Sala 10", 1),
    ]


def test_valores_ausentes_vao_para_o_fim() -> None:
    ordem = _ordenar(
        [
            _faixa(especialidade=None, nome="Ana"),
            _faixa(especialidade="Psicologia", nome=""),
            _faixa(especialidade="Psicologia", nome="Zeca", sala=""),
            _faixa(especialidade="Psicologia", nome="Zeca", sala="Sala 9"),
        ]
    )

    assert [(f.profissional.especialidade, f.profissional.nome, f.sala_nome) for f in ordem] == [
        ("Psicologia", "Zeca", "Sala 9"),
        ("Psicologia", "Zeca", ""),
        ("Psicologia", "", "Sala 1"),
        (None, "Ana", "Sala 1"),
    ]
