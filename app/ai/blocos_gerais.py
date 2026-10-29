"""Blocos e resumos curtos dos relatórios gerais (tool `gerar_relatorio`).

Cada função recebe o resultado da engine (`app.engine.relatorios_prontos` ou
`app.engine.relatorio_generico`) e devolve `(texto, bloco)`: o bloco vai como
artefato para o app visual; o texto, curto, é o que o modelo lê. Os dois saem
do mesmo resultado, então nunca discordam. Percentuais são formatados a partir
dos inteiros (`formatar_razao`), como nas tools que já existiam.

Nenhum bloco daqui traz id ou nome de paciente, exceto `agenda_paciente`, que só
é gerado para o paciente pedido explicitamente.
"""

from collections import Counter, defaultdict
from datetime import date, time
from hashlib import sha256
from typing import Final

from app.ai.relatorios import (
    COLUNAS_DATA,
    DIAS_DA_SEMANA,
    AguardandoProfissionalDia,
    BlocoRelatorio,
    ColunaTabela,
    DadosAgendaPaciente,
    DadosConvenio,
    DadosDisponibilidade,
    DadosQualidadeAgenda,
    DadosTabelaGenerica,
    DiaAgendaPacienteRelatorio,
    FaixaVaga,
    Formato,
    IntervaloAgendaPaciente,
    ItemConvenio,
    ItemResumo,
    PacienteRelatorio,
    PeriodoRelatorio,
    ProfissionalRelatorio,
    QualidadeProfissional,
    QualidadeProfissionalDiaRelatorio,
    SessaoAgendaPaciente,
    VagasDiaPeriodo,
    ValorSimples,
    tabela,
)
from app.domain import Especialidade
from app.domain.constants import DURACAO_SLOT_MINUTOS
from app.domain.formatacao import chave_natural, chave_texto, formatar_razao, rotulo_enum
from app.engine.fotografia import FotografiaPeriodo
from app.engine.pacientes_por_plano import PacientesPorPlano
from app.engine.planejamento import PlanoDia, PlanoSemanal, SessaoPlano, maior_sequencia
from app.engine.relatorio_generico import (
    Dimensao,
    LinhaRelatorio,
    Metrica,
    ResultadoRelatorio,
    ValorMetrica,
    rotulo_convenio,
)
from app.engine.relatorios_prontos import (
    AgendaPaciente,
    QualidadeProfissionalDia,
    RelatorioCapacidade,
    RelatorioConvenios,
    RelatorioQualidade,
    RelatorioVagas,
    SituacaoCapacidade,
)

#: Máximo de linhas de detalhe no texto que o modelo lê (o bloco traz todas).
MAX_LINHAS_NO_TEXTO: Final[int] = 10

_DIAS_CURTOS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")

#: (rótulo, formato) de cada métrica nas tabelas.
ROTULOS_METRICAS: Final[dict[Metrica, tuple[str, Formato]]] = {
    Metrica.OCUPACAO: ("Ocupação", "percentual"),
    Metrica.SLOTS_ESCALADOS: ("Slots escalados", "inteiro"),
    Metrica.SLOTS_OCUPADOS: ("Slots ocupados", "inteiro"),
    Metrica.VAGAS_LIVRES: ("Vagas livres", "inteiro"),
    Metrica.SLOTS_PARA_META: ("Slots para a meta", "inteiro"),
    Metrica.SLOTS_FECHADOS: ("Slots fechados", "inteiro"),
    Metrica.PACIENTES_DISTINTOS: ("Pacientes distintos", "inteiro"),
    Metrica.SESSOES: ("Sessões", "inteiro"),
    Metrica.HORAS_ESCALADAS: ("Horas escaladas", "decimal"),
    Metrica.HORAS_OCUPADAS: ("Horas ocupadas", "decimal"),
    Metrica.HORAS_LIVRES: ("Horas livres", "decimal"),
    Metrica.AGUARDANDO_AUTORIZACAO: ("Aguardando autorização", "inteiro"),
    Metrica.JANELAS_LIVRES: ("Janelas livres", "inteiro"),
}

#: (chave, rótulo) de cada dimensão nas tabelas; dia vira data + dia da semana.
ROTULOS_DIMENSOES: Final[dict[Dimensao, tuple[str, str]]] = {
    Dimensao.DIA: ("data", "Data"),
    Dimensao.PROFISSIONAL: ("profissional", "Profissional"),
    Dimensao.ESPECIALIDADE: ("especialidade", "Especialidade"),
    Dimensao.SALA: ("sala", "Sala"),
    Dimensao.POSTO: ("posto", "Sala e posto"),
    Dimensao.PERIODO: ("periodo", "Período"),
    Dimensao.CONVENIO: ("convenio", "Convênio"),
}

#: Nome de cada dimensão em títulos ("por dia e especialidade").
NOMES_DIMENSOES: Final[dict[Dimensao, str]] = {
    Dimensao.DIA: "dia",
    Dimensao.PROFISSIONAL: "profissional",
    Dimensao.ESPECIALIDADE: "especialidade",
    Dimensao.SALA: "sala",
    Dimensao.POSTO: "posto",
    Dimensao.PERIODO: "período do dia",
    Dimensao.CONVENIO: "convênio",
}


def enumerar(itens: list[str]) -> str:
    """`a`, `a e b`, `a, b e c`."""
    if len(itens) <= 1:
        return "".join(itens)
    return f"{', '.join(itens[:-1])} e {itens[-1]}"


_ROTULOS_SITUACAO = {
    SituacaoCapacidade.SOBRANDO: "Sobrando (abaixo da meta)",
    SituacaoCapacidade.EQUILIBRADA: "Equilibrada",
    SituacaoCapacidade.NO_LIMITE: "No limite (quase sem vaga)",
}


def rotulo_periodo(inicio: date, fim: date) -> str:
    """`de 05/10/2026` ou `de 28/09/2026 a 03/10/2026`."""
    if inicio == fim:
        return f"de {inicio:%d/%m/%Y}"
    return f"de {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}"


def rotulo_duracao(minutos: int) -> str:
    """`60` -> `1 h`; `90` -> `1 h 30`; `30` -> `30 min`."""
    horas, resto = divmod(minutos, 60)
    if not horas:
        return f"{resto} min"
    return f"{horas} h" + (f" {resto:02d}" if resto else "")


def _n(quantidade: int, singular: str, plural: str) -> str:
    """`1 sessão`, `2 sessões`."""
    return f"{quantidade} {singular if quantidade == 1 else plural}"


def _hora(hora: time) -> str:
    return hora.strftime("%H:%M")


def _dia_curto(dia: date) -> str:
    return f"{_DIAS_CURTOS[dia.weekday()]} {dia:%d/%m}"


def _horas(valor: ValorMetrica) -> str:
    """`12.5` -> `12,5`: horas são meios inteiros, formatados a partir dos slots."""
    return formatar_razao(round(float(valor) * 2), 2)


def _profissional(foto: FotografiaPeriodo, profissional_id: str) -> ProfissionalRelatorio:
    profissional = foto.profissionais.get(profissional_id)
    return ProfissionalRelatorio(
        id=sha256(profissional_id.encode()).hexdigest(),
        nome=foto.nome_profissional(profissional_id),
        # Rótulo legível (`Terapia Ocupacional`), nunca o identificador.
        especialidade=rotulo_enum(profissional.especialidade.value)
        if profissional is not None
        else None,
    )


#: Especialidade de vaga sem especialidade no cadastro nem na grade.
ESPECIALIDADE_NAO_INFORMADA = "Não informada"


def _especialidade_da_faixa(especialidade: Especialidade | None) -> str:
    return (
        rotulo_enum(especialidade.value)
        if especialidade is not None
        else (ESPECIALIDADE_NAO_INFORMADA)
    )


def _profissional_da_faixa(
    foto: FotografiaPeriodo, profissional_id: str, especialidade_da_grade: Especialidade | None
) -> ProfissionalRelatorio:
    """Como `_profissional`, mas a vaga nunca sai sem especialidade.

    Quem está na grade mas fora de `listar_profissionais` não tem cadastro: vale a
    especialidade da própria grade (do slot) e, se nem ela existir, "Não informada".
    """
    profissional = _profissional(foto, profissional_id)
    if profissional.especialidade is not None:
        return profissional
    return profissional.model_copy(
        update={"especialidade": _especialidade_da_faixa(especialidade_da_grade)}
    )


def _rotulo_especialidade(especialidade: Especialidade | None) -> str:
    return rotulo_enum(especialidade.value) if especialidade is not None else "—"


def _bloco(
    tipo: str,
    titulo: str,
    foto: FotografiaPeriodo,
    avisos: list[str],
    meta: float | None,
    resumo: list[ItemResumo],
    dados: object,
    tabelas: list[object],
    *,
    com_periodo: bool = True,
) -> BlocoRelatorio:
    """Envelope comum. `com_periodo=False` só no plano semanal: `periodo` vai null."""
    return BlocoRelatorio.model_validate(
        {
            "tipo": tipo,
            "titulo": titulo,
            "periodo": PeriodoRelatorio(inicio=foto.inicio, fim=foto.fim) if com_periodo else None,
            "meta": meta,
            "parcial": foto.parcial,
            "dias_nao_lidos": foto.dias_nao_lidos,
            "avisos": avisos,
            "resumo": resumo,
            "dados": dados,
            "tabelas": tabelas,
        }
    )


def _cabecalho_texto(titulo: str, foto: FotografiaPeriodo) -> list[str]:
    linhas = [titulo]
    if foto.parcial:
        nao_lidos = ", ".join(_dia_curto(dia) for dia in foto.dias_nao_lidos)
        linhas.append(f"Não foi possível ler: {nao_lidos}; os números são parciais.")
    return linhas


def _mais(quantidade: int) -> list[str]:
    return [f"(+{quantidade} linhas no relatório da tela)"] if quantidade > 0 else []


# --- disponibilidade -------------------------------------------------------


def ordem_vaga(
    faixa: FaixaVaga,
) -> tuple[date, tuple[bool, str], str, tuple[bool, str], tuple[bool, tuple[str | int, ...]], int]:
    """Data -> especialidade -> início -> profissional -> sala (natural) -> posto.

    Textos sem diferenciar acento nem maiúsculas; valores ausentes vão para o fim.
    """
    especialidade = faixa.profissional.especialidade
    nome = faixa.profissional.nome
    return (
        faixa.data,
        (not especialidade, chave_texto(especialidade or "")),
        faixa.inicio,
        (not nome, chave_texto(nome)),
        (not faixa.sala_nome, chave_natural(faixa.sala_nome)),
        faixa.posto,
    )


def disponibilidade(
    foto: FotografiaPeriodo, relatorio: RelatorioVagas, avisos: list[str], recorte: str = ""
) -> tuple[str, BlocoRelatorio]:
    """Vagas livres (ou janelas contínuas) do período. `recorte`: ` (Psicologia · tarde)`."""
    minutos = relatorio.slots_por_janela * DURACAO_SLOT_MINUTOS
    janela = relatorio.slots_por_janela > 1
    titulo = (
        f"Janelas livres de {rotulo_duracao(minutos)}{recorte} "
        f"{rotulo_periodo(foto.inicio, foto.fim)}"
        if janela
        else f"Vagas livres{recorte} {rotulo_periodo(foto.inicio, foto.fim)}"
    )
    faixas = [
        FaixaVaga(
            data=f.data,
            dia_semana=DIAS_DA_SEMANA[f.data.weekday()],
            periodo=f.periodo.value,
            profissional=_profissional_da_faixa(foto, f.profissional_id, f.especialidade),
            sala_id=f.sala_id,
            sala_nome=f.sala_nome,
            posto=f.indice_posto + 1,
            inicio=_hora(f.inicio),
            fim=_hora(f.fim),
            slots=f.slots,
            cabem=f.cabem,
        )
        for f in relatorio.faixas
    ]
    por_dia = [
        VagasDiaPeriodo(
            data=date.fromisoformat(linha.chaves[Dimensao.DIA].id),
            dia_semana=DIAS_DA_SEMANA[date.fromisoformat(linha.chaves[Dimensao.DIA].id).weekday()],
            periodo="manha" if linha.chaves[Dimensao.PERIODO].id == "manha" else "tarde",
            vagas_livres=int(linha.valores[Metrica.VAGAS_LIVRES]),
            horas_livres=float(linha.valores[Metrica.HORAS_LIVRES]),
            janelas=int(linha.valores[Metrica.JANELAS_LIVRES]) if janela else None,
        )
        for linha in relatorio.resumo.linhas
    ]
    total = relatorio.resumo.total
    vagas = int(total[Metrica.VAGAS_LIVRES])
    resumo = [
        ItemResumo(rotulo="Vagas livres", valor=vagas, formato="inteiro", exibicao=str(vagas)),
        ItemResumo(
            rotulo="Horas livres",
            valor=float(total[Metrica.HORAS_LIVRES]),
            formato="decimal",
            exibicao=f"{_horas(total[Metrica.HORAS_LIVRES])} h",
        ),
    ]
    if janela:
        cabem = int(total[Metrica.JANELAS_LIVRES])
        resumo.append(
            ItemResumo(
                rotulo=f"Atendimentos de {rotulo_duracao(minutos)} que cabem",
                valor=cabem,
                formato="inteiro",
                exibicao=str(cabem),
            )
        )
    bloco = _bloco(
        "disponibilidade",
        titulo,
        foto,
        avisos,
        None,
        resumo,
        DadosDisponibilidade(minutos_por_janela=minutos, faixas=faixas, por_dia=por_dia),
        [
            tabela(
                "Vagas",
                [
                    *COLUNAS_DATA,
                    ("periodo", "Período", "texto"),
                    ("profissional", "Profissional", "texto"),
                    ("especialidade", "Especialidade", "texto"),
                    ("sala", "Sala", "texto"),
                    ("posto", "Posto", "inteiro"),
                    ("inicio", "Início", "texto"),
                    ("fim", "Fim", "texto"),
                    ("slots", "Slots livres", "inteiro"),
                    ("cabem", f"Cabem ({rotulo_duracao(minutos)})", "inteiro"),
                ],
                [
                    {
                        "data": f.data.isoformat(),
                        "dia_semana": f.dia_semana,
                        "periodo": "Manhã" if f.periodo == "manha" else "Tarde",
                        "profissional": f.profissional.nome,
                        "especialidade": f.profissional.especialidade or "—",
                        "sala": f.sala_nome,
                        "posto": f.posto,
                        "inicio": f.inicio,
                        "fim": f.fim,
                        "slots": f.slots,
                        "cabem": f.cabem,
                    }
                    for f in sorted(faixas, key=ordem_vaga)
                ],
            ),
            tabela(
                "Resumo por dia",
                [
                    *COLUNAS_DATA,
                    ("periodo", "Período", "texto"),
                    ("vagas_livres", "Vagas livres", "inteiro"),
                    ("horas_livres", "Horas livres", "decimal"),
                    ("janelas", f"Cabem ({rotulo_duracao(minutos)})", "inteiro"),
                ],
                [
                    {
                        **d.model_dump(mode="json", include={"data", "dia_semana"}),
                        "periodo": "Manhã" if d.periodo == "manha" else "Tarde",
                        "vagas_livres": d.vagas_livres,
                        "horas_livres": d.horas_livres,
                        "janelas": d.janelas,
                    }
                    for d in por_dia
                ],
            ),
        ],
    )

    texto = _cabecalho_texto(titulo, foto)
    total_linha = (
        f"Total: {_n(vagas, 'vaga', 'vagas')} de 30 min ({_horas(total[Metrica.HORAS_LIVRES])} h)"
    )
    if janela:
        total_linha += (
            f"; cabem {_n(int(total[Metrica.JANELAS_LIVRES]), 'atendimento', 'atendimentos')} "
            f"de {rotulo_duracao(minutos)} em {_n(len(faixas), 'faixa', 'faixas')}"
        )
    texto.append(total_linha + ".")
    por_data: dict[date, dict[str, int]] = defaultdict(dict)
    for d in por_dia:
        por_data[d.data][d.periodo] = (
            d.janelas if janela and d.janelas is not None else (d.vagas_livres)
        )
    texto.append(
        "Por dia ("
        + ("atendimentos que cabem" if janela else "vagas")
        + ", manhã/tarde): "
        + "; ".join(
            f"{_dia_curto(dia)} {v.get('manha', 0)}/{v.get('tarde', 0)}"
            for dia, v in sorted(por_data.items())
        )
        + "."
    )
    maiores = sorted(relatorio.faixas, key=lambda f: (-f.slots, f.data, f.inicio))
    if maiores:
        texto.append("Maiores faixas livres:")
        texto.extend(
            f"- {_dia_curto(f.data)} {_hora(f.inicio)}-{_hora(f.fim)}: {f.profissional_nome} "
            f"({_especialidade_da_faixa(f.especialidade)}), {f.sala_nome}"
            for f in maiores[:5]
        )
    return "\n".join(texto), bloco


# --- tabela genérica -------------------------------------------------------


def _valor_tabela(linha: LinhaRelatorio, dimensao: Dimensao) -> dict[str, ValorSimples]:
    valor = linha.chaves[dimensao]
    chave, _ = ROTULOS_DIMENSOES[dimensao]
    if dimensao is Dimensao.DIA:
        dia = date.fromisoformat(valor.id)
        return {"data": valor.id, "dia_semana": DIAS_DA_SEMANA[dia.weekday()]}
    return {chave: valor.rotulo}


def _exibicao_metrica(metrica: Metrica, valor: ValorMetrica, razao: tuple[int, int] | None) -> str:
    if metrica is Metrica.OCUPACAO and razao is not None:
        return formatar_razao(razao[0], razao[1], percentual=True)
    if metrica in (Metrica.HORAS_ESCALADAS, Metrica.HORAS_OCUPADAS, Metrica.HORAS_LIVRES):
        return f"{_horas(valor)} h"
    return str(valor)


def _rotulo_linha(linha: LinhaRelatorio, dimensoes: tuple[Dimensao, ...]) -> str:
    partes = []
    for dimensao in dimensoes:
        valor = linha.chaves[dimensao]
        if dimensao is Dimensao.DIA:
            partes.append(_dia_curto(date.fromisoformat(valor.id)))
        else:
            partes.append(valor.rotulo)
    return " · ".join(partes) or "Total"


def tabela_generica(
    foto: FotografiaPeriodo,
    resultado: ResultadoRelatorio,
    *,
    relatorio: str,
    titulo: str,
    avisos: list[str],
    extras: dict[str, tuple[str, dict[int, ValorSimples]]] | None = None,
) -> tuple[str, BlocoRelatorio]:
    """Dimensões x métricas em uma tabela. `extras`: coluna -> (rótulo, valor por linha)."""
    extras = extras or {}
    colunas: list[tuple[str, str, Formato]] = []
    for dimensao in resultado.dimensoes:
        if dimensao is Dimensao.DIA:
            colunas.extend(COLUNAS_DATA)
        else:
            chave, rotulo = ROTULOS_DIMENSOES[dimensao]
            colunas.append((chave, rotulo, "texto"))
    colunas.extend((m.value, *ROTULOS_METRICAS[m]) for m in resultado.metricas)
    if resultado.meta is not None:
        colunas.append(("abaixo_da_meta", "Abaixo da meta", "texto"))
    colunas.extend((chave, rotulo, "texto") for chave, (rotulo, _) in extras.items())

    linhas: list[dict[str, ValorSimples]] = []
    for indice, linha in enumerate(resultado.linhas):
        registro: dict[str, ValorSimples] = {}
        for dimensao in resultado.dimensoes:
            registro.update(_valor_tabela(linha, dimensao))
        registro.update({m.value: linha.valores[m] for m in resultado.metricas})
        if resultado.meta is not None:
            registro["abaixo_da_meta"] = linha.abaixo_da_meta
        for chave, (_, valores) in extras.items():
            registro[chave] = valores.get(indice)
        linhas.append(registro)

    total: dict[str, ValorSimples] = {m.value: resultado.total[m] for m in resultado.metricas}
    if resultado.meta is not None:
        total["abaixo_da_meta"] = resultado.total_abaixo_da_meta
    colunas_total: list[tuple[str, str, Formato]] = [
        (m.value, *ROTULOS_METRICAS[m]) for m in resultado.metricas
    ]
    if resultado.meta is not None:
        colunas_total.append(("abaixo_da_meta", "Abaixo da meta", "texto"))

    resumo = [
        ItemResumo(
            rotulo=f"{ROTULOS_METRICAS[m][0]} (total)",
            valor=resultado.total[m],
            formato=ROTULOS_METRICAS[m][1],
            exibicao=_exibicao_metrica(m, resultado.total[m], resultado.total_razao_ocupacao),
        )
        for m in resultado.metricas
    ]
    bloco = _bloco(
        "tabela_generica",
        titulo,
        foto,
        [*avisos, *resultado.avisos],
        resultado.meta,
        resumo,
        DadosTabelaGenerica(
            relatorio=relatorio,
            dimensoes=[d.value for d in resultado.dimensoes],
            metricas=[m.value for m in resultado.metricas],
            colunas=[ColunaTabela(chave=c, rotulo=r, formato=f) for c, r, f in colunas],
            linhas=linhas,
            total=total,
            linhas_omitidas=resultado.linhas_omitidas,
        ),
        [tabela("Resultado", colunas, linhas), tabela("Total", colunas_total, [total])],
    )

    def valores_texto(valores: dict[Metrica, ValorMetrica], razao: tuple[int, int] | None) -> str:
        return ", ".join(
            f"{ROTULOS_METRICAS[m][0].lower()} {_exibicao_metrica(m, valores[m], razao)}"
            for m in resultado.metricas
        )

    texto = _cabecalho_texto(titulo, foto)
    total_texto = valores_texto(resultado.total, resultado.total_razao_ocupacao)
    if resultado.meta is not None:
        total_texto += (
            " (abaixo da meta de 80%)" if resultado.total_abaixo_da_meta else " (meta atingida)"
        )
    texto.append(f"Total: {total_texto}.")
    if resultado.dimensoes:
        for indice, linha in enumerate(resultado.linhas[:MAX_LINHAS_NO_TEXTO]):
            sufixo = "".join(
                f"; {rotulo.lower()}: {valores[indice]}"
                for rotulo, valores in extras.values()
                if indice in valores
            )
            meta = " (abaixo da meta)" if linha.abaixo_da_meta else ""
            texto.append(
                f"- {_rotulo_linha(linha, resultado.dimensoes)}: "
                f"{valores_texto(linha.valores, linha.razao_ocupacao)}{meta}{sufixo}"
            )
        texto.extend(
            _mais(
                len(resultado.linhas) - MAX_LINHAS_NO_TEXTO + resultado.linhas_omitidas
                if len(resultado.linhas) > MAX_LINHAS_NO_TEXTO
                else resultado.linhas_omitidas
            )
        )
    texto.extend(resultado.avisos)
    return "\n".join(texto), bloco


def capacidade(
    foto: FotografiaPeriodo, relatorio: RelatorioCapacidade, avisos: list[str], recorte: str = ""
) -> tuple[str, BlocoRelatorio]:
    """Capacidade por especialidade, com a coluna de situação."""
    situacoes = {
        indice: _ROTULOS_SITUACAO[relatorio.situacoes[linha.chaves[Dimensao.ESPECIALIDADE].id]]
        for indice, linha in enumerate(relatorio.resultado.linhas)
    }
    return tabela_generica(
        foto,
        relatorio.resultado,
        relatorio="capacidade_especialidade",
        titulo=f"Capacidade por especialidade{recorte} {rotulo_periodo(foto.inicio, foto.fim)}",
        avisos=avisos,
        extras={"situacao": ("Situação", dict(situacoes))},
    )


# --- convênio --------------------------------------------------------------


def convenio(
    foto: FotografiaPeriodo, relatorio: RelatorioConvenios, avisos: list[str], recorte: str = ""
) -> tuple[str, BlocoRelatorio]:
    """Atendimentos por convênio e os que aguardam autorização."""
    titulo = f"Convênios e autorizações{recorte} {rotulo_periodo(foto.inicio, foto.fim)}"
    por_convenio = [
        ItemConvenio(
            convenio=linha.chaves[Dimensao.CONVENIO].id,
            rotulo=linha.chaves[Dimensao.CONVENIO].rotulo,
            sessoes=int(linha.valores[Metrica.SESSOES]),
            pacientes_distintos=int(linha.valores[Metrica.PACIENTES_DISTINTOS]),
            slots_ocupados=int(linha.valores[Metrica.SLOTS_OCUPADOS]),
            aguardando_autorizacao=int(linha.valores[Metrica.AGUARDANDO_AUTORIZACAO]),
        )
        for linha in relatorio.por_convenio.linhas
    ]
    aguardando = [
        AguardandoProfissionalDia(
            data=date.fromisoformat(linha.chaves[Dimensao.DIA].id),
            dia_semana=DIAS_DA_SEMANA[date.fromisoformat(linha.chaves[Dimensao.DIA].id).weekday()],
            profissional=_profissional(foto, linha.chaves[Dimensao.PROFISSIONAL].id),
            sessoes=int(linha.valores[Metrica.AGUARDANDO_AUTORIZACAO]),
        )
        for linha in relatorio.aguardando.linhas
    ]
    total = relatorio.por_convenio.total
    sessoes = int(total[Metrica.SESSOES])
    pendentes = int(total[Metrica.AGUARDANDO_AUTORIZACAO])
    bloco = _bloco(
        "convenio",
        titulo,
        foto,
        avisos,
        None,
        [
            ItemResumo(rotulo="Sessões", valor=sessoes, formato="inteiro", exibicao=str(sessoes)),
            ItemResumo(
                rotulo="Sessões aguardando autorização",
                valor=pendentes,
                formato="inteiro",
                exibicao=str(pendentes),
            ),
        ],
        DadosConvenio(por_convenio=por_convenio, aguardando=aguardando),
        [
            tabela(
                "Por convênio",
                [
                    ("rotulo", "Convênio", "texto"),
                    ("sessoes", "Sessões", "inteiro"),
                    ("pacientes_distintos", "Pacientes distintos", "inteiro"),
                    ("slots_ocupados", "Slots ocupados", "inteiro"),
                    ("aguardando_autorizacao", "Aguardando autorização", "inteiro"),
                ],
                [item.model_dump(mode="json", exclude={"convenio"}) for item in por_convenio],
            ),
            tabela(
                "Aguardando autorização",
                [
                    *COLUNAS_DATA,
                    ("profissional", "Profissional", "texto"),
                    ("sessoes", "Sessões aguardando", "inteiro"),
                ],
                [
                    {
                        "data": item.data.isoformat(),
                        "dia_semana": item.dia_semana,
                        "profissional": item.profissional.nome,
                        "sessoes": item.sessoes,
                    }
                    for item in aguardando
                ],
            ),
        ],
    )
    texto = _cabecalho_texto(titulo, foto)
    texto.append(f"Total: {_n(sessoes, 'sessão', 'sessões')}; {pendentes} aguardando autorização.")
    texto.extend(
        f"- {item.rotulo}: {_n(item.sessoes, 'sessão', 'sessões')}, "
        f"{_n(item.pacientes_distintos, 'paciente', 'pacientes')}, "
        f"{item.aguardando_autorizacao} aguardando"
        for item in por_convenio
    )
    if aguardando:
        texto.append(
            "Aguardando autorização: "
            + "; ".join(
                f"{_dia_curto(item.data)} {item.profissional.nome} ({item.sessoes})"
                for item in aguardando[:MAX_LINHAS_NO_TEXTO]
            )
            + "."
        )
    return "\n".join(texto), bloco


# --- qualidade da agenda ---------------------------------------------------


def _qualidade(foto: FotografiaPeriodo, item: QualidadeProfissionalDia) -> dict[str, object]:
    return {
        "profissional": _profissional(foto, item.profissional_id),
        **item.model_dump(
            include={
                "slots_escalados",
                "slots_ocupados",
                "vagas_livres",
                "buracos",
                "slots_em_buracos",
                "slots_isolados",
                "faixas_livres",
                "maior_faixa_livre",
            }
        ),
    }


_COLUNAS_QUALIDADE: list[tuple[str, str, Formato]] = [
    ("profissional", "Profissional", "texto"),
    ("especialidade", "Especialidade", "texto"),
    ("slots_escalados", "Slots escalados", "inteiro"),
    ("slots_ocupados", "Slots ocupados", "inteiro"),
    ("vagas_livres", "Vagas livres", "inteiro"),
    ("buracos", "Buracos", "inteiro"),
    ("slots_em_buracos", "Slots em buracos", "inteiro"),
    ("slots_isolados", "Slots isolados", "inteiro"),
    ("faixas_livres", "Faixas livres", "inteiro"),
    ("maior_faixa_livre", "Maior faixa livre (slots)", "inteiro"),
]


def _linha_qualidade(item: QualidadeProfissional) -> dict[str, ValorSimples]:
    return {
        **item.model_dump(mode="json", exclude={"profissional"}),
        "profissional": item.profissional.nome,
        "especialidade": item.profissional.especialidade or "—",
    }


def qualidade_agenda(
    foto: FotografiaPeriodo, relatorio: RelatorioQualidade, avisos: list[str], recorte: str = ""
) -> tuple[str, BlocoRelatorio]:
    """Buracos e fragmentação da agenda por profissional."""
    titulo = f"Buracos e fragmentação da agenda{recorte} {rotulo_periodo(foto.inicio, foto.fim)}"
    por_profissional = [
        QualidadeProfissional.model_validate(_qualidade(foto, item))
        for item in relatorio.por_profissional
    ]
    por_dia = [
        QualidadeProfissionalDiaRelatorio.model_validate(
            {
                **_qualidade(foto, item),
                "data": item.data,
                "dia_semana": DIAS_DA_SEMANA[item.data.weekday()],
            }
        )
        for item in relatorio.por_dia
    ]
    buracos = sum(item.buracos for item in por_profissional)
    presos = sum(item.slots_em_buracos for item in por_profissional)
    isolados = sum(item.slots_isolados for item in por_profissional)
    bloco = _bloco(
        "qualidade_agenda",
        titulo,
        foto,
        avisos,
        None,
        [
            ItemResumo(rotulo="Buracos", valor=buracos, formato="inteiro", exibicao=str(buracos)),
            ItemResumo(
                rotulo="Slots presos em buracos",
                valor=presos,
                formato="inteiro",
                exibicao=str(presos),
            ),
            ItemResumo(
                rotulo="Slots isolados (não cabe 1 h)",
                valor=isolados,
                formato="inteiro",
                exibicao=str(isolados),
            ),
        ],
        DadosQualidadeAgenda(por_profissional=por_profissional, por_dia=por_dia),
        [
            tabela(
                "Por profissional",
                _COLUNAS_QUALIDADE,
                [_linha_qualidade(item) for item in por_profissional],
            ),
            tabela(
                "Por profissional e dia",
                [*COLUNAS_DATA, *_COLUNAS_QUALIDADE],
                [
                    {
                        **_linha_qualidade(item),
                        "data": item.data.isoformat(),
                        "dia_semana": item.dia_semana,
                    }
                    for item in por_dia
                ],
            ),
        ],
    )
    texto = _cabecalho_texto(titulo, foto)
    texto.append(
        f"Total: {_n(buracos, 'buraco', 'buracos')} entre atendimentos "
        f"({_n(presos, 'slot preso', 'slots presos')}), "
        f"{_n(isolados, 'slot isolado', 'slots isolados')}."
    )
    texto.append("Quem tem mais slots presos em buracos:")
    texto.extend(
        f"- {item.profissional.nome}: {_n(item.buracos, 'buraco', 'buracos')}, "
        f"{_n(item.slots_em_buracos, 'slot preso', 'slots presos')}, "
        f"{_n(item.slots_isolados, 'isolado', 'isolados')}, maior faixa livre "
        f"{_n(item.maior_faixa_livre, 'slot', 'slots')}"
        for item in por_profissional[:5]
    )
    texto.extend(_mais(len(por_profissional) - 5))
    return "\n".join(texto), bloco


# --- agenda de um paciente -------------------------------------------------


def agenda_paciente(
    foto: FotografiaPeriodo, agenda: AgendaPaciente, avisos: list[str]
) -> tuple[str, BlocoRelatorio]:
    """Agenda do paciente pedido: o único bloco que cita o nome de um paciente."""
    titulo = f"Agenda de {agenda.nome} {rotulo_periodo(foto.inicio, foto.fim)}"
    dias = [
        DiaAgendaPacienteRelatorio(
            data=dia.data,
            dia_semana=DIAS_DA_SEMANA[dia.data.weekday()],
            sessoes=[
                SessaoAgendaPaciente(
                    inicio=_hora(s.inicio),
                    fim=_hora(s.fim),
                    profissional_nome=s.profissional_nome,
                    especialidade=rotulo_enum(s.especialidade.value),
                    sala_nome=s.sala_nome,
                    aguardando_autorizacao=s.aguardando_autorizacao,
                )
                for s in dia.sessoes
            ],
            intervalos=[
                IntervaloAgendaPaciente(
                    inicio=_hora(i.inicio),
                    fim=_hora(i.fim),
                    minutos=i.minutos,
                    minutos_de_pausa=i.minutos_de_pausa,
                )
                for i in dia.intervalos
            ],
        )
        for dia in agenda.dias
    ]
    convenio_rotulo = rotulo_convenio(agenda.convenio) if agenda.convenio else None
    espera = agenda.minutos_em_intervalos
    bloco = _bloco(
        "agenda_paciente",
        titulo,
        foto,
        avisos,
        None,
        [
            ItemResumo(
                rotulo="Sessões",
                valor=agenda.total_sessoes,
                formato="inteiro",
                exibicao=str(agenda.total_sessoes),
            ),
            ItemResumo(
                rotulo="Espera entre terapias (sem o almoço)",
                valor=espera,
                formato="inteiro",
                exibicao=f"{espera} min",
            ),
        ],
        DadosAgendaPaciente(
            paciente=PacienteRelatorio(nome=agenda.nome, convenio=convenio_rotulo),
            dias=dias,
            total_sessoes=agenda.total_sessoes,
            minutos_em_intervalos=espera,
        ),
        [
            tabela(
                "Sessões",
                [
                    *COLUNAS_DATA,
                    ("inicio", "Início", "texto"),
                    ("fim", "Fim", "texto"),
                    ("profissional", "Profissional", "texto"),
                    ("especialidade", "Especialidade", "texto"),
                    ("sala", "Sala", "texto"),
                    ("aguardando_autorizacao", "Aguardando autorização", "texto"),
                ],
                [
                    {
                        "data": dia.data.isoformat(),
                        "dia_semana": dia.dia_semana,
                        "inicio": s.inicio,
                        "fim": s.fim,
                        "profissional": s.profissional_nome,
                        "especialidade": s.especialidade,
                        "sala": s.sala_nome,
                        "aguardando_autorizacao": s.aguardando_autorizacao,
                    }
                    for dia in dias
                    for s in dia.sessoes
                ],
            ),
            tabela(
                "Intervalos",
                [
                    *COLUNAS_DATA,
                    ("inicio", "Início", "texto"),
                    ("fim", "Fim", "texto"),
                    ("minutos", "Minutos", "inteiro"),
                    ("minutos_de_pausa", "Minutos de pausa (almoço)", "inteiro"),
                ],
                [
                    {
                        "data": dia.data.isoformat(),
                        "dia_semana": dia.dia_semana,
                        **i.model_dump(mode="json"),
                    }
                    for dia in dias
                    for i in dia.intervalos
                ],
            ),
        ],
    )
    texto = _cabecalho_texto(titulo, foto)
    if convenio_rotulo:
        texto.append(f"Convênio: {convenio_rotulo}.")
    for dia in dias:
        sessoes = "; ".join(
            f"{s.inicio}-{s.fim} {s.profissional_nome} ({s.especialidade}, {s.sala_nome})"
            + (" — aguardando autorização" if s.aguardando_autorizacao else "")
            for s in dia.sessoes
        )
        texto.append(f"- {_dia_curto(dia.data)}: {sessoes}")
        texto.extend(
            f"  espera {i.inicio}-{i.fim}: {i.minutos} min"
            + (f" ({i.minutos_de_pausa} de almoço)" if i.minutos_de_pausa else "")
            for i in dia.intervalos
        )
    texto.append(f"Espera total entre terapias (sem o almoço): {espera} min.")
    return "\n".join(texto), bloco


# --- plano semanal -------------------------------------------------------

#: Identificador estável do relatório do plano em `tabela_generica`.
RELATORIO_PLANO_SEMANAL: Final[str] = "plano_semanal"

#: Valores da coluna `situacao` do plano.
SITUACAO_PLANO: Final[str] = "Plano"
SITUACAO_NAO_COUBE: Final[str] = "Não coube"
SITUACAO_ALTERNATIVA: Final[str] = "Alternativa"

COLUNAS_PLANO: Final[list[tuple[str, str, Formato]]] = [
    ("dia", "Dia", "texto"),
    ("situacao", "Situação", "texto"),
    ("especialidade", "Especialidade", "texto"),
    ("horario", "Horário", "texto"),
    ("profissional", "Profissional", "texto"),
    ("sala", "Sala", "texto"),
    ("posto", "Posto", "inteiro"),
]

#: Aviso fixo do plano: a recorrência é a grade, não o calendário.
AVISO_PLANO_GRADE: Final[str] = (
    "A recorrência semanal usa a grade semanal da planilha (uma fotografia da semana, "
    "sem datas): feriados e exceções do calendário não são vistos."
)


def _horario(sessao: SessaoPlano) -> str:
    return f"{sessao.inicio:%H:%M} às {sessao.fim:%H:%M}"


def _linha_sessao(
    foto: FotografiaPeriodo, sessao: SessaoPlano, situacao: str
) -> dict[str, ValorSimples]:
    return {
        "dia": DIAS_DA_SEMANA[sessao.data.weekday()],
        "situacao": situacao,
        "especialidade": rotulo_enum(sessao.especialidade.value),
        "horario": _horario(sessao),
        "profissional": foto.nome_profissional(sessao.profissional_id),
        "sala": foto.nome_sala(sessao.sala_id),
        "posto": sessao.indice_posto + 1,
    }


def _sessao_no_texto(foto: FotografiaPeriodo, sessao: SessaoPlano) -> str:
    return (
        f"{rotulo_enum(sessao.especialidade.value)} {_horario(sessao)} com "
        f"{foto.nome_profissional(sessao.profissional_id)} ({foto.nome_sala(sessao.sala_id)})"
    )


def _no_dia(dia: date) -> str:
    """`na quarta`, `no sábado`."""
    nome = DIAS_DA_SEMANA[dia.weekday()].split("-")[0]
    return f"{'no' if nome == 'sábado' else 'na'} {nome}"


def _seguidas_no_texto(dia: PlanoDia) -> str | None:
    """`3 seguidas das 10:00 às 11:30`; `None` sem duas sessões seguidas."""
    sequencia = maior_sequencia(dia.sessoes)
    if len(sequencia) < 2:
        return None
    return f"{len(sequencia)} seguidas das {sequencia[0].inicio:%H:%M} às {sequencia[-1].fim:%H:%M}"


def _alternativas_no_texto(dia: date, alternativas: list[SessaoPlano]) -> str:
    if not alternativas:
        return "sem alternativa nos dias pedidos"
    por_dia = Counter(a.data for a in alternativas)
    total = f"{len(alternativas)} alternativa(s) fora da janela na tabela"
    if set(por_dia) == {dia}:
        return total
    return total + ": " + enumerar([f"{n} {_no_dia(d)}" for d, n in sorted(por_dia.items())])


def plano_semanal(
    foto: FotografiaPeriodo, plano: PlanoSemanal, titulo: str, avisos: list[str]
) -> tuple[str, BlocoRelatorio]:
    """O plano em `tabela_generica`: por dia, as sessões do plano e, para cada
    especialidade que não coube, a linha "Não coube" seguida das alternativas que
    acontecem nesse dia (as do próprio dia e as que completam outro dia pedido).
    Com `seguidas`, quem ficou fora da sequência só tem a linha "Não coube".
    """
    pedidas = len(plano.pedido.itens)
    seguidas = plano.pedido.seguidas
    linhas: list[dict[str, ValorSimples]] = []
    texto = _cabecalho_texto(titulo, foto)
    em_outros_dias: list[str] = []
    for dia in plano.dias:
        nome_do_dia = DIAS_DA_SEMANA[dia.data.weekday()]
        linhas.extend(_linha_sessao(foto, s, SITUACAO_PLANO) for s in dia.sessoes)
        sequencia = _seguidas_no_texto(dia)
        contagem = f"{len(dia.sessoes)} de {pedidas} especialidades"
        if not seguidas:
            partes = [contagem]
        elif sequencia is not None:
            partes = [f"{sequencia} ({contagem})"]
        else:
            partes = [f"nenhuma sequência de 2 ou mais seguidas ({contagem})"]
        if dia.sessoes:
            partes.append("; ".join(_sessao_no_texto(foto, s) for s in dia.sessoes))
        if not seguidas and sequencia is not None:
            partes.append(sequencia)
        for nao_coube in dia.nao_couberam:
            especialidade = rotulo_enum(nao_coube.especialidade.value)
            linhas.append(
                {
                    "dia": nome_do_dia,
                    "situacao": SITUACAO_NAO_COUBE,
                    "especialidade": especialidade,
                    "horario": "—",
                    "profissional": "—",
                    "sala": "—",
                    "posto": None,
                }
            )
            # As alternativas que acontecem neste dia, de qualquer dia pedido: as
            # do próprio dia primeiro. Cada uma aparece uma vez na tabela.
            linhas.extend(
                _linha_sessao(foto, a, SITUACAO_ALTERNATIVA)
                for outro in sorted(plano.dias, key=lambda d: d.data != dia.data)
                for n in outro.nao_couberam
                if n.especialidade is nao_coube.especialidade
                for a in n.alternativas
                if a.data == dia.data
            )
            if seguidas:
                partes.append(f"fora da sequência: {especialidade}")
                continue
            partes.append(
                f"não coube {especialidade} "
                f"({_alternativas_no_texto(dia.data, nao_coube.alternativas)})"
            )
            if nao_coube.ha_em_outros_dias and especialidade not in em_outros_dias:
                em_outros_dias.append(especialidade)
        texto.append(f"- {nome_do_dia.capitalize()}: {'. '.join(partes)}.")
    if em_outros_dias:
        texto.append(
            f"Faltaram alternativas nos dias pedidos para {enumerar(em_outros_dias)}, mas há "
            "horários em outros dias da semana (fora da tabela): pergunte se a coordenadora "
            "quer vê-los."
        )
    if seguidas and any(dia.nao_couberam for dia in plano.dias):
        texto.append(
            "Para caber mais especialidades em sequência, ofereça ampliar a janela de horário."
        )
    texto.extend(avisos)

    nao_couberam = sum(len(dia.nao_couberam) for dia in plano.dias)
    resumo = [
        ItemResumo(
            rotulo=DIAS_DA_SEMANA[dia.data.weekday()].capitalize(),
            valor=f"{len(dia.sessoes)} de {pedidas}",
            formato="texto",
            exibicao=f"{len(dia.sessoes)} de {pedidas} especialidades",
        )
        for dia in plano.dias
    ]
    resumo.append(
        ItemResumo(
            rotulo="Não couberam",
            valor=nao_couberam,
            formato="inteiro",
            exibicao=str(nao_couberam),
        )
    )
    bloco = _bloco(
        "tabela_generica",
        titulo,
        foto,
        avisos,
        None,
        resumo,
        DadosTabelaGenerica(
            relatorio=RELATORIO_PLANO_SEMANAL,
            dimensoes=[],
            metricas=[],
            colunas=[ColunaTabela(chave=c, rotulo=r, formato=f) for c, r, f in COLUNAS_PLANO],
            linhas=linhas,
            total={},
            linhas_omitidas=0,
        ),
        [tabela("Plano", COLUNAS_PLANO, linhas)],
        # A recorrência é a grade semanal, sem datas: o bloco não inventa período.
        com_periodo=False,
    )
    return "\n".join(texto), bloco


# --- pacientes por plano ----------------------------------------------------

#: Identificador estável do relatório de pacientes por plano em `tabela_generica`.
RELATORIO_PACIENTES_POR_PLANO: Final[str] = "pacientes_por_plano"

#: Uma linha por paciente, agrupadas por plano (na ordem de `PLANO_POR_COR`).
#: Os totais ficam só na tabela de resumo, nunca no meio da lista.
COLUNAS_PACIENTES_POR_PLANO: Final[list[tuple[str, str, Formato]]] = [
    ("plano", "Plano", "texto"),
    ("paciente", "Paciente", "texto"),
    ("atendimentos_semana", "Atendimentos na semana", "inteiro"),
]
COLUNAS_RESUMO_POR_PLANO: Final[list[tuple[str, str, Formato]]] = [
    ("plano", "Plano", "texto"),
    ("pacientes", "Pacientes", "inteiro"),
    ("atendimentos_semana", "Atendimentos na semana", "inteiro"),
]

TITULO_PACIENTES_POR_PLANO: Final[str] = "Pacientes por plano de saúde (grade semanal vigente)"

#: Limites do relatório, sempre presentes nos avisos.
AVISOS_PACIENTES_POR_PLANO: Final[tuple[str, ...]] = (
    "Relatório gerencial da grade semanal vigente: cobre só pacientes com sessão na grade "
    "desta semana, não todos os pacientes da clínica.",
    "O paciente é identificado só pelo nome: homônimos aparecem como um só e grafias "
    "diferentes do mesmo nome aparecem como dois.",
    "O plano vem da cor do texto do nome; nomes em preto não entram na lista nem nas contagens.",
    "Atendimentos contados apenas com cor de plano: o mesmo paciente em preto em outro horário "
    "não entra na contagem.",
    "Faturamento, guias, valores e cadastro ficam fora do RealocAI.",
)


def avisos_pacientes_por_plano(resultado: PacientesPorPlano) -> list[str]:
    """Os avisos fixos e os numéricos (sem nomes), só quando a contagem passa de zero."""
    avisos = list(AVISOS_PACIENTES_POR_PLANO)
    if resultado.nomes_nao_reconhecidos:
        n = resultado.nomes_nao_reconhecidos
        avisos.append(
            f"{n} nome com cor não reconhecida foi ignorado."
            if n == 1
            else f"{n} nomes com cor não reconhecida foram ignorados."
        )
    if resultado.pacientes_em_mais_de_um_plano:
        n = resultado.pacientes_em_mais_de_um_plano
        avisos.append(
            f"{n} paciente aparece com cores de mais de um plano na semana e está em cada um deles."
            if n == 1
            else f"{n} pacientes aparecem com cores de mais de um plano na semana e estão em "
            "cada um deles."
        )
    return avisos


def pacientes_por_plano(
    resultado: PacientesPorPlano, avisos: list[str], dias_nao_lidos: list[date]
) -> tuple[str, BlocoRelatorio]:
    """Texto para o modelo (SÓ contagens) e bloco com a lista de nomes (só para a tela).

    Os nomes de pacientes vão apenas nas linhas do bloco: nem o texto, nem o
    título, nem o resumo os citam, porque o texto é o que o modelo lê e o
    resumo é o que o resumo determinístico repete.
    """
    linhas: list[dict[str, ValorSimples]] = [
        {"plano": grupo.plano, "paciente": p.nome, "atendimentos_semana": p.atendimentos}
        for grupo in resultado.grupos
        for p in grupo.pacientes
    ]
    totais: list[dict[str, ValorSimples]] = [
        {
            "plano": grupo.plano,
            "pacientes": len(grupo.pacientes),
            "atendimentos_semana": grupo.atendimentos,
        }
        for grupo in resultado.grupos
    ]

    texto = [
        f"{TITULO_PACIENTES_POR_PLANO}: {_n(resultado.pacientes, 'paciente', 'pacientes')} "
        "com plano."
    ]
    if dias_nao_lidos:
        nao_lidos = ", ".join(_dia_curto(dia) for dia in dias_nao_lidos)
        texto.append(f"Não foi possível ler: {nao_lidos}; os números são parciais.")
    texto.extend(
        f"- {grupo.plano}: {_n(len(grupo.pacientes), 'paciente', 'pacientes')}, "
        f"{_n(grupo.atendimentos, 'atendimento', 'atendimentos')} na semana."
        for grupo in resultado.grupos
    )
    texto.extend(avisos)
    texto.append(
        "A lista com os nomes está só no relatório da tela: não escreva nomes de pacientes "
        "na resposta."
    )

    resumo = [
        ItemResumo(
            rotulo=grupo.plano,
            valor=len(grupo.pacientes),
            formato="inteiro",
            exibicao=(
                f"{_n(len(grupo.pacientes), 'paciente', 'pacientes')}, "
                f"{_n(grupo.atendimentos, 'atendimento', 'atendimentos')}"
            ),
        )
        for grupo in resultado.grupos
    ]
    resumo.append(
        ItemResumo(
            rotulo="Pacientes com plano",
            valor=resultado.pacientes,
            formato="inteiro",
            exibicao=str(resultado.pacientes),
        )
    )
    bloco = BlocoRelatorio.model_validate(
        {
            "tipo": "tabela_generica",
            "titulo": TITULO_PACIENTES_POR_PLANO,
            # A fonte é a grade semanal, sem datas: o bloco não inventa período.
            "periodo": None,
            "meta": None,
            "parcial": bool(dias_nao_lidos),
            "dias_nao_lidos": sorted(set(dias_nao_lidos)),
            "avisos": avisos,
            "resumo": resumo,
            "dados": DadosTabelaGenerica(
                relatorio=RELATORIO_PACIENTES_POR_PLANO,
                dimensoes=[],
                metricas=[],
                colunas=[
                    ColunaTabela(chave=c, rotulo=r, formato=f)
                    for c, r, f in COLUNAS_PACIENTES_POR_PLANO
                ],
                linhas=linhas,
                total={},
                linhas_omitidas=0,
            ),
            "tabelas": [
                tabela("Pacientes", COLUNAS_PACIENTES_POR_PLANO, linhas),
                tabela("Resumo por plano", COLUNAS_RESUMO_POR_PLANO, totais),
            ],
        }
    )
    return "\n".join(texto), bloco


def nomes_de_pacientes_no_bloco(bloco: BlocoRelatorio) -> list[str]:
    """Nomes de paciente que um bloco de `pacientes_por_plano` leva (para a proteção do texto)."""
    dados = bloco.dados
    if not isinstance(dados, DadosTabelaGenerica) or dados.relatorio != (
        RELATORIO_PACIENTES_POR_PLANO
    ):
        return []
    return [str(linha["paciente"]) for linha in dados.linhas if linha.get("paciente")]
