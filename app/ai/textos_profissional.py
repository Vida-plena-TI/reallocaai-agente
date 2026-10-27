"""Textos dos desfechos de uma busca por profissional, iguais em todas as tools.

Os três estados (`EstadoProfissional`) têm sempre a mesma frase, para o modelo
não confundir "não existe", "não atende neste dia" e "atende, mas está sem
vaga na janela". Ambiguidade lista os candidatos e pede escolha.
"""

from datetime import date, time

from app.ai.servico_agenda import ProfissionalAmbiguo
from app.domain.catalogo_agenda import CatalogoAgenda, ProfissionalCatalogo
from app.domain.formatacao import rotulo_enum

#: Por `date.weekday()`; com o artigo, para "na terça-feira" e "no sábado".
_DIAS = (
    "na segunda-feira",
    "na terça-feira",
    "na quarta-feira",
    "na quinta-feira",
    "na sexta-feira",
    "no sábado",
    "no domingo",
)


def no_dia(dia: date) -> str:
    """`na quinta-feira (01/10)`."""
    return f"{_DIAS[dia.weekday()]} ({dia:%d/%m})"


def dia_curto(dia: date) -> str:
    return _DIAS[dia.weekday()].split()[1]


def texto_ambiguo(texto: str, ambiguo: ProfissionalAmbiguo) -> str:
    candidatos = ", ".join(
        f"{c.nome} ({rotulo_enum(c.especialidade.value)})" for c in ambiguo.candidatos
    )
    return (
        f"Mais de uma profissional corresponde a '{texto}': {candidatos}. Pergunte qual "
        "delas a pessoa quis dizer antes de seguir; não escolha por conta própria."
    )


def texto_inexistente(texto: str, catalogo: CatalogoAgenda) -> str:
    nomes = ", ".join(sorted(p.nome for p in catalogo.profissionais.values())) or "nenhuma"
    inicio, fim = catalogo.dias[0], catalogo.dias[-1]
    return (
        f"'{texto}' não existe na grade da semana de {inicio:%d/%m} a {fim:%d/%m/%Y}. "
        f"Profissionais da semana: {nomes}."
    )


def texto_nao_atende(profissional: ProfissionalCatalogo, dia: date) -> str:
    dias = ", ".join(dia_curto(d) for d in profissional.dias) or "nenhum dia"
    return f"{profissional.nome} não atende {no_dia(dia)}. Nesta semana, atende: {dias}."


def texto_sem_vaga(
    profissional: ProfissionalCatalogo, dia: date, inicio: time | None, fim: time | None
) -> str:
    if inicio is None and fim is None:
        return f"{profissional.nome} atende {no_dia(dia)}, mas está sem vaga nesse dia."
    if inicio is not None and fim is not None:
        janela = f"entre {inicio:%H:%M} e {fim:%H:%M}"
    elif inicio is not None:
        janela = f"a partir de {inicio:%H:%M}"
    else:
        janela = f"até {fim:%H:%M}"
    return f"{profissional.nome} atende {no_dia(dia)}, mas está sem vaga {janela}."
