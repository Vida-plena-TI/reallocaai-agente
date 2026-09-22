"""Template do e-mail de relatório de ocupação (Fase 7).

A Fase 4a (`app.engine.ocupacao`) já calcula os percentuais de ocupação por
sala e por especialidade — este módulo só formata esse resultado para
e-mail, sem nenhuma lógica de negócio nova. Composição de strings Python
simples, sem template engine: o conteúdo é curto o bastante para não
justificar essa dependência.

Sempre as duas versões (HTML e texto puro) são geradas juntas — decisão de
entregabilidade de e-mail: alguns clientes de e-mail não renderizam HTML
corretamente, ou o destinatário prefere abrir em modo texto.
"""

from datetime import date

from app.domain import Especialidade
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


def _data_por_extenso(data: date) -> str:
    return f"{data.day} de {_MESES[data.month - 1]} de {data.year}"


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
    return [
        (_rotulo_sala(sala_id), agregada)
        for sala_id, agregada in sorted(relatorio.por_sala().items())
    ]


def _linha_html(rotulo: str, agregada: OcupacaoAgregada) -> str:
    estilo = ' style="color: #c00000; font-weight: bold;"' if agregada.abaixo_da_meta else ""
    aviso = " (abaixo da meta de 80%)" if agregada.abaixo_da_meta else ""
    return (
        f"<tr{estilo}>"
        f'<td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{rotulo}{aviso}</td>'
        f'<td style="padding: 6px 12px; border-bottom: 1px solid #eee; text-align: right;">'
        f"{agregada.percentual:.0%}</td>"
        "</tr>"
    )


def _tabela_html(titulo: str, itens: list[_ItemOcupacaoRotulado]) -> str:
    if not itens:
        return f"<h3>{titulo}</h3><p>Nenhum dado para o dia.</p>"
    linhas = "".join(_linha_html(rotulo, agregada) for rotulo, agregada in itens)
    return (
        f"<h3>{titulo}</h3>"
        '<table style="border-collapse: collapse; width: 100%; margin-bottom: 24px;">'
        "<thead><tr>"
        '<th style="text-align: left; padding: 6px 12px; border-bottom: 2px solid #ccc;">Nome</th>'
        '<th style="text-align: right; padding: 6px 12px; border-bottom: 2px solid #ccc;">'
        "Ocupação</th>"
        "</tr></thead>"
        f"<tbody>{linhas}</tbody>"
        "</table>"
    )


def _bloco_texto(titulo: str, itens: list[_ItemOcupacaoRotulado]) -> list[str]:
    if not itens:
        return [titulo, "  (nenhum dado para o dia)"]
    linhas = [titulo]
    for rotulo, agregada in itens:
        aviso = " — ABAIXO DA META DE 80%" if agregada.abaixo_da_meta else ""
        linhas.append(f"  - {rotulo}: {agregada.percentual:.0%}{aviso}")
    return linhas


def renderizar_relatorio(relatorio: RelatorioOcupacaoDoDia) -> tuple[str, str, str]:
    """Monta assunto, corpo HTML e corpo texto puro do relatório de ocupação do dia."""
    data_extenso = _data_por_extenso(relatorio.data)
    assunto = f"RealocAI — Relatório de ocupação de {data_extenso}"

    por_especialidade = _itens_por_especialidade(relatorio)
    por_sala = _itens_por_sala(relatorio)

    corpo_html = (
        '<html><body style="font-family: Arial, sans-serif; color: #222;">'
        f"<h2>Relatório de ocupação — {data_extenso}</h2>"
        + _tabela_html("Por especialidade", por_especialidade)
        + _tabela_html("Por sala", por_sala)
        + "</body></html>"
    )

    corpo_texto = "\n".join(
        [
            f"Relatório de ocupação — {data_extenso}",
            "",
            *_bloco_texto("Por especialidade:", por_especialidade),
            "",
            *_bloco_texto("Por sala:", por_sala),
        ]
    )

    return assunto, corpo_html, corpo_texto
