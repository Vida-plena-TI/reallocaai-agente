"""Contrato versionado de dados para renderização e exportação pelo app visual.

Só projeções explícitas dos resultados da engine: nunca texto analisado,
entidades de pacientes, arquivos ou desenho. IDs de profissionais são opacos.
"""

from datetime import date
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.formatacao import formatar_razao
from app.domain.constants import META_OCUPACAO_POR_SALA
from app.engine.carga_profissionais import CargaProfissionais, CargaProfissional
from app.engine.ocupacao import OcupacaoAgregada, RelatorioOcupacaoDoDia
from app.engine.ocupacao_profissional import OcupacaoSemanalProfissional

Formato = Literal["texto", "inteiro", "decimal", "percentual", "data"]
TipoRelatorio = Literal["ocupacao_profissional", "pacientes_por_profissional", "ocupacao_agregada"]
ValorSimples = str | int | float | bool | None
#: Aviso de ocupação acima de 100%, repetido no texto das tools.
AVISO_ACIMA_DE_CEM = (
    "Atenção: há ocupação acima de 100% (mais slots ocupados que escalados); "
    "possível inconsistência na planilha."
)
DIAS_DA_SEMANA = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


class _ModeloRelatorio(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ColunaTabela(_ModeloRelatorio):
    chave: str
    rotulo: str
    formato: Formato


class TabelaExportacao(_ModeloRelatorio):
    nome: str
    colunas: list[ColunaTabela]
    linhas: list[dict[str, ValorSimples]]

    @model_validator(mode="after")
    def _validar_colunas(self) -> TabelaExportacao:
        chaves = {coluna.chave for coluna in self.colunas}
        if len(chaves) != len(self.colunas) or any(set(linha) != chaves for linha in self.linhas):
            raise ValueError(
                "Cada linha deve conter exatamente as chaves das colunas, sem repetição."
            )
        return self


class ItemResumo(_ModeloRelatorio):
    rotulo: str
    valor: ValorSimples
    formato: Formato
    exibicao: str


class PeriodoRelatorio(_ModeloRelatorio):
    inicio: date
    fim: date


class DataRelatorio(_ModeloRelatorio):
    data: date
    dia_semana: str


class ProfissionalRelatorio(_ModeloRelatorio):
    id: str
    nome: str
    especialidade: str | None


class MetricasOcupacao(_ModeloRelatorio):
    escalados: int
    ocupados: int
    livres: int
    # Pode passar de 1 (inconsistência na grade): o valor da engine não é limitado.
    percentual: float = Field(ge=0)
    abaixo_da_meta: bool
    slots_para_meta: int


class TurnoOcupacao(_ModeloRelatorio):
    escalados: int
    ocupados: int


class SalaPostoRelatorio(TurnoOcupacao):
    sala_id: str
    sala_nome: str
    # Número de exibição, contado a partir de 1, como no texto das tools.
    posto: int = Field(ge=1)


class DiaOcupacao(DataRelatorio, MetricasOcupacao):
    manha: TurnoOcupacao
    tarde: TurnoOcupacao
    por_sala_posto: list[SalaPostoRelatorio]


class DadosOcupacaoProfissional(_ModeloRelatorio):
    tipo: Literal["ocupacao_profissional"] = "ocupacao_profissional"
    profissional: ProfissionalRelatorio
    semana: MetricasOcupacao
    dias: list[DiaOcupacao]
    dias_sem_agenda: list[DataRelatorio]
    inconsistencia: bool


class DiaPacientes(DataRelatorio):
    pacientes: int
    sessoes: int
    slots_ocupados: int


class PacientesProfissional(ProfissionalRelatorio):
    dias: list[DiaPacientes]
    dias_sem_agenda: list[DataRelatorio]
    media_pacientes_por_dia: float | None
    pacientes_distintos_semana: int


class ClinicaDia(DataRelatorio):
    pacientes_distintos: int


class DadosPacientesProfissional(_ModeloRelatorio):
    tipo: Literal["pacientes_por_profissional"] = "pacientes_por_profissional"
    escopo: Literal["semana", "dia"]
    profissionais: list[PacientesProfissional]
    # Clínica inteira, inclusive quando a lista de profissionais está filtrada.
    clinica_por_dia: list[ClinicaDia]


class ItemOcupacaoAgregada(_ModeloRelatorio):
    """Mesmo conteúdo de OcupacaoItemResponse, sem depender de app.api."""

    rotulo: str
    slots_escalados: int
    slots_ocupados: int
    percentual: float = Field(ge=0)
    abaixo_da_meta: bool


class DadosOcupacaoAgregada(DataRelatorio):
    tipo: Literal["ocupacao_agregada"] = "ocupacao_agregada"
    por_especialidade: list[ItemOcupacaoAgregada]
    por_sala: list[ItemOcupacaoAgregada]


DadosRelatorio = Annotated[
    DadosOcupacaoProfissional | DadosPacientesProfissional | DadosOcupacaoAgregada,
    Field(discriminator="tipo"),
]


class BlocoRelatorio(_ModeloRelatorio):
    versao: int = Field(default=1, ge=1, le=1, description="Versão do contrato do bloco: 1.")
    tipo: TipoRelatorio
    titulo: str
    periodo: PeriodoRelatorio
    meta: float | None
    parcial: bool
    # Datas ISO ordenadas dos dias cuja leitura falhou; vazia quando não é parcial.
    dias_nao_lidos: list[date]
    avisos: list[str]
    resumo: list[ItemResumo]
    dados: DadosRelatorio
    tabelas: list[TabelaExportacao]

    @model_validator(mode="after")
    def _validar_tipo(self) -> BlocoRelatorio:
        if self.tipo != self.dados.tipo:
            raise ValueError("O tipo do bloco deve corresponder ao tipo dos dados.")
        if self.parcial != bool(self.dias_nao_lidos):
            raise ValueError("dias_nao_lidos deve ser vazio exatamente quando parcial é False.")
        if self.dias_nao_lidos != sorted(set(self.dias_nao_lidos)):
            raise ValueError("dias_nao_lidos deve estar ordenado e sem repetição.")
        if (self.meta is None) != (self.tipo == "pacientes_por_profissional"):
            raise ValueError("Só pacientes_por_profissional não tem meta.")
        return self


def _data(dia: date) -> DataRelatorio:
    return DataRelatorio(data=dia, dia_semana=DIAS_DA_SEMANA[dia.weekday()])


def _profissional(
    resultado: OcupacaoSemanalProfissional | CargaProfissional,
) -> ProfissionalRelatorio:
    return ProfissionalRelatorio(
        id=sha256(resultado.profissional_id.encode()).hexdigest(),
        nome=resultado.nome,
        especialidade=resultado.especialidade.value if resultado.especialidade else None,
    )


def _metricas(resultado: OcupacaoSemanalProfissional) -> MetricasOcupacao:
    return MetricasOcupacao(
        escalados=resultado.slots_escalados,
        ocupados=resultado.slots_ocupados,
        livres=resultado.slots_livres,
        percentual=resultado.percentual,
        abaixo_da_meta=resultado.abaixo_da_meta,
        slots_para_meta=resultado.slots_para_meta,
    )


def ocupacao_profissional_acima_de_cem(resultado: OcupacaoSemanalProfissional) -> bool:
    """Se a semana, algum dia ou alguma sala/posto passa de 100% (com slots escalados)."""
    contagens = [(m.slots_escalados, m.slots_ocupados) for m in [resultado, *resultado.dias]]
    contagens += [
        (s.slots_escalados, s.slots_ocupados) for d in resultado.dias for s in d.por_sala_posto
    ]
    return any(0 < escalados < ocupados for escalados, ocupados in contagens)


def ocupacao_agregada_acima_de_cem(resultado: RelatorioOcupacaoDoDia) -> bool:
    """Se alguma especialidade ou sala passa de 100% (com slots escalados)."""
    return any(
        0 < a.slots_escalados < a.slots_ocupados
        for a in [*resultado.por_especialidade().values(), *resultado.por_sala().values()]
    )


def _com_aviso(avisos: list[str], acima_de_cem: bool) -> list[str]:
    if acima_de_cem and AVISO_ACIMA_DE_CEM not in avisos:
        return [*avisos, AVISO_ACIMA_DE_CEM]
    return list(avisos)


def _tabela(
    nome: str,
    colunas: list[tuple[str, str, Formato]],
    linhas: list[dict[str, ValorSimples]],
) -> TabelaExportacao:
    return TabelaExportacao(
        nome=nome,
        colunas=[ColunaTabela(chave=c, rotulo=r, formato=f) for c, r, f in colunas],
        linhas=linhas,
    )


COLUNAS_DATA: list[tuple[str, str, Formato]] = [
    ("data", "Data", "data"),
    ("dia_semana", "Dia da semana", "texto"),
]
COLUNAS_METRICAS: list[tuple[str, str, Formato]] = [
    ("escalados", "Slots escalados", "inteiro"),
    ("ocupados", "Slots ocupados", "inteiro"),
    ("livres", "Slots livres", "inteiro"),
    ("percentual", "Ocupação", "percentual"),
    ("abaixo_da_meta", "Abaixo da meta", "texto"),
    ("slots_para_meta", "Slots para a meta", "inteiro"),
]
COLUNAS_PROFISSIONAL: list[tuple[str, str, Formato]] = [
    ("id", "Id do profissional", "texto"),
    ("nome", "Profissional", "texto"),
    ("especialidade", "Especialidade", "texto"),
]


def bloco_ocupacao_profissional(
    resultado: OcupacaoSemanalProfissional,
    avisos: list[str],
) -> BlocoRelatorio:
    dias = [
        DiaOcupacao(
            **_data(dia.data).model_dump(),
            escalados=dia.slots_escalados,
            ocupados=dia.slots_ocupados,
            livres=dia.slots_livres,
            percentual=dia.percentual,
            abaixo_da_meta=dia.abaixo_da_meta,
            slots_para_meta=dia.slots_para_meta,
            manha=TurnoOcupacao(escalados=dia.manha_escalados, ocupados=dia.manha_ocupados),
            tarde=TurnoOcupacao(escalados=dia.tarde_escalados, ocupados=dia.tarde_ocupados),
            por_sala_posto=[
                SalaPostoRelatorio(
                    sala_id=s.sala_id,
                    sala_nome=s.sala_nome,
                    posto=s.indice_posto + 1,
                    escalados=s.slots_escalados,
                    ocupados=s.slots_ocupados,
                )
                for s in dia.por_sala_posto
            ],
        )
        for dia in resultado.dias
    ]
    semana = _metricas(resultado)
    acima_de_cem = ocupacao_profissional_acima_de_cem(resultado)
    linhas: list[dict[str, ValorSimples]] = [
        {
            **dia.model_dump(mode="json", exclude={"manha", "tarde", "por_sala_posto"}),
            "manha_escalados": dia.manha.escalados,
            "manha_ocupados": dia.manha.ocupados,
            "tarde_escalados": dia.tarde.escalados,
            "tarde_ocupados": dia.tarde.ocupados,
        }
        for dia in dias
    ]
    return BlocoRelatorio(
        tipo="ocupacao_profissional",
        titulo=f"Ocupação de {resultado.nome}",
        periodo=PeriodoRelatorio(inicio=resultado.semana_inicio, fim=resultado.semana_fim),
        meta=META_OCUPACAO_POR_SALA,
        parcial=resultado.parcial,
        dias_nao_lidos=sorted(resultado.dias_com_falha),
        avisos=_com_aviso(avisos, acima_de_cem),
        resumo=[
            ItemResumo(
                rotulo="Ocupação da semana",
                valor=semana.percentual,
                formato="percentual",
                exibicao=formatar_razao(semana.ocupados, semana.escalados, percentual=True),
            ),
            ItemResumo(
                rotulo="Slots ocupados / escalados",
                valor=semana.ocupados,
                formato="inteiro",
                exibicao=f"{semana.ocupados} de {semana.escalados}",
            ),
            ItemResumo(
                rotulo="Slots para a meta",
                valor=semana.slots_para_meta,
                formato="inteiro",
                exibicao=str(semana.slots_para_meta),
            ),
        ],
        dados=DadosOcupacaoProfissional(
            profissional=_profissional(resultado),
            semana=semana,
            dias=dias,
            dias_sem_agenda=[_data(d) for d in resultado.dias_sem_agenda],
            inconsistencia=resultado.tem_inconsistencia or acima_de_cem,
        ),
        tabelas=[
            _tabela(
                "Por dia",
                COLUNAS_DATA
                + COLUNAS_METRICAS
                + [
                    ("manha_escalados", "Manhã: escalados", "inteiro"),
                    ("manha_ocupados", "Manhã: ocupados", "inteiro"),
                    ("tarde_escalados", "Tarde: escalados", "inteiro"),
                    ("tarde_ocupados", "Tarde: ocupados", "inteiro"),
                ],
                linhas,
            ),
            _tabela("Resumo da semana", COLUNAS_METRICAS, [semana.model_dump(mode="json")]),
        ],
    )


def bloco_pacientes_profissional(
    resultado: CargaProfissionais,
    cargas: list[CargaProfissional],
    escopo: Literal["semana", "dia"],
    avisos: list[str],
    *,
    individual: bool = False,
) -> BlocoRelatorio:
    profissionais = [
        PacientesProfissional(
            **_profissional(carga).model_dump(),
            dias=[
                DiaPacientes(**d.model_dump(), dia_semana=DIAS_DA_SEMANA[d.data.weekday()])
                for d in carga.dias
            ],
            dias_sem_agenda=[_data(d) for d in carga.dias_sem_agenda],
            media_pacientes_por_dia=carga.media_pacientes_por_dia,
            pacientes_distintos_semana=carga.pacientes_distintos_semana,
        )
        for carga in cargas
    ]
    clinica = [
        ClinicaDia(**_data(d.data).model_dump(), pacientes_distintos=d.pacientes_distintos_clinica)
        for d in resultado.por_dia
    ]
    resumo: list[ItemResumo] = []
    if individual and escopo == "dia":
        pacientes = cargas[0].dias[0].pacientes
        resumo = [
            ItemResumo(
                rotulo="Pacientes distintos no dia",
                valor=pacientes,
                formato="inteiro",
                exibicao=str(pacientes),
            )
        ]
    elif individual and escopo == "semana":
        carga = cargas[0]
        resumo = [
            ItemResumo(
                rotulo="Pacientes distintos na semana",
                valor=carga.pacientes_distintos_semana,
                formato="inteiro",
                exibicao=str(carga.pacientes_distintos_semana),
            ),
            ItemResumo(
                rotulo="Média de pacientes por dia",
                valor=carga.media_pacientes_por_dia,
                formato="decimal",
                exibicao=formatar_razao(sum(d.pacientes for d in carga.dias), len(carga.dias)),
            ),
        ]
    elif escopo == "dia" and not individual:
        resumo = [
            ItemResumo(
                rotulo="Pacientes distintos na clínica no dia",
                valor=clinica[0].pacientes_distintos,
                formato="inteiro",
                exibicao=str(clinica[0].pacientes_distintos),
            )
        ]
    return BlocoRelatorio(
        tipo="pacientes_por_profissional",
        titulo="Pacientes por profissional",
        periodo=PeriodoRelatorio(inicio=resultado.dias[0], fim=resultado.dias[-1]),
        meta=None,
        parcial=resultado.parcial,
        dias_nao_lidos=sorted(resultado.dias_com_falha),
        avisos=avisos,
        resumo=resumo,
        dados=DadosPacientesProfissional(
            escopo=escopo, profissionais=profissionais, clinica_por_dia=clinica
        ),
        tabelas=[
            _tabela(
                "Por profissional e dia",
                COLUNAS_PROFISSIONAL
                + COLUNAS_DATA
                + [
                    ("pacientes", "Pacientes", "inteiro"),
                    ("sessoes", "Sessões", "inteiro"),
                    ("slots_ocupados", "Slots ocupados", "inteiro"),
                ],
                [
                    {
                        **p.model_dump(mode="json", include={"id", "nome", "especialidade"}),
                        **d.model_dump(mode="json"),
                    }
                    for p in profissionais
                    for d in p.dias
                ],
            ),
            _tabela(
                "Resumo por profissional",
                [
                    *COLUNAS_PROFISSIONAL,
                    ("media_pacientes_por_dia", "Média de pacientes por dia com agenda", "decimal"),
                    ("pacientes_distintos_semana", "Pacientes distintos no período", "inteiro"),
                ],
                [
                    p.model_dump(mode="json", exclude={"dias", "dias_sem_agenda"})
                    for p in profissionais
                ],
            ),
            _tabela(
                "Clínica por dia",
                [
                    *COLUNAS_DATA,
                    ("pacientes_distintos", "Pacientes distintos", "inteiro"),
                ],
                [d.model_dump(mode="json") for d in clinica],
            ),
        ],
    )


def bloco_ocupacao_agregada(
    resultado: RelatorioOcupacaoDoDia,
    nomes_salas: dict[str, str],
) -> BlocoRelatorio:
    def item(rotulo: str, agregada: OcupacaoAgregada) -> ItemOcupacaoAgregada:
        return ItemOcupacaoAgregada(
            rotulo=rotulo,
            slots_escalados=agregada.slots_escalados,
            slots_ocupados=agregada.slots_ocupados,
            percentual=agregada.percentual,
            abaixo_da_meta=agregada.abaixo_da_meta,
        )

    especialidades = [
        item(e.value.replace("_", " ").title(), a)
        for e, a in sorted(resultado.por_especialidade().items(), key=lambda par: par[0].value)
    ]
    salas = [item(nomes_salas.get(s, s), a) for s, a in sorted(resultado.por_sala().items())]
    colunas: list[tuple[str, str, Formato]] = [
        ("rotulo", "Nome", "texto"),
        ("slots_escalados", "Slots escalados", "inteiro"),
        ("slots_ocupados", "Slots ocupados", "inteiro"),
        ("percentual", "Ocupação", "percentual"),
        ("abaixo_da_meta", "Abaixo da meta", "texto"),
    ]
    return BlocoRelatorio(
        tipo="ocupacao_agregada",
        titulo=f"Ocupação de {resultado.data.isoformat()}",
        periodo=PeriodoRelatorio(inicio=resultado.data, fim=resultado.data),
        meta=META_OCUPACAO_POR_SALA,
        parcial=False,
        dias_nao_lidos=[],
        avisos=_com_aviso([], ocupacao_agregada_acima_de_cem(resultado)),
        resumo=[],
        dados=DadosOcupacaoAgregada(
            **_data(resultado.data).model_dump(), por_especialidade=especialidades, por_sala=salas
        ),
        tabelas=[
            _tabela(nome, colunas, [i.model_dump(mode="json") for i in itens])
            for nome, itens in [("Por especialidade", especialidades), ("Por sala", salas)]
        ],
    )
