"""Oráculo dos casos reais: o resultado esperado, contado direto na grade crua.

Independente do motor que está sendo testado: não importa nada de `app` (nem
parser, nem engine, nem domínio — `test_oraculo.py` garante) e não reaproveita
regra nenhuma de lá. Lê o mesmo payload que a API do Sheets devolve e conta
do jeito mais simples possível:

- cabeçalho de bloco: linha com alguma célula `Sala N`; a linha seguinte traz
  o profissional de cada coluna;
- linha de horário: `HH:MM` na coluna A, dentro do expediente (07:00 a 11:30
  e 13:00 a 17:30);
- célula vazia sob um profissional = vaga; `FECHADO` = não existe; qualquer
  outro texto = ocupada;
- especialidade de uma vaga: o rótulo entre parênteses do profissional
  (`Bia (Fono)` -> `Fono`).

Só entende o layout simples (uma sala por coluna, sem merge). As
inconsistências da planilha real são assunto dos testes do parser, não daqui.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

#: Aba de cada `date.weekday()`; domingo não tem aba.
_ABAS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado")

_SALA = re.compile(r"^sala\s*0*(\d+)")
_HORA = re.compile(r"^(\d{1,2}):(\d{2})$")

EstadoProfissional = Literal["nao_existe", "nao_atende_no_dia", "sem_vaga_na_janela", "com_vaga"]


@dataclass(frozen=True, order=True)
class Vaga:
    dia: date
    sala: int
    hora: time
    profissional: str
    #: Rótulo entre parênteses do profissional (`Fono`, `TO`...); vazio sem ele.
    especialidade: str = ""


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.casefold().split())


def _no_expediente(hora: time) -> bool:
    return time(7) <= hora <= time(11, 30) or time(13) <= hora <= time(17, 30)


def _grade(payload: Mapping[str, Any], dia: date) -> list[list[str]]:
    """Texto das células da aba de `dia`; vazio se a aba não existe (ou é domingo)."""
    if dia.weekday() >= len(_ABAS):
        return []
    for aba in payload.get("sheets", []):
        if _normalizar(str(aba.get("properties", {}).get("title", ""))) != _ABAS[dia.weekday()]:
            continue
        return [
            [str(celula.get("formattedValue", "")) for celula in linha.get("values", [])]
            for dados in aba.get("data", [])
            for linha in dados.get("rowData", [])
        ]
    return []


def _celula(linhas: Sequence[Sequence[str]], linha: int, coluna: int) -> str:
    if linha >= len(linhas) or coluna >= len(linhas[linha]):
        return ""
    return linhas[linha][coluna].strip()


def nome_do_profissional(texto: str) -> str:
    """`Bia (Fono)` -> `Bia`: o que vem antes do parêntese ou da quebra de linha."""
    return re.split(r"[(\n]", texto, maxsplit=1)[0].strip()


def rotulo_do_profissional(texto: str) -> str:
    """`Bia (Fono)` -> `Fono`: o que está entre parênteses; vazio sem eles."""
    achado = re.search(r"\(([^)]*)\)", texto)
    return achado.group(1).strip() if achado else ""


@dataclass(frozen=True)
class _Celula:
    sala: int
    profissional: str
    hora: time
    texto: str
    especialidade: str = ""


def _celulas(payload: Mapping[str, Any], dia: date) -> list[_Celula]:
    linhas = _grade(payload, dia)
    celulas: list[_Celula] = []
    salas: dict[int, int] = {}
    profissionais: dict[int, str] = {}
    rotulos: dict[int, str] = {}
    for indice, linha in enumerate(linhas):
        cabecalho = {
            coluna: int(achado.group(1))
            for coluna, texto in enumerate(linha)
            if coluna > 0 and (achado := _SALA.match(_normalizar(texto)))
        }
        if cabecalho:
            salas = cabecalho
            profissionais = {
                coluna: nome_do_profissional(_celula(linhas, indice + 1, coluna))
                for coluna in salas
            }
            rotulos = {
                coluna: rotulo_do_profissional(_celula(linhas, indice + 1, coluna))
                for coluna in salas
            }
            continue
        achado = _HORA.match(_celula(linhas, indice, 0))
        if achado is None or not salas:
            continue
        hora = time(int(achado.group(1)), int(achado.group(2)))
        if not _no_expediente(hora):
            continue
        for coluna, sala in salas.items():
            if not profissionais.get(coluna):
                continue
            texto = _celula(linhas, indice, coluna)
            if _normalizar(texto) == "fechado":
                continue
            celulas.append(_Celula(sala, profissionais[coluna], hora, texto, rotulos[coluna]))
    return celulas


def vagas(payload: Mapping[str, Any], dia: date) -> list[Vaga]:
    """Toda célula vazia sob um profissional, no expediente de `dia`."""
    return sorted(
        Vaga(dia, celula.sala, celula.hora, celula.profissional, celula.especialidade)
        for celula in _celulas(payload, dia)
        if not celula.texto
    )


def profissionais_do_dia(payload: Mapping[str, Any], dia: date) -> set[str]:
    return {celula.profissional for celula in _celulas(payload, dia)}


def estado_profissional(
    payload: Mapping[str, Any],
    nome: str | None,
    dia: date,
    semana: Sequence[date],
    inicio: time,
    fim: time,
) -> EstadoProfissional:
    """O estado de `nome` (como escrito na planilha) em `dia`, na janela [inicio, fim).

    `nome=None` é o profissional que não existe na planilha.
    """
    alvo = _normalizar(nome or "")
    if not alvo or not any(
        alvo == _normalizar(outro) for d in semana for outro in profissionais_do_dia(payload, d)
    ):
        return "nao_existe"
    if not any(alvo == _normalizar(outro) for outro in profissionais_do_dia(payload, dia)):
        return "nao_atende_no_dia"
    na_janela = [
        vaga
        for vaga in vagas(payload, dia)
        if _normalizar(vaga.profissional) == alvo and inicio <= vaga.hora < fim
    ]
    return "com_vaga" if na_janela else "sem_vaga_na_janela"


def maior_sequencia(
    payload: Mapping[str, Any],
    dia: date,
    especialidades: Sequence[str],
    inicio: time,
    fim: time,
) -> int:
    """Quantas sessões de 30 min, uma logo após a outra e cada uma de uma
    especialidade diferente (rótulos como na planilha: `Fono`, `TO`...), cabem
    em vagas de `dia` dentro de [inicio, fim). Busca exaustiva, sem atalhos."""
    alvos = {_normalizar(e) for e in especialidades}
    por_hora: dict[time, set[str]] = {}
    for vaga in vagas(payload, dia):
        rotulo = _normalizar(vaga.especialidade)
        if rotulo in alvos and inicio <= vaga.hora < fim:
            por_hora.setdefault(vaga.hora, set()).add(rotulo)

    def seguinte(hora: time) -> time:
        return (datetime.combine(dia, hora) + timedelta(minutes=30)).time()

    def a_partir_de(hora: time, usadas: frozenset[str]) -> int:
        return max(
            (
                1 + a_partir_de(seguinte(hora), usadas | {rotulo})
                for rotulo in por_hora.get(hora, set()) - usadas
            ),
            default=0,
        )

    return max((a_partir_de(hora, frozenset()) for hora in por_hora), default=0)


#: Plano de cada cor de texto, por hex EXATO (o motor usa faixas de matiz:
#: aqui, de propósito, a regra é outra e mais simples). Preto é ignorado; o
#: resto é cor não reconhecida.
_PLANO_DA_COR = {
    "#0000FF": "SulAmérica",
    "#FF0000": "Klini",
    "#9900FF": "Particulares",
    "#FF9900": "Care Plus",
    "#38761D": "Unimed",
    "#274E13": "Unimed",
}
_PRETO = "#000000"


def _hex(cor: Mapping[str, Any] | None) -> str:
    if cor is None:
        return _PRETO
    canais = (round(float(cor.get(nome, 0.0)) * 255) for nome in ("red", "green", "blue"))
    return "#{:02X}{:02X}{:02X}".format(*canais)


def _celulas_brutas(payload: Mapping[str, Any], dia: date) -> list[list[Mapping[str, Any]]]:
    if dia.weekday() >= len(_ABAS):
        return []
    for aba in payload.get("sheets", []):
        if _normalizar(str(aba.get("properties", {}).get("title", ""))) == _ABAS[dia.weekday()]:
            return [
                list(linha.get("values", []))
                for dados in aba.get("data", [])
                for linha in dados.get("rowData", [])
            ]
    return []


def _nomes_da_celula(celula: Mapping[str, Any]) -> list[tuple[str, str | None]]:
    """(nome normalizado, plano ou None = não reconhecida) de cada nome não preto."""
    texto = str(celula.get("formattedValue", ""))
    base = _hex(celula.get("userEnteredFormat", {}).get("textFormat", {}).get("foregroundColor"))
    cores = [base] * len(texto)
    trechos = celula.get("textFormatRuns", [])
    for indice, trecho in enumerate(trechos):
        inicio = int(trecho.get("startIndex", 0))
        fim = int(trechos[indice + 1]["startIndex"]) if indice + 1 < len(trechos) else len(texto)
        formato = trecho.get("format", {})
        cor = _hex(formato["foregroundColor"]) if "foregroundColor" in formato else base
        cores[inicio:fim] = [cor] * (fim - inicio)
    nomes: list[tuple[str, str | None]] = []
    inicio = 0
    for fim in [*(i for i, c in enumerate(texto) if c == "/"), len(texto)]:
        pedaco = range(inicio, fim)
        inicio = fim + 1
        coloridas = [i for i in pedaco if cores[i] != _PRETO]
        letras = Counter(cores[i] for i in coloridas if texto[i].isalpha())
        if not letras:
            continue
        nome = " ".join(re.findall(r"[a-z0-9]+", _normalizar("".join(texto[i] for i in coloridas))))
        nomes.append((nome, _PLANO_DA_COR.get(letras.most_common(1)[0][0])))
    return nomes


@dataclass(frozen=True)
class PacientesPorPlano:
    #: plano -> nome normalizado -> atendimentos (sessões) na semana com a cor do plano.
    atendimentos: dict[str, dict[str, int]]
    nomes_nao_reconhecidos: int
    pacientes_em_mais_de_um_plano: int


def pacientes_por_plano(payload: Mapping[str, Any], dias: Sequence[date]) -> PacientesPorPlano:
    """Conta na grade crua: célula de paciente = texto sob um profissional, numa
    linha de horário do expediente; sessão = a mesma coluna com o mesmo texto em
    linhas de horário seguidas."""
    sessoes: dict[str, dict[str, set[tuple[date, int, int]]]] = {}
    nao_reconhecidos: set[str] = set()
    for dia in dias:
        linhas = _celulas_brutas(payload, dia)
        textos = [[str(c.get("formattedValue", "")) for c in linha] for linha in linhas]
        colunas: set[int] = set()
        inicio_da_sessao: dict[int, tuple[int, str]] = {}
        for indice, linha in enumerate(textos):
            cabecalho = {
                coluna
                for coluna, texto in enumerate(linha)
                if coluna > 0 and _SALA.match(_normalizar(texto))
            }
            if cabecalho:
                colunas = {c for c in cabecalho if _celula(textos, indice + 1, c)}
                inicio_da_sessao = {}
                continue
            achado = _HORA.match(_celula(textos, indice, 0))
            if achado is None or not _no_expediente(time(int(achado[1]), int(achado[2]))):
                inicio_da_sessao = {}
                continue
            for coluna in colunas:
                texto = _celula(textos, indice, coluna)
                if not texto or _normalizar(texto) == "fechado":
                    inicio_da_sessao.pop(coluna, None)
                    continue
                anterior = inicio_da_sessao.get(coluna)
                if anterior is None or anterior[1] != texto:
                    inicio_da_sessao[coluna] = (indice, texto)
                sessao = (dia, coluna, inicio_da_sessao[coluna][0])
                for nome, plano in _nomes_da_celula(linhas[indice][coluna]):
                    if plano is None:
                        nao_reconhecidos.add(nome)
                    else:
                        sessoes.setdefault(plano, {}).setdefault(nome, set()).add(sessao)
    planos_por_nome = Counter(nome for por_nome in sessoes.values() for nome in por_nome)
    return PacientesPorPlano(
        atendimentos={
            plano: {nome: len(s) for nome, s in por_nome.items()}
            for plano, por_nome in sessoes.items()
        },
        nomes_nao_reconhecidos=len(nao_reconhecidos),
        pacientes_em_mais_de_um_plano=sum(1 for n in planos_por_nome.values() if n > 1),
    )


def nome_normalizado(texto: str) -> str:
    """A mesma normalização de nomes do oráculo, para comparar com o motor."""
    return " ".join(re.findall(r"[a-z0-9]+", _normalizar(texto)))


#: Abreviação do dia por `date.weekday()`, como na coluna "dias" do relatório.
_DIAS_CURTOS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb")


@dataclass(frozen=True)
class PacienteDoProfissional:
    #: Dias da semana com sessão, em ordem ("Seg, Qua").
    dias: str
    atendimentos: int


def pacientes_por_profissional(
    payload: Mapping[str, Any], dias: Sequence[date]
) -> dict[str, dict[str, PacienteDoProfissional]]:
    """profissional (`Bia`) -> nome normalizado do paciente -> dias e sessões.

    Célula ocupada = texto sob um profissional no expediente; cada nome
    separado por `/` é um paciente (sessão em grupo conta para cada um);
    sessão = a mesma sala com o mesmo texto em meias horas seguidas.
    """
    sessoes: dict[str, dict[str, set[tuple[date, int, time]]]] = {}
    for dia in dias:
        anterior: dict[int, tuple[time, str, time]] = {}
        for celula in sorted(_celulas(payload, dia), key=lambda c: (c.sala, c.hora)):
            if not celula.texto:
                continue
            ultimo = anterior.get(celula.sala)
            seguida = (
                ultimo is not None
                and ultimo[1] == celula.texto
                and (datetime.combine(dia, ultimo[2]) + timedelta(minutes=30)).time() == celula.hora
            )
            inicio = ultimo[0] if seguida and ultimo is not None else celula.hora
            anterior[celula.sala] = (inicio, celula.texto, celula.hora)
            for nome in celula.texto.split("/"):
                normalizado = nome_normalizado(nome)
                if normalizado:
                    sessoes.setdefault(celula.profissional, {}).setdefault(normalizado, set()).add(
                        (dia, celula.sala, inicio)
                    )
    return {
        profissional: {
            nome: PacienteDoProfissional(
                dias=", ".join(_DIAS_CURTOS[d.weekday()] for d in sorted({s[0] for s in conjunto})),
                atendimentos=len(conjunto),
            )
            for nome, conjunto in por_nome.items()
        }
        for profissional, por_nome in sessoes.items()
    }


@dataclass(frozen=True)
class LugarDoPlano:
    dia: date
    inicio: time
    fim: time
    profissional: str
    sala: int


@dataclass(frozen=True)
class PassoDoPlano:
    """Um passo do plano de realocação, como a tela o mostra."""

    #: Nome do paciente; `None` = paciente novo sem nome.
    paciente: str | None
    #: `None` = paciente novo, sem lugar de origem.
    de: LugarDoPlano | None
    para: LugarDoPlano


def _meias_horas_entre(inicio: time, fim: time) -> list[time]:
    horas = []
    atual = datetime.combine(date(2000, 1, 1), inicio)
    while atual.time() < fim:
        horas.append(atual.time())
        atual += timedelta(minutes=30)
    return horas


_NOVO = "\x00paciente novo"


def _nomes_da_celula_texto(texto: str) -> set[str]:
    if texto == _NOVO:
        return {_NOVO}
    return {nome_normalizado(parte) for parte in texto.split("/") if nome_normalizado(parte)}


def _sessoes(
    grade: Mapping[tuple[date, int, time], str], rotulos: Mapping[tuple[date, int], str]
) -> list[tuple[frozenset[str], str, date, int, time, time]]:
    """(pacientes, especialidade, dia, sala, início, fim) de cada sessão da grade."""
    sessoes = []
    for (dia, sala, hora), texto in sorted(grade.items()):
        if not texto:
            continue
        anterior = (datetime.combine(dia, hora) - timedelta(minutes=30)).time()
        if grade.get((dia, sala, anterior)) == texto:
            continue
        fim = hora
        while grade.get((dia, sala, fim)) == texto:
            fim = (datetime.combine(dia, fim) + timedelta(minutes=30)).time()
        sessoes.append(
            (frozenset(_nomes_da_celula_texto(texto)), rotulos[(dia, sala)], dia, sala, hora, fim)
        )
    return sessoes


def validar_plano(
    payload: Mapping[str, Any], dias: Sequence[date], passos: Sequence[PassoDoPlano]
) -> list[str]:
    """Problemas do plano aplicado à grade crua, na ordem dos passos (vazio: válido).

    Cada passo tira a sessão INTEIRA do paciente do lugar de origem e a põe no
    destino, que precisa existir sob o mesmo profissional e estar vago naquele
    momento. No fim: ninguém em dois lugares ao mesmo tempo, a contagem de
    sessões por (paciente, especialidade, dia) não passa de max(antes, 1) e as
    especialidades de cada paciente na semana não mudam (o paciente novo ganha
    só a do pedido).
    """
    grade: dict[tuple[date, int, time], str] = {}
    profissionais: dict[tuple[date, int], str] = {}
    rotulos: dict[tuple[date, int], str] = {}
    for dia in dias:
        for celula in _celulas(payload, dia):
            grade[(dia, celula.sala, celula.hora)] = celula.texto
            profissionais[(dia, celula.sala)] = celula.profissional
            rotulos[(dia, celula.sala)] = celula.especialidade
    antes = _sessoes(grade, rotulos)
    problemas: list[str] = []
    envolvidos: set[str] = set()
    novos: dict[str, str] = {}
    for numero, passo in enumerate(passos, 1):
        nome = nome_normalizado(passo.paciente) if passo.paciente else _NOVO
        texto = passo.paciente or _NOVO
        para = passo.para
        if passo.de is not None:
            de = passo.de
            envolvidos.add(nome)
            horas = _meias_horas_entre(de.inicio, de.fim)
            if any(
                nome not in _nomes_da_celula_texto(grade.get((de.dia, de.sala, h), ""))
                for h in horas
            ):
                problemas.append(f"passo {numero}: a origem não traz esse paciente")
                continue
            texto = grade[(de.dia, de.sala, horas[0])]
            antes_da_sessao = (datetime.combine(de.dia, de.inicio) - timedelta(minutes=30)).time()
            if texto in (
                grade.get((de.dia, de.sala, antes_da_sessao)),
                grade.get((de.dia, de.sala, de.fim)),
            ):
                problemas.append(f"passo {numero}: a origem não é a sessão inteira")
            if profissionais.get((de.dia, de.sala)) != de.profissional:
                problemas.append(f"passo {numero}: profissional de origem não confere")
            if (de.profissional, len(horas)) != (
                para.profissional,
                len(_meias_horas_entre(para.inicio, para.fim)),
            ):
                problemas.append(f"passo {numero}: muda profissional ou duração")
            if rotulos.get((de.dia, de.sala)) != rotulos.get((para.dia, para.sala)):
                problemas.append(f"passo {numero}: muda a especialidade")
            for h in horas:
                grade[(de.dia, de.sala, h)] = ""
        elif passo.paciente:
            envolvidos.add(nome)
            novos[nome] = rotulos.get((para.dia, para.sala), "")
        if profissionais.get((para.dia, para.sala)) != para.profissional:
            problemas.append(f"passo {numero}: o destino não é desse profissional")
            continue
        for h in _meias_horas_entre(para.inicio, para.fim):
            if (para.dia, para.sala, h) not in grade:
                problemas.append(f"passo {numero}: destino fora da grade ou do expediente")
            elif grade[(para.dia, para.sala, h)]:
                problemas.append(f"passo {numero}: destino ocupado nesse momento")
            grade[(para.dia, para.sala, h)] = texto

    depois = _sessoes(grade, rotulos)
    for nome in envolvidos:
        das_antes = [s for s in antes if nome in s[0]]
        das_depois = [s for s in depois if nome in s[0]]
        cruzam = sum(
            1
            for i, a in enumerate(das_depois)
            for b in das_depois[i + 1 :]
            if a[2] == b[2] and a[4] < b[5] and b[4] < a[5]
        )
        cruzavam = sum(
            1
            for i, a in enumerate(das_antes)
            for b in das_antes[i + 1 :]
            if a[2] == b[2] and a[4] < b[5] and b[4] < a[5]
        )
        if cruzam > cruzavam:
            problemas.append("um paciente ficaria em dois lugares ao mesmo tempo")
        contagem_antes = Counter((s[1], s[2]) for s in das_antes)
        for chave, quantidade in Counter((s[1], s[2]) for s in das_depois).items():
            if quantidade > max(contagem_antes[chave], 1):
                problemas.append("um paciente ficaria com duas sessões da mesma terapia no dia")
        especialidades_antes = {s[1] for s in das_antes} | (
            {novos[nome]} if nome in novos else set()
        )
        if {s[1] for s in das_depois} != especialidades_antes:
            problemas.append("as terapias de um paciente na semana mudariam")
    return problemas


# ------------------------------------------------------------ enxugamento

Turno = Literal["manha", "tarde"]


def _turno(hora: time) -> Turno:
    """Turno pelo horário de início: manhã antes do meio-dia."""
    return "manha" if hora < time(12) else "tarde"


def _profissionais_das_colunas(
    payload: Mapping[str, Any], dias: Sequence[date]
) -> tuple[
    dict[tuple[date, int, time], str], dict[tuple[date, int], str], dict[tuple[date, int], str]
]:
    grade: dict[tuple[date, int, time], str] = {}
    profissionais: dict[tuple[date, int], str] = {}
    rotulos: dict[tuple[date, int], str] = {}
    for dia in dias:
        for celula in _celulas(payload, dia):
            grade[(dia, celula.sala, celula.hora)] = celula.texto
            profissionais[(dia, celula.sala)] = celula.profissional
            rotulos[(dia, celula.sala)] = celula.especialidade
    return grade, profissionais, rotulos


def _turnos_da_grade(
    grade: Mapping[tuple[date, int, time], str],
    profissionais: Mapping[tuple[date, int], str],
    rotulos: Mapping[tuple[date, int], str],
) -> set[tuple[str, date, Turno]]:
    return {
        (_normalizar(profissionais[(dia, sala)]), dia, _turno(inicio))
        for _, _, dia, sala, inicio, _ in _sessoes(grade, rotulos)
    }


def turnos_pagos(payload: Mapping[str, Any], dias: Sequence[date]) -> set[tuple[str, date, Turno]]:
    """(profissional normalizado, dia, turno) com pelo menos uma sessão começando nele.

    Duas colunas do mesmo profissional no mesmo dia contam um turno só.
    """
    grade, profissionais, rotulos = _profissionais_das_colunas(payload, dias)
    return _turnos_da_grade(grade, profissionais, rotulos)


@dataclass(frozen=True)
class PassoDoCenario:
    """Um passo de um cenário de enxugamento, como a tela o mostra."""

    paciente: str
    de: LugarDoPlano
    para: LugarDoPlano
    #: Paciente do colega deslocado para abrir espaço: fica com o mesmo profissional.
    abre_espaco: bool = False


@dataclass(frozen=True)
class ResultadoCenario:
    problemas: list[str]
    turnos_antes: set[tuple[str, date, Turno]]
    turnos_depois: set[tuple[str, date, Turno]]

    @property
    def liberados(self) -> int:
        return len(self.turnos_antes - self.turnos_depois)

    @property
    def novos(self) -> int:
        return len(self.turnos_depois - self.turnos_antes)


def validar_cenario(
    payload: Mapping[str, Any], dias: Sequence[date], passos: Sequence[PassoDoCenario]
) -> ResultadoCenario:
    """Aplica o cenário à grade crua, na ordem dos passos, e confere do zero.

    Cada passo tira a sessão INTEIRA do paciente da origem e a põe no destino, que
    precisa existir sob o profissional citado, com o mesmo rótulo de terapia e a
    mesma duração, e estar vago naquele momento (trocar de profissional da mesma
    terapia vale, menos para quem só abre espaço). No fim: ninguém em dois lugares
    ao mesmo tempo, sessões por (paciente, terapia, dia) até max(antes, 1), as
    terapias de cada paciente na semana iguais e nenhum turno novo.
    """
    grade, profissionais, rotulos = _profissionais_das_colunas(payload, dias)
    antes_turnos = _turnos_da_grade(grade, profissionais, rotulos)
    antes = _sessoes(grade, rotulos)
    problemas: list[str] = []
    envolvidos: set[str] = set()
    for numero, passo in enumerate(passos, 1):
        de, para = passo.de, passo.para
        nomes = _nomes_da_celula_texto(passo.paciente)
        envolvidos.update(nomes)
        horas = _meias_horas_entre(de.inicio, de.fim)
        if not nomes or any(
            not nomes <= _nomes_da_celula_texto(grade.get((de.dia, de.sala, h), "")) for h in horas
        ):
            problemas.append(f"passo {numero}: a origem não traz esse paciente")
            continue
        texto = grade[(de.dia, de.sala, horas[0])]
        antes_da_sessao = (datetime.combine(de.dia, de.inicio) - timedelta(minutes=30)).time()
        if texto in (
            grade.get((de.dia, de.sala, antes_da_sessao)),
            grade.get((de.dia, de.sala, de.fim)),
        ):
            problemas.append(f"passo {numero}: a origem não é a sessão inteira")
        if _normalizar(profissionais.get((de.dia, de.sala), "")) != _normalizar(de.profissional):
            problemas.append(f"passo {numero}: profissional de origem não confere")
        if _normalizar(profissionais.get((para.dia, para.sala), "")) != _normalizar(
            para.profissional
        ):
            problemas.append(f"passo {numero}: o destino não é desse profissional")
            continue
        if len(horas) != len(_meias_horas_entre(para.inicio, para.fim)):
            problemas.append(f"passo {numero}: muda a duração")
        if rotulos.get((de.dia, de.sala)) != rotulos.get((para.dia, para.sala)):
            problemas.append(f"passo {numero}: muda a especialidade")
        if passo.abre_espaco and _normalizar(de.profissional) != _normalizar(para.profissional):
            problemas.append(f"passo {numero}: quem abre espaço mudou de profissional")
        for h in horas:
            grade[(de.dia, de.sala, h)] = ""
        for h in _meias_horas_entre(para.inicio, para.fim):
            if (para.dia, para.sala, h) not in grade:
                problemas.append(f"passo {numero}: destino fora da grade ou do expediente")
            elif grade[(para.dia, para.sala, h)]:
                problemas.append(f"passo {numero}: destino ocupado nesse momento")
            grade[(para.dia, para.sala, h)] = texto

    depois = _sessoes(grade, rotulos)
    for nome in envolvidos:
        das_antes = [s for s in antes if nome in s[0]]
        das_depois = [s for s in depois if nome in s[0]]

        def cruzamentos(sessoes: list[tuple[frozenset[str], str, date, int, time, time]]) -> int:
            return sum(
                1
                for i, a in enumerate(sessoes)
                for b in sessoes[i + 1 :]
                if a[2] == b[2] and a[4] < b[5] and b[4] < a[5]
            )

        if cruzamentos(das_depois) > cruzamentos(das_antes):
            problemas.append("um paciente ficaria em dois lugares ao mesmo tempo")
        contagem_antes = Counter((s[1], s[2]) for s in das_antes)
        for chave, quantidade in Counter((s[1], s[2]) for s in das_depois).items():
            if quantidade > max(contagem_antes[chave], 1):
                problemas.append("um paciente ficaria com duas sessões da mesma terapia no dia")
        if {s[1] for s in das_depois} != {s[1] for s in das_antes}:
            problemas.append("as terapias de um paciente na semana mudariam")
        duracoes = sorted((s[1], len(_meias_horas_entre(s[4], s[5]))) for s in das_antes)
        if duracoes != sorted((s[1], len(_meias_horas_entre(s[4], s[5]))) for s in das_depois):
            problemas.append("a duração das sessões de um paciente mudaria")
    depois_turnos = _turnos_da_grade(grade, profissionais, rotulos)
    if depois_turnos - antes_turnos:
        problemas.append("o cenário abre turno que estava vazio")
    return ResultadoCenario(problemas, antes_turnos, depois_turnos)
