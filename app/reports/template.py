"""Template do e-mail de relatório de ocupação (Fase 7).

A Fase 4a (`app.engine.ocupacao`) já calcula os percentuais de ocupação por
sala e por especialidade — este módulo só formata esse resultado para
e-mail, sem nenhuma lógica de negócio nova. Composição de strings Python
simples, sem template engine: o conteúdo é curto o bastante para não
justificar essa dependência.

Sempre as duas versões (HTML e texto puro) são geradas juntas — decisão de
entregabilidade de e-mail: alguns clientes de e-mail não renderizam HTML
corretamente, ou o destinatário prefere abrir em modo texto.

As tabelas têm a primeira coluna nomeada pelo que ela lista ("Especialidade",
"Sala"), a ocupação e a coluna "Situação" ("Abaixo da meta" ou "Na meta"); a
linha abaixo da meta continua destacada no HTML. Antes das tabelas vêm a meta
de ocupação e o resumo de quantas especialidades e salas estão abaixo dela.
As salas saem na ordem natural (`RelatorioOcupacaoDoDia.por_sala`).
"""

from collections.abc import Sequence
from datetime import date
from html import escape

from app.domain import Especialidade
from app.domain.constants import META_OCUPACAO_POR_SALA
from app.domain.formatacao import formatar_razao
from app.engine.ocupacao import OcupacaoAgregada, RelatorioOcupacaoDoDia

_MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

_ItemOcupacaoRotulado = tuple[str, OcupacaoAgregada]

#: Estilo de uma linha que precisa de atenção (abaixo da meta) no HTML.
ESTILO_DESTAQUE = "color: #c00000; font-weight: bold;"
_CELULA = "padding: 6px 12px; border-bottom: 1px solid #eee;"
_CABECALHO = "padding: 6px 12px; border-bottom: 2px solid #ccc;"
ABAIXO_DA_META = "Abaixo da meta"
NA_META = "Na meta"


def data_por_extenso(data: date) -> str:
    """`8 de setembro de 2026`."""
    return f"{data.day} de {_MESES[data.month - 1]} de {data.year}"


def rotulo_meta() -> str:
    """`Meta de ocupação: 80%`."""
    return f"Meta de ocupação: {round(META_OCUPACAO_POR_SALA * 100)}%"


def plural(quantidade: int, singular: str, plural_: str) -> str:
    return singular if quantidade == 1 else plural_


def _rotulo_especialidade(especialidade: Especialidade) -> str:
    """`terapia_ocupacional` -> `Terapia Ocupacional`."""
    return especialidade.value.replace("_", " ").title()


def _rotulo_sala(sala_id: str) -> str:
    """`sala-1` -> `Sala 1` (mesma convenção usada para rotular especialidade)."""
    return sala_id.replace("-", " ").title()


def _itens_por_especialidade(relatorio: RelatorioOcupacaoDoDia) -> list[_ItemOcupacaoRotulado]:
    return [
        (_rotulo_especialidade(especialidade), agregada)
        for especialidade, agregada in sorted(
            relatorio.por_especialidade().items(), key=lambda par: par[0].value
        )
    ]


def _itens_por_sala(relatorio: RelatorioOcupacaoDoDia) -> list[_ItemOcupacaoRotulado]:
    # `por_sala` já vem em ordem natural (Sala 2 antes de Sala 10).
    return [(_rotulo_sala(sala_id), agregada) for sala_id, agregada in relatorio.por_sala().items()]


def _percentual(agregada: OcupacaoAgregada) -> str:
    """`105,0%`: uma casa, `ROUND_HALF_UP` e vírgula, como no chat; sem limitar a 100%."""
    return formatar_razao(agregada.slots_ocupados, agregada.slots_escalados, percentual=True)


def _situacao(agregada: OcupacaoAgregada) -> str:
    return ABAIXO_DA_META if agregada.abaixo_da_meta else NA_META


def tabela_html(
    titulo: str,
    cabecalhos: Sequence[str],
    linhas: Sequence[tuple[Sequence[str], bool]],
    *,
    vazio: str,
    alinhar_a_direita: Sequence[int] = (),
    rodape: str = "",
) -> str:
    """Tabela do e-mail: `linhas` são (células, destacar). Tudo é escapado.

    `alinhar_a_direita` são os índices das colunas numéricas; `rodape` vai
    num parágrafo logo abaixo (ex.: "+3 linhas").
    """
    if not linhas:
        return f"<h3>{escape(titulo)}</h3><p>{escape(vazio)}</p>"

    def alinhamento(indice: int) -> str:
        return "right" if indice in alinhar_a_direita else "left"

    cabecalho = "".join(
        f'<th style="text-align: {alinhamento(i)}; {_CABECALHO}">{escape(nome)}</th>'
        for i, nome in enumerate(cabecalhos)
    )
    corpo = "".join(
        f"<tr{f' style="{ESTILO_DESTAQUE}"' if destacar else ''}>"
        + "".join(
            f'<td style="{_CELULA} text-align: {alinhamento(i)};">{escape(celula)}</td>'
            for i, celula in enumerate(celulas)
        )
        + "</tr>"
        for celulas, destacar in linhas
    )
    nota = f'<p style="color: #555;">{escape(rodape)}</p>' if rodape else ""
    return (
        f"<h3>{escape(titulo)}</h3>"
        '<table style="border-collapse: collapse; width: 100%; margin-bottom: 24px;">'
        f"<thead><tr>{cabecalho}</tr></thead>"
        f"<tbody>{corpo}</tbody>"
        "</table>"
        f"{nota}"
    )


def tabela_texto(
    titulo: str,
    linhas: Sequence[str],
    *,
    vazio: str,
    rodape: str = "",
) -> list[str]:
    """Bloco de texto puro: título e uma linha por item, com recuo."""
    if not linhas:
        return [titulo, f"  ({vazio})"]
    return [titulo, *(f"  - {linha}" for linha in linhas), *([f"  {rodape}"] if rodape else [])]


def _tabela_ocupacao_html(titulo: str, rotulo: str, itens: list[_ItemOcupacaoRotulado]) -> str:
    return tabela_html(
        titulo,
        [rotulo, "Ocupação", "Situação"],
        [
            ([nome, _percentual(agregada), _situacao(agregada)], agregada.abaixo_da_meta)
            for nome, agregada in itens
        ],
        vazio="Nenhum dado para o dia.",
        alinhar_a_direita=(1,),
    )


def _tabela_ocupacao_texto(titulo: str, itens: list[_ItemOcupacaoRotulado]) -> list[str]:
    return tabela_texto(
        titulo,
        [f"{nome}: {_percentual(agregada)} — {_situacao(agregada)}" for nome, agregada in itens],
        vazio="nenhum dado para o dia",
    )


def _resumo(
    por_especialidade: list[_ItemOcupacaoRotulado], por_sala: list[_ItemOcupacaoRotulado]
) -> str:
    """`1 de 3 especialidades e 2 de 4 salas abaixo da meta`."""
    especialidades = sum(agregada.abaixo_da_meta for _, agregada in por_especialidade)
    salas = sum(agregada.abaixo_da_meta for _, agregada in por_sala)
    total_especialidades = len(por_especialidade)
    total_salas = len(por_sala)
    return (
        f"{especialidades} de {total_especialidades} "
        f"{plural(total_especialidades, 'especialidade', 'especialidades')} e "
        f"{salas} de {total_salas} {plural(total_salas, 'sala', 'salas')} abaixo da meta"
    )


def secao_ocupacao(relatorio: RelatorioOcupacaoDoDia) -> tuple[str, list[str]]:
    """Corpo da ocupação do dia: meta, resumo e as duas tabelas, em HTML e em texto."""
    por_especialidade = _itens_por_especialidade(relatorio)
    por_sala = _itens_por_sala(relatorio)
    resumo = _resumo(por_especialidade, por_sala)
    html = (
        f"<p>{escape(rotulo_meta())}<br>{escape(resumo)}</p>"
        + _tabela_ocupacao_html("Por especialidade", "Especialidade", por_especialidade)
        + _tabela_ocupacao_html("Por sala", "Sala", por_sala)
    )
    texto = [
        rotulo_meta(),
        resumo,
        "",
        *_tabela_ocupacao_texto("Por especialidade:", por_especialidade),
        "",
        *_tabela_ocupacao_texto("Por sala:", por_sala),
    ]
    return html, texto


def documento_html(titulo: str, corpo: str) -> str:
    return (
        '<html><body style="font-family: Arial, sans-serif; color: #222;">'
        f"<h2>{escape(titulo)}</h2>{corpo}</body></html>"
    )


def renderizar_relatorio(relatorio: RelatorioOcupacaoDoDia) -> tuple[str, str, str]:
    """Monta assunto, corpo HTML e corpo texto puro do relatório de ocupação do dia."""
    data_extenso = data_por_extenso(relatorio.data)
    assunto = f"RealocAI — Relatório de ocupação de {data_extenso}"
    titulo = f"Relatório de ocupação — {data_extenso}"

    corpo_html, linhas_texto = secao_ocupacao(relatorio)
    return assunto, documento_html(titulo, corpo_html), "\n".join([titulo, "", *linhas_texto])
