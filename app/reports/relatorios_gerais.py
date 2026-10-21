"""E-mail com os relatórios gerais (`POST /relatorio/enviar` com `relatorios`).

A agenda do período é lida uma vez (`fotografar_periodo`) e cada relatório
pedido vira uma seção, em HTML e em texto puro, na ordem da lista. Os números
saem dos relatórios prontos da engine (`app.engine.relatorios_prontos`) e do
motor genérico — este módulo só formata.

O e-mail **nunca contém nome de paciente**: os relatórios aqui são contagens
(vagas, ocupação, sessões, pacientes distintos, buracos). A agenda de um
paciente não pode ir por e-mail e é recusada antes de qualquer leitura.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from html import escape
from typing import Final

from app.domain.formatacao import chave_natural, formatar_razao, rotulo_enum
from app.engine import visoes_fotografia
from app.engine.fotografia import FotografiaPeriodo, Periodo, SlotFotografado
from app.engine.relatorio_generico import (
    Dimensao,
    FiltrosRelatorio,
    LinhaRelatorio,
    Metrica,
    calcular_metricas,
)
from app.engine.relatorios_prontos import (
    convenios,
    qualidade_agenda,
    ranking_ocupacao,
    salas_ociosas,
)
from app.reports.template import (
    ABAIXO_DA_META,
    NA_META,
    data_por_extenso,
    documento_html,
    plural,
    rotulo_meta,
    secao_ocupacao,
    tabela_html,
    tabela_texto,
)

#: Relatórios que podem ir por e-mail, na ordem em que o catálogo os apresenta.
TIPOS_RELATORIO_EMAIL: Final[tuple[str, ...]] = (
    "ocupacao",
    "vagas_livres",
    "ranking_ocupacao",
    "convenios",
    "qualidade_agenda",
    "salas_ociosas",
)

#: Só a ocupação é de um dia; os demais aceitam `inicio`/`fim`.
TIPOS_DE_UM_DIA: Final[frozenset[str]] = frozenset({"ocupacao"})

#: Maior período de um e-mail (dias corridos, domingo incluído).
MAX_DIAS_EMAIL: Final[int] = 14

#: Linhas de uma tabela do e-mail; o que passar vira "+N linhas".
MAX_LINHAS_EMAIL: Final[int] = 15

_TITULOS: Final[dict[str, str]] = {
    "ocupacao": "Ocupação do dia",
    "vagas_livres": "Vagas por período e especialidade",
    "ranking_ocupacao": "Ranking de ocupação por profissional",
    "convenios": "Convênios e autorizações pendentes",
    "qualidade_agenda": "Buracos e fragmentação da agenda",
    "salas_ociosas": "Salas ociosas",
}

_NOTA_GRADE: Final[str] = (
    "A planilha é uma grade semanal: os números refletem a grade vigente, não uma semana "
    "específica do calendário."
)

_SEM_FILTRO = FiltrosRelatorio()


def validar_tipos(relatorios: Sequence[str]) -> list[str]:
    """Os tipos pedidos, sem repetição e na ordem pedida; `ValueError` com mensagem clara."""
    if not relatorios:
        raise ValueError(
            f"Informe ao menos um relatório em 'relatorios': {', '.join(TIPOS_RELATORIO_EMAIL)}."
        )
    if "agenda_paciente" in relatorios:
        raise ValueError(
            "agenda_paciente não pode ser enviado por e-mail: o e-mail nunca contém nome de "
            "paciente. Consulte a agenda do paciente pelo chat ou por POST /relatorios/gerar."
        )
    invalidos = [tipo for tipo in relatorios if tipo not in TIPOS_RELATORIO_EMAIL]
    if invalidos:
        raise ValueError(
            f"Relatório(s) que não vão por e-mail: {', '.join(invalidos)}. Válidos: "
            f"{', '.join(TIPOS_RELATORIO_EMAIL)}."
        )
    return list(dict.fromkeys(relatorios))


def resolver_periodo(
    relatorios: Sequence[str],
    data: date | None,
    inicio: date | None,
    fim: date | None,
    hoje: date,
) -> tuple[date, date]:
    """Período do e-mail. `ValueError` com mensagem clara quando não faz sentido.

    `data` é um dia só (padrão: hoje); `inicio`/`fim` são o período dos
    relatórios que aceitam período, com teto de `MAX_DIAS_EMAIL` dias. Só um
    dos dois vale: `data` junto com `inicio`/`fim` é recusado.
    """
    if data is not None and (inicio is not None or fim is not None):
        raise ValueError("Informe 'data' (um dia) ou 'inicio'/'fim' (um período), não os dois.")
    if inicio is None and fim is None:
        dia = data or hoje
        return dia, dia
    de_um_dia = [tipo for tipo in relatorios if tipo in TIPOS_DE_UM_DIA]
    if de_um_dia:
        raise ValueError(
            f"{', '.join(de_um_dia)} é de um dia só: use 'data' em vez de 'inicio'/'fim', ou "
            "peça esse relatório num envio separado."
        )
    inicio = inicio or fim
    fim = fim or inicio
    assert inicio is not None and fim is not None
    if fim < inicio:
        raise ValueError("O fim do período é anterior ao início.")
    dias = (fim - inicio).days + 1
    if dias > MAX_DIAS_EMAIL:
        raise ValueError(
            f"Período longo demais para e-mail ({dias} dias): o máximo é {MAX_DIAS_EMAIL} dias."
        )
    return inicio, fim


def _rotulo_periodo(foto: FotografiaPeriodo) -> str:
    if foto.inicio == foto.fim:
        return data_por_extenso(foto.inicio)
    return f"{foto.inicio:%d/%m/%Y} a {foto.fim:%d/%m/%Y}"


def _horas(slots: int) -> str:
    """`3` slots -> `1,5 h`."""
    horas = slots / 2
    texto = f"{horas:.1f}".replace(".", ",").removesuffix(",0")
    return f"{texto} h"


def _percentual(razao: tuple[int, int] | None) -> str:
    if razao is None:
        return "—"
    ocupados, escalados = razao
    return formatar_razao(ocupados, escalados, percentual=True)


def _situacao(abaixo: bool | None) -> str:
    return ABAIXO_DA_META if abaixo else NA_META


def _cortar[T](itens: Sequence[T]) -> tuple[Sequence[T], str]:
    """No máximo `MAX_LINHAS_EMAIL` itens e o rodapé `+N linhas` do que sobrou."""
    sobra = len(itens) - MAX_LINHAS_EMAIL
    if sobra <= 0:
        return itens, ""
    return itens[:MAX_LINHAS_EMAIL], f"+{sobra} {plural(sobra, 'linha', 'linhas')}"


Secao = tuple[str, list[str]]


def _secao(titulo: str, paragrafos: Sequence[str], tabelas: Sequence[Secao]) -> Secao:
    html = f"<h3>{escape(titulo)}</h3>" + "".join(f"<p>{escape(p)}</p>" for p in paragrafos)
    texto = [titulo.upper(), *paragrafos]
    for tabela_em_html, tabela_em_texto in tabelas:
        html += tabela_em_html
        texto += ["", *tabela_em_texto]
    return html, texto


def _tabela(
    titulo: str,
    cabecalhos: Sequence[str],
    linhas: Sequence[tuple[Sequence[str], bool]],
    *,
    vazio: str,
    numericas: Sequence[int],
) -> Secao:
    """A mesma tabela em HTML e em texto (`Primeira coluna: demais · separadas`)."""
    visiveis, rodape = _cortar(linhas)
    html = tabela_html(
        titulo, cabecalhos, visiveis, vazio=vazio, alinhar_a_direita=numericas, rodape=rodape
    )
    texto = tabela_texto(
        f"{titulo}:",
        [
            f"{celulas[0]}: "
            + " · ".join(
                f"{cabecalho} {celula}"
                for cabecalho, celula in zip(cabecalhos[1:], celulas[1:], strict=True)
            )
            for celulas, _ in visiveis
        ],
        vazio=vazio.rstrip(".").lower(),
        rodape=rodape,
    )
    return html, texto


def _vagas(foto: FotografiaPeriodo) -> Secao:
    por_especialidade: dict[str, dict[Periodo, list[SlotFotografado]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for slot in foto.slots:
        if slot.especialidade is not None:
            por_especialidade[rotulo_enum(slot.especialidade.value)][slot.periodo].append(slot)
    linhas: list[tuple[Sequence[str], bool]] = []
    for rotulo in sorted(por_especialidade, key=chave_natural):
        manha, tarde = (
            int(
                calcular_metricas(por_especialidade[rotulo][p], (Metrica.VAGAS_LIVRES,))[
                    Metrica.VAGAS_LIVRES
                ]
            )
            for p in (Periodo.MANHA, Periodo.TARDE)
        )
        linhas.append(([rotulo, str(manha), str(tarde), str(manha + tarde)], False))
    total = int(calcular_metricas(foto.slots, (Metrica.VAGAS_LIVRES,))[Metrica.VAGAS_LIVRES])
    resumo = (
        f"{total} {plural(total, 'vaga', 'vagas')} de 30 min ({_horas(total)}) no período, "
        "contando cada posto de sala como uma vaga."
    )
    return _secao(
        _TITULOS["vagas_livres"],
        [resumo],
        [
            _tabela(
                "Vagas por especialidade",
                ["Especialidade", "Manhã", "Tarde", "Total"],
                linhas,
                vazio="Nenhuma vaga no período.",
                numericas=(1, 2, 3),
            )
        ],
    )


def _linhas_de_ocupacao(
    linhas: Sequence[LinhaRelatorio], dimensao: Dimensao
) -> list[tuple[str, tuple[int, int] | None, bool | None, LinhaRelatorio]]:
    return [
        (linha.chaves[dimensao].rotulo, linha.razao_ocupacao, linha.abaixo_da_meta, linha)
        for linha in linhas
    ]


def _resumo_meta(abaixo: int, total: int, singular: str, plural_: str) -> str:
    return f"{abaixo} de {total} {plural(total, singular, plural_)} abaixo da meta"


def _ranking(foto: FotografiaPeriodo) -> Secao:
    resultado = ranking_ocupacao(foto, _SEM_FILTRO)
    itens = _linhas_de_ocupacao(resultado.linhas, Dimensao.PROFISSIONAL)
    linhas: list[tuple[Sequence[str], bool]] = []
    for posicao, (nome, razao, abaixo, linha) in enumerate(itens, start=1):
        profissional = foto.profissionais.get(linha.chaves[Dimensao.PROFISSIONAL].id)
        especialidade = rotulo_enum(profissional.especialidade.value) if profissional else "—"
        ocupados, escalados = razao or (0, 0)
        linhas.append(
            (
                [
                    f"{posicao}. {nome}",
                    especialidade,
                    _percentual(razao),
                    f"{ocupados} de {escalados}",
                    _situacao(abaixo),
                ],
                bool(abaixo),
            )
        )
    abaixo_da_meta = sum(1 for *_, abaixo, _ in itens if abaixo)
    return _secao(
        _TITULOS["ranking_ocupacao"],
        [rotulo_meta(), _resumo_meta(abaixo_da_meta, len(itens), "profissional", "profissionais")],
        [
            _tabela(
                "Da maior para a menor ocupação",
                ["Profissional", "Especialidade", "Ocupação", "Slots ocupados", "Situação"],
                linhas,
                vazio="Nenhuma profissional escalada no período.",
                numericas=(2, 3),
            )
        ],
    )


def _convenios(foto: FotografiaPeriodo) -> Secao:
    relatorio = convenios(foto, _SEM_FILTRO)
    por_convenio = [
        (
            [
                linha.chaves[Dimensao.CONVENIO].rotulo,
                str(linha.valores[Metrica.SESSOES]),
                str(linha.valores[Metrica.PACIENTES_DISTINTOS]),
                str(linha.valores[Metrica.AGUARDANDO_AUTORIZACAO]),
            ],
            False,
        )
        for linha in relatorio.por_convenio.linhas
    ]
    aguardando = [
        (
            [
                linha.chaves[Dimensao.DIA].rotulo,
                linha.chaves[Dimensao.PROFISSIONAL].rotulo,
                str(linha.valores[Metrica.AGUARDANDO_AUTORIZACAO]),
            ],
            True,
        )
        for linha in relatorio.aguardando.linhas
    ]
    total_aguardando = int(relatorio.por_convenio.total[Metrica.AGUARDANDO_AUTORIZACAO])
    sessoes = int(relatorio.por_convenio.total[Metrica.SESSOES])
    resumo = (
        f"{sessoes} {plural(sessoes, 'sessão', 'sessões')} no período, "
        f"{total_aguardando} {plural(total_aguardando, 'aguardando', 'aguardando')} autorização. "
        "Só contagens: o e-mail não traz nomes de pacientes."
    )
    return _secao(
        _TITULOS["convenios"],
        [resumo],
        [
            _tabela(
                "Por convênio",
                ["Convênio", "Sessões", "Pacientes", "Aguardando autorização"],
                por_convenio,
                vazio="Nenhum atendimento no período.",
                numericas=(1, 2, 3),
            ),
            _tabela(
                "Aguardando autorização por dia e profissional",
                ["Dia", "Profissional", "Sessões aguardando"],
                aguardando,
                vazio="Nenhuma sessão aguardando autorização.",
                numericas=(2,),
            ),
        ],
    )


def _qualidade(foto: FotografiaPeriodo) -> Secao:
    relatorio = qualidade_agenda(foto, _SEM_FILTRO)
    itens = relatorio.por_profissional
    linhas = [
        (
            [
                item.profissional_nome,
                rotulo_enum(item.especialidade.value) if item.especialidade else "—",
                str(item.buracos),
                _horas(item.slots_em_buracos),
                str(item.slots_isolados),
                _horas(item.maior_faixa_livre),
            ],
            item.buracos > 0,
        )
        for item in itens
    ]
    buracos = sum(item.buracos for item in itens)
    slots_em_buracos = sum(item.slots_em_buracos for item in itens)
    isoladas = sum(item.slots_isolados for item in itens)
    com_buraco = sum(1 for item in itens if item.buracos)
    resumo = (
        f"{buracos} {plural(buracos, 'buraco', 'buracos')} entre atendimentos "
        f"({_horas(slots_em_buracos)} presas) e {isoladas} "
        f"{plural(isoladas, 'vaga isolada', 'vagas isoladas')} de 30 min; "
        f"{com_buraco} de {len(itens)} {plural(len(itens), 'profissional', 'profissionais')} "
        "com buraco."
    )
    return _secao(
        _TITULOS["qualidade_agenda"],
        [resumo],
        [
            _tabela(
                "Por profissional (mais tempo preso em buraco primeiro)",
                [
                    "Profissional",
                    "Especialidade",
                    "Buracos",
                    "Tempo em buracos",
                    "Vagas isoladas",
                    "Maior faixa livre",
                ],
                linhas,
                vazio="Nenhuma profissional escalada no período.",
                numericas=(2, 3, 4, 5),
            )
        ],
    )


def _salas_ociosas(foto: FotografiaPeriodo) -> Secao:
    resultado = salas_ociosas(foto, _SEM_FILTRO)
    itens = _linhas_de_ocupacao(resultado.linhas, Dimensao.SALA)
    linhas = [
        (
            [
                nome,
                _percentual(razao),
                str(linha.valores[Metrica.VAGAS_LIVRES]),
                _horas(int(linha.valores[Metrica.VAGAS_LIVRES])),
                _situacao(abaixo),
            ],
            bool(abaixo),
        )
        for nome, razao, abaixo, linha in itens
    ]
    abaixo_da_meta = sum(1 for *_, abaixo, _ in itens if abaixo)
    return _secao(
        _TITULOS["salas_ociosas"],
        [rotulo_meta(), _resumo_meta(abaixo_da_meta, len(itens), "sala", "salas")],
        [
            _tabela(
                "Da menos para a mais ocupada",
                ["Sala", "Ocupação", "Vagas livres", "Horas livres", "Situação"],
                linhas,
                vazio="Nenhuma sala com escala no período.",
                numericas=(1, 2, 3),
            )
        ],
    )


def _ocupacao(foto: FotografiaPeriodo) -> Secao:
    html, texto = secao_ocupacao(visoes_fotografia.ocupacao_do_dia(foto, foto.inicio))
    return f"<h3>{escape(_TITULOS['ocupacao'])}</h3>{html}", [
        _TITULOS["ocupacao"].upper(),
        *texto,
    ]


_SECOES = {
    "ocupacao": _ocupacao,
    "vagas_livres": _vagas,
    "ranking_ocupacao": _ranking,
    "convenios": _convenios,
    "qualidade_agenda": _qualidade,
    "salas_ociosas": _salas_ociosas,
}


def renderizar_relatorios_gerais(
    foto: FotografiaPeriodo, relatorios: Sequence[str]
) -> tuple[str, str, str]:
    """Assunto, HTML e texto puro do e-mail com `relatorios` (já validados), na ordem pedida."""
    periodo = _rotulo_periodo(foto)
    assunto = f"RealocAI — Relatórios de {periodo}"
    titulo = f"Relatórios — {periodo}"
    avisos = [_NOTA_GRADE]
    if foto.parcial:
        dias = ", ".join(f"{dia:%d/%m}" for dia in foto.dias_nao_lidos)
        avisos.insert(0, f"Não foi possível ler: {dias}; os números são parciais.")
    if not foto.dias_lidos:
        avisos.insert(0, "Não há agenda no período (domingo não tem expediente).")

    secoes = [_SECOES[tipo](foto) for tipo in relatorios]
    corpo_html = "".join(html for html, _ in secoes) + "".join(
        f'<p style="color: #555;">{escape(aviso)}</p>' for aviso in avisos
    )
    linhas_texto = [titulo]
    for _, texto in secoes:
        linhas_texto += ["", *texto]
    linhas_texto += ["", *avisos]
    return assunto, documento_html(titulo, corpo_html), "\n".join(linhas_texto)
