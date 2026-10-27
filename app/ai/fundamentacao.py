"""Verificação de fundamentação da resposta, antes de ela sair para quem perguntou.

Confere, no texto da resposta, o que dá para conferir sem interpretar:

- vocabulário proibido (sempre): termos internos e verbos de ação que o
  agente não pode usar, porque ele só sugere;
- quando a rodada chamou ferramentas: horários `HH:MM`, contagens com unidade
  ("12 vagas"), percentuais e nomes de profissionais da equipe precisam
  existir no resultado das ferramentas da mesma rodada. Um horário também
  vale se veio da pergunta de quem perguntou (a janela "10h às 12h") ou é um
  horário fixo da clínica; o modelo pode juntar faixas seguidas, então basta o
  horário existir como início ou fim de alguma faixa.

Quem chama (`app.ai.agente.perguntar_com_mensagens`) tenta UMA nova geração
com o aviso do que não bateu e, se ainda não bater, usa um resumo
determinístico do relatório. O log registra só contagens: nunca o texto nem
nomes.
"""

import json
import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from app.ai.relatorios import BlocoRelatorio
from app.domain.constants import (
    FIM_PAUSA,
    HORARIO_ABERTURA,
    HORARIO_FECHAMENTO,
    HORARIO_PREFERENCIAL_PADRAO,
    INICIO_PAUSA,
)

logger = logging.getLogger(__name__)

#: Nunca podem aparecer na resposta. Plural com "s" também conta ("slots").
VOCABULARIO_PROIBIDO: tuple[str, ...] = (
    "atendimento_id",
    "tool",
    "slot",
    "bloco",
    "parâmetros inválidos",
    "exatamente como veio",
    "encaixar",
    "encaixei",
    "registrar",
    "registrei",
    "agendar",
    "agendei",
    "marquei",
)

#: Horários que o prompt dá como fatos da clínica (expediente, pausa, piso padrão).
_HORARIOS_DA_CLINICA = frozenset(
    hora.strftime("%H:%M")
    for hora in (
        HORARIO_ABERTURA,
        HORARIO_FECHAMENTO,
        INICIO_PAUSA,
        FIM_PAUSA,
        HORARIO_PREFERENCIAL_PADRAO,
    )
)

_HORARIO = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?!\d|:\d)")
#: Horários e datas saem do texto antes de colher números de contagem.
_HORARIO_OU_DATA = re.compile(r"\d{1,2}:\d{2}|\d{1,4}[/-]\d{1,2}(?:[/-]\d{2,4})?")
#: "10h", "10 h", "10h30" na pergunta de quem perguntou.
_HORA_FALADA = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s?h(?:\s?([0-5]\d))?(?![a-z])")
_UNIDADES = (
    r"vagas?|horários?|horarios?|pacientes?|sessões|sessoes|sessão|sessao|"
    r"profissionais|profissional|faixas?|atendimentos?|horas?"
)
_CONTAGEM = re.compile(rf"(?<![\d,.:/])(\d+)\s+(?:{_UNIDADES})\b", re.IGNORECASE)
_PERCENTUAL = re.compile(r"(?<![\d,])(\d+(?:,\d+)?)\s?%")
#: Linha de lista de horários ("- 10:00 às 10:30: ..."): com a tela, o relatório já mostra.
_LINHA_DE_HORARIO = re.compile(
    r"(?m)^\s*(?:[-*•]|\d+[.)])?\s*\**\d{1,2}:\d{2}\s*(?:às|as|-|\u2013)\s*\d{1,2}:\d{2}\**\s*:"
)
_NUMERO = re.compile(r"(?<![\d,])\d+(?:[,.]\d+)?")


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.casefold().split())


def termos_proibidos(texto: str) -> list[str]:
    """Termos de `VOCABULARIO_PROIBIDO` no texto (palavra inteira, sem acento nem caixa)."""
    normalizado = _normalizar(texto)
    return [
        termo
        for termo in VOCABULARIO_PROIBIDO
        if re.search(rf"(?<!\w){re.escape(_normalizar(termo))}s?(?!\w)", normalizado)
    ]


def _hora(horas: str, minutos: str | None) -> str:
    return f"{int(horas):02d}:{int(minutos or 0):02d}"


def horarios(texto: str) -> set[str]:
    return {_hora(h, m) for h, m in _HORARIO.findall(texto)}


def horarios_falados(texto: str) -> set[str]:
    """Horários escritos por quem perguntou: "10:00" e também "10h" ou "10h30"."""
    return horarios(texto) | {_hora(h, m) for h, m in _HORA_FALADA.findall(texto.casefold())}


@dataclass(frozen=True)
class Problemas:
    """O que não bateu. Só contagens saem no log."""

    vocabulario: list[str] = field(default_factory=list)
    horarios: list[str] = field(default_factory=list)
    numeros: list[str] = field(default_factory=list)
    nomes: list[str] = field(default_factory=list)
    #: Linhas de lista de horários no texto quando a tela já mostra o relatório.
    linhas_de_lista: int = 0

    @property
    def ok(self) -> bool:
        return not (
            self.vocabulario or self.horarios or self.numeros or self.nomes or self.linhas_de_lista
        )

    @property
    def total(self) -> int:
        return (
            len(self.vocabulario)
            + len(self.horarios)
            + len(self.numeros)
            + len(self.nomes)
            + self.linhas_de_lista
        )

    def aviso(self) -> str:
        """Instrução para a nova geração (vai só para o modelo, nunca para o histórico)."""
        partes = []
        if self.vocabulario:
            partes.append(f"termos proibidos: {', '.join(self.vocabulario)}")
        if self.horarios:
            partes.append(f"horários que não vieram das ferramentas: {', '.join(self.horarios)}")
        if self.numeros:
            partes.append(f"números que não vieram das ferramentas: {', '.join(self.numeros)}")
        if self.nomes:
            partes.append(f"profissionais que não vieram das ferramentas: {', '.join(self.nomes)}")
        if self.linhas_de_lista:
            partes.append("lista de horários linha a linha, que o relatório na tela já mostra")
        return (
            "[Verificação interna, não responda a esta mensagem como pergunta] A resposta "
            f"anterior não pode sair assim ({'; '.join(partes)}). Reescreva a resposta à última "
            "pergunta usando só o que as ferramentas devolveram nesta rodada, em 1 a 3 frases, "
            "sem esses itens."
        )


def _fontes(mensagens_do_turno: Sequence[BaseMessage]) -> str:
    """Texto e relatório (JSON) de cada ferramenta chamada na rodada."""
    partes: list[str] = []
    for mensagem in mensagens_do_turno:
        if isinstance(mensagem, ToolMessage):
            partes.append(str(mensagem.content))
            if mensagem.artifact is not None:
                partes.append(json.dumps(mensagem.artifact, ensure_ascii=False))
    return "\n".join(partes)


def chamou_ferramenta(mensagens_do_turno: Sequence[BaseMessage]) -> bool:
    return any(isinstance(m, ToolMessage) for m in mensagens_do_turno)


def verificar(
    resposta: str,
    mensagens_do_turno: Sequence[BaseMessage],
    historico: Sequence[BaseMessage],
    profissionais: Iterable[str] = (),
    *,
    com_tela: bool = False,
) -> Problemas:
    """Confere `resposta` contra as ferramentas da rodada e o que foi dito na conversa.

    `com_tela`: o cliente renderiza os relatórios; se a rodada trouxe relatório,
    o texto não pode repetir a lista de horários.
    """
    vocabulario = termos_proibidos(resposta)
    if not chamou_ferramenta(mensagens_do_turno):
        return Problemas(vocabulario=vocabulario)
    trouxe_relatorio = any(
        isinstance(m, ToolMessage) and m.artifact is not None for m in mensagens_do_turno
    )
    linhas_de_lista = (
        len(_LINHA_DE_HORARIO.findall(resposta)) if com_tela and trouxe_relatorio else 0
    )

    fontes = _fontes(mensagens_do_turno)
    ditos = "\n".join(str(m.content) for m in historico if isinstance(m, HumanMessage))
    horarios_validos = horarios(fontes) | horarios_falados(ditos) | _HORARIOS_DA_CLINICA
    numeros_validos = {
        n.replace(".", ",")
        for n in _NUMERO.findall(_HORARIO_OU_DATA.sub(" ", fontes + "\n" + ditos))
    }
    fontes_normalizadas = _normalizar(fontes + "\n" + ditos)

    sem_fonte_horario = sorted(horarios(resposta) - horarios_validos)
    sem_fonte_numero = sorted(
        {n for n in _CONTAGEM.findall(resposta) if n not in numeros_validos}
        | {p for p in _PERCENTUAL.findall(resposta) if p not in numeros_validos}
    )
    resposta_normalizada = _normalizar(resposta)
    sem_fonte_nome = sorted(
        nome
        for nome in set(profissionais)
        if len(_normalizar(nome)) >= 3
        and re.search(rf"(?<!\w){re.escape(_normalizar(nome))}(?!\w)", resposta_normalizada)
        and not re.search(rf"(?<!\w){re.escape(_normalizar(nome))}(?!\w)", fontes_normalizadas)
    )
    return Problemas(
        vocabulario=vocabulario,
        horarios=sem_fonte_horario,
        numeros=sem_fonte_numero,
        nomes=sem_fonte_nome,
        linhas_de_lista=linhas_de_lista,
    )


def resumo_deterministico(
    blocos: Sequence[BlocoRelatorio], mensagens_do_turno: Sequence[BaseMessage]
) -> str:
    """Resposta montada pelo código quando a do modelo não passou na verificação."""
    if blocos:
        frases = []
        for bloco in blocos[:3]:
            itens = "; ".join(f"{item.rotulo}: {item.exibicao}" for item in bloco.resumo[:3])
            frases.append(f"{bloco.titulo}: {itens}." if itens else f"{bloco.titulo}.")
        return " ".join(frases) + " O detalhe está no relatório."
    for mensagem in reversed(mensagens_do_turno):
        if isinstance(mensagem, ToolMessage):
            primeira = str(mensagem.content).strip().splitlines()[0] if mensagem.content else ""
            if primeira and not termos_proibidos(primeira) and not primeira.endswith(":"):
                return primeira
    return (
        "Consultei a agenda, mas não consegui montar uma resposta segura com o resultado. "
        "Pode reformular a pergunta?"
    )


class Contadores:
    """Contagens do processo, só para o log (nunca texto nem nomes)."""

    def __init__(self) -> None:
        self.novas_geracoes = 0
        self.novas_geracoes_por_vocabulario = 0
        self.resumos_deterministicos = 0


CONTADORES = Contadores()


def registrar_nova_geracao(problemas: Problemas) -> None:
    CONTADORES.novas_geracoes += 1
    logger.warning(
        "Fundamentação: %d item(ns) sem fonte na resposta; nova geração (total: %d).",
        problemas.total,
        CONTADORES.novas_geracoes,
    )
    if problemas.vocabulario:
        CONTADORES.novas_geracoes_por_vocabulario += 1
        logger.warning(
            "Vocabulário proibido forçou nova geração (total: %d).",
            CONTADORES.novas_geracoes_por_vocabulario,
        )


def registrar_resumo_deterministico(problemas: Problemas) -> None:
    CONTADORES.resumos_deterministicos += 1
    logger.warning(
        "Fundamentação: %d item(ns) sem fonte depois da nova geração; resumo determinístico "
        "(total: %d).",
        problemas.total,
        CONTADORES.resumos_deterministicos,
    )
