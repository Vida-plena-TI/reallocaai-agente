"""Parser da planilha da agenda: dados crus da aba de um dia -> entidades do domínio.

Este módulo é **puro**: ele não conhece gspread, credencial nem rede. Recebe o
que a API do Sheets já devolveu (textos das células, intervalos mesclados e cor
da fonte) e devolve `DadosAgendaDoDia`. É o que torna o parsing testável sem
tocar na planilha de produção — `GoogleSheetsDataSource` é só a casca que busca
os dados crus e chama `parse_worksheet_data`.

A planilha é preenchida à mão e cada aba é um dia da semana com dois blocos
(manhã e tarde). Um bloco começa numa linha de cabeçalho de salas, seguida da
linha dos profissionais e das linhas de horário. Nada disso tem posição fixa:
o offset das linhas varia por aba, salas ocupam mais de uma coluna via merge e
os nomes vêm com quebra de linha, especialidade solta ou entre parênteses.
Todas as tolerâncias abaixo existem por causa de um caso concreto encontrado na
planilha real — quando o dado é irrecuperável, o parser loga e segue adiante,
porque perder uma coluna é melhor do que perder o dia inteiro.
"""

import logging
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.domain import (
    COLUNAS_IGNORADAS_PROFISSIONAL,
    COR_AGUARDANDO_AUTORIZACAO,
    CORES_CONVENIO,
    MAPA_ALIAS_PROFISSIONAL,
    MAPA_ESPECIALIDADE_FALLBACK,
    MAPA_SALA_FALLBACK,
    PADROES_ESPECIALIDADE,
    PROFISSIONAIS_IGNORAR,
    Atendimento,
    Convenio,
    EntradaGrade,
    Especialidade,
    Paciente,
    Profissional,
    Sala,
    Slot,
    SlotInvalidoError,
    normalizar_id,
)

logger = logging.getLogger(__name__)

#: Aba de cada dia da semana (`date.weekday()`). Domingo não está aqui: a
#: clínica não abre e a planilha não tem aba correspondente.
NOMES_DE_ABA_POR_DIA_DA_SEMANA: Final[dict[int, str]] = {
    0: "Segunda",
    1: "Terça",
    2: "Quarta",
    3: "Quinta",
    4: "Sexta",
    5: "Sábado",
}

#: A coluna A é o eixo de horários do bloco, nunca uma sala.
_COLUNA_DOS_HORARIOS: Final[int] = 0

#: Cabeçalho de sala: `Sala 1`, `Sala 09`, `Sala 10   janela`, `Sala 11\nJanela`.
_PADRAO_SALA: Final[re.Pattern[str]] = re.compile(r"^sala\s*0*(\d+)\b")

#: Linha de dados: a que traz o horário do slot na coluna A.
_PADRAO_HORA: Final[re.Pattern[str]] = re.compile(r"^(\d{1,2}):(\d{2})$")

#: Intervalo mesclado em notação A1 (`H2:I2`).
_PADRAO_INTERVALO_A1: Final[re.Pattern[str]] = re.compile(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$")

#: Slot que a clínica marcou como indisponível.
_MARCADOR_FECHADO: Final[str] = "fechado"

#: Pontuação que sobra nas bordas depois de separar nome e especialidade
#: (uma célula "TO - Rossana" vira "Rossana", "(Calebe)" vira "Calebe").
_BORDAS_DESCARTAVEIS: Final[str] = " ()[].,;:-_|"


class DadosAgendaDoDia(BaseModel):
    """Tudo o que a aba de um dia produz, já em entidades do domínio."""

    model_config = ConfigDict(frozen=True)

    salas: list[Sala] = Field(default_factory=list)
    profissionais: list[Profissional] = Field(default_factory=list)
    pacientes: list[Paciente] = Field(default_factory=list)
    grade: list[EntradaGrade] = Field(default_factory=list)
    atendimentos: list[Atendimento] = Field(default_factory=list)


def nome_da_aba(dia: date) -> str | None:
    """Nome esperado da aba do dia, ou `None` quando a clínica não abre."""
    return NOMES_DE_ABA_POR_DIA_DA_SEMANA.get(dia.weekday())


def encontrar_titulo_da_aba(dia: date, titulos: Iterable[str]) -> str | None:
    """Acha o título real da aba do dia entre os títulos da planilha.

    A comparação ignora caixa, acento e espaço sobrando porque a aba do sábado
    se chama `' Sábado '` na planilha real.
    """
    esperado = nome_da_aba(dia)
    if esperado is None:
        return None
    alvo = _normalizar(esperado)
    return next((titulo for titulo in titulos if _normalizar(titulo) == alvo), None)


def parse_worksheet_data(
    dia: date,
    raw_values: Sequence[Sequence[str]],
    merges: Sequence[str] = (),
    font_colors: Mapping[str, str] | None = None,
) -> DadosAgendaDoDia:
    """Traduz a aba de um dia em entidades do domínio.

    `raw_values` são os textos das células (linha a linha, como o
    `get_all_values` devolve), `merges` os intervalos mesclados em notação A1
    (`H2:I2`) e `font_colors` a cor da fonte por célula (`{"B6": "#0000FF"}`) —
    é ela que codifica o convênio. Célula sem cor informada fica com o convênio
    desconhecido, sem virar aviso.

    Nada aqui levanta exceção por dado torto: domingo devolve o resultado vazio
    e cada inconsistência de preenchimento vira log.
    """
    titulo_da_aba = nome_da_aba(dia)
    if titulo_da_aba is None:
        logger.debug("%s é domingo: a clínica não abre e não há aba para ler.", dia.isoformat())
        return DadosAgendaDoDia()

    cores = font_colors if font_colors is not None else {}
    blocos = _detectar_blocos(raw_values)
    if not blocos:
        logger.warning(
            "Nenhuma linha de cabeçalho de sala encontrada na aba de %s: nada a extrair.",
            dia.isoformat(),
        )
        return DadosAgendaDoDia()

    intervalos = _intervalos_mesclados(merges)
    salas_por_bloco = [_salas_por_coluna(raw_values, bloco, intervalos) for bloco in blocos]
    leituras_por_bloco = [_analisar_profissionais(raw_values, bloco) for bloco in blocos]
    analises_por_bloco = [analises for analises, _ in leituras_por_bloco]
    ignoradas_por_bloco = [ignoradas for _, ignoradas in leituras_por_bloco]

    # A mesma coluna costuma ser a mesma sala nos dois blocos do dia. Isso é o
    # que salva os cabeçalhos que faltam num deles (a tarde de sexta não repete
    # "Sala 6", mas mantém a profissional embaixo).
    salas_do_dia = _salas_por_coluna_no_dia(salas_por_bloco)
    especialidades_do_dia = _especialidades_por_profissional(analises_por_bloco)

    coletor = _Coletor()
    # Uma coluna ignorada não sustenta a sala sozinha: a sala só entra se outra
    # coluna (mesmo vazia) do cabeçalho a cobrir.
    for salas_do_bloco, ignoradas in zip(salas_por_bloco, ignoradas_por_bloco, strict=True):
        for coluna, posto_do_cabecalho in salas_do_bloco.items():
            if coluna not in ignoradas:
                coletor.registrar_sala(posto_do_cabecalho.sala)

    # Horários vistos valem para a aba inteira: é assim que a linha 11:30
    # duplicada da quinta-feira é reduzida à primeira ocorrência.
    linhas_por_horario: dict[time, int] = {}
    # Colunas perdidas por falta de sala: o aviso individual se dilui no meio
    # dos outros, então elas viram um resumo no fim da aba.
    colunas_sem_sala: list[int] = []
    for bloco, salas_do_bloco, analises in zip(
        blocos, salas_por_bloco, analises_por_bloco, strict=True
    ):
        linhas_de_slot = _linhas_de_slot(raw_values, bloco, dia, linhas_por_horario)
        for coluna, analise in sorted(analises.items()):
            posto = salas_do_bloco.get(coluna) or salas_do_dia.get(coluna)
            if posto is None:
                sala = _sala_de_fallback(_normalizar(titulo_da_aba), analise.id)
                if sala is None:
                    colunas_sem_sala.append(coluna)
                    # Só o endereço: o texto da célula (nome do profissional)
                    # não vai para o log.
                    logger.warning(
                        "Coluna %s tem profissional no bloco da linha %d, mas nenhuma sala "
                        "no cabeçalho: coluna ignorada.",
                        rotulo_da_coluna(coluna),
                        bloco.linha_das_salas + 1,
                    )
                    continue
                logger.info(
                    "Coluna %s não tem sala no cabeçalho, mas o profissional está no mapa de "
                    "fallback da aba %r: coluna atribuída à %s.",
                    rotulo_da_coluna(coluna),
                    titulo_da_aba,
                    sala.nome,
                )
                coletor.registrar_sala(sala)
                posto = _Posto(sala=sala, indice=0)

            especialidade = (
                analise.especialidade
                or especialidades_do_dia.get(analise.id)
                or MAPA_ESPECIALIDADE_FALLBACK.get(analise.id)
            )
            if especialidade is None:
                logger.warning(
                    "Não dá para saber a especialidade do profissional da coluna %s, linha %d, "
                    "e o nome não aparece com especialidade em nenhum outro bloco do dia: "
                    "coluna ignorada.",
                    rotulo_da_coluna(coluna),
                    bloco.linha_dos_profissionais + 1,
                )
                continue

            profissional = Profissional(
                id=analise.id, nome=analise.nome, especialidade=especialidade
            )
            coletor.registrar_profissional(profissional)
            # Normalmente já registrada pelo cabeçalho; garante a sala de uma
            # coluna ativa cujo cabeçalho só existe num bloco em que ela foi ignorada.
            coletor.registrar_sala(posto.sala)
            _extrair_coluna(
                dia=dia,
                raw_values=raw_values,
                cores=cores,
                coluna=coluna,
                posto=posto,
                profissional=profissional,
                linhas_de_slot=linhas_de_slot,
                coletor=coletor,
            )

    _logar_colunas_sem_sala(titulo_da_aba, colunas_sem_sala)
    return coletor.consolidar()


def _logar_colunas_sem_sala(titulo_da_aba: str, colunas: Sequence[int]) -> None:
    """Resume, no fim da aba, tudo o que se perdeu por sala não identificada.

    A mesma coluna costuma cair nos dois blocos do dia, então a contagem é de
    ocorrências e a lista é de colunas distintas: `2 coluna(s) ... coluna Q`.
    """
    if not colunas:
        return
    rotulos = ", ".join(f"coluna {rotulo_da_coluna(coluna)}" for coluna in sorted(set(colunas)))
    logger.warning(
        "%d coluna(s) ignorada(s) por sala não identificada na aba %r: %s",
        len(colunas),
        titulo_da_aba,
        rotulos,
    )


def _sala_de_fallback(aba_normalizada: str, profissional_id: str) -> Sala | None:
    """Sala fixada à mão para uma coluna cujo cabeçalho de merge veio vazio.

    É um remendo para erro de preenchimento já confirmado com a clínica, por
    isso só é consultado depois de o cabeçalho falhar nos dois blocos do dia.
    A capacidade é 1: sem merge no cabeçalho não há como saber outra coisa.
    """
    nome = MAPA_SALA_FALLBACK.get((aba_normalizada, profissional_id))
    if nome is None:
        return None
    encontrado = _PADRAO_SALA.match(_normalizar(nome))
    if encontrado is None:
        logger.warning(
            "Sala %r do mapa de fallback não está no formato 'Sala N': fallback ignorado.", nome
        )
        return None
    numero = int(encontrado.group(1))
    return Sala(id=f"sala-{numero}", nome=f"Sala {numero}", capacidade_simultanea=1)


@dataclass(frozen=True, slots=True)
class _Bloco:
    """Um turno dentro da aba: cabeçalho de salas, profissionais e horários."""

    linha_das_salas: int
    linha_dos_profissionais: int
    primeira_linha_de_dados: int
    #: Exclusivo, como em `range`.
    fim_das_linhas_de_dados: int


@dataclass(frozen=True, slots=True)
class _Posto:
    """Onde uma coluna de agendamento cai: a sala e o posto dentro dela.

    `indice` é a posição da coluna dentro do merge do cabeçalho, da esquerda
    para a direita a partir de 0 — decidido só pela geometria da planilha,
    nunca por quem está escalado na coluna.
    """

    sala: Sala
    indice: int


@dataclass(frozen=True, slots=True)
class _AnaliseProfissional:
    """O que se conseguiu ler da célula de um profissional.

    `id` já vem canônico (grafia alternativa resolvida) e `nome` é o nome de
    exibição correspondente. `especialidade` é opcional porque muita célula traz
    só o nome — nesse caso ela é buscada em outra aparição do mesmo profissional
    no mesmo dia.
    """

    id: str
    nome: str
    especialidade: Especialidade | None


@dataclass(frozen=True, slots=True)
class _ColunaIgnorada:
    """Coluna cujo profissional está em `COLUNAS_IGNORADAS_PROFISSIONAL`."""


@dataclass(slots=True)
class _Sessao:
    """Slots contíguos da mesma coluna com o mesmo conjunto de pacientes.

    É o acumulador que evita quebrar uma sessão de uma hora em dois
    atendimentos de 30 minutos.
    """

    pacientes: list[str]
    slots: list[Slot]
    primeira_celula: str
    aguardando_autorizacao: bool

    def aceita(self, pacientes: Sequence[str], slot: Slot) -> bool:
        """Diz se o slot é a continuação direta desta sessão."""
        return sorted(self.pacientes) == sorted(pacientes) and self.slots[-1].e_contiguo_a(slot)


@dataclass(slots=True)
class _Coletor:
    """Junta o resultado do dia deduplicando salas, profissionais e pacientes."""

    salas: dict[str, Sala] = field(default_factory=dict)
    profissionais: dict[str, Profissional] = field(default_factory=dict)
    pacientes: dict[str, Paciente] = field(default_factory=dict)
    grade: list[EntradaGrade] = field(default_factory=list)
    atendimentos: list[Atendimento] = field(default_factory=list)

    def registrar_sala(self, sala: Sala) -> None:
        """Guarda a sala mantendo a maior capacidade vista entre os blocos."""
        atual = self.salas.get(sala.id)
        if atual is None or sala.capacidade_simultanea > atual.capacidade_simultanea:
            self.salas[sala.id] = sala

    def registrar_profissional(self, profissional: Profissional) -> None:
        """Guarda o profissional; a primeira grafia do nome é a que vale."""
        self.profissionais.setdefault(profissional.id, profissional)

    def registrar_paciente(self, identificador: str, nome: str, convenio: Convenio | None) -> None:
        """Guarda o paciente sem nunca apagar um convênio já conhecido.

        O mesmo paciente aparece em várias células do dia e só algumas delas
        trazem a cor do convênio: quem já foi identificado continua identificado.
        """
        atual = self.pacientes.get(identificador)
        if atual is None:
            self.pacientes[identificador] = Paciente(id=identificador, nome=nome, convenio=convenio)
        elif atual.convenio is None and convenio is not None:
            self.pacientes[identificador] = atual.model_copy(update={"convenio": convenio})

    def consolidar(self) -> DadosAgendaDoDia:
        """Fecha o resultado do dia em ordem estável."""
        return DadosAgendaDoDia(
            salas=sorted(self.salas.values(), key=_numero_da_sala),
            profissionais=sorted(self.profissionais.values(), key=lambda item: item.id),
            pacientes=sorted(self.pacientes.values(), key=lambda item: item.id),
            grade=sorted(
                self.grade, key=lambda item: (item.slot, item.sala_id, item.profissional_id)
            ),
            atendimentos=sorted(self.atendimentos, key=lambda item: (item.slots[0], item.id)),
        )


def _extrair_coluna(
    *,
    dia: date,
    raw_values: Sequence[Sequence[str]],
    cores: Mapping[str, str],
    coluna: int,
    posto: _Posto,
    profissional: Profissional,
    linhas_de_slot: Sequence[tuple[int, Slot]],
    coletor: _Coletor,
) -> None:
    """Percorre uma coluna de sala, alimentando grade e atendimentos."""
    sessao: _Sessao | None = None

    def encerrar(sessao: _Sessao | None) -> None:
        if sessao is not None:
            coletor.atendimentos.append(_montar_atendimento(dia, sessao, posto, profissional))

    for linha, slot in linhas_de_slot:
        endereco = _endereco_a1(linha, coluna)
        texto = _achatar(_celula(raw_values, linha, coluna))

        # `FECHADO` é o único caso em que a janela não existe: célula vazia é
        # profissional disponível e continua fazendo parte da grade.
        if _normalizar(texto) == _MARCADOR_FECHADO:
            encerrar(sessao)
            sessao = None
            continue

        coletor.grade.append(
            EntradaGrade(
                sala_id=posto.sala.id,
                profissional_id=profissional.id,
                especialidade=profissional.especialidade,
                slot=slot,
                indice_posto=posto.indice,
            )
        )

        nomes = _nomes_de_paciente(texto, endereco)
        if not nomes:
            encerrar(sessao)
            sessao = None
            continue

        convenio, aguardando = _resolver_convenio(cores.get(endereco), endereco)
        identificadores = [normalizar_id(nome) for nome in nomes]
        for identificador, nome in zip(identificadores, nomes, strict=True):
            coletor.registrar_paciente(identificador, nome, convenio)

        if sessao is not None and sessao.aceita(identificadores, slot):
            sessao.slots.append(slot)
            sessao.aguardando_autorizacao = sessao.aguardando_autorizacao or aguardando
            continue

        encerrar(sessao)
        sessao = _Sessao(
            pacientes=identificadores,
            slots=[slot],
            primeira_celula=endereco,
            aguardando_autorizacao=aguardando,
        )

    encerrar(sessao)


def _montar_atendimento(
    dia: date, sessao: _Sessao, posto: _Posto, profissional: Profissional
) -> Atendimento:
    """Monta o atendimento de uma sessão fechada.

    O id vem da célula em que a sessão começa (`2026-09-10-B6`): é estável entre
    leituras e leva direto ao lugar da planilha onde o dado nasceu.
    """
    return Atendimento(
        id=f"{dia.isoformat()}-{sessao.primeira_celula}",
        paciente_ids=sessao.pacientes,
        profissional_id=profissional.id,
        sala_id=posto.sala.id,
        especialidade=profissional.especialidade,
        slots=sessao.slots,
        aguardando_autorizacao=sessao.aguardando_autorizacao,
        indice_posto=posto.indice,
    )


def _detectar_blocos(raw_values: Sequence[Sequence[str]]) -> list[_Bloco]:
    """Acha os blocos da aba pela linha de cabeçalho de salas.

    A linha é encontrada pelo conteúdo, nunca por índice fixo: o offset muda de
    aba para aba (segunda usa 2-3 e 16-17, terça usa 1-2 e 15-16).
    """
    cabecalhos = [indice for indice, linha in enumerate(raw_values) if _e_cabecalho_de_salas(linha)]
    blocos: list[_Bloco] = []
    for posicao, linha_das_salas in enumerate(cabecalhos):
        seguinte = posicao + 1
        fim = cabecalhos[seguinte] if seguinte < len(cabecalhos) else len(raw_values)
        primeira_linha_de_dados = linha_das_salas + 2
        if primeira_linha_de_dados >= fim:
            logger.warning(
                "Cabeçalho de salas na linha %d não é seguido de linhas de horário: "
                "bloco ignorado.",
                linha_das_salas + 1,
            )
            continue
        blocos.append(
            _Bloco(
                linha_das_salas=linha_das_salas,
                linha_dos_profissionais=linha_das_salas + 1,
                primeira_linha_de_dados=primeira_linha_de_dados,
                fim_das_linhas_de_dados=fim,
            )
        )
    return blocos


def _e_cabecalho_de_salas(linha: Sequence[str]) -> bool:
    """Diz se a linha é o cabeçalho de salas de um bloco."""
    return any(_PADRAO_SALA.match(_normalizar(celula)) for celula in linha)


def _salas_por_coluna(
    raw_values: Sequence[Sequence[str]],
    bloco: _Bloco,
    intervalos: Sequence[tuple[int, int, int, int]],
) -> dict[int, _Posto]:
    """Mapeia cada coluna do bloco para a sala do cabeçalho que a cobre e o
    posto que ela representa dentro dessa sala.

    A capacidade simultânea sai do merge do cabeçalho: uma sala que ocupa três
    colunas atende três pacientes ao mesmo tempo, nos postos 0, 1 e 2.
    """
    salas: dict[int, _Posto] = {}
    linha = bloco.linha_das_salas
    largura = len(raw_values[linha]) if linha < len(raw_values) else 0
    for coluna in range(largura):
        encontrado = _PADRAO_SALA.match(_normalizar(_celula(raw_values, linha, coluna)))
        if encontrado is None:
            continue
        numero = int(encontrado.group(1))
        primeira, limite = _colunas_do_merge(intervalos, linha, coluna)
        sala = Sala(
            id=f"sala-{numero}",
            nome=f"Sala {numero}",
            capacidade_simultanea=limite - primeira,
        )
        for coberta in range(primeira, limite):
            salas[coberta] = _Posto(sala=sala, indice=coberta - primeira)
    return salas


def _salas_por_coluna_no_dia(
    salas_por_bloco: Sequence[Mapping[int, _Posto]],
) -> dict[int, _Posto]:
    """Une o mapa coluna -> posto de todos os blocos; o primeiro bloco decide."""
    unificado: dict[int, _Posto] = {}
    for salas_do_bloco in salas_por_bloco:
        for coluna, posto in salas_do_bloco.items():
            unificado.setdefault(coluna, posto)
    return unificado


def _analisar_profissionais(
    raw_values: Sequence[Sequence[str]], bloco: _Bloco
) -> tuple[dict[int, _AnaliseProfissional], set[int]]:
    """Lê a linha de profissionais do bloco, coluna a coluna.

    Devolve as colunas com profissional identificado e, à parte, as colunas
    descartadas de propósito (`COLUNAS_IGNORADAS_PROFISSIONAL`) — estas nunca
    entram nas análises, então também não emprestam especialidade a ninguém.
    """
    analises: dict[int, _AnaliseProfissional] = {}
    ignoradas: set[int] = set()
    linha = bloco.linha_dos_profissionais
    largura = len(raw_values[linha]) if linha < len(raw_values) else 0
    for coluna in range(_COLUNA_DOS_HORARIOS + 1, largura):
        texto = _achatar(_celula(raw_values, linha, coluna))
        if not texto:
            continue
        analise = _analisar_celula_de_profissional(texto)
        if isinstance(analise, _ColunaIgnorada):
            ignoradas.add(coluna)
            # Info, não warning: o descarte é pedido da clínica e se repete a
            # cada leitura da planilha.
            logger.info(
                "Coluna %s está em COLUNAS_IGNORADAS_PROFISSIONAL: coluna ignorada no bloco.",
                rotulo_da_coluna(coluna),
            )
            continue
        if analise is None:
            logger.warning(
                "Célula %s não resolve para um único profissional: coluna ignorada no bloco.",
                _endereco_a1(linha, coluna),
            )
            continue
        analises[coluna] = analise
    return analises, ignoradas


def _analisar_celula_de_profissional(
    texto: str,
) -> _AnaliseProfissional | _ColunaIgnorada | None:
    """Separa nome e especialidade de uma célula de profissional.

    A especialidade sai primeiro, do texto inteiro, porque ela costuma estar
    grudada no nome que será descartado (uma coluna "Aline/Raíssa", com o "TO"
    na segunda linha da célula). Só depois o que sobra é dividido por barra e
    filtrado pelos estagiários. Devolve None quando sobra mais de um nome
    (dois profissionais dividindo a coluna) ou nenhum, e `_ColunaIgnorada`
    quando o nome que sobra está em `COLUNAS_IGNORADAS_PROFISSIONAL` — conferido
    antes do alias, pela grafia da própria célula.
    """
    especialidade, restante = _separar_especialidade(texto)

    nomes: list[str] = []
    for parte in restante.split("/"):
        nome = _achatar(parte).strip(_BORDAS_DESCARTAVEIS).strip()
        if not any(caractere.isalpha() for caractere in nome) or _e_ignorado(nome):
            continue
        nomes.append(nome)

    if len(nomes) != 1:
        return None
    if normalizar_id(nomes[0]) in COLUNAS_IGNORADAS_PROFISSIONAL:
        return _ColunaIgnorada()
    identificador, nome = _resolver_profissional(nomes[0])
    return _AnaliseProfissional(id=identificador, nome=nome, especialidade=especialidade)


def _separar_especialidade(texto: str) -> tuple[Especialidade | None, str]:
    """Encontra a especialidade escrita na célula e a remove do texto.

    A busca roda sobre uma cópia sem acento e em minúsculas com os mesmos
    índices do original, então o trecho reconhecido pode ser recortado do texto
    de verdade sem estragar a acentuação do nome.
    """
    chave = _sem_acento(texto).lower()
    for padrao, especialidade in PADROES_ESPECIALIDADE:
        encontrado = padrao.search(chave)
        if encontrado is None:
            continue
        inicio, fim = encontrado.span()
        return especialidade, f"{texto[:inicio]} {texto[fim:]}"
    return None, texto


def _resolver_profissional(nome: str) -> tuple[str, str]:
    """Id canônico e nome de exibição de um profissional lido da planilha.

    A mesma pessoa aparece escrita de duas formas em dias diferentes. Quando a
    grafia é uma das alternativas conhecidas (`MAPA_ALIAS_PROFISSIONAL`), o id
    vira o canônico e o nome de exibição é reconstruído a partir dele — assim as
    duas grafias colapsam na mesma pessoa, não importa qual apareceu primeiro.

    A célula que já traz o id canônico (ex.: "laryssa", em minúsculo) também tem
    o nome reconstruído: senão o nome de exibição dependeria de qual grafia
    apareceu primeiro na semana. Quem não é destino de alias mantém o nome como
    foi digitado.
    """
    identificador = normalizar_id(nome)
    canonico = MAPA_ALIAS_PROFISSIONAL.get(identificador, identificador)
    if canonico not in MAPA_ALIAS_PROFISSIONAL.values():
        return identificador, nome
    # `_nome_do_id` parte do id, que não tem acento: o nome reconstruído também
    # não terá. Isso só afeta destinos de alias, e nenhum deles tem acento hoje.
    return canonico, _nome_do_id(canonico)


def _nome_do_id(identificador: str) -> str:
    """Nome de exibição a partir de um id (`talita-aylla` -> `Talita Aylla`)."""
    return " ".join(parte.capitalize() for parte in identificador.split("-"))


def _e_ignorado(nome: str) -> bool:
    """Diz se o nome é de estagiário e não deve virar profissional alocável."""
    normalizado = _normalizar(nome)
    primeiro_nome, _, _ = normalizado.partition(" ")
    return normalizado in PROFISSIONAIS_IGNORAR or primeiro_nome in PROFISSIONAIS_IGNORAR


def _especialidades_por_profissional(
    analises_por_bloco: Sequence[Mapping[int, _AnaliseProfissional]],
) -> dict[str, Especialidade]:
    """Especialidade de cada profissional que apareceu identificado no dia.

    Serve de rede para as células que trazem só o nome: a mesma pessoa que
    aparece sem especialidade à tarde costuma estar completa no bloco da manhã.
    """
    especialidades: dict[str, Especialidade] = {}
    for analises in analises_por_bloco:
        for analise in analises.values():
            if analise.especialidade is not None:
                especialidades.setdefault(analise.id, analise.especialidade)
    return especialidades


def _linhas_de_slot(
    raw_values: Sequence[Sequence[str]],
    bloco: _Bloco,
    dia: date,
    linhas_por_horario: dict[time, int],
) -> list[tuple[int, Slot]]:
    """Linhas de horário do bloco, já convertidas em Slot.

    `linhas_por_horario` é compartilhado pela aba inteira: horário repetido
    (a linha 11:30 duplicada da quinta) vale pela primeira ocorrência.
    """
    linhas: list[tuple[int, Slot]] = []
    for linha in range(bloco.primeira_linha_de_dados, bloco.fim_das_linhas_de_dados):
        hora = _parse_hora(_celula(raw_values, linha, _COLUNA_DOS_HORARIOS))
        if hora is None:
            continue
        anterior = linhas_por_horario.get(hora)
        if anterior is not None:
            logger.warning(
                "Horário %s aparece de novo na linha %d (já estava na linha %d): "
                "a primeira ocorrência é a que vale.",
                hora.isoformat("minutes"),
                linha + 1,
                anterior + 1,
            )
            continue
        try:
            slot = Slot(data=dia, hora_inicio=hora)
        except SlotInvalidoError as erro:
            # A faixa entre os blocos traz o horário do almoço na coluna A: não
            # é uma linha de atendimento, é a divisória.
            logger.debug("Linha %d ignorada: %s", linha + 1, erro)
            continue
        linhas_por_horario[hora] = linha
        linhas.append((linha, slot))
    return linhas


def _nomes_de_paciente(texto: str, endereco: str) -> list[str]:
    """Nomes dos pacientes de uma célula, já separados por barra."""
    if not texto:
        return []
    if not any(caractere.isalpha() for caractere in texto):
        logger.warning("Célula %s não tem nome nenhum: tratada como vazia.", endereco)
        return []
    nomes: list[str] = []
    for parte in texto.split("/"):
        nome = parte.strip(_BORDAS_DESCARTAVEIS).strip()
        if any(caractere.isalpha() for caractere in nome):
            nomes.append(nome)
    return nomes


def _resolver_convenio(cor: str | None, endereco: str) -> tuple[Convenio | None, bool]:
    """Traduz a cor da fonte em (convênio, aguardando autorização).

    Sem cor informada não há aviso: só não dá para saber o convênio.
    """
    if cor is None:
        return None, False
    normalizada = cor.strip().upper()
    if normalizada == COR_AGUARDANDO_AUTORIZACAO:
        return None, True
    convenio = CORES_CONVENIO.get(normalizada)
    if convenio is None:
        logger.warning(
            "Cor de fonte %s (célula %s) não está no mapa de convênios: convênio desconhecido.",
            normalizada,
            endereco,
        )
        return None, False
    return convenio, False


def _intervalos_mesclados(merges: Sequence[str]) -> list[tuple[int, int, int, int]]:
    """Converte os intervalos A1 em (linha inicial, fim, coluna inicial, fim).

    Os índices são 0-based e o fim é exclusivo, como em `range`.
    """
    intervalos: list[tuple[int, int, int, int]] = []
    for merge in merges:
        encontrado = _PADRAO_INTERVALO_A1.match(merge.strip().upper())
        if encontrado is None:
            logger.warning("Intervalo mesclado %r não está em notação A1: ignorado.", merge)
            continue
        coluna_inicial, linha_inicial, coluna_final, linha_final = encontrado.groups()
        intervalos.append(
            (
                int(linha_inicial) - 1,
                int(linha_final),
                _indice_da_coluna(coluna_inicial),
                _indice_da_coluna(coluna_final) + 1,
            )
        )
    return intervalos


def _colunas_do_merge(
    intervalos: Sequence[tuple[int, int, int, int]], linha: int, coluna: int
) -> tuple[int, int]:
    """Colunas cobertas pelo merge daquela célula, ou só ela mesma."""
    for inicio_linha, fim_linha, inicio_coluna, fim_coluna in intervalos:
        if inicio_linha <= linha < fim_linha and inicio_coluna <= coluna < fim_coluna:
            return inicio_coluna, fim_coluna
    return coluna, coluna + 1


def _celula(raw_values: Sequence[Sequence[str]], linha: int, coluna: int) -> str:
    """Texto de uma célula, tolerando linha curta ou inexistente."""
    if not 0 <= linha < len(raw_values):
        return ""
    valores = raw_values[linha]
    if not 0 <= coluna < len(valores):
        return ""
    return str(valores[coluna])


def _parse_hora(texto: str) -> time | None:
    """Lê o horário da coluna A (`08:30`), ou None se a linha não for de dados."""
    encontrado = _PADRAO_HORA.match(texto.strip())
    if encontrado is None:
        return None
    horas, minutos = int(encontrado.group(1)), int(encontrado.group(2))
    if horas > 23 or minutos > 59:
        return None
    return time(horas, minutos)


def _numero_da_sala(sala: Sala) -> int:
    """Ordena as salas por número (`sala-9` antes de `sala-10`)."""
    _, _, numero = sala.id.rpartition("-")
    return int(numero) if numero.isdigit() else 0


def _endereco_a1(linha: int, coluna: int) -> str:
    """Endereço A1 de uma célula a partir dos índices 0-based (`0, 1` -> `B1`)."""
    return f"{rotulo_da_coluna(coluna)}{linha + 1}"


def rotulo_da_coluna(indice: int) -> str:
    """Letra da coluna em notação A1 (0 -> A, 26 -> AA).

    É pública porque quem alimenta o parser precisa montar os mesmos endereços
    A1 que ele espera receber em `merges` e `font_colors`.
    """
    rotulo = ""
    atual = indice + 1
    while atual > 0:
        atual, resto = divmod(atual - 1, 26)
        rotulo = chr(ord("A") + resto) + rotulo
    return rotulo


def _indice_da_coluna(rotulo: str) -> int:
    """Índice 0-based da coluna a partir da letra em notação A1 (`AA` -> 26)."""
    indice = 0
    for caractere in rotulo:
        indice = indice * 26 + (ord(caractere) - ord("A") + 1)
    return indice - 1


def _achatar(texto: str) -> str:
    """Junta uma célula multilinha numa linha só, sem espaço sobrando."""
    return " ".join(texto.split())


def _sem_acento(texto: str) -> str:
    """Remove os acentos preservando a posição de cada caractere.

    O índice de cada letra continua o mesmo do texto original, o que permite
    recortar do original um trecho encontrado na versão sem acento.
    """
    return "".join(unicodedata.normalize("NFKD", caractere)[0] for caractere in texto)


def _normalizar(texto: str) -> str:
    """Forma canônica para comparar textos da planilha: sem acento nem caixa."""
    return _achatar(_sem_acento(texto)).casefold()
