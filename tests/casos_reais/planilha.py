"""Semana fictícia dos casos reais, no formato cru que a API do Sheets devolve.

É a "cópia da planilha" sobre a qual os casos de `casos.yaml` rodam por
padrão: o motor a lê pelo caminho de produção (`GoogleSheetsDataSource` ->
parser -> engine) e o oráculo (`oraculo.py`) a lê direto, sem passar por
nada de `app`. Por isso este módulo não importa `app` (ver
`test_oraculo.py`).

Layout simples e consistente, de propósito: uma sala por coluna, um
profissional por sala, sem merge, manhã de 07:00 a 11:30 e tarde de 13:00 a
17:30. As inconsistências da planilha real ficam com os testes do parser
(`tests/data_sources/fixtures.py`). Nomes de pacientes e de profissionais são
fictícios.

As abas são nomeadas pelo dia da semana, como na planilha real: a mesma
semana fictícia vale para qualquer data "hoje" dos casos.
"""

from collections.abc import Callable, Mapping
from typing import Any

#: Profissional de cada sala, como escrito na linha de profissionais.
PROFISSIONAIS: dict[str, str] = {
    "Sala 1": "Bia (Fono)",
    "Sala 2": "Assizely (TO)",
    "Sala 3": "Carol (Psicologia)",
    "Sala 4": "Duda (Psicomotricidade)",
    "Sala 5": "Elo (Fono)",
}

HORAS_DA_MANHA = [f"{h:02d}:{m:02d}" for h in range(7, 12) for m in (0, 30)]
HORAS_DA_TARDE = [f"{h:02d}:{m:02d}" for h in range(13, 18) for m in (0, 30)]

#: Agenda de cada sala por aba: 10 caracteres de manhã, espaço, 10 de tarde.
#: `.` = vaga, `#` = FECHADO, letra = sessão de um paciente fictício (a mesma
#: letra seguida é a mesma sessão). Sala fora do dicionário: sem coluna na aba.
#:
#: O que cada caso usa (manhã, posições 6 a 9 = 10:00 a 11:30):
#: - terça: as duas fonos lotadas das 10h às 12h, com vaga antes e à tarde;
#:   psicologia livre às 10:00 (c02);
#: - quarta e quinta: fono e psicomotricidade com vagas antes das 10h e dentro
#:   da janela (c03); TO lotada das 10h às 12h (c04: "não coube");
#: - quinta: Elo não atende, Carol sem vaga das 10h às 12h (c05);
#: - sábado: 07:00 e 07:30 FECHADO na Sala 1.
AGENDA: dict[str, dict[str, str]] = {
    "Segunda": {
        "Sala 1": "AA..BB.CC. DD..EE..FF",
        "Sala 2": "..AABB..CC ..DD..EEFF",
        "Sala 3": "A.BB..CC.. ..DDEE....",
        "Sala 4": "AABB..CCDD AA..BB..CC",
        "Sala 5": "....AABB.. AABB......",
    },
    "Terça": {
        "Sala 1": "AA..BBCCDD ..EEFF..GG",
        "Sala 2": "AABBCC..DD ..EE..FF..",
        "Sala 3": "AABBCC.D.. AA..BBCC..",
        "Sala 4": "..AABB..CC AABB..CC..",
        "Sala 5": "..AABBCCDD AA..BB....",
    },
    "Quarta": {
        "Sala 1": "..AA..B..C AABB..CC..",
        "Sala 2": "......AABB ..AABB..CC",
        "Sala 3": "AA....BB.C AA..BBCC..",
        "Sala 4": "...A..A... ..AABB....",
        "Sala 5": "AA..BB..CC ....AABB..",
    },
    "Quinta": {
        "Sala 1": "..AABB..CC AA..BB..CC",
        "Sala 2": "AA..BBCCDD ..AA..BBCC",
        "Sala 3": "..AABBCCDD AABB..CC..",
        "Sala 4": "AA...BB... ..AA..BB..",
    },
    "Sexta": {
        "Sala 1": "AABB..CC.. ..AABB..CC",
        "Sala 2": "..AA..BBCC AA..BB....",
        "Sala 3": "AA..BB..CC ....AABB..",
        "Sala 4": "..AABBCC.. AABB..CC..",
        "Sala 5": "AA..BBCC.. ..AA..BB..",
    },
    "Sábado": {
        "Sala 1": "##AABB.... ..........",
        "Sala 3": "..AA..BB.. ..........",
    },
}

#: Cenários próprios de alguns casos (campo `cenario` do YAML), com as mesmas
#: salas e profissionais; a `AGENDA` acima continua sendo a semana padrão.
#:
#: `poucas_vagas` (c09): quarta e quinta das 10h às 12h com fono livre e TO e
#: psicologia lotadas. Fora da janela, a TO tem 1 vaga na quarta (14:00) e 4
#: na quinta (07:00, 07:30, 08:30, 09:00); a psicologia, nenhuma nos dois dias.
#: Segunda e terça (não pedidas) têm TO e psicologia livres, inclusive dentro
#: da janela: mais perto dela que qualquer vaga da quarta ou da quinta.
CENARIOS: dict[str, dict[str, dict[str, str]]] = {
    "poucas_vagas": {
        "Segunda": {
            "Sala 1": "AABBCCDDEE FFGGAABBCC",
            "Sala 2": "......AABB ..........",
            "Sala 3": "AA..BB.... ..........",
        },
        "Terça": {
            "Sala 1": "AABBCCDDEE FFGGAABBCC",
            "Sala 2": "AABBCC..DD ..........",
            "Sala 3": "AABBCC.... ..........",
        },
        "Quarta": {
            "Sala 1": "AABBCC.DDE FFGGAABBCC",
            "Sala 2": "AABBCCDDEE FF.GGAABBC",
            "Sala 3": "AABBCCDDEE FFGGAABBCC",
        },
        "Quinta": {
            "Sala 1": "AABBCCD.EE FFGGAABBCC",
            "Sala 2": "..A..BCCDD AABBCCDDEE",
            "Sala 3": "AABBCCDDEE FFGGAABBCC",
        },
        "Sexta": {
            "Sala 1": "AABBCCDDEE FFGGAABBCC",
            "Sala 2": "AABBCCDDEE FFGGAABBCC",
            "Sala 3": "AABBCCDDEE FFGGAABBCC",
        },
        "Sábado": {
            "Sala 1": "AABBCCDDEE FFGGAABBCC",
        },
    },
    # `planos_por_cor` (c11): pacientes com a cor do texto de cada plano; o
    # texto e as cores de cada letra estão em `PECAS_DOS_CENARIOS`.
    "planos_por_cor": {
        "Segunda": {
            "Sala 1": "AA..BB.CC. DD..EE..FF",
            "Sala 2": "..AAGG..HH ..II..KK..",
            "Sala 3": "JJ..LL.... ..........",
        },
        "Terça": {
            "Sala 1": "AA........ ..........",
            "Sala 4": "..EE...... ..........",
        },
    },
    # `nomes_por_profissional` (c12): nomes de pacientes por profissional; o
    # texto de cada letra está em `PECAS_DOS_CENARIOS`.
    "nomes_por_profissional": {
        "Segunda": {
            "Sala 1": "AA..BB.... ..CC......",
            "Sala 2": "EE..FF.... ..........",
            "Sala 3": "..AA...... ..........",
        },
        "Terça": {
            "Sala 1": "..AA...... ..........",
            "Sala 5": "FF........ ..........",
        },
        "Quarta": {
            "Sala 1": "DD........ ..AA......",
            "Sala 2": "..EE...... ..........",
        },
    },
    # `realocacao` (c13): a Carol (psicologia) só atende na quarta e a Assizely
    # (TO) na quarta e na quinta, sem nenhuma vaga. Na quarta, a Carol tem B
    # das 10:00 às 11:00 e vagas só das 16:00 às 18:00, quando B está na TO:
    # psicologia na quarta às 10h só sai com 2 deslocados (B para o lugar de
    # outro paciente da Carol, que vai para a vaga da tarde). TO na quinta às
    # 10h não tem solução: nenhuma cadeia termina numa vaga da TO.
    "realocacao": {
        "Quarta": {
            "Sala 2": "KKLLMMNNOO PPQQRRBBBB",
            "Sala 3": "AACCDDBBEE FFGGHH....",
        },
        "Quinta": {
            "Sala 2": "KKLLMMNNOO PPQQRRSSSS",
        },
    },
    # Panorama de enxugamento (c14, c15 e `tests/engine/test_enxugamento.py`). As
    # profissionais (fictícias) de cada sala estão em `PROFISSIONAIS_DOS_CENARIOS`;
    # todos os pacientes, em `PECAS_DOS_CENARIOS`, fazem só terapia alimentar ("Nutri" na planilha)
    # (menos A do `enxugamento_cadeia`). Manhã leve e o que libera cada turno:
    # - segunda: Acácia com 2 pacientes (A, B) e Bromélia com vagas nos mesmos
    #   horários -> L2 (e o contrário: C, D vão para as vagas da Acácia);
    # - terça: só a Acácia, 1 paciente de manhã (E) e 1 à tarde (F) -> L1;
    # - quarta: Acácia só de manhã (G), Bromélia só à tarde (H) -> L3;
    # - quinta: Acácia só de manhã (I); a Bromélia está escalada à tarde, mas sem
    #   ninguém: levar I para lá abriria turno novo, então sem solução (com
    #   `permitir_trocar_dia`, L4 para outro dia da Acácia);
    # - sexta: só a Acácia, 1 paciente de manhã (J): sem solução no mesmo dia.
    "enxugamento": {
        "Segunda": {
            "Sala 7": "..AA..BB.. ##########",
            "Sala 8": "CC..DD.... ##########",
        },
        "Terça": {
            "Sala 7": ".EE....... FF........",
        },
        "Quarta": {
            "Sala 7": "..GG...... ##########",
            "Sala 8": "########## HH........",
        },
        "Quinta": {
            "Sala 7": "..II...... ##########",
            "Sala 8": "########## ..........",
        },
        "Sexta": {
            "Sala 7": "JJ........ ##########",
        },
    },
    # Três profissionais da mesma terapia, só na segunda de manhã (liberar
    # qualquer turno deixa alguém sem atendimento na semana). A Camélia atende
    # em dois postos ao mesmo tempo (Sala 6 e Sala 9) e está ocupada na Sala 6
    # das 8:00 às 12:00; a Bromélia está lotada. Acácia e Bromélia só liberam
    # indo para o posto simultâneo da Camélia (Sala 9); a Camélia não libera: L
    # (4 h) não cabe em lugar nenhum.
    "enxugamento_tres": {
        "Segunda": {
            "Sala 6": "KKLLLLLLLL ##########",
            "Sala 9": "MM........ ##########",
            "Sala 7": "..NN..OO.. ##########",
            "Sala 8": "QQQQPPRRRR ##########",
        },
    },
    # Turno que só libera com cadeia: A faz terapia alimentar com a Acácia das
    # 10:00 às 11:00 e psicologia com a Carol das 9:00 às 10:00. A única vaga da
    # Bromélia é das 9:00 às 10:00: A só vai para ela se B (10:00) for para a vaga
    # e A ficar no lugar de B.
    "enxugamento_cadeia": {
        "Segunda": {
            "Sala 7": "......AA.. ##########",
            "Sala 8": "CCDD..BBEE ##########",
            "Sala 3": "....AA.... ##########",
        },
    },
    # Turnos que não podem ser liberados: sessão em grupo (segunda), nome em preto
    # (terça) e paciente fixo (quarta, F; os testes o passam nas regras). A
    # Bromélia tem 1 paciente por manhã e a Acácia tem vagas: esses saem.
    "enxugamento_presos": {
        "Segunda": {
            "Sala 7": "..GG...... ##########",
            "Sala 8": "CC........ ##########",
        },
        "Terça": {
            "Sala 7": "..PP...... ##########",
            "Sala 8": "QQ........ ##########",
        },
        "Quarta": {
            "Sala 7": "..FF...... ##########",
            "Sala 8": "RR........ ##########",
        },
    },
}

#: Profissional de cada sala que muda (ou só existe) num cenário; as demais
#: salas seguem `PROFISSIONAIS`. Nomes fictícios.
PROFISSIONAIS_DOS_CENARIOS: dict[str, dict[str, str]] = {
    "enxugamento": {
        "Sala 7": "Ficticia Acacia (Nutri)",
        "Sala 8": "Ficticia Bromelia (Nutri)",
    },
    "enxugamento_tres": {
        "Sala 6": "Ficticia Camelia (Nutri)",
        "Sala 9": "Ficticia Camelia (Nutri)",
        "Sala 7": "Ficticia Acacia (Nutri)",
        "Sala 8": "Ficticia Bromelia (Nutri)",
    },
    "enxugamento_cadeia": {
        "Sala 7": "Ficticia Acacia (Nutri)",
        "Sala 8": "Ficticia Bromelia (Nutri)",
    },
    "enxugamento_presos": {
        "Sala 7": "Ficticia Acacia (Nutri)",
        "Sala 8": "Ficticia Bromelia (Nutri)",
    },
}

#: Cores de texto usadas nos cenários (as da planilha real).
AZUL, VERMELHO, LILAS, LARANJA = "#0000FF", "#FF0000", "#9900FF", "#FF9900"
VERDE, VERDE_ESCURO, PRETO, MAGENTA = "#38761D", "#274E13", "#000000", "#FF00FF"

#: Texto de cada letra de um cenário, em pedaços (texto, cor do texto): o
#: primeiro pedaço dá a cor da célula e, com mais de um, cada pedaço vira um
#: trecho (`textFormatRuns`). Nomes fictícios.
#:
#: `planos_por_cor` (c11), esperado: SulAmérica 2 pacientes (Azul em três
#: salas, um homônimo que se funde, e Obs, cuja observação preta sai do nome);
#: Klini 2 (Vermelho, também na sessão em grupo K, e Azul em vermelho na J:
#: o mesmo paciente com duas cores); Particulares 1 (Lilas; em preto na L não
#: conta); Care Plus 1 (Laranja, inclusive no grupo K); Unimed 2 (os dois
#: verdes). Preto (G) fica de fora e magenta (H) é a cor não reconhecida.
PECAS_DOS_CENARIOS: dict[str, dict[str, tuple[tuple[str, str], ...]]] = {
    "planos_por_cor": {
        "A": (("Paciente Ficticio Azul", AZUL),),
        "B": (("Paciente Ficticio Vermelho", VERMELHO),),
        "C": (("Paciente Ficticio Lilas", LILAS),),
        "D": (("Paciente Ficticio Laranja", LARANJA),),
        "E": (("Paciente Ficticio Verde", VERDE),),
        "F": (("Paciente Ficticio Musgo", VERDE_ESCURO),),
        "G": (("Paciente Ficticio Preto", PRETO),),
        "H": (("Paciente Ficticio Magenta", MAGENTA),),
        "I": (("Paciente Ficticio Obs", AZUL), (" (falta)", PRETO)),
        "J": (("Paciente Ficticio Azul", VERMELHO),),
        "K": (("Paciente Ficticio Vermelho / ", VERMELHO), ("Paciente Ficticio Laranja", LARANJA)),
        "L": (("Paciente Ficticio Lilas", PRETO),),
    },
    # `nomes_por_profissional` (c12), esperado:
    # - Bia (Fono, Sala 1): Ametista (Seg, Ter, Qua; 3), Basalto (Seg; 1) e Omega
    #   (Seg, Qua; 2: "Fictício Ômega" e "ficticio omega" são homônimos e se
    #   fundem) -> 3 pacientes, 6 atendimentos;
    # - Assizely (TO, Sala 2): Basalto e Esmeralda (Seg, Qua; 2, sessão em grupo E) e
    #   Feldspato (Seg; 1) -> 3 pacientes, 3 atendimentos;
    # - Carol (Psicologia, Sala 3): Ametista (Seg; 1);
    # - Elo (Fono, Sala 5): Feldspato (Ter; 1).
    # 5 pacientes distintos; Ametista, Basalto e Feldspato têm duas profissionais.
    "nomes_por_profissional": {
        "A": (("Paciente Ficticio Ametista", AZUL),),
        "B": (("Paciente Ficticio Basalto", AZUL),),
        "C": (("Paciente Fictício Ômega", AZUL),),
        "D": (("paciente ficticio omega", AZUL),),
        "E": (("Paciente Ficticio Esmeralda / Paciente Ficticio Basalto", AZUL),),
        "F": (("Paciente Ficticio Feldspato", AZUL),),
    },
    # `realocacao` (c13): B (Berilo) faz psicologia e TO; os outros, uma terapia só.
    # R e S começam igual ("Paciente Ficticio Rubi ..."): o nome sem o sobrenome é
    # ambíguo (exemplo da pergunta em docs/exemplos-blocos).
    "realocacao": {
        "A": (("Paciente Ficticio Agata", AZUL),),
        "B": (("Paciente Ficticio Berilo", AZUL),),
        "C": (("Paciente Ficticio Calcita", AZUL),),
        "D": (("Paciente Ficticio Dolomita", AZUL),),
        "E": (("Paciente Ficticio Epidoto", AZUL),),
        "F": (("Paciente Ficticio Fluorita", AZUL),),
        "G": (("Paciente Ficticio Granada", AZUL),),
        "H": (("Paciente Ficticio Hematita", AZUL),),
        "K": (("Paciente Ficticio Cianita", AZUL),),
        "L": (("Paciente Ficticio Lapislazuli", AZUL),),
        "M": (("Paciente Ficticio Malaquita", AZUL),),
        "N": (("Paciente Ficticio Nefrita", AZUL),),
        "O": (("Paciente Ficticio Opala", AZUL),),
        "P": (("Paciente Ficticio Pirita", AZUL),),
        "Q": (("Paciente Ficticio Quartzo", AZUL),),
        "R": (("Paciente Ficticio Rubi Alva", AZUL),),
        "S": (("Paciente Ficticio Rubi Estrela", AZUL),),
    },
    "enxugamento": {
        "A": (("Paciente Ficticio Aroeira", AZUL),),
        "B": (("Paciente Ficticio Baoba", AZUL),),
        "C": (("Paciente Ficticio Cedro", AZUL),),
        "D": (("Paciente Ficticio Dendezeiro", AZUL),),
        "E": (("Paciente Ficticio Embauba", AZUL),),
        "F": (("Paciente Ficticio Figueira", AZUL),),
        "G": (("Paciente Ficticio Goiabeira", AZUL),),
        "H": (("Paciente Ficticio Hortela", AZUL),),
        "I": (("Paciente Ficticio Inga", AZUL),),
        "J": (("Paciente Ficticio Jatoba", AZUL),),
    },
    "enxugamento_tres": {
        "K": (("Paciente Ficticio Kiwi", AZUL),),
        "L": (("Paciente Ficticio Louro", AZUL),),
        "M": (("Paciente Ficticio Mangueira", AZUL),),
        "N": (("Paciente Ficticio Nogueira", AZUL),),
        "O": (("Paciente Ficticio Oliveira", AZUL),),
        "P": (("Paciente Ficticio Pitangueira", AZUL),),
        "Q": (("Paciente Ficticio Quaresmeira", AZUL),),
        "R": (("Paciente Ficticio Romanzeira", AZUL),),
    },
    "enxugamento_cadeia": {
        "A": (("Paciente Ficticio Aroeira", AZUL),),
        "B": (("Paciente Ficticio Baoba", AZUL),),
        "C": (("Paciente Ficticio Cedro", AZUL),),
        "D": (("Paciente Ficticio Dendezeiro", AZUL),),
        "E": (("Paciente Ficticio Embauba", AZUL),),
    },
    # G: sessão em grupo; P: nome em preto (aguardando autorização); F: paciente fixo.
    "enxugamento_presos": {
        "C": (("Paciente Ficticio Cedro", AZUL),),
        "G": (("Paciente Ficticio Ipe / Paciente Ficticio Jacaranda", AZUL),),
        "P": (("Paciente Ficticio Pitangueira", PRETO),),
        "Q": (("Paciente Ficticio Quaresmeira", AZUL),),
        "F": (("Paciente Ficticio Figueira", AZUL),),
        "R": (("Paciente Ficticio Romanzeira", AZUL),),
    },
}

#: Letra -> parte fictícia do nome do paciente.
_NOMES = {
    "A": "Alfa",
    "B": "Bravo",
    "C": "Charlie",
    "D": "Delta",
    "E": "Echo",
    "F": "Foxtrot",
    "G": "Golf",
}

#: Fonte azul (convênio fictício conhecido): sem azul o parser leria preto,
#: "aguardando autorização", o que não importa aos casos.
_AZUL = {"blue": 1.0}


def _celula_do_paciente(letra: str, sala: str) -> str:
    return f"Paciente Ficticio {_NOMES[letra]} {sala.removeprefix('Sala ')}"


def _texto(marca: str, sala: str) -> str:
    if marca == ".":
        return ""
    if marca == "#":
        return "FECHADO"
    return _celula_do_paciente(marca, sala)


def valores_da_aba(
    dia: str,
    semana: dict[str, dict[str, str]] = AGENDA,
    pecas: Mapping[str, tuple[tuple[str, str], ...]] | None = None,
    profissionais: Mapping[str, str] = PROFISSIONAIS,
) -> list[list[str]]:
    """Texto das células da aba `dia` ("Segunda", ..., "Sábado"), linha a linha.

    Com `pecas`, o texto de cada letra é o dos seus pedaços; `profissionais` dá o
    profissional de cada sala.
    """

    def texto(marca: str, sala: str) -> str:
        if pecas is not None and marca in pecas:
            return "".join(pedaco for pedaco, _ in pecas[marca])
        return _texto(marca, sala)

    agenda = semana[dia]
    salas = list(agenda)
    linhas: list[list[str]] = []
    for metade, horas in enumerate((HORAS_DA_MANHA, HORAS_DA_TARDE)):
        linhas.append(["", *salas])
        linhas.append(["", *(profissionais[sala] for sala in salas)])
        for posicao, hora in enumerate(horas):
            linhas.append(
                [hora, *(texto(agenda[sala].split()[metade][posicao], sala) for sala in salas)]
            )
        if metade == 0:
            linhas.append(["12:00", *("" for _ in salas)])
    return linhas


def canais(cor: str) -> dict[str, float]:
    """`#RRGGBB` no formato de cor da API (canais de 0 a 1, zeros omitidos)."""
    valores = {
        nome: int(cor[posicao : posicao + 2], 16) / 255
        for nome, posicao in (("red", 1), ("green", 3), ("blue", 5))
    }
    return {nome: valor for nome, valor in valores.items() if valor}


def _marcas_da_aba(dia: str, semana: dict[str, dict[str, str]]) -> list[list[str]]:
    """A letra de cada célula, no mesmo layout de `valores_da_aba` ("" fora da agenda)."""
    agenda = semana[dia]
    salas = list(agenda)
    linhas: list[list[str]] = []
    for metade, horas in enumerate((HORAS_DA_MANHA, HORAS_DA_TARDE)):
        linhas.extend([["", *("" for _ in salas)], ["", *("" for _ in salas)]])
        for posicao, _hora in enumerate(horas):
            linhas.append(["", *(agenda[sala].split()[metade][posicao] for sala in salas)])
        if metade == 0:
            linhas.append(["", *("" for _ in salas)])
    return linhas


def _payload_da_aba(
    titulo: str,
    valores: list[list[str]],
    pecas: Mapping[str, tuple[tuple[str, str], ...]] | None = None,
    marcas: list[list[str]] | None = None,
) -> dict[str, Any]:
    linhas: list[dict[str, Any]] = []
    for numero, linha in enumerate(valores):
        celulas: list[dict[str, Any]] = []
        for indice, texto in enumerate(linha):
            celula: dict[str, Any] = {"formattedValue": texto} if texto else {}
            marca = marcas[numero][indice] if marcas is not None else ""
            pedacos = (pecas or {}).get(marca) if indice > 0 else None
            if pedacos is not None:
                celula["userEnteredFormat"] = {
                    "textFormat": {"foregroundColor": canais(pedacos[0][1])}
                }
                if len(pedacos) > 1:
                    inicios = [0]
                    for pedaco, _ in pedacos[:-1]:
                        inicios.append(inicios[-1] + len(pedaco))
                    celula["textFormatRuns"] = [
                        {"startIndex": inicio, "format": {"foregroundColor": canais(cor)}}
                        for inicio, (_, cor) in zip(inicios, pedacos, strict=True)
                    ]
            # Coluna A (horário) e cabeçalhos ficam sem cor; só paciente é azul.
            elif texto.startswith("Paciente") and indice > 0:
                celula["userEnteredFormat"] = {"textFormat": {"foregroundColor": _AZUL}}
            celulas.append(celula)
        linhas.append({"values": celulas})
    return {"properties": {"title": titulo}, "merges": [], "data": [{"rowData": linhas}]}


def payload_da_semana(cenario: str | None = None) -> dict[str, Any]:
    """O que `fetch_sheet_metadata` devolveria para a planilha fictícia inteira
    (a semana padrão, ou a de um dos `CENARIOS`)."""
    semana = AGENDA if cenario is None else CENARIOS[cenario]
    return payload_de(
        semana,
        {**PROFISSIONAIS, **PROFISSIONAIS_DOS_CENARIOS.get(cenario or "", {})},
        PECAS_DOS_CENARIOS.get(cenario or ""),
    )


def payload_de(
    semana: dict[str, dict[str, str]],
    profissionais: Mapping[str, str],
    pecas: Mapping[str, tuple[tuple[str, str], ...]] | None = None,
) -> dict[str, Any]:
    """O payload de uma semana desenhada no formato de `AGENDA` (com o profissional
    de cada sala), para os testes que montam a semana na hora (escala)."""
    return {
        "sheets": [
            _payload_da_aba(
                dia,
                valores_da_aba(dia, semana, pecas, profissionais),
                pecas,
                _marcas_da_aba(dia, semana),
            )
            for dia in semana
        ]
    }


class PlanilhaContada:
    """Planilha em memória que conta as consultas, no lugar de `gspread.Spreadsheet`.

    `buscar` é chamado só na primeira consulta (a planilha fictícia, ou uma
    leitura única da planilha de testes); as seguintes devolvem o mesmo
    payload, sem gravar nada em disco. `chamadas` conta todas: é quantas
    vezes o RealocAI teria ido à planilha.
    """

    def __init__(
        self, buscar: Callable[[Mapping[str, str] | None], dict[str, Any]] | None = None
    ) -> None:
        self._buscar = buscar or (lambda _params: payload_da_semana())
        self._payload: dict[str, Any] | None = None
        self.chamadas = 0

    def fetch_sheet_metadata(self, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        self.chamadas += 1
        if self._payload is None:
            self._payload = self._buscar(params)
        return self._payload
