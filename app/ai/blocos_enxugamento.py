"""Texto (para o modelo) e blocos (para a tela) do panorama de enxugamento.

Dois blocos `tabela_generica` na mesma resposta, ambos com
`dados.relatorio = "panorama_enxugamento"` (a restrição por grupo cobre os dois):
o quadro comparativo (uma linha por cenário, só números) e os passos dos cenários
detalhados (por padrão, só o recomendado; o não recomendado mostra só o que
difere do Cenário 1).

Privacidade e sensibilidade de RH: o bloco de passos leva nomes de pacientes e
de profissionais; os dois saem com `contem_dados_pacientes: true` (só pelo chat).
O texto que o modelo lê leva SÓ contagens e o mapa "Cenário N = código": nenhum
nome, nenhum valor em dinheiro. Os códigos opacos ficam só nesse texto, nunca
num bloco. A guarda de `app.ai.fundamentacao` troca pelo resumo determinístico
(`resumo_do_panorama`) qualquer resposta que cite um nome ou um código.
"""

import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from typing import Final, cast

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
from app.engine.grade_simulada import Lugar

#: Prefixo do texto que o modelo lê (não é título de bloco).
TITULO_PANORAMA_ENXUGAMENTO: Final[str] = "Panorama de enxugamento (grade semanal vigente)"
TITULO_COMPARATIVO: Final[str] = "Comparativo de cenários"
TITULO_PASSOS_RECOMENDADO: Final[str] = "Passos do cenário recomendado"

#: Quadro comparativo (primeiro bloco): uma linha por cenário, só números.
#: `recomendado` é booleano JSON; o enum de formatos não tem booleano, então vai
#: como "texto" (regra geral do contrato).
COLUNAS_COMPARATIVO: Final[list[tuple[str, str, Formato]]] = [
    ("cenario", "Cenário", "texto"),
    ("turnos_liberados", "Turnos liberados", "inteiro"),
    ("passos", "Passos", "inteiro"),
    ("profissionais_afetadas", "Profissionais afetadas", "inteiro"),
    ("pacientes_movidos", "Pacientes movidos", "inteiro"),
    ("trocam_de_profissional", "Trocam de profissional", "inteiro"),
    ("trocam_de_dia", "Trocam de dia", "inteiro"),
    ("profissionais_sem_atendimento", "Profissionais sem atendimento", "inteiro"),
    ("turnos_sem_profissional", "Turnos sem profissional", "inteiro"),
    ("passos_iguais_ao_recomendado", "Passos iguais ao recomendado", "inteiro"),
    ("recomendado", "Recomendado", "texto"),
]
#: Passos (segundo bloco): só dos cenários detalhados.
COLUNAS_PASSOS: Final[list[tuple[str, str, Formato]]] = [
    ("cenario", "Cenário", "texto"),
    ("turno_liberado", "Turno liberado", "texto"),
    ("passo", "Passo", "inteiro"),
    ("paciente", "Quem", "texto"),
    ("terapia", "Terapia", "texto"),
    ("de", "De", "texto"),
    ("para", "Para", "texto"),
    ("tipo_de_movimento", "Tipo de movimento", "texto"),
    ("observacao", "Observação", "texto"),
]
AGRUPAR_PASSOS: Final[tuple[str, ...]] = ("cenario",)
#: Nomes das abas da exportação: únicos entre os dois blocos, até 31 caracteres e
#: sem os caracteres proibidos do Excel.
ABA_CENARIOS: Final[str] = "Cenários"
ABA_PASSOS: Final[str] = "Passos"

#: Limites da sugestão, sempre presentes nos avisos do quadro comparativo.
AVISO_CONFIRMAR: Final[str] = (
    "Cada mudança precisa ser confirmada com a família e feita manualmente na planilha."
)
AVISOS_PANORAMA: Final[tuple[str, ...]] = (
    "Sugestão: considera apenas os atendimentos presentes na semana lida (terapias "
    "quinzenais ou fora desta semana não aparecem).",
    "Não considera a disponibilidade dos pacientes.",
    "Vagas livres na grade podem estar reservadas (reuniões, supervisão); confira antes de mudar.",
    AVISO_CONFIRMAR,
)
#: Legenda das colunas de cobertura do quadro (um aviso geral, no lugar dos avisos
#: por cenário).
AVISO_LEGENDA_COBERTURA: Final[str] = (
    "Profissionais sem atendimento: ficam sem nenhum paciente na semana. Turnos sem "
    "profissional: turnos de uma terapia em que ninguém atenderia, num dia em que hoje há "
    "atendimento."
)
AVISO_BUSCA_LIMITADA: Final[str] = (
    "Busca limitada: havia combinações demais e nem todas foram avaliadas; pode existir "
    "cenário melhor."
)

#: Rótulos legíveis das alavancas do motor (L1 a L5).
TIPOS_DE_MOVIMENTO: Final[dict[Alavanca, str]] = {
    Alavanca.L1: "Mesmo profissional · outro turno",
    Alavanca.L2: "Outra profissional da mesma terapia · mesmo turno",
    Alavanca.L3: "Outra profissional da mesma terapia · outro turno",
    Alavanca.L4: "Mesmo profissional · outro dia",
    Alavanca.L5: "Abre espaço deslocando outro paciente",
}
OBSERVACAO_ABRE_ESPACO: Final[str] = "deslocado para abrir espaço, com a mesma profissional"
OBSERVACAO_SEM_PRESENCA: Final[str] = "deixa a profissional sem atendimento na semana"

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


#: Por que o cenário recomendado vem primeiro, pelo perfil dele.
_PORQUES: Final[dict[str, str]] = {
    "equilibrado": "que equilibra turnos liberados e mudanças",
    "mais turnos": "que libera mais turnos",
    "menos mudança": "que pede menos mudanças",
}
#: "Cenário 3 · equilibrado..." -> número e perfil (o resumo determinístico relê o bloco).
_ROTULO_NUMERADO: Final[re.Pattern[str]] = re.compile(r"^Cenário (\d+) · ([^·]+?)(?: · |$)")


def _n(quantidade: int, singular: str, plural: str) -> str:
    return f"{quantidade} {singular if quantidade == 1 else plural}"


def _lista(numeros: Sequence[int]) -> str:
    """`1`, `1 e 3`, `1, 2 e 3`."""
    textos = [str(n) for n in numeros]
    return textos[0] if len(textos) == 1 else f"{', '.join(textos[:-1])} e {textos[-1]}"


def _terapia(especialidade: Especialidade | None) -> str:
    return rotulo_enum(especialidade.value) if especialidade is not None else "—"


def _rotulo_curto(numero: int, cenario: Cenario) -> str:
    """`Cenário 1 · equilibrado`: sem código e sem nomes (coluna do quadro)."""
    return f"Cenário {numero} · {_PERFIS[cenario.perfil]}"


def _rotulo_do_grupo(numero: int, cenario: Cenario) -> str:
    """`Cenário 1 · equilibrado · libera 2 turnos`: título do grupo dos passos."""
    turnos = _n(cenario.resumo.turnos_liberados, "turno", "turnos")
    return f"{_rotulo_curto(numero, cenario)} · libera {turnos}"


def _turno_na_tela(foto: FotografiaPeriodo, turno: TurnoPago) -> str:
    profissional = foto.profissionais.get(turno.profissional_id)
    especialidade = profissional.especialidade if profissional is not None else None
    return (
        f"{_DIAS_CURTOS[turno.data.weekday()]} {_TURNOS[turno.turno]} · "
        f"{foto.nome_profissional(turno.profissional_id)} · {_terapia(especialidade)}"
    )


def _observacao(passo: PassoEnxugamento, sem_presenca: frozenset[str]) -> str:
    """Vazia no caso padrão; só as exceções."""
    partes = []
    if passo.abre_espaco:
        partes.append(OBSERVACAO_ABRE_ESPACO)
    # Com outra profissional, a sala costuma mudar junto: só é exceção com a mesma.
    if passo.de.sala_id != passo.para.sala_id and not passo.outro_profissional:
        partes.append("outra sala")
    if passo.posto_extra:
        partes.append("posto simultâneo da profissional")
    if passo.turno_liberado.profissional_id in sem_presenca:
        partes.append(OBSERVACAO_SEM_PRESENCA)
    return "; ".join(partes)


def _chave_do_passo(passo: PassoEnxugamento) -> tuple[str, Lugar, Lugar]:
    """Dois passos são iguais quando movem o mesmo paciente, da mesma origem ao mesmo destino."""
    return passo.paciente, passo.de, passo.para


def passos_iguais(cenario: Cenario, recomendado: Cenario) -> int:
    """Passos de `cenario` iguais aos do recomendado (0 para o próprio recomendado)."""
    if cenario is recomendado:
        return 0
    chaves = {_chave_do_passo(p) for p in recomendado.passos}
    return sum(1 for p in cenario.passos if _chave_do_passo(p) in chaves)


def so_o_que_difere(cenario: Cenario, recomendado: Cenario) -> bool:
    """Se o cenário detalhado mostra só os passos que diferem do recomendado: só quando
    há passos que diferem E passos iguais. Subconjunto do recomendado, cenário idêntico
    ou sem nenhum passo igual saem com os passos completos (nunca um bloco vazio)."""
    if cenario is recomendado:
        return False
    return 0 < passos_iguais(cenario, recomendado) < len(cenario.passos)


def profissionais_afetadas(cenario: Cenario) -> int:
    """Profissionais distintas em qualquer origem ou destino dos passos."""
    return len({lugar.profissional_id for p in cenario.passos for lugar in (p.de, p.para)})


def profissionais_sem_atendimento(cenario: Cenario) -> int:
    """Profissionais que ficam sem nenhum atendimento na semana (zeram a semana)."""
    return len(cenario.resumo.profissionais_sem_presenca)


def turnos_sem_profissional(cenario: Cenario) -> int:
    """(dia, turno, terapia) com atendimento hoje que ficaria sem nenhuma profissional."""
    return len(cenario.resumo.sem_cobertura)


def _linhas_do_comparativo(panorama: PanoramaEnxugamento) -> list[dict[str, ValorSimples]]:
    recomendado = panorama.cenarios[0]
    return [
        {
            "cenario": _rotulo_curto(numero, cenario),
            "turnos_liberados": cenario.resumo.turnos_liberados,
            "passos": len(cenario.passos),
            "profissionais_afetadas": profissionais_afetadas(cenario),
            "pacientes_movidos": cenario.resumo.pacientes_movidos,
            "trocam_de_profissional": cenario.resumo.pacientes_trocam_profissional,
            "trocam_de_dia": cenario.resumo.pacientes_trocam_dia,
            "profissionais_sem_atendimento": profissionais_sem_atendimento(cenario),
            "turnos_sem_profissional": turnos_sem_profissional(cenario),
            "passos_iguais_ao_recomendado": passos_iguais(cenario, recomendado),
            "recomendado": numero == 1,
        }
        for numero, cenario in enumerate(panorama.cenarios, 1)
    ]


def _linhas_dos_passos(
    foto: FotografiaPeriodo, panorama: PanoramaEnxugamento, detalhados: Sequence[int]
) -> list[dict[str, ValorSimples]]:
    """Linhas contíguas por cenário; o não recomendado só com o que difere do Cenário 1
    quando há passos que diferem e passos iguais (`so_o_que_difere`)."""
    recomendado = panorama.cenarios[0]
    do_recomendado = {_chave_do_passo(p) for p in recomendado.passos}
    linhas: list[dict[str, ValorSimples]] = []
    for numero in detalhados:
        cenario = panorama.cenarios[numero - 1]
        rotulo = _rotulo_do_grupo(numero, cenario)
        sem_presenca = frozenset(cenario.resumo.profissionais_sem_presenca)
        so_diferentes = so_o_que_difere(cenario, recomendado)
        # A ordem dos passos (por turno liberado) é a da execução manual; o número do
        # passo é o do cenário inteiro, mesmo quando só o que difere aparece.
        for indice, passo in enumerate(cenario.passos, 1):
            if so_diferentes and _chave_do_passo(passo) in do_recomendado:
                continue
            linhas.append(
                {
                    "cenario": rotulo,
                    "turno_liberado": _turno_na_tela(foto, passo.turno_liberado),
                    "passo": indice,
                    "paciente": passo.paciente,
                    "terapia": _terapia(passo.especialidade),
                    "de": lugar_na_tela(foto, passo.de),
                    "para": lugar_na_tela(foto, passo.para),
                    "tipo_de_movimento": TIPOS_DE_MOVIMENTO[passo.alavanca],
                    "observacao": _observacao(passo, sem_presenca),
                }
            )
    return linhas


def titulo_dos_passos(detalhados: Sequence[int], diferentes: Sequence[int] = ()) -> str:
    """Título do bloco de passos; `diferentes` são os cenários mostrados só com o que
    difere do Cenário 1 (o título deixa isso explícito)."""
    outros = [n for n in detalhados if n != 1]
    diferentes = [n for n in detalhados if n in diferentes]
    if not outros:
        return TITULO_PASSOS_RECOMENDADO
    if len(detalhados) == 1:
        if diferentes:
            return f"Passos do Cenário {outros[0]} (apenas o que difere do Cenário 1)"
        return f"Passos do Cenário {outros[0]}"
    base = f"Passos dos Cenários {_lista(detalhados)}"
    if not diferentes:
        return base
    if diferentes == list(detalhados):
        return f"{base} (apenas o que difere do Cenário 1)"
    quais = (
        f"o Cenário {diferentes[0]}"
        if len(diferentes) == 1
        else f"os Cenários {_lista(diferentes)}"
    )
    return f"{base} ({quais} só com o que difere do Cenário 1)"


def _aviso_do_cenario(cenario: Cenario, recomendado: Cenario) -> str:
    """Como o cenário detalhado se compara ao Cenário 1, só com contagens."""
    iguais = passos_iguais(cenario, recomendado)
    total = len(cenario.passos)
    if so_o_que_difere(cenario, recomendado):
        quantos = "1 passo é igual" if iguais == 1 else f"{iguais} passos são iguais"
        return f"Mostra só o que difere do Cenário 1; {quantos} aos dele."
    if iguais == 0:
        return "Nenhum passo deste cenário é igual aos do Cenário 1."
    do_recomendado = len(recomendado.passos)
    if iguais == total == do_recomendado:
        mesmos = "o mesmo passo" if total == 1 else f"os mesmos {total} passos"
        return f"Este cenário é idêntico ao Cenário 1 ({mesmos})."
    # Subconjunto do Cenário 1: todos os passos dele estão no recomendado.
    menos = recomendado.resumo.turnos_liberados - cenario.resumo.turnos_liberados
    if menos > 0:
        turnos = f"libera {_n(menos, 'turno', 'turnos')} a menos"
    elif menos < 0:
        turnos = f"libera {_n(-menos, 'turno', 'turnos')} a mais"
    else:
        turnos = "libera o mesmo número de turnos"
    return (
        f"Este cenário faz {total} dos {do_recomendado} passos do Cenário 1 e deixa de fazer "
        f"{do_recomendado - total}; {turnos}."
    )


def _avisos_dos_passos(panorama: PanoramaEnxugamento, detalhados: Sequence[int]) -> list[str]:
    recomendado = panorama.cenarios[0]
    avisos = []
    outros = [n for n in detalhados if n != 1]
    for numero in outros:
        prefixo = "" if len(outros) == 1 else f"Cenário {numero}: "
        avisos.append(prefixo + _aviso_do_cenario(panorama.cenarios[numero - 1], recomendado))
    avisos.append(AVISO_CONFIRMAR)
    return avisos


def _presos(presos: dict[MotivoPreso, int] | Counter[MotivoPreso]) -> str:
    partes = [
        _n(quantidade, *_MOTIVOS_PRESO[motivo])
        for motivo, quantidade in presos.items()
        if quantidade
    ]
    return ", ".join(partes)


def avisos_do_panorama(foto: FotografiaPeriodo, panorama: PanoramaEnxugamento) -> list[str]:
    """Avisos gerais, só com contagens (sem nomes). Os números por cenário (quem fica
    sem atendimento, turnos sem profissional) são colunas do quadro, com uma legenda aqui."""
    avisos = list(AVISOS_PANORAMA)
    presos = sum(panorama.presos.values())
    if presos:
        leves = _n(
            presos, "turno leve não pode ser liberado", "turnos leves não podem ser liberados"
        )
        avisos.append(f"{leves} por regra: {_presos(dict(panorama.presos))}.")
    if panorama.estatistica.busca_limitada:
        avisos.append(AVISO_BUSCA_LIMITADA)
    if foto.dias_nao_lidos:
        dias = ", ".join(_DIAS_CURTOS[d.weekday()] for d in sorted(set(foto.dias_nao_lidos)))
        avisos.append(f"Não foi possível ler: {dias}; a sugestão não considera esses dias.")
    if panorama.cenarios:
        # A legenda das colunas do quadro, por último.
        avisos.append(AVISO_LEGENDA_COBERTURA)
    return avisos


def _inteiro(rotulo: str, valor: int) -> ItemResumo:
    return ItemResumo(rotulo=rotulo, valor=valor, formato="inteiro", exibicao=str(valor))


def _descricao_do_cenario(numero: int, cenario: Cenario, recomendado: Cenario) -> str:
    r = cenario.resumo
    marca = "recomendado; " if cenario is recomendado else ""
    iguais = (
        ""
        if cenario is recomendado
        else f"; {_n(passos_iguais(cenario, recomendado), 'passo igual', 'passos iguais')} "
        "aos do Cenário 1"
    )
    ocupacao = (
        "; ocupação de quem absorve: "
        + formatar_razao(
            r.slots_ocupados_absorventes, r.slots_escalados_absorventes, percentual=True
        )
        if r.slots_escalados_absorventes
        else ""
    )
    return (
        f"- Cenário {numero} ({marca}{_PERFIS[cenario.perfil]}): libera "
        f"{_n(r.turnos_liberados, 'turno', 'turnos')} por semana, "
        f"{_n(r.turnos_novos, 'turno novo', 'turnos novos')}; "
        f"{_n(len(cenario.passos), 'passo', 'passos')}; "
        f"{_n(r.pacientes_movidos, 'paciente muda', 'pacientes mudam')} "
        f"({r.pacientes_trocam_profissional} de profissional, {r.pacientes_trocam_dia} de dia, "
        f"{r.pacientes_trocam_turno} de turno); "
        f"{_n(profissionais_afetadas(cenario), 'profissional afetada', 'profissionais afetadas')}"
        f"; {_n(profissionais_sem_atendimento(cenario), 'profissional', 'profissionais')} "
        "sem atendimento na semana; "
        f"{_n(turnos_sem_profissional(cenario), 'turno', 'turnos')} sem profissional"
        f"; deslocamento médio de {str(r.minutos_medio).replace('.', ',')} min e máximo de "
        f"{r.minutos_maximo} min{ocupacao}{iguais}."
    )


def _mapa_de_codigos(panorama: PanoramaEnxugamento) -> str:
    """O único lugar com os códigos: o `content`, para o modelo chamar a tool de novo."""
    mapa = "; ".join(
        f"Cenário {numero} = {cenario.codigo}"
        for numero, cenario in enumerate(panorama.cenarios, 1)
    )
    return (
        f"Códigos dos cenários, só para chamar a ferramenta de novo (NUNCA escreva um código "
        f"na resposta): {mapa}."
    )


def _pergunta_final(proximo: int | None) -> str:
    if proximo is None:
        return "Quer que eu procure outras opções de cenário?"
    return f"Quer ver os passos do Cenário {proximo}?"


def _fecho(proximo: int | None) -> str:
    return (
        "Os nomes de pacientes e de profissionais estão só no relatório da tela, e os códigos "
        "servem só para a ferramenta: não escreva nenhum nome nem código. Responda em 3 a 4 "
        'frases, em prosa, sem listas, como sugestão ("sugiro..."): (1) qual cenário você '
        "recomenda e por quê, com os números acima (turnos liberados, pacientes movidos, quantos "
        "trocam de profissional e quantos trocam de dia); (2) que cada mudança precisa ser "
        "confirmada com as famílias e feita manualmente na planilha; (3) termine com a "
        f'pergunta "{_pergunta_final(proximo)}" (o número do cenário, nunca o código). Não '
        "fale em valores nem em dinheiro: o panorama só conta turnos liberados. Se pedirem para "
        'ver um cenário ("quero ver o Cenário 3"), chame de novo com os mesmos parâmetros e '
        "detalhar_cenarios com o código dele; para mais opções, chame de novo com "
        "cenarios_ja_vistos com os códigos acima."
    )


_FECHO_CODIGO_INEXISTENTE: Final[str] = (
    "O cenário pedido em detalhar_cenarios não está entre os gerados agora com estes "
    "parâmetros (eles podem ter mudado): na tela vai só o comparativo, sem passos. Diga em 1 a "
    "2 frases que esse cenário não está disponível com o pedido atual e pergunte qual cenário, "
    "pelo número, a pessoa quer ver; não escreva nomes nem códigos."
)


def _detalhados(panorama: PanoramaEnxugamento, detalhar: Sequence[str]) -> list[int] | None:
    """Números dos cenários a detalhar (por padrão, só o recomendado); `None` se algum
    código pedido não existir entre os cenários gerados."""
    numeros = {c.codigo: numero for numero, c in enumerate(panorama.cenarios, 1)}
    pedidos = list(dict.fromkeys(str(codigo).strip().upper() for codigo in detalhar))
    if not pedidos:
        return [1]
    if any(codigo not in numeros for codigo in pedidos):
        return None
    return sorted(numeros[codigo] for codigo in pedidos)


def _tabela_generica(
    titulo: str,
    avisos: list[str],
    resumo: list[ItemResumo],
    colunas: list[tuple[str, str, Formato]],
    linhas: list[dict[str, ValorSimples]],
    aba: str,
    dias_nao_lidos: list[date],
    agrupar: bool,
) -> BlocoRelatorio:
    return BlocoRelatorio.model_validate(
        {
            "tipo": "tabela_generica",
            "titulo": titulo,
            # A fonte é a grade semanal, sem datas: o bloco não inventa período.
            "periodo": None,
            "meta": None,
            "parcial": bool(dias_nao_lidos),
            "dias_nao_lidos": dias_nao_lidos,
            "avisos": avisos,
            "resumo": resumo,
            # Os dois blocos: a restrição é pelo `relatorio`, e os passos têm nomes.
            "contem_dados_pacientes": True,
            "dados": DadosTabelaGenerica(
                relatorio=RELATORIO_PANORAMA_ENXUGAMENTO,
                dimensoes=[],
                metricas=[],
                colunas=[ColunaTabela(chave=c, rotulo=r, formato=f) for c, r, f in colunas],
                linhas=linhas,
                total={},
                linhas_omitidas=0,
                agrupar_por=list(AGRUPAR_PASSOS) if agrupar else None,
                unidade_grupo=UnidadeGrupo(singular="passo", plural="passos") if agrupar else None,
            ),
            "tabelas": [tabela(aba, colunas, linhas)],
        }
    )


def panorama_enxugamento(
    foto: FotografiaPeriodo, panorama: PanoramaEnxugamento, detalhar: Sequence[str] = ()
) -> tuple[str, list[BlocoRelatorio]]:
    """Texto (só contagens e o mapa dos códigos) e os blocos: o quadro comparativo e os
    passos dos cenários detalhados (por padrão, só o recomendado)."""
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
        return "\n".join(texto), []

    recomendado = panorama.cenarios[0]
    melhor = max(c.resumo.turnos_liberados for c in panorama.cenarios)
    detalhados = _detalhados(panorama, detalhar)
    quantos = _n(len(panorama.cenarios), "cenário", "cenários")
    if detalhados is None:
        na_tela = f"Na tela: só o comparativo dos {quantos}."
        fecho = _FECHO_CODIGO_INEXISTENTE
    else:
        mostrados = (
            "do cenário recomendado"
            if detalhados == [1]
            else (
                f"do Cenário {detalhados[0]}"
                if len(detalhados) == 1
                else f"dos Cenários {_lista(detalhados)}"
            )
        )
        na_tela = f"Na tela: o comparativo dos {quantos} e os passos {mostrados}."
        diferentes = [
            n for n in detalhados if so_o_que_difere(panorama.cenarios[n - 1], recomendado)
        ]
        if diferentes:
            na_tela += (
                f" {'Do Cenário' if len(diferentes) == 1 else 'Dos Cenários'} "
                f"{_lista(diferentes)}, só os passos que diferem do Cenário 1."
            )
        proximo = next(
            (n for n in range(1, len(panorama.cenarios) + 1) if n not in detalhados), None
        )
        fecho = _fecho(proximo)
    texto = [
        f"{TITULO_PANORAMA_ENXUGAMENTO}: {quantos}; o recomendado é o Cenário 1, que libera "
        f"{_n(recomendado.resumo.turnos_liberados, 'turno', 'turnos')} por semana (o de mais "
        f"turnos libera {_n(melhor, 'turno', 'turnos')}), sem abrir turno novo.",
        *(
            _descricao_do_cenario(numero, cenario, recomendado)
            for numero, cenario in enumerate(panorama.cenarios, 1)
        ),
        _mapa_de_codigos(panorama),
        na_tela,
        contexto,
        *avisos,
        fecho,
    ]
    dias_nao_lidos = sorted(set(foto.dias_nao_lidos))
    comparativo = _tabela_generica(
        TITULO_COMPARATIVO,
        avisos,
        [
            _inteiro("Cenários", len(panorama.cenarios)),
            _inteiro("Maior número de turnos liberados", melhor),
            _inteiro("Turnos com presença paga na semana", panorama.turnos_pagos),
            _inteiro("Turnos presos por regra", presos),
        ],
        COLUNAS_COMPARATIVO,
        _linhas_do_comparativo(panorama),
        ABA_CENARIOS,
        dias_nao_lidos,
        agrupar=False,
    )
    if detalhados is None:
        return "\n".join(texto), [comparativo]

    linhas = _linhas_dos_passos(foto, panorama, detalhados)
    if len(detalhados) == 1:
        resumo_cenario = panorama.cenarios[detalhados[0] - 1].resumo
        resumo = [
            _inteiro("Turnos liberados", resumo_cenario.turnos_liberados),
            _inteiro("Passos", len(linhas)),
            _inteiro("Pacientes movidos", resumo_cenario.pacientes_movidos),
        ]
    else:
        resumo = [_inteiro("Cenários detalhados", len(detalhados)), _inteiro("Passos", len(linhas))]
    passos = _tabela_generica(
        titulo_dos_passos(
            detalhados,
            [n for n in detalhados if so_o_que_difere(panorama.cenarios[n - 1], recomendado)],
        ),
        _avisos_dos_passos(panorama, detalhados),
        resumo,
        COLUNAS_PASSOS,
        linhas,
        ABA_PASSOS,
        dias_nao_lidos,
        agrupar=True,
    )
    return "\n".join(texto), [comparativo, passos]


def e_comparativo_do_panorama(bloco: BlocoRelatorio) -> bool:
    dados = bloco.dados
    return (
        isinstance(dados, DadosTabelaGenerica)
        and dados.relatorio == RELATORIO_PANORAMA_ENXUGAMENTO
        and any(coluna.chave == "recomendado" for coluna in dados.colunas)
    )


def resumo_do_panorama(blocos: Sequence[BlocoRelatorio]) -> str | None:
    """Resumo determinístico (3 a 4 frases, sem nomes nem códigos) a partir dos blocos do
    panorama; `None` se a rodada não trouxe o quadro comparativo."""
    comparativo = next((b for b in blocos if e_comparativo_do_panorama(b)), None)
    if comparativo is None or not isinstance(comparativo.dados, DadosTabelaGenerica):
        return None
    linhas = comparativo.dados.linhas
    if not linhas:
        return None
    escolhido = next((linha for linha in linhas if linha.get("recomendado") is True), linhas[0])
    achado = _ROTULO_NUMERADO.match(str(escolhido["cenario"]))
    numero, perfil = (int(achado[1]), achado[2].strip()) if achado else (1, "")
    porque = f", {_PORQUES[perfil]}" if perfil in _PORQUES else ""
    mostrados: set[int] = set()
    for bloco in blocos:
        dados = bloco.dados
        if bloco is comparativo or not isinstance(dados, DadosTabelaGenerica):
            continue
        if dados.relatorio != RELATORIO_PANORAMA_ENXUGAMENTO:
            continue
        for linha in dados.linhas:
            if achou := _ROTULO_NUMERADO.match(str(linha.get("cenario") or "")):
                mostrados.add(int(achou[1]))
    numeros = [
        int(achou[1])
        for linha in linhas
        if (achou := _ROTULO_NUMERADO.match(str(linha["cenario"])))
    ]
    proximo = next((n for n in numeros if n not in mostrados), None)
    turnos = int(cast(int, escolhido["turnos_liberados"]))
    pacientes = int(cast(int, escolhido["pacientes_movidos"]))
    trocam_profissional = int(cast(int, escolhido["trocam_de_profissional"]))
    trocam_dia = int(cast(int, escolhido["trocam_de_dia"]))
    return " ".join(
        [
            f"Sugiro o Cenário {numero}{porque}: libera {_n(turnos, 'turno', 'turnos')} por "
            f"semana sem abrir turno novo e muda {_n(pacientes, 'paciente', 'pacientes')} de "
            f"lugar ({trocam_profissional} com troca de profissional e {trocam_dia} com troca "
            "de dia).",
            "Cada mudança precisa ser confirmada com as famílias e feita manualmente na planilha.",
            f"O comparativo de {_n(len(linhas), 'cenário', 'cenários')} e os passos estão no "
            "relatório.",
            _pergunta_final(proximo),
        ]
    )


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
