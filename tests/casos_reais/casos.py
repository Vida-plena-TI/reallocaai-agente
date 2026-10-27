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
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import yaml

from scripts.avaliar_prompt_renderiza import contar_frases
from tests.casos_reais import oraculo
from tests.casos_reais.planilha import CENARIOS

CAMINHO_DOS_CASOS = Path(__file__).resolve().parent / "casos.yaml"

DIAS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado")

#: Nunca podem aparecer numa resposta (critério `vocabulario_proibido`, que o
#: modo `--llm` aplica a todos os casos).
VOCABULARIO_PROIBIDO = (
    "atendimento_id",
    "exatamente como veio",
    "parâmetros inválidos",
    "tool",
    "slot",
    "bloco",
    "encaixar",
    "encaixei",
    "registrar",
    "registrei",
    "agendar",
    "agendei",
    "marquei",
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
    #: Só no --deterministico: o turno roda pelo agente real (tool, verificação de
    #: fundamentação) com um modelo roteirizado que responde estes textos em ordem
    #: (o segundo só é usado se o primeiro for recusado). Testa a última barreira
    #: do código, não o modelo.
    respostas_simuladas: tuple[str, ...] = ()


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
    #: Semana própria do caso (`planilha.CENARIOS`); `None` = a semana padrão.
    cenario: str | None = None

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
        "cenario",
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
            respostas_simuladas=tuple(str(r) for r in turno.get("respostas_simuladas") or ()),
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
    cenario = str(bruto["cenario"]) if bruto.get("cenario") else None
    if cenario is not None and cenario not in CENARIOS:
        raise ValueError(f"{bruto['id']}: cenário desconhecido {cenario!r}")
    hoje = bruto["hoje"]
    return Caso(
        id=str(bruto["id"]),
        descricao=" ".join(str(bruto["descricao"]).split()),
        hoje=hoje if isinstance(hoje, date) else date.fromisoformat(str(hoje)),
        turnos=turnos,
        criterios=tuple(criterios),
        xfail=" ".join(str(bruto["xfail"]).split()) if bruto.get("xfail") else None,
        # Cenário próprio só existe na planilha fictícia.
        so_ficticia=bool(bruto.get("so_ficticia", False)) or cenario is not None,
        cenario=cenario,
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


#: `dados.relatorio` e valores de `situacao` da tabela do plano semanal
#: (`planejar_semana`); repetidos aqui para o critério não depender de `app`.
RELATORIO_PLANO = "plano_semanal"
SITUACAO_PLANO = "Plano"
SITUACAO_NAO_COUBE = "Não coube"
SITUACAO_ALTERNATIVA = "Alternativa"


@dataclass(frozen=True)
class SessaoDoPlano:
    dia: str
    inicio: time
    fim: time
    sala: str
    especialidade: str


def linhas_do_plano(turnos: Sequence[TurnoObservado], situacao: str) -> list[dict[str, Any]]:
    """Linhas da tabela do plano semanal com essa `situacao`, em todos os blocos."""
    return [
        linha
        for turno in turnos
        for bloco in turno.blocos
        if bloco.get("dados", {}).get("relatorio") == RELATORIO_PLANO
        for linha in bloco["dados"]["linhas"]
        if linha.get("situacao") == situacao
    ]


def _tem_plano(turnos: Sequence[TurnoObservado]) -> bool:
    return any(
        bloco.get("dados", {}).get("relatorio") == RELATORIO_PLANO
        for turno in turnos
        for bloco in turno.blocos
    )


def _dia_da_linha(linha: Mapping[str, Any]) -> str:
    """`quinta-feira` -> `quinta`; `sábado` -> `sabado` (como em `DIAS`)."""
    return _normalizar(str(linha["dia"])).split("-")[0]


def sessoes_do_plano(
    turnos: Sequence[TurnoObservado], situacao: str = SITUACAO_PLANO
) -> list[SessaoDoPlano] | None:
    """As sessões da tabela com essa `situacao` ("Plano" ou "Alternativa"); `None`
    quando o turno não trouxe plano."""
    if not _tem_plano(turnos):
        return None
    sessoes: list[SessaoDoPlano] = []
    for linha in linhas_do_plano(turnos, situacao):
        inicio, _, fim = str(linha["horario"]).partition(" às ")
        sessoes.append(
            SessaoDoPlano(
                dia=_dia_da_linha(linha),
                inicio=_hora(inicio),
                fim=_hora(fim),
                sala=str(linha["sala"]),
                especialidade=str(linha["especialidade"]),
            )
        )
    return sessoes


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
    # Com plano semanal, só as sessões "Plano" contam: as alternativas ficam
    # fora da janela de propósito.
    plano = sessoes_do_plano(turnos)
    achados = (
        [(s.inicio, s.fim) for s in plano]
        if plano is not None
        else [faixa for turno in turnos for faixa in intervalos(turno)]
    )
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
    """Sessões de um mesmo dia não se cruzam: as "Plano" da tabela do plano semanal,
    ou, sem ela, as `HH:MM às HH:MM` de cada seção de dia do texto."""
    secoes: dict[str, list[tuple[time, time]]] = {}
    for sessao in sessoes_do_plano(turnos) or []:
        secoes.setdefault(sessao.dia, []).append((sessao.inicio, sessao.fim))
    for turno in turnos if not secoes else []:
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
    tem = {
        "nao coube": "nao coube" in texto or bool(linhas_do_plano(turnos, "Não coube")),
        "alternativa": "alternativa" in texto or bool(linhas_do_plano(turnos, "Alternativa")),
    }
    especialidade = c.params.get("especialidade")
    if especialidade is not None:
        nao_couberam = {str(n["especialidade"]) for n in linhas_do_plano(turnos, "Não coube")}
        tem[f"{especialidade} nao coube"] = especialidade in nao_couberam
    return _resultado(c, all(tem.values()), f"{tem}")


def _meias_horas(inicio: time, fim: time) -> list[time]:
    """`10:00`, `10:30` para uma sessão de 10:00 às 11:00."""
    momento, limite = datetime.combine(date.min, inicio), datetime.combine(date.min, fim)
    horas: list[time] = []
    while momento < limite:
        horas.append(momento.time())
        momento += timedelta(minutes=30)
    return horas


@_sobre_a_saida
def _plano_em_vagas_reais(
    c: Criterio, turnos: Sequence[TurnoObservado], ctx: Contexto
) -> ResultadoCriterio:
    """Cada slot de cada sessão "Plano" (ou da `situacao` pedida, como "Alternativa")
    é uma célula vazia da grade crua (oráculo)."""
    plano = sessoes_do_plano(turnos, c.params.get("situacao", SITUACAO_PLANO))
    if not plano:
        return _resultado(c, False, "sem sessões de plano")
    livres = {
        (dia, vaga.sala, vaga.hora)
        for dia in {s.dia for s in plano}
        for vaga in oraculo.vagas(ctx.payload, ctx.semana[dia])
    }
    falsas = [
        f"{s.dia} {s.sala} {momento.strftime('%H:%M')}"
        for s in plano
        for momento in _meias_horas(s.inicio, s.fim)
        if (s.dia, _numero_da_sala(s.sala), momento) not in livres
    ]
    return _resultado(c, not falsas, f"{len(plano)} sessão(ões); sem vaga: {', '.join(falsas[:5])}")


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


def _linhas_de_todos_os_planos(turnos: Sequence[TurnoObservado]) -> list[dict[str, Any]]:
    return [
        linha
        for turno in turnos
        for bloco in turno.blocos
        if bloco.get("dados", {}).get("relatorio") == RELATORIO_PLANO
        for linha in bloco["dados"]["linhas"]
    ]


@_sobre_a_saida
def _alternativas_nos_dias_pedidos(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    alternativas = linhas_do_plano(turnos, SITUACAO_ALTERNATIVA)
    if not _tem_plano(turnos):
        return _resultado(c, False, "sem plano")
    fora = sorted({_dia_da_linha(a) for a in alternativas} - set(c.params["dias"]))
    return _resultado(
        c, not fora, f"{len(alternativas)} alternativa(s); fora dos dias pedidos: {fora}"
    )


@_sobre_a_saida
def _alternativas_sem_repeticao(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    if not _tem_plano(turnos):
        return _resultado(c, False, "sem plano")
    chaves = Counter(
        (_dia_da_linha(a), str(a["especialidade"]), str(a["horario"]), str(a["sala"]))
        for a in linhas_do_plano(turnos, SITUACAO_ALTERNATIVA)
    )
    repetidas = [f"{d} {h} {s}" for (d, _e, h, s), n in sorted(chaves.items()) if n > 1]
    return _resultado(c, not repetidas, f"repetidas: {repetidas[:5]}" if repetidas else "")


@_sobre_a_saida
def _alternativa_no_grupo_do_dia(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    """Toda "Alternativa" vem logo depois do "Não coube" (ou de outra "Alternativa")
    da mesma especialidade, e o `dia` dela é o do grupo."""
    if not _tem_plano(turnos):
        return _resultado(c, False, "sem plano")
    grupo: Mapping[str, Any] | None = None
    erradas: list[str] = []
    for linha in _linhas_de_todos_os_planos(turnos):
        situacao = linha.get("situacao")
        if situacao == SITUACAO_NAO_COUBE:
            grupo = linha
        elif situacao == SITUACAO_ALTERNATIVA:
            if grupo is None or (_dia_da_linha(linha), linha["especialidade"]) != (
                _dia_da_linha(grupo),
                grupo["especialidade"],
            ):
                erradas.append(f"{_dia_da_linha(linha)} {linha['horario']}")
        else:
            grupo = None
    return _resultado(c, not erradas, f"fora do grupo: {erradas[:5]}" if erradas else "")


@_sobre_a_saida
def _texto_contem(c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto) -> ResultadoCriterio:
    texto = _normalizar(" ".join(turno.visivel for turno in turnos))
    faltam = [frase for frase in c.params["frases"] if _normalizar(frase) not in texto]
    return _resultado(c, not faltam, f"faltam: {faltam}" if faltam else "")


_DATA = re.compile(r"(?<![\d:])\d{1,2}/\d{1,2}(?![\d:])|\d{4}-\d{2}-\d{2}")


@_sobre_a_saida
def _sem_datas(c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto) -> ResultadoCriterio:
    """Nem o texto nem o plano citam datas (`28/10`, `2026-10-28`): é grade semanal."""
    no_texto = sum(len(_DATA.findall(turno.visivel)) for turno in turnos)
    nos_planos = sum(len(_DATA.findall(str(linha))) for linha in _linhas_de_todos_os_planos(turnos))
    return _resultado(
        c, no_texto + nos_planos == 0, f"datas: {no_texto} no texto, {nos_planos} no plano"
    )


@_sobre_a_saida
def _sequencia_maxima(
    c: Criterio, turnos: Sequence[TurnoObservado], ctx: Contexto
) -> ResultadoCriterio:
    """Em cada dia, as sessões "Plano" são seguidas e são tantas quanto a maior
    sequência que o oráculo acha na grade crua."""
    plano = sessoes_do_plano(turnos)
    if plano is None:
        return _resultado(c, False, "sem plano")
    inicio, fim = _hora(c.params["inicio"]), _hora(c.params["fim"])
    problemas: list[str] = []
    contagens: list[str] = []
    for dia in c.params["dias"]:
        sessoes = sorted((s for s in plano if s.dia == dia), key=lambda s: s.inicio)
        esperado = oraculo.maior_sequencia(
            ctx.payload, ctx.semana[dia], c.params["especialidades"], inicio, fim
        )
        contagens.append(f"{dia} {len(sessoes)}/{esperado}")
        if len(sessoes) != esperado:
            problemas.append(f"{dia}: {len(sessoes)} no plano, {esperado} no oráculo")
        if any(a.fim != b.inicio for a, b in pairwise(sessoes)):
            problemas.append(f"{dia}: sessões não seguidas")
    return _resultado(c, not problemas, "; ".join(problemas or contagens))


def _tools_chamadas(
    c: Criterio, turnos: Sequence[TurnoObservado], _: Contexto
) -> ResultadoCriterio:
    """As tools chamadas no turno: todas as `tools` e nenhuma de `nunca`."""
    chamadas = [tool for turno in turnos for tool in turno.tools]
    faltam = [t for t in c.params.get("tools", []) if t not in chamadas]
    proibidas = [t for t in c.params.get("nunca", []) if t in chamadas]
    return _resultado(c, not faltam and not proibidas, f"chamadas: {chamadas}")


CRITERIOS: dict[
    str, Callable[[Criterio, Sequence[TurnoObservado], Contexto], ResultadoCriterio]
] = {
    "bloco_presente": _bloco_presente,
    "maximo_de_frases": _maximo_de_frases,
    "sem_linhas_hh_mm": _sem_linhas_hh_mm,
    "vagas_dentro_da_janela": _vagas_dentro_da_janela,
    "sem_sobreposicao_no_dia": _sem_sobreposicao_no_dia,
    "nao_coube_com_alternativas": _nao_coube_com_alternativas,
    "plano_em_vagas_reais": _plano_em_vagas_reais,
    "frase_proibida": _frase_proibida,
    "vocabulario_proibido": _vocabulario_proibido,
    "nao_pede_atendimento_id": _nao_pede_atendimento_id,
    "max_planos": _max_planos,
    "max_chamadas_planilha": _max_chamadas_planilha,
    "max_segundos": _max_segundos,
    "vagas_iguais_ao_oraculo": _vagas_iguais_ao_oraculo,
    "estado_profissional": _estado_profissional,
    "alternativas_nos_dias_pedidos": _alternativas_nos_dias_pedidos,
    "alternativas_sem_repeticao": _alternativas_sem_repeticao,
    "alternativa_no_grupo_do_dia": _alternativa_no_grupo_do_dia,
    "texto_contem": _texto_contem,
    "sem_datas": _sem_datas,
    "sequencia_maxima": _sequencia_maxima,
    "tools_chamadas": _tools_chamadas,
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
