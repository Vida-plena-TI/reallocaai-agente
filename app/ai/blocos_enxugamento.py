"""Texto (para o modelo) e bloco (para a tela) do panorama de enxugamento.

Privacidade e sensibilidade de RH: o bloco leva nomes de pacientes e de
profissionais (`contem_dados_pacientes: true`, só pelo chat). O texto que o
modelo lê leva SÓ contagens e os códigos opacos dos cenários: nenhum nome de
paciente nem de profissional, nenhum valor em dinheiro. A guarda de
`app.ai.fundamentacao` troca pelo resumo determinístico qualquer resposta
desta rodada que cite um nome.

`dados.relatorio = "panorama_enxugamento"`: um valor só para o front.
"""

from collections import Counter
from typing import Final

from app.ai.blocos_realocacao import lugar_na_tela
from app.ai.relatorios import (
    RELATORIO_PANORAMA_ENXUGAMENTO,
    BlocoRelatorio,
    ColunaTabela,
    DadosTabelaGenerica,
    Formato,
    ItemResumo,
    UnidadeGrupo,
    ValorSimples,
    tabela,
)
from app.domain import Especialidade
from app.domain.formatacao import formatar_razao, rotulo_enum
from app.engine.enxugamento import (
    Alavanca,
    Cenario,
    MotivoPreso,
    Objetivo,
    PanoramaEnxugamento,
    PassoEnxugamento,
    Turno,
    TurnoPago,
)
from app.engine.fotografia import FotografiaPeriodo

TITULO_PANORAMA_ENXUGAMENTO: Final[str] = "Panorama de enxugamento (grade semanal vigente)"

COLUNAS_PANORAMA: Final[list[tuple[str, str, Formato]]] = [
    ("cenario", "Cenário", "texto"),
    ("turno_liberado", "Turno liberado", "texto"),
    ("passo", "Passo", "inteiro"),
    ("paciente", "Quem", "texto"),
    ("terapia", "Terapia", "texto"),
    ("de", "De", "texto"),
    ("para", "Para", "texto"),
    ("alavanca", "Alavanca", "texto"),
    ("observacao", "Observação", "texto"),
]
COLUNAS_RESUMO_DOS_CENARIOS: Final[list[tuple[str, str, Formato]]] = [
    ("cenario", "Cenário", "texto"),
    ("codigo", "Código", "texto"),
    ("perfil", "Perfil", "texto"),
    ("turnos_liberados", "Turnos liberados", "inteiro"),
    ("turnos_novos", "Turnos novos", "inteiro"),
    ("trocam_profissional", "Pacientes que trocam de profissional", "inteiro"),
    ("trocam_turno", "Pacientes que trocam de turno", "inteiro"),
    ("trocam_dia", "Pacientes que trocam de dia", "inteiro"),
    ("minutos_medio", "Deslocamento médio (min)", "decimal"),
    ("minutos_maximo", "Deslocamento máximo (min)", "inteiro"),
    ("ocupacao_absorventes", "Ocupação de quem absorve", "percentual"),
    ("sem_presenca", "Profissionais sem atendimento na semana", "inteiro"),
    ("sem_cobertura", "Turnos de terapia sem profissional", "inteiro"),
]
AGRUPAR_PANORAMA: Final[tuple[str, ...]] = ("cenario", "turno_liberado")
#: Nomes das abas da exportação (limite de 31 caracteres do Excel).
ABA_PASSOS: Final[str] = "Passos"
ABA_RESUMO: Final[str] = "Resumo dos cenários"

#: Limites da sugestão, sempre presentes nos avisos do panorama.
AVISOS_PANORAMA: Final[tuple[str, ...]] = (
    "Sugestão: considera apenas os atendimentos presentes na semana lida (terapias "
    "quinzenais ou fora desta semana não aparecem).",
    "Não considera a disponibilidade dos pacientes.",
    "Vagas livres na grade podem estar reservadas (reuniões, supervisão); confira antes de mudar.",
    "Cada mudança precisa ser confirmada com a família e feita manualmente na planilha.",
)
AVISO_BUSCA_LIMITADA: Final[str] = (
    "Busca limitada: havia combinações demais e nem todas foram avaliadas; pode existir "
    "cenário melhor."
)

_PERFIS: Final[dict[Objetivo, str]] = {
    Objetivo.MENOS_MUDANCA: "menos mudança",
    Objetivo.MAIS_TURNOS: "mais turnos",
    Objetivo.EQUILIBRADO: "equilibrado",
}
_MOTIVOS_PRESO: Final[dict[MotivoPreso, tuple[str, str]]] = {
    MotivoPreso.SESSAO_EM_GRUPO: ("sessão em grupo", "sessões em grupo"),
    MotivoPreso.AGUARDANDO_AUTORIZACAO: (
        "atendimento aguardando autorização",
        "atendimentos aguardando autorização",
    ),
    MotivoPreso.FORA_DA_GRADE: ("atendimento fora da grade", "atendimentos fora da grade"),
    MotivoPreso.PACIENTE_FIXO: ("paciente fixo", "pacientes fixos"),
    MotivoPreso.PROFISSIONAL_FIXO: ("profissional fixo", "profissionais fixos"),
    MotivoPreso.SEM_ESPECIALIDADE: (
        "profissional sem especialidade no cadastro",
        "profissionais sem especialidade no cadastro",
    ),
}
_DIAS_CURTOS: Final[tuple[str, ...]] = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb")
_TURNOS: Final[dict[Turno, str]] = {Turno.MANHA: "manhã", Turno.TARDE: "tarde"}


def _n(quantidade: int, singular: str, plural: str) -> str:
    return f"{quantidade} {singular if quantidade == 1 else plural}"


def _terapia(especialidade: Especialidade | None) -> str:
    return rotulo_enum(especialidade.value) if especialidade is not None else "—"


def _rotulo_do_cenario(numero: int, cenario: Cenario) -> str:
    """Só contagens e o código: nunca nome (é também o título do grupo na tela)."""
    turnos = _n(cenario.resumo.turnos_liberados, "turno", "turnos")
    return f"Cenário {numero} ({cenario.codigo}) · {_PERFIS[cenario.perfil]} · libera {turnos}"


def _turno_na_tela(foto: FotografiaPeriodo, turno: TurnoPago) -> str:
    profissional = foto.profissionais.get(turno.profissional_id)
    especialidade = profissional.especialidade if profissional is not None else None
    return (
        f"{_DIAS_CURTOS[turno.data.weekday()]} {_TURNOS[turno.turno]} · "
        f"{foto.nome_profissional(turno.profissional_id)} · {_terapia(especialidade)}"
    )


def _alavanca(passo: PassoEnxugamento) -> str:
    if passo.alavanca is Alavanca.L5:
        return "L5 · abre espaço" if passo.abre_espaco else "L5 · cadeia, outro profissional"
    return {
        Alavanca.L1: "L1 · outro turno",
        Alavanca.L2: "L2 · outro profissional",
        Alavanca.L3: "L3 · outro profissional e turno",
        Alavanca.L4: "L4 · outro dia",
    }[passo.alavanca]


def _observacao(passo: PassoEnxugamento, sem_presenca: frozenset[str]) -> str:
    partes = []
    if passo.abre_espaco:
        partes.append("mesma terapia e profissional, para abrir espaço")
    elif passo.outro_profissional:
        partes.append("mesma terapia; outro profissional — confirmar")
    else:
        partes.append("mesma terapia e profissional")
    if passo.de.sala_id != passo.para.sala_id:
        partes.append("outra sala")
    if passo.posto_extra:
        partes.append("posto simultâneo da profissional")
    if passo.turno_liberado.profissional_id in sem_presenca:
        partes.append("esta profissional fica sem atendimento na semana")
    return "; ".join(partes)


def _linhas(
    foto: FotografiaPeriodo, panorama: PanoramaEnxugamento
) -> list[dict[str, ValorSimples]]:
    linhas: list[dict[str, ValorSimples]] = []
    for numero, cenario in enumerate(panorama.cenarios, 1):
        rotulo = _rotulo_do_cenario(numero, cenario)
        sem_presenca = frozenset(cenario.resumo.profissionais_sem_presenca)
        # Linhas contíguas por turno liberado (a ordem dos planos já é essa).
        for indice, passo in enumerate(cenario.passos, 1):
            linhas.append(
                {
                    "cenario": rotulo,
                    "turno_liberado": _turno_na_tela(foto, passo.turno_liberado),
                    "passo": indice,
                    "paciente": passo.paciente,
                    "terapia": _terapia(passo.especialidade),
                    "de": lugar_na_tela(foto, passo.de),
                    "para": lugar_na_tela(foto, passo.para),
                    "alavanca": _alavanca(passo),
                    "observacao": _observacao(passo, sem_presenca),
                }
            )
    return linhas


def _resumo_dos_cenarios(panorama: PanoramaEnxugamento) -> list[dict[str, ValorSimples]]:
    return [
        {
            "cenario": f"Cenário {numero}",
            "codigo": cenario.codigo,
            "perfil": _PERFIS[cenario.perfil],
            "turnos_liberados": cenario.resumo.turnos_liberados,
            "turnos_novos": cenario.resumo.turnos_novos,
            "trocam_profissional": cenario.resumo.pacientes_trocam_profissional,
            "trocam_turno": cenario.resumo.pacientes_trocam_turno,
            "trocam_dia": cenario.resumo.pacientes_trocam_dia,
            "minutos_medio": cenario.resumo.minutos_medio,
            "minutos_maximo": cenario.resumo.minutos_maximo,
            "ocupacao_absorventes": cenario.resumo.ocupacao_absorventes,
            "sem_presenca": len(cenario.resumo.profissionais_sem_presenca),
            "sem_cobertura": len(cenario.resumo.sem_cobertura),
        }
        for numero, cenario in enumerate(panorama.cenarios, 1)
    ]


def _presos(presos: dict[MotivoPreso, int] | Counter[MotivoPreso]) -> str:
    partes = [
        _n(quantidade, *_MOTIVOS_PRESO[motivo])
        for motivo, quantidade in presos.items()
        if quantidade
    ]
    return ", ".join(partes)


def avisos_do_panorama(foto: FotografiaPeriodo, panorama: PanoramaEnxugamento) -> list[str]:
    """Avisos fixos e os do resultado, só com contagens (sem nomes)."""
    avisos = list(AVISOS_PANORAMA)
    presos = sum(panorama.presos.values())
    if presos:
        leves = _n(
            presos, "turno leve não pode ser liberado", "turnos leves não podem ser liberados"
        )
        avisos.append(f"{leves} por regra: {_presos(dict(panorama.presos))}.")
    for numero, cenario in enumerate(panorama.cenarios, 1):
        sem_cobertura = len(cenario.resumo.sem_cobertura)
        if sem_cobertura:
            turnos = _n(sem_cobertura, "turno de terapia ficaria", "turnos de terapia ficariam")
            avisos.append(
                f"Cenário {numero}: {turnos} sem nenhum profissional atendendo, num dia em que "
                "hoje tem."
            )
        sem_presenca = len(cenario.resumo.profissionais_sem_presenca)
        if sem_presenca:
            avisos.append(
                f"Cenário {numero}: este cenário deixa "
                f"{_n(sem_presenca, 'profissional', 'profissionais')} sem atendimento na semana."
            )
    if panorama.estatistica.busca_limitada:
        avisos.append(AVISO_BUSCA_LIMITADA)
    if foto.dias_nao_lidos:
        dias = ", ".join(_DIAS_CURTOS[d.weekday()] for d in sorted(set(foto.dias_nao_lidos)))
        avisos.append(f"Não foi possível ler: {dias}; a sugestão não considera esses dias.")
    return avisos


def _inteiro(rotulo: str, valor: int) -> ItemResumo:
    return ItemResumo(rotulo=rotulo, valor=valor, formato="inteiro", exibicao=str(valor))


def _descricao_do_cenario(numero: int, cenario: Cenario, recomendado: bool) -> str:
    r = cenario.resumo
    marca = ", recomendado para o objetivo pedido" if recomendado else ""
    ocupacao = (
        "; ocupação de quem absorve: "
        + formatar_razao(
            r.slots_ocupados_absorventes, r.slots_escalados_absorventes, percentual=True
        )
        if r.slots_escalados_absorventes
        else ""
    )
    return (
        f"- Cenário {numero} ({cenario.codigo}, {_PERFIS[cenario.perfil]}{marca}): libera "
        f"{_n(r.turnos_liberados, 'turno', 'turnos')} por semana, "
        f"{_n(r.turnos_novos, 'turno novo', 'turnos novos')}; "
        f"{_n(len({p.paciente for p in cenario.passos}), 'paciente muda', 'pacientes mudam')} "
        f"({r.pacientes_trocam_profissional} de profissional, {r.pacientes_trocam_turno} de "
        f"turno, {r.pacientes_trocam_dia} de dia); deslocamento médio de "
        f"{str(r.minutos_medio).replace('.', ',')} min e máximo de {r.minutos_maximo} min"
        f"{ocupacao}."
    )


_FECHO: Final[str] = (
    "Os nomes de pacientes e de profissionais estão só no relatório da tela: não escreva "
    "nenhum nome. Responda em 1 a 3 frases, só com contagens e os códigos dos cenários, como "
    'sugestão ("sugiro..."), e avise uma vez que cada mudança precisa ser confirmada com a '
    "família e feita manualmente na planilha. Não fale em valores nem em dinheiro: o "
    "panorama só conta turnos liberados. Para mais opções, chame de novo com "
    "cenarios_ja_vistos com os códigos acima."
)


def panorama_enxugamento(
    foto: FotografiaPeriodo, panorama: PanoramaEnxugamento
) -> tuple[str, BlocoRelatorio | None]:
    """Texto (só contagens e códigos) e bloco (com os nomes) do panorama."""
    presos = sum(panorama.presos.values())
    contexto = (
        f"Turnos com presença paga na semana lida: {panorama.turnos_pagos}; turnos leves "
        f"avaliados: {panorama.candidatos}; sem nenhum plano possível: "
        f"{panorama.candidatos_sem_plano}; presos por regra: {presos}"
        + (f" ({_presos(dict(panorama.presos))})." if presos else ".")
    )
    avisos = avisos_do_panorama(foto, panorama)
    if not panorama.cenarios:
        texto = [
            f"{TITULO_PANORAMA_ENXUGAMENTO}: não encontrei movimentação que libere turno sem "
            "abrir outro, mantendo a terapia e a duração de cada paciente.",
            contexto,
            *avisos,
            "Não escreva nomes; responda em 1 a 3 frases.",
        ]
        return "\n".join(texto), None

    melhor = max(c.resumo.turnos_liberados for c in panorama.cenarios)
    recomendado = panorama.cenarios[0].perfil is panorama.objetivo
    texto = [
        f"{TITULO_PANORAMA_ENXUGAMENTO}: "
        f"{_n(len(panorama.cenarios), 'cenário', 'cenários')}; o de mais turnos libera "
        f"{_n(melhor, 'turno', 'turnos')} por semana, sem abrir turno novo.",
        *(
            _descricao_do_cenario(numero, cenario, recomendado and numero == 1)
            for numero, cenario in enumerate(panorama.cenarios, 1)
        ),
        contexto,
        *avisos,
        _FECHO,
    ]
    resumo = [
        _inteiro("Cenários", len(panorama.cenarios)),
        _inteiro("Maior número de turnos liberados", melhor),
        _inteiro("Turnos com presença paga na semana", panorama.turnos_pagos),
        _inteiro("Turnos presos por regra", presos),
    ]
    linhas = _linhas(foto, panorama)
    por_cenario = _resumo_dos_cenarios(panorama)
    dias_nao_lidos = sorted(set(foto.dias_nao_lidos))
    bloco = BlocoRelatorio.model_validate(
        {
            "tipo": "tabela_generica",
            "titulo": TITULO_PANORAMA_ENXUGAMENTO,
            # A fonte é a grade semanal, sem datas: o bloco não inventa período.
            "periodo": None,
            "meta": None,
            "parcial": bool(dias_nao_lidos),
            "dias_nao_lidos": dias_nao_lidos,
            "avisos": avisos,
            "resumo": resumo,
            "contem_dados_pacientes": True,
            "dados": DadosTabelaGenerica(
                relatorio=RELATORIO_PANORAMA_ENXUGAMENTO,
                dimensoes=[],
                metricas=[],
                colunas=[
                    ColunaTabela(chave=c, rotulo=r, formato=f) for c, r, f in COLUNAS_PANORAMA
                ],
                linhas=linhas,
                total={},
                linhas_omitidas=0,
                agrupar_por=list(AGRUPAR_PANORAMA),
                unidade_grupo=UnidadeGrupo(singular="passo", plural="passos"),
            ),
            "tabelas": [
                tabela(ABA_PASSOS, COLUNAS_PANORAMA, linhas),
                tabela(ABA_RESUMO, COLUNAS_RESUMO_DOS_CENARIOS, por_cenario),
            ],
        }
    )
    return "\n".join(texto), bloco


def nomes_de_profissionais_no_bloco(bloco: BlocoRelatorio) -> list[str]:
    """Nomes de profissional que um bloco do panorama leva (para a proteção do texto)."""
    dados = bloco.dados
    if (
        not isinstance(dados, DadosTabelaGenerica)
        or dados.relatorio != RELATORIO_PANORAMA_ENXUGAMENTO
    ):
        return []
    nomes: set[str] = set()
    for linha in dados.linhas:
        for chave in ("turno_liberado", "de", "para"):
            partes = str(linha.get(chave) or "").split(" · ")
            if len(partes) >= 2 and partes[1].strip():
                nomes.add(partes[1].strip())
    return sorted(nomes)
