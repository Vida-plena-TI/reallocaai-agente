"""Casos reais: leitura de `casos.yaml` e os critérios verificáveis.

Um critério olha o que o usuário veria em cada turno: a resposta do agente
(modo `--llm`) ou, no modo `--deterministico`, o texto da tool que a LLM
receberia no lugar dela. Os blocos (`artifact` das tools) entram nos dois. Os
critérios que só fazem sentido sobre a resposta escrita pela LLM (frases,
vocabulário) ficam "n/a" no modo determinístico.

O resultado esperado nunca sai do motor: vem do YAML ou de `oraculo.py`, que
conta direto na grade crua da planilha. Os detalhes dos critérios trazem só
horários, salas, contagens e o termo encontrado — nunca o texto da resposta
nem nomes de pacientes.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

from scripts.avaliar_prompt_renderiza import contar_frases
from tests.casos_reais import oraculo

CAMINHO_DOS_CASOS = Path(__file__).resolve().parent / "casos.yaml"

DIAS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado")

#: Nunca podem aparecer numa resposta (critério `vocabulario_proibido`, que o
#: modo `--llm` aplica a todos os casos).
VOCABULARIO_PROIBIDO = (
    "atendimento_id",
    "exatamente como veio",
    "registrar",
    "encaixei",
    "tool",
    "slot",
)

Status = Literal["passou", "falhou", "xfail", "xpass", "n/a", "pulado"]


@dataclass(frozen=True)
class Chamada:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Turno:
    pergunta: str
    chamada: Chamada | None


@dataclass(frozen=True)
class Criterio:
    tipo: str
    params: dict[str, Any]
    #: Turno (a partir de 1) a que o critério se limita; `None` = todos.
    turno: int | None = None


@dataclass(frozen=True)
class Caso:
    id: str
    descricao: str
    hoje: date
    turnos: tuple[Turno, ...]
    criterios: tuple[Criterio, ...]
    xfail: str | None = None
    so_ficticia: bool = False

    @property
    def semana(self) -> dict[str, date]:
        """`segunda` ... `sabado` -> data na semana de `hoje`."""
        segunda = self.hoje - timedelta(days=self.hoje.weekday())
        return {nome: segunda + timedelta(days=indice) for indice, nome in enumerate(DIAS)}


@dataclass
class TurnoObservado:
    """O que aconteceu num turno, sem texto de resposta guardado fora da memória."""

    tools: list[str] = field(default_factory=list)
    textos_das_tools: list[str] = field(default_factory=list)
    blocos: list[dict[str, Any]] = field(default_factory=list)
    #: Resposta da LLM; `None` no modo determinístico.
    resposta: str | None = None
    chamadas_planilha: int = 0
    segundos: float = 0.0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    erro: str | None = None

    @property
    def visivel(self) -> str:
        """O que o usuário leria: a resposta, ou o texto das tools no lugar dela."""
        if self.resposta is not None:
            return self.resposta
        return "\n".join(self.textos_das_tools)


@dataclass(frozen=True)
class Contexto:
    """Dados para o esperado: o payload cru da planilha e a semana do caso."""

    payload: Mapping[str, Any]
    semana: Mapping[str, date]


@dataclass(frozen=True)
class ResultadoCriterio:
    tipo: str
    #: `None` = não se aplica a este modo.
    ok: bool | None
    detalhe: str = ""


# ---------------------------------------------------------------- leitura


def resolver_datas(valor: Any, semana: Mapping[str, date]) -> Any:
    """Troca `"{segunda}"` ... `"{sabado}"` pela data ISO, em qualquer nível."""
    if isinstance(valor, str):
        achado = re.fullmatch(r"\{(\w+)\}", valor)
        if achado and achado.group(1) in semana:
            return semana[achado.group(1)].isoformat()
        return valor
    if isinstance(valor, list):
        return [resolver_datas(item, semana) for item in valor]
    if isinstance(valor, dict):
        return {chave: resolver_datas(item, semana) for chave, item in valor.items()}
    return valor


def _caso(bruto: Mapping[str, Any]) -> Caso:
    desconhecidos = set(bruto) - {
        "id",
        "descricao",
        "hoje",
        "turnos",
        "criterios",
        "xfail",
        "so_ficticia",
    }
    if desconhecidos:
        raise ValueError(f"{bruto.get('id')}: campos desconhecidos {sorted(desconhecidos)}")
    turnos = tuple(
        Turno(
            pergunta=str(turno["pergunta"]),
            chamada=(
                Chamada(str(turno["chamada"]["tool"]), dict(turno["chamada"].get("args") or {}))
                if turno.get("chamada")
                else None
            ),
        )
        for turno in bruto["turnos"]
    )
    criterios: list[Criterio] = []
    for item in bruto["criterios"]:
        params = {k: v for k, v in item.items() if k not in ("tipo", "turno")}
        criterio = Criterio(str(item["tipo"]), params, item.get("turno"))
        if criterio.tipo not in CRITERIOS:
            raise ValueError(f"{bruto['id']}: critério desconhecido {criterio.tipo!r}")
        if criterio.turno is not None and not 1 <= criterio.turno <= len(turnos):
            raise ValueError(f"{bruto['id']}: turno {criterio.turno} não existe")
        criterios.append(criterio)
    hoje = bruto["hoje"]
    return Caso(
        id=str(bruto["id"]),
        descricao=" ".join(str(bruto["descricao"]).split()),
        hoje=hoje if isinstance(hoje, date) else date.fromisoformat(str(hoje)),
        turnos=turnos,
        criterios=tuple(criterios),
        xfail=" ".join(str(bruto["xfail"]).split()) if bruto.get("xfail") else None,
        so_ficticia=bool(bruto.get("so_ficticia", False)),
    )


def carregar_casos(caminho: Path = CAMINHO_DOS_CASOS) -> list[Caso]:
    casos = [_caso(bruto) for bruto in yaml.safe_load(caminho.read_text(encoding="utf-8"))]
    ids = [caso.id for caso in casos]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        raise ValueError(f"ids repetidos: {repetidos}")
    return casos


# ---------------------------------------------------------------- critérios


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.casefold().split())


def _hora(texto: str) -> time:
    return datetime.strptime(texto, "%H:%M").time()


_INTERVALO = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*(?:às|as|a|até|-|\u2013)\s*(\d{1,2}):(\d{2})")


def intervalos(turno: TurnoObservado) -> list[tuple[time, time]]:
    """Faixas de horário no que o usuário veria: texto (`10:00 às 10:30`) e blocos."""
    achados = [
        (time(int(h1), int(m1)), time(int(h2), int(m2)))
        for h1, m1, h2, m2 in _INTERVALO.findall(turno.visivel)
        if int(h1) < 24 and int(h2) < 24
    ]
    for bloco in turno.blocos:
        for faixa in bloco.get("dados", {}).get("faixas", []):
            achados.append((_hora(faixa["inicio"]), _hora(faixa["fim"])))
    return achados


def _erro(turnos: Sequence[TurnoObservado]) -> str | None:
    return next((turno.erro for turno in turnos if turno.erro), None)


def _so_da_resposta(
    verificar: Callable[[Criterio, Sequence[TurnoObservado], Contexto], ResultadoCriterio],
) -> Callable[[Criterio, Sequence[TurnoObservado], Contexto], ResultadoCriterio]:
    """Critério sobre o texto da LLM: n/a no modo determinístico."""

    def criterio(c: Criterio, turnos: Sequence[TurnoObservado], ctx: Contexto) -> ResultadoCriterio:
        if any(turno.resposta is None for turno in turnos):
            return ResultadoCriterio(c.tipo, None, "só no --llm")
        return verificar(c, turnos, ctx)

    return criterio


def _sobre_a_saida(
    verificar: Callable[[Criterio, Sequence[TurnoObservado], Contexto], ResultadoCriterio],
) -> Callable[[Criterio, Sequence[TurnoObservado], Contexto], ResultadoCriterio]:
    """Critério sobre o que a tool/o agente devolveu: falha se o turno deu erro."""

    def criterio(c: Criterio, turnos: Sequence[TurnoObservado], ctx: Contexto) -> ResultadoCriterio:
        erro = _erro(turnos)
        if erro is not None:
            return ResultadoCriterio(c.tipo, False, erro)
        return verificar(c, turnos, ctx)

    return criterio


def _resultado(c: Criterio, ok: bool, detalhe: str = "") -> ResultadoCriterio:
    return ResultadoCriterio(c.tipo, ok, detalhe)


@_sobre_a_saida
def _bloco_presente(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    tipos = [bloco.get("tipo") for turno in turnos for bloco in turno.blocos]
    return _resultado(c, c.params["bloco"] in tipos, f"blocos: {tipos or 'nenhum'}")


@_so_da_resposta
def _maximo_de_frases(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    frases = [contar_frases(turno.resposta or "") for turno in turnos]
    return _resultado(c, all(f <= c.params["maximo"] for f in frases), f"frases: {frases}")


_LINHA_HH_MM = re.compile(
    r"(?m)^\s*(?:[-*•]|\d+[.)])?\s*\**\d{1,2}:\d{2}\s*(?:às|-|\u2013)\s*\d{1,2}:\d{2}"
)


@_so_da_resposta
def _sem_linhas_hh_mm(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    linhas = sum(len(_LINHA_HH_MM.findall(turno.resposta or "")) for turno in turnos)
    return _resultado(c, linhas == 0, f"{linhas} linha(s) 'HH:MM às HH:MM'")


def _faixa(inicio: time, fim: time) -> str:
    return f"{inicio.strftime('%H:%M')}-{fim.strftime('%H:%M')}"


@_sobre_a_saida
def _vagas_dentro_da_janela(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    inicio, fim = _hora(c.params["inicio"]), _hora(c.params["fim"])
    achados = [faixa for turno in turnos for faixa in intervalos(turno)]
    if not achados:
        return _resultado(c, False, "nenhuma vaga devolvida")
    fora = sorted({faixa for faixa in achados if faixa[0] < inicio or faixa[1] > fim})
    exemplos = ", ".join(_faixa(*faixa) for faixa in fora[:5])
    return _resultado(c, not fora, f"{len(fora)} de {len(set(achados))} faixa(s) fora: {exemplos}")


_DIA_NO_TEXTO = re.compile(r"segunda|terca|quarta|quinta|sexta|sabado|\b\d{2}/\d{2}\b")


@_sobre_a_saida
def _sem_sobreposicao_no_dia(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    """Sessões (`HH:MM às HH:MM`) de uma mesma seção de dia não se cruzam."""
    secoes: dict[str, list[tuple[time, time]]] = {}
    for turno in turnos:
        secao = ""
        for linha in turno.visivel.splitlines():
            achado = _DIA_NO_TEXTO.search(_normalizar(linha))
            if achado:
                secao = achado.group(0)
            for h1, m1, h2, m2 in _INTERVALO.findall(linha):
                secoes.setdefault(secao, []).append(
                    (time(int(h1), int(m1)), time(int(h2), int(m2)))
                )
    if not secoes:
        return _resultado(c, False, "nenhuma sessão encontrada")
    cruzadas = [
        f"{dia or '?'} {_faixa(*a)} x {_faixa(*b)}"
        for dia, faixas in secoes.items()
        for i, a in enumerate(sorted(faixas))
        for b in sorted(faixas)[i + 1 :]
        if b[0] < a[1]
    ]
    return _resultado(c, not cruzadas, "; ".join(cruzadas[:5]) or "sem sobreposição")


@_sobre_a_saida
def _nao_coube_com_alternativas(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    texto = _normalizar(" ".join(turno.visivel for turno in turnos))
    tem = {"nao coube": "nao coube" in texto, "alternativa": "alternativa" in texto}
    return _resultado(c, all(tem.values()), f"{tem}")


@_sobre_a_saida
def _frase_proibida(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    texto = _normalizar(" ".join(turno.visivel for turno in turnos))
    achadas = [frase for frase in c.params["frases"] if _normalizar(frase) in texto]
    return _resultado(c, not achadas, f"encontradas: {achadas}" if achadas else "")


def termos_proibidos(texto: str) -> list[str]:
    normalizado = _normalizar(texto)
    return [
        termo
        for termo in VOCABULARIO_PROIBIDO
        if re.search(rf"(?<!\w){re.escape(_normalizar(termo))}s?(?!\w)", normalizado)
    ]


@_so_da_resposta
def _vocabulario_proibido(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    achados = sorted(
        {termo for turno in turnos for termo in termos_proibidos(turno.resposta or "")}
    )
    return _resultado(c, not achados, f"encontrados: {achados}" if achados else "")


_PEDE_ID = re.compile(r"atendimento_id|\b(?:id|identificador|codigo) do atendimento\b")


@_so_da_resposta
def _nao_pede_atendimento_id(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    pede = any(_PEDE_ID.search(_normalizar(turno.resposta or "")) for turno in turnos)
    return _resultado(c, not pede, "pede o id do atendimento" if pede else "")


_PLANO = re.compile(r"(?m)^[\W_]*(?:plano|opcao)\s*(\d+)")


@_sobre_a_saida
def _max_planos(c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto) -> ResultadoCriterio:
    planos = {
        numero
        for turno in turnos
        for linha in turno.visivel.splitlines()
        for numero in _PLANO.findall(_normalizar(linha))
    }
    return _resultado(c, 1 <= len(planos) <= c.params["maximo"], f"{len(planos)} plano(s)")


def _max_chamadas_planilha(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    chamadas = sum(turno.chamadas_planilha for turno in turnos)
    return _resultado(c, chamadas <= c.params["maximo"], f"{chamadas} chamada(s)")


def _max_segundos(c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto) -> ResultadoCriterio:
    segundos = sum(turno.segundos for turno in turnos)
    return _resultado(c, segundos < c.params["maximo"], f"{segundos:.1f}s")


def _numero_da_sala(nome: str) -> int:
    achado = re.search(r"\d+", nome)
    return int(achado.group(0)) if achado else -1


@_sobre_a_saida
def _vagas_iguais_ao_oraculo(
    c: Criterio, turnos: Sequence[TurnoObservado], ctx: Contexto
) -> ResultadoCriterio:
    """Cada vaga de 30 min dos blocos de disponibilidade = cada célula vazia da grade."""
    faixas = [
        faixa
        for turno in turnos
        for bloco in turno.blocos
        if bloco.get("tipo") == "disponibilidade"
        for faixa in bloco["dados"]["faixas"]
    ]
    if not faixas:
        return _resultado(c, False, "sem bloco de disponibilidade")
    do_motor: Counter[tuple[date, int, time]] = Counter()
    for faixa in faixas:
        inicio = datetime.combine(date.fromisoformat(faixa["data"]), _hora(faixa["inicio"]))
        for indice in range(faixa["slots"]):
            momento = inicio + timedelta(minutes=30 * indice)
            do_motor[(momento.date(), _numero_da_sala(faixa["sala_nome"]), momento.time())] += 1
    esperado: Counter[tuple[date, int, time]] = Counter(
        (vaga.dia, vaga.sala, vaga.hora)
        for dia in c.params["dias"]
        for vaga in oraculo.vagas(ctx.payload, ctx.semana[dia])
    )
    faltam, sobram = esperado - do_motor, do_motor - esperado
    exemplos = [
        f"{'falta' if chave in faltam else 'sobra'} {chave[0].strftime('%d/%m')} "
        f"Sala {chave[1]} {chave[2].strftime('%H:%M')}"
        for chave in sorted({*faltam, *sobram})[:5]
    ]
    detalhe = (
        f"esperado {esperado.total()}, motor {do_motor.total()}; "
        f"faltam {faltam.total()}, sobram {sobram.total()}"
    )
    return _resultado(c, not faltam and not sobram, "; ".join([detalhe, *exemplos]))


_ESTADOS: dict[str, re.Pattern[str]] = {
    "nao_existe": re.compile(r"nao (?:existe|ha profissional|encontr|achei|consta)"),
    "nao_atende_no_dia": re.compile(
        r"nao atende|nao (?:esta|trabalha|e) escalad|nao trabalha|folga"
    ),
    "sem_vaga_na_janela": re.compile(
        r"sem vaga|nao (?:ha|tem) (?:vaga|horario)|lotad|nenhum horario|nenhuma vaga"
    ),
}


@_sobre_a_saida
def _estado_profissional(
    c: Criterio, turnos: Sequence[TurnoObservado], ctx: Contexto
) -> ResultadoCriterio:
    """O estado dito (não existe, não atende no dia, sem vaga, com vaga) = o do oráculo."""
    inicio, fim = _hora(c.params["inicio"]), _hora(c.params["fim"])
    na_planilha: str | None = c.params.get("na_planilha")
    esperado = oraculo.estado_profissional(
        ctx.payload,
        na_planilha,
        ctx.semana[c.params["dia"]],
        list(ctx.semana.values()),
        inicio,
        fim,
    )
    texto = _normalizar(" ".join(turno.visivel for turno in turnos))
    ditos = {estado for estado, padrao in _ESTADOS.items() if padrao.search(texto)}
    if any(inicio <= i and f <= fim for turno in turnos for i, f in intervalos(turno)):
        ditos.add("com_vaga")
    cita = na_planilha is None or _normalizar(na_planilha) in texto
    ok = ditos == {esperado} and cita
    detalhe = f"esperado {esperado}, dito {sorted(ditos) or 'nenhum'}"
    if not cita:
        detalhe += f"; não cita {na_planilha}"
    return _resultado(c, ok, detalhe)


CRITERIOS: dict[
    str, Callable[[Criterio, Sequence[TurnoObservado], Contexto], ResultadoCriterio]
] = {
    "bloco_presente": _bloco_presente,
    "maximo_de_frases": _maximo_de_frases,
    "sem_linhas_hh_mm": _sem_linhas_hh_mm,
    "vagas_dentro_da_janela": _vagas_dentro_da_janela,
    "sem_sobreposicao_no_dia": _sem_sobreposicao_no_dia,
    "nao_coube_com_alternativas": _nao_coube_com_alternativas,
    "frase_proibida": _frase_proibida,
    "vocabulario_proibido": _vocabulario_proibido,
    "nao_pede_atendimento_id": _nao_pede_atendimento_id,
    "max_planos": _max_planos,
    "max_chamadas_planilha": _max_chamadas_planilha,
    "max_segundos": _max_segundos,
    "vagas_iguais_ao_oraculo": _vagas_iguais_ao_oraculo,
    "estado_profissional": _estado_profissional,
}


def criterios_do_modo(caso: Caso, *, llm: bool) -> list[Criterio]:
    """Os critérios do caso; no `--llm`, o vocabulário proibido vale para todos."""
    criterios = list(caso.criterios)
    if llm and not any(c.tipo == "vocabulario_proibido" for c in criterios):
        criterios.append(Criterio("vocabulario_proibido", {}))
    return criterios


def avaliar(
    caso: Caso, observados: Sequence[TurnoObservado], contexto: Contexto, *, llm: bool
) -> list[ResultadoCriterio]:
    resultados: list[ResultadoCriterio] = []
    for criterio in criterios_do_modo(caso, llm=llm):
        turnos = observados if criterio.turno is None else [observados[criterio.turno - 1]]
        resultados.append(CRITERIOS[criterio.tipo](criterio, turnos, contexto))
    return resultados


def status_do_caso(caso: Caso, resultados: Sequence[ResultadoCriterio]) -> Status:
    """`xfail`/`xpass` só quando o caso tem motivo de falha esperada."""
    aplicaveis = [r.ok for r in resultados if r.ok is not None]
    if not aplicaveis:
        return "n/a"
    passou = all(aplicaveis)
    if caso.xfail:
        return "xpass" if passou else "xfail"
    return "passou" if passou else "falhou"
