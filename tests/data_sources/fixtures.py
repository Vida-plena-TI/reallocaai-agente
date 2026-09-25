"""Abas fictícias que reproduzem as inconsistências da planilha real.

Nenhum nome aqui é de gente de verdade. O que estas fixtures reproduzem é a
**forma** do preenchimento encontrada na planilha da clínica — offset de bloco
diferente por aba, sala mesclada em várias colunas, estagiário dividindo a
coluna com o profissional, cabeçalho de sala que some num dos blocos,
especialidade escrita só num deles, sessão em grupo separada por barra, sujeira
de digitação, linha de horário duplicada, cabeçalho de sala em branco,
especialidade que não aparece escrita em lugar nenhum e a mesma profissional
escrita de duas formas. Os dados reais (nomes de pacientes e de profissionais)
nunca entram no repositório: os nomes cobertos pelos mapas de fallback de
`app.domain.constants` são estendidos com nomes fictícios no próprio teste.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AbaFicticia:
    """O que a API do Sheets devolveria de uma aba, no formato que o parser espera."""

    valores: list[list[str]]
    merges: list[str] = field(default_factory=list)
    cores: dict[str, str] = field(default_factory=dict)


#: Aba no formato da segunda-feira real: linha de título mesclada, cabeçalho de
#: salas na linha 2 e o bloco da tarde a partir da linha 9.
#:
#: Reproduz, em ordem de coluna: sala normal (B), sala cujo cabeçalho só existe
#: no bloco da manhã (C), sala mesclada em duas colunas (D:E), sala escrita como
#: `Sala 04` (F), estagiária dividindo a coluna com o profissional (C3),
#: profissional sem especialidade no bloco da tarde (B10, C10, D10),
#: profissional que nunca aparece com especialidade (F10), `FECHADO`, célula
#: vazia, sessão em grupo (E4:E5), sessão de uma hora (B4:B5), sujeira de
#: digitação (F4) e a divisória de 12:00 entre os blocos.
SEGUNDA_FICTICIA = AbaFicticia(
    valores=[
        ["Agenda fictícia", "", "", "", "", ""],
        ["", "Sala 1", "Sala 2", "Sala 3", "", "Sala 04"],
        [
            "",
            "Ana Beatriz (Fono)",
            "Bruno/Raissa\nTO",
            "Carla (Psicologia)",
            "Dora (Psicomotricidade)",
            "Elis Nutri",
        ],
        ["08:00", "Zezinho Mendes", "", "FECHADO", "Ravy Fictício/Samuel Ficto", ","],
        ["08:30", "Zezinho Mendes", "Marina Duarte", " fechado ", "Ravy Fictício/Samuel Ficto", ""],
        ["09:00", "Marina Duarte", "", "", "", ""],
        ["12:00", "", "", "", "", ""],
        ["", "", "", "", "", ""],
        ["", "Sala 1", "", "Sala 3", "", "Sala 04"],
        ["", "Ana Beatriz", "Bruno TO", "Carla", "", "Fábio"],
        ["13:00", "Zezinho Mendes", "Marina Duarte", "", "", ""],
        ["13:30", "", "", "", "", ""],
    ],
    merges=["A1:F1", "D2:E2", "D9:E9"],
    cores={
        "B4": "#0000FF",
        "B5": "#0000FF",
        "B6": "#FF0000",
        "C5": "#FF0000",
        "E4": "#9900FF",
        "E5": "#9900FF",
        # O mesmo paciente da manhã, agora em preto: o encaixe da tarde ainda
        # aguarda autorização, mas o convênio dele já é conhecido.
        "B11": "#000000",
        "C11": "#123456",
    },
)

#: Aba no formato da terça-feira real: sem linha de título, cabeçalho de salas
#: já na linha 1. Traz a linha de horário duplicada vista na quinta-feira.
TERCA_FICTICIA = AbaFicticia(
    valores=[
        ["", "Sala 1", "Sala 2"],
        ["", "Gustavo (TO)", "Helena (Fono)"],
        ["09:00", "Paciente Um", ""],
        ["09:30", "Paciente Um", "Paciente Dois"],
        ["09:30", "Paciente Três", "Paciente Quatro"],
        ["10:00", "", ""],
    ],
)


def aba_de_uma_celula(conteudo: str, cor: str | None = None) -> AbaFicticia:
    """Aba mínima com uma sala, um profissional e um único slot preenchido."""
    return AbaFicticia(
        valores=[
            ["", "Sala 1"],
            ["", "Ivo (Fono)"],
            ["09:00", conteudo],
        ],
        cores={} if cor is None else {"B3": cor},
    )


#: Aba com colunas de profissional cujo cabeçalho de sala ficou em branco.
#:
#: Reproduz o erro de preenchimento confirmado com a clínica: a coluna B tem
#: sala, as colunas C e D não têm em bloco nenhum. Serve para os dois lados do
#: `MAPA_SALA_FALLBACK` — o profissional que está no mapa e o que não está.
COLUNAS_SEM_SALA_FICTICIA = AbaFicticia(
    valores=[
        ["", "Sala 1", "", ""],
        ["", "Ivo (Fono)", "Jonas (Fono)", "Kelly (Fono)"],
        ["09:00", "Paciente Um", "Paciente Dois", "Paciente Três"],
    ],
)

#: Aba em que a especialidade não aparece escrita em lugar nenhum: a célula do
#: profissional traz só o nome. Sem `MAPA_ESPECIALIDADE_FALLBACK` a coluna é
#: descartada.
ESPECIALIDADE_INVISIVEL_FICTICIA = AbaFicticia(
    valores=[
        ["", "Sala 1"],
        ["", "Lia"],
        ["09:00", "Paciente Um"],
    ],
)

#: Aba com estagiário dividindo a coluna com o profissional titular — mesmo
#: formato de `Bruno/Raissa` em `SEGUNDA_FICTICIA`, mas com dois nomes
#: fictícios: nenhum dos dois está em `PROFISSIONAIS_IGNORAR` de produção, é o
#: teste que estende o conjunto com o nome do estagiário, para exercitar o
#: mecanismo sem depender de quem a clínica confirmou.
ESTAGIARIO_DIVIDE_COLUNA_FICTICIA = AbaFicticia(
    valores=[
        ["", "Sala 1"],
        ["", "Nadia/Otavia\nTO"],
        ["09:00", "Paciente Um"],
    ],
)

#: Aba com a mesma profissional titular nas três colunas de uma sala mesclada
#: (`B1:D1`), cada coluna com uma estagiária diferente (ou nenhuma) — a forma da
#: Sala 12 da quarta-feira real. Depois que as estagiárias são filtradas as três
#: colunas viram o mesmo profissional, mas continuam sendo três postos: às 09:00
#: cada posto tem um paciente diferente e às 09:30 só o posto 0 segue ocupado.
#: A coluna E é uma sala comum, de capacidade 1, para comparação.
#:
#: Os nomes das estagiárias (`Iara`, `Joana`) não estão em
#: `PROFISSIONAIS_IGNORAR` de produção: o teste estende o conjunto.
POSTOS_DO_MESMO_TITULAR_FICTICIA = AbaFicticia(
    valores=[
        ["", "Sala 5", "", "", "Sala 6"],
        ["", "Helena/Iara\nTO", "Helena TO", "Helena/Joana\nTO", "Kátia (Fono)"],
        ["09:00", "Paciente Um", "Paciente Dois", "Paciente Três", "Paciente Quatro"],
        ["09:30", "Paciente Um", "", "", ""],
    ],
    merges=["B1:D1"],
)

#: Aba com a mesma profissional escrita de duas formas em blocos diferentes
#: (`Mirna Sousa` e `Mirna Souza`), como acontece na planilha real. Sem o
#: `MAPA_ALIAS_PROFISSIONAL` ela viraria dois profissionais com metade da
#: agenda cada.
ALIAS_DE_PROFISSIONAL_FICTICIA = AbaFicticia(
    valores=[
        ["", "Sala 1"],
        ["", "Mirna Sousa (Fono)"],
        ["09:00", "Paciente Um"],
        ["", "Sala 2"],
        ["", "Mirna Souza (Fono)"],
        ["10:00", "Paciente Dois"],
    ],
)
