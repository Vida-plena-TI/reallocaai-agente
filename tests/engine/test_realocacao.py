"""Motor do plano de realocação (`app.engine.realocacao`).

As semanas são montadas à mão, sala a sala, no mesmo desenho da planilha
fictícia (`tests/casos_reais/planilha.py`): 10 posições de manhã (07:00 a
11:30), espaço, 10 à tarde (13:00 a 17:30). `.` = vaga, `#` = fora da grade,
letra = sessão de um paciente fictício (letra seguida = a mesma sessão).

Toda sugestão de todos os testes passa por `_conferir`, que refaz as regras
de fora do motor: cada deslocado mantém especialidade, profissional e
duração; as especialidades de cada paciente na semana não mudam; a contagem
de (paciente, especialidade, dia) não aumenta além de max(antes, 1); a grade
final não tem dois atendimentos no mesmo posto e horário nem paciente em dois
lugares.
"""

import time as relogio
from collections import Counter
from dataclasses import replace
from datetime import date, time

import pytest

from app.domain import Atendimento, EntradaGrade, Especialidade, Paciente, Profissional, Sala, Slot
from app.domain.normalizacao import normalizar_id
from app.engine.fotografia import FotografiaPeriodo, fotografar_periodo
from app.engine.realocacao import (
    ROTULO_PACIENTE_NOVO,
    Lugar,
    MotivoPergunta,
    MotivoSemSolucao,
    OpcaoRealocacao,
    PedidoRealocacao,
    Pergunta,
    PlanoRealocacao,
    Regras,
    ResultadoRealocacao,
    SemSolucao,
    Situacao,
    planejar_realocacao,
)
from tests.support.fake_schedule_data_source import FakeScheduleDataSource

SEGUNDA = date(2026, 9, 28)
TERCA = date(2026, 9, 29)
QUARTA = date(2026, 9, 30)
QUINTA = date(2026, 10, 1)
SEXTA = date(2026, 10, 2)
SABADO = date(2026, 10, 3)
SEMANA = (SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA, SABADO)

FONO = Especialidade.FONOAUDIOLOGIA
TO = Especialidade.TERAPIA_OCUPACIONAL
PSICO = Especialidade.PSICOLOGIA

PROFISSIONAIS = {
    "bia": Profissional(id="bia", nome="Bia", especialidade=FONO),
    "elo": Profissional(id="elo", nome="Elo", especialidade=FONO),
    "assizely": Profissional(id="assizely", nome="Assizely", especialidade=TO),
    "carol": Profissional(id="carol", nome="Carol", especialidade=PSICO),
}

_HORAS = [time(h, m) for h in range(7, 12) for m in (0, 30)] + [
    time(h, m) for h in range(13, 18) for m in (0, 30)
]

#: (profissional, sala, posto) -> desenho do dia.
Dia = dict[tuple[str, str, int], str]


def _nome(letra: str) -> str:
    return f"Paciente Ficticio {letra}"


def _id(letra: str) -> str:
    return normalizar_id(_nome(letra))


def montar(
    semana: dict[date, Dia],
    *,
    grupos: dict[str, tuple[str, ...]] | None = None,
    pretos: frozenset[str] = frozenset(),
) -> FotografiaPeriodo:
    """A fotografia da semana desenhada (grupos: letra -> letras dos pacientes)."""
    grupos = grupos or {}
    fonte = FakeScheduleDataSource()
    for dia, desenho in semana.items():
        grade: list[EntradaGrade] = []
        atendimentos: list[Atendimento] = []
        pacientes: dict[str, Paciente] = {}
        profissionais: dict[str, Profissional] = {}
        salas: dict[str, Sala] = {}
        for (profissional_id, sala_id, posto), texto in desenho.items():
            profissional = PROFISSIONAIS[profissional_id]
            profissionais[profissional_id] = profissional
            salas[sala_id] = Sala(id=sala_id, nome=sala_id.replace("sala-", "Sala "))
            marcas = texto.replace(" ", "")
            assert len(marcas) == len(_HORAS), texto
            sessoes: list[tuple[str, list[time]]] = []
            for hora, marca in zip(_HORAS, marcas, strict=True):
                if marca == "#":
                    continue
                grade.append(
                    EntradaGrade(
                        sala_id=sala_id,
                        profissional_id=profissional_id,
                        especialidade=profissional.especialidade,
                        slot=Slot(data=dia, hora_inicio=hora),
                        indice_posto=posto,
                    )
                )
                if marca == ".":
                    continue
                anterior = sessoes[-1] if sessoes else None
                seguida = (
                    anterior is not None
                    and anterior[0] == marca
                    and _HORAS.index(anterior[1][-1]) + 1 == _HORAS.index(hora)
                    and anterior[1][-1] != time(11, 30)
                )
                if seguida and anterior is not None:
                    anterior[1].append(hora)
                else:
                    sessoes.append((marca, [hora]))
            for marca, horas in sessoes:
                letras = grupos.get(marca, (marca,))
                for letra in letras:
                    pacientes[_id(letra)] = Paciente(id=_id(letra), nome=_nome(letra))
                atendimentos.append(
                    Atendimento(
                        id=f"at-{dia.isoformat()}-{sala_id}-{posto}-{horas[0]:%H%M}",
                        paciente_ids=[_id(letra) for letra in letras],
                        profissional_id=profissional_id,
                        sala_id=sala_id,
                        especialidade=profissional.especialidade,
                        slots=[Slot(data=dia, hora_inicio=h) for h in horas],
                        indice_posto=posto,
                        aguardando_autorizacao=marca in pretos,
                    )
                )
        fonte.grade[dia] = grade
        fonte.atendimentos[dia] = atendimentos
        fonte.pacientes[dia] = list(pacientes.values())
        fonte.profissionais[dia] = list(profissionais.values())
        fonte.salas[dia] = list(salas.values())
    return fotografar_periodo(fonte, SEGUNDA, SABADO)


# ------------------------------------------------------------- conferência


def _ocupa(lugar: Lugar) -> list[tuple[str, str, int, date, int]]:
    return list(lugar.chaves())


def _conferir(foto: FotografiaPeriodo, opcao: OpcaoRealocacao) -> None:
    """As regras que nenhuma sugestão pode quebrar, refeitas sobre a fotografia."""
    lugares: dict[str, tuple[tuple[str, ...], Especialidade, Lugar]] = {}
    for at in foto.atendimentos:
        horas = [s.hora_inicio for s in at.slots]
        lugares[at.id] = (
            tuple(at.paciente_ids),
            at.especialidade,
            Lugar(
                at.slots[0].data,
                horas[0].hour * 60 + horas[0].minute,
                at.sala_id,
                at.indice_posto,
                at.profissional_id,
                len(horas),
            ),
        )
    antes = dict(lugares)
    oferta = {
        (
            s.profissional_id,
            s.sala_id,
            s.indice_posto,
            s.data,
            s.hora_inicio.hour * 60 + s.hora_inicio.minute,
        )
        for s in foto.slots
        if s.na_grade and s.profissional_id
    }
    movidos: set[str] = set()
    for passo in opcao.passos:
        if passo.de is None:
            assert passo.pedido
            lugares["pedido"] = ((), passo.especialidade, passo.para)
            continue
        (at_id,) = [
            i for i, (_, _, lugar) in antes.items() if lugar == passo.de and i not in movidos
        ]
        movidos.add(at_id)
        pacientes, especialidade, de = lugares[at_id]
        assert especialidade is passo.especialidade
        if not passo.pedido:
            assert passo.para.profissional_id == de.profissional_id, "mudou de profissional"
            assert passo.para.blocos == de.blocos, "mudou a duração"
        lugares[at_id] = (pacientes, especialidade, passo.para)
    tocadas = {c for passo in opcao.passos for c in _ocupa(passo.para)}
    contagem = Counter(c for _, _, lugar in lugares.values() for c in _ocupa(lugar))
    assert all(contagem[c] == 1 for c in tocadas), "dois atendimentos no mesmo posto e horário"
    assert tocadas <= oferta, "fora da grade escalada"
    for paciente in {p for ps, _, _ in lugares.values() for p in ps}:
        depois = [(e, lugar) for ps, e, lugar in lugares.values() if paciente in ps]
        original = [(e, lugar) for ps, e, lugar in antes.values() if paciente in ps]
        assert {e for e, _ in depois} == {e for e, _ in original}, "mudou a terapia"
        cruzam = sum(1 for i, (_, a) in enumerate(depois) for _, b in depois[i + 1 :] if a.cruza(b))
        cruzavam = sum(
            1 for i, (_, a) in enumerate(original) for _, b in original[i + 1 :] if a.cruza(b)
        )
        assert cruzam <= cruzavam, "paciente em dois lugares"
        por_dia_antes = Counter((e, lugar.data) for e, lugar in original)
        for chave, n in Counter((e, lugar.data) for e, lugar in depois).items():
            assert n <= max(por_dia_antes[chave], 1), "duas sessões da mesma terapia no dia"


def planejar(
    foto: FotografiaPeriodo, pedido: PedidoRealocacao, regras: Regras | None = None
) -> ResultadoRealocacao:
    resultado = planejar_realocacao(foto, pedido, regras)
    if isinstance(resultado, PlanoRealocacao):
        assert 1 <= len(resultado.opcoes) <= (regras or Regras()).max_opcoes
        for opcao in resultado.opcoes:
            assert sum(1 for p in opcao.passos if p.pedido) == 1
            assert opcao.passos[-1].pedido
            assert len(opcao.deslocados) <= (regras or Regras()).max_deslocados
            _conferir(foto, opcao)
    return resultado


def _plano(resultado: ResultadoRealocacao) -> PlanoRealocacao:
    assert isinstance(resultado, PlanoRealocacao), resultado
    return resultado


def _sem_solucao(resultado: ResultadoRealocacao) -> SemSolucao:
    assert isinstance(resultado, SemSolucao), resultado
    return resultado


def _pergunta(resultado: ResultadoRealocacao, motivo: MotivoPergunta) -> Pergunta:
    assert isinstance(resultado, Pergunta), resultado
    assert resultado.motivo is motivo
    return resultado


_DIAS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb")


def _hm(lugar: Lugar | None) -> str | None:
    return None if lugar is None else f"{_DIAS[lugar.data.weekday()]} {lugar.hora_inicio:%H:%M}"


def _psico_quarta_10h(**extra: object) -> PedidoRealocacao:
    campos: dict[str, object] = {
        "dias": (QUARTA,),
        "especialidade": PSICO,
        "duracao_minutos": 60,
        "horario_desejado": time(10),
        "horario_minimo": time(10),
        "horario_maximo": time(11),
    }
    campos.update(extra)
    return PedidoRealocacao(**campos)  # type: ignore[arg-type]


CAROL = ("carol", "sala-3", 0)

# ------------------------------------------------------------------- casos


def test_sem_deslocar_ninguem_quando_ha_vaga_na_janela() -> None:
    foto = montar({QUARTA: {CAROL: "AA....BB.. ..CC......"}})
    plano = _plano(planejar(foto, _psico_quarta_10h(horario_maximo=time(12))))

    # A opção direta vem primeiro; as outras completam com 1 deslocado.
    assert [len(o.deslocados) for o in plano.opcoes] == [0, 1, 1]
    assert plano.opcoes[0].pedido.para.hora_inicio == time(11)
    assert plano.opcoes[0].pedido.paciente == ROTULO_PACIENTE_NOVO


def test_um_deslocado_mesma_terapia_e_profissional() -> None:
    # O c06: B das 10h às 11h, vagas das 08h às 10h.
    foto = montar({QUARTA: {CAROL: "AA....BB.C ..........".replace("..........", "CCCCCCCCCC")}})
    plano = _plano(planejar(foto, _psico_quarta_10h()))

    assert [len(o.deslocados) for o in plano.opcoes] == [1, 1, 1]
    # O mais perto da origem primeiro.
    assert [_hm(o.deslocados[0].para) for o in plano.opcoes] == [
        "Qua 09:00",
        "Qua 08:30",
        "Qua 08:00",
    ]
    deslocado = plano.opcoes[0].deslocados[0]
    assert deslocado.paciente == _nome("B")
    assert (deslocado.outro_dia, deslocado.outra_sala) == (False, False)
    assert plano.opcoes[0].pedido.para.hora_inicio == time(10)


def test_dois_deslocados_em_cadeia() -> None:
    # B não cabe na vaga da tarde (está na TO); quem cabe é quem B substitui.
    foto = montar(
        {
            QUARTA: {
                CAROL: "AACCDDBBEE FFGGHH....",
                ("assizely", "sala-2", 0): "KKLLMMNNOO PPQQRRBBBB",
            }
        }
    )
    plano = _plano(planejar(foto, _psico_quarta_10h()))

    assert {len(o.deslocados) for o in plano.opcoes} == {2}
    for opcao in plano.opcoes:
        fim_da_cadeia, b, pedido = opcao.passos
        # Ordem de execução: o fim da cadeia libera o lugar antes.
        assert b.paciente == _nome("B")
        assert b.para == fim_da_cadeia.de
        assert pedido.para == b.de
        assert fim_da_cadeia.para.hora_inicio >= time(16)


def test_dois_deslocados_diretos() -> None:
    foto = montar({QUARTA: {CAROL: "AA....BC.. DDDDDDDDDD"}})
    plano = _plano(planejar(foto, _psico_quarta_10h()))

    assert {len(o.deslocados) for o in plano.opcoes} == {2}
    assert {p.paciente for p in plano.opcoes[0].deslocados} == {_nome("B"), _nome("C")}


def test_tres_deslocados_nunca() -> None:
    # A única saída: B para o lugar de C, C para o de D e D para a vaga da
    # tarde (3 deslocados). B está na TO às 09:00 e à tarde; C, na fono à tarde.
    foto = montar(
        {
            QUARTA: {
                CAROL: "##CCDDBB## ......####",
                ("assizely", "sala-2", 0): "####BB#### BBBBBB####",
                ("bia", "sala-1", 0): "########## CCCCCC####",
            }
        }
    )
    sem = _sem_solucao(planejar(foto, _psico_quarta_10h()))
    assert sem.motivo is MotivoSemSolucao.NENHUMA_COMBINACAO


def test_limite_de_deslocados_configuravel() -> None:
    foto = montar(
        {
            QUARTA: {
                CAROL: "AACCDDBBEE FFGGHH....",
                ("assizely", "sala-2", 0): "KKLLMMNNOO PPQQRRBBBB",
            }
        }
    )
    sem = _sem_solucao(planejar(foto, _psico_quarta_10h(), Regras(max_deslocados=1)))
    assert sem.motivo is MotivoSemSolucao.NENHUMA_COMBINACAO


def test_paciente_ja_na_grade_libera_o_lugar_dele() -> None:
    # A (Berilo = B) quer sair das 10h para o começo da manhã: troca com C ou D.
    foto = montar({QUARTA: {CAROL: "##CCDDBB## ##########"}})
    pedido = PedidoRealocacao(
        dias=(QUARTA,),
        paciente="Paciente Ficticio B",
        horario_minimo=time(8),
        horario_maximo=time(10),
    )
    plano = _plano(planejar(foto, pedido))

    primeira = plano.opcoes[0]
    assert primeira.pedido.de is not None and primeira.pedido.de.hora_inicio == time(10)
    assert primeira.pedido.paciente == _nome("B")
    # Quem sai vai exatamente para o lugar que A deixou.
    assert primeira.deslocados[0].para == primeira.pedido.de


def test_paciente_novo_pela_especialidade_usa_qualquer_profissional_dela() -> None:
    foto = montar(
        {
            QUARTA: {
                ("bia", "sala-1", 0): "AAAAAAAAAA AAAAAAAAAA".replace("A", "B"),
                ("elo", "sala-5", 0): "EEEEEE..EE EEEEEEEEEE",
            }
        }
    )
    pedido = PedidoRealocacao(
        dias=(QUARTA,),
        especialidade=FONO,
        duracao_minutos=60,
        horario_minimo=time(10),
        horario_maximo=time(11),
    )
    plano = _plano(planejar(foto, pedido))

    assert len(plano.opcoes[0].deslocados) == 0
    assert plano.opcoes[0].pedido.para.profissional_id == "elo"


def test_mudar_de_dia_perde_para_o_mesmo_dia_e_pode_ser_proibido() -> None:
    semana = {
        QUARTA: {CAROL: "AA..CCBBDD EEEEEEEEEE"},
        QUINTA: {CAROL: "FF..GG.... HHHHHHHHHH"},
    }
    foto = montar(semana)
    plano = _plano(planejar(foto, _psico_quarta_10h()))
    # Na quarta há a vaga das 08:00: ela vem antes de qualquer outro dia.
    assert not plano.opcoes[0].deslocados[0].outro_dia
    assert any(o.deslocados[0].outro_dia for o in plano.opcoes)

    so_outro_dia = montar(
        {QUARTA: {CAROL: "AAEECCBBDD EEEEEEEEEE".replace("EE", "XX")}, QUINTA: semana[QUINTA]}
    )
    plano = _plano(planejar(so_outro_dia, _psico_quarta_10h()))
    assert all(o.deslocados[0].outro_dia for o in plano.opcoes)
    assert all(o.mudam_de_dia == 1 for o in plano.opcoes)
    sem = _sem_solucao(planejar(so_outro_dia, _psico_quarta_10h(), Regras(pode_mudar_de_dia=False)))
    assert sem.motivo is MotivoSemSolucao.NENHUMA_COMBINACAO


def test_dias_pedidos_para_o_pedido_sao_equivalentes() -> None:
    # Quarta às 11h (1h do desejado) x quinta às 10h (no desejado): vence a quinta.
    foto = montar(
        {
            QUARTA: {CAROL: "AAAAAAAA.. AAAAAAAAAA"},
            QUINTA: {CAROL: "BBBBBB..BB BBBBBBBBBB"},
        }
    )
    pedido = _psico_quarta_10h(dias=(QUARTA, QUINTA), horario_maximo=time(12))
    plano = _plano(planejar(foto, pedido))

    assert _hm(plano.opcoes[0].pedido.para) == "Qui 10:00"
    assert _hm(plano.opcoes[1].pedido.para) == "Qua 11:00"


def test_nao_cria_duas_sessoes_da_mesma_terapia_no_dia() -> None:
    # B só teria vaga na quinta, onde já tem psicologia com a Carol.
    foto = montar(
        {
            QUARTA: {CAROL: "AACCDDBBEE FFGGHHIIJJ"},
            QUINTA: {CAROL: "BB..KKLLMM NNOOPPQQRR"},
        }
    )
    resultado = planejar(foto, _psico_quarta_10h(), Regras(max_deslocados=1))
    assert _sem_solucao(resultado).motivo is MotivoSemSolucao.NENHUMA_COMBINACAO


def test_paciente_so_de_to_nunca_vai_para_outra_terapia() -> None:
    # A TO está lotada; sobra vaga só na fono: nenhuma sugestão.
    foto = montar(
        {
            QUARTA: {
                ("assizely", "sala-2", 0): "AABBCCDDEE FFGGHHIIJJ",
                ("bia", "sala-1", 0): ".......... ..........",
            }
        }
    )
    pedido = PedidoRealocacao(
        dias=(QUARTA,),
        especialidade=TO,
        duracao_minutos=60,
        horario_minimo=time(10),
        horario_maximo=time(11),
    )
    sem = _sem_solucao(planejar(foto, pedido))
    assert sem.motivo is MotivoSemSolucao.NENHUMA_COMBINACAO
    assert sem.alternativa_fora_da_janela is None


def test_saida_que_exigiria_outro_profissional_nao_e_sugerida() -> None:
    semana = {
        QUARTA: {
            ("bia", "sala-1", 0): "AABBCCDDEE FFGGHHIIJJ",
            ("elo", "sala-5", 0): "########## ......####",
        }
    }
    pedido = PedidoRealocacao(
        dias=(QUARTA,),
        profissional_id="bia",
        duracao_minutos=60,
        horario_minimo=time(10),
        horario_maximo=time(11),
    )
    foto = montar(semana)
    sem = _sem_solucao(planejar(foto, pedido))
    assert sem.motivo is MotivoSemSolucao.NENHUMA_COMBINACAO

    # Só com a regra ligada, e o passo sai marcado.
    plano = planejar_realocacao(foto, pedido, Regras(permitir_outro_profissional=True))
    plano = _plano(plano)
    assert plano.opcoes[0].deslocados[0].outro_profissional


def test_profissional_em_dois_postos_ao_mesmo_tempo() -> None:
    foto = montar(
        {
            QUARTA: {
                ("bia", "sala-1", 0): "AABBCCDDEE FFGGHHIIJJ",
                ("bia", "sala-1", 1): "KKLLMM..OO PPQQRRSSTT",
            }
        }
    )
    pedido = PedidoRealocacao(
        dias=(QUARTA,),
        especialidade=FONO,
        duracao_minutos=60,
        horario_minimo=time(10),
        horario_maximo=time(11),
    )
    plano = _plano(planejar(foto, pedido))

    destino = plano.opcoes[0].pedido.para
    assert (destino.indice_posto, len(plano.opcoes[0].deslocados)) == (1, 0)


@pytest.mark.parametrize(
    ("grupos", "pretos", "regras"),
    [
        ({"B": ("B", "Z")}, frozenset(), Regras()),
        ({}, frozenset({"B"}), Regras()),
        ({}, frozenset(), Regras(pacientes_fixos=frozenset({_id("B")}))),
        ({}, frozenset(), Regras(profissionais_fixos=frozenset({"carol"}))),
    ],
    ids=["sessao-em-grupo", "aguardando-autorizacao", "paciente-fixo", "profissional-fixo"],
)
def test_quem_nao_pode_sair_do_lugar(
    grupos: dict[str, tuple[str, ...]], pretos: frozenset[str], regras: Regras
) -> None:
    foto = montar({QUARTA: {CAROL: "AA....BB.. CCCCCCCCCC"}}, grupos=grupos, pretos=pretos)
    sem = _sem_solucao(planejar(foto, _psico_quarta_10h(), regras))
    assert sem.motivo is MotivoSemSolucao.NENHUMA_COMBINACAO
    # O encaixe direto mais perto da janela, fora dela, vem junto.
    assert sem.alternativa_fora_da_janela is not None
    assert sem.alternativa_fora_da_janela.hora_inicio == time(9)


def test_regras_que_liberam_grupo_e_aguardando() -> None:
    foto = montar(
        {QUARTA: {CAROL: "AA....BB.. CCCCCCCCCC"}}, grupos={"B": ("B", "Z")}, pretos=frozenset()
    )
    plano = _plano(planejar(foto, _psico_quarta_10h(), Regras(mover_grupo=True)))
    assert plano.opcoes[0].deslocados

    foto = montar({QUARTA: {CAROL: "AA....BB.. CCCCCCCCCC"}}, pretos=frozenset({"B"}))
    assert _plano(planejar(foto, _psico_quarta_10h(), Regras(mover_aguardando=True)))


def test_deslocado_pode_ir_para_outra_sala_do_mesmo_profissional() -> None:
    foto = montar(
        {
            QUARTA: {CAROL: "AACCDDBBEE FFGGHHIIJJ"},
            QUINTA: {("carol", "sala-4", 0): "KK..LLMMNN OOPPQQRRSS"},
        }
    )
    plano = _plano(planejar(foto, _psico_quarta_10h(), Regras(max_deslocados=1)))

    deslocado = plano.opcoes[0].deslocados[0]
    assert (deslocado.outra_sala, deslocado.outro_dia) == (True, True)
    assert deslocado.para.sala_id == "sala-4"


def test_teto_de_cadeias_avisa_busca_limitada() -> None:
    foto = montar(
        {
            QUARTA: {
                CAROL: "AACCDDBBEE FFGGHH....",
                ("assizely", "sala-2", 0): "KKLLMMNNOO PPQQRRBBBB",
            }
        }
    )
    resultado = planejar(foto, _psico_quarta_10h(), Regras(max_cadeias=5))

    assert isinstance(resultado, PlanoRealocacao | SemSolucao)
    assert resultado.estatistica.busca_limitada
    assert resultado.estatistica.cadeias_avaliadas == 5


def test_mesma_entrada_mesma_saida() -> None:
    semana = {
        QUARTA: {
            CAROL: "AACCDDBBEE FFGGHH....",
            ("assizely", "sala-2", 0): "KKLLMMNNOO PPQQRRBBBB",
        }
    }
    primeiro = _plano(planejar(montar(semana), _psico_quarta_10h()))
    segundo = _plano(planejar(montar(semana), _psico_quarta_10h()))

    assert primeiro.opcoes == segundo.opcoes


def test_grade_de_tamanho_real_atinge_o_teto_em_menos_de_3_segundos() -> None:
    """8 profissionais de fono, 5 dias, 2 postos cada, tudo lotado com sessões de
    30 min: muitos destinos para cada deslocado e nenhuma vaga, então a busca
    percorre cadeias até o teto (20 000) e para."""
    profissionais = {
        f"fono{i}": Profissional(id=f"fono{i}", nome=f"Fono {i}", especialidade=FONO)
        for i in range(8)
    }
    PROFISSIONAIS.update(profissionais)
    try:
        semana: dict[date, Dia] = {}
        letras = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for d, dia in enumerate(SEMANA[:5]):
            desenho: Dia = {}
            for i in range(8):
                for posto in range(2):
                    # Letras alternadas: cada meia hora é uma sessão de outro paciente.
                    deslocamento = (d * 7 + i * 3 + posto) % 20
                    texto = "".join(letras[(deslocamento + j) % 20] for j in range(20))
                    desenho[(f"fono{i}", f"sala-{i + 1}", posto)] = f"{texto[:10]} {texto[10:]}"
            semana[dia] = desenho
        # Pacientes em vários lugares ao mesmo tempo não importam aqui: o teste é de custo.
        foto = montar(semana)
        pedido = PedidoRealocacao(
            dias=SEMANA[:5],
            especialidade=FONO,
            duracao_minutos=30,
            horario_minimo=time(8),
            horario_maximo=time(17),
        )
        comeco = relogio.perf_counter()
        resultado = planejar_realocacao(foto, pedido)
        segundos = relogio.perf_counter() - comeco
    finally:
        for chave in profissionais:
            PROFISSIONAIS.pop(chave)

    assert isinstance(resultado, PlanoRealocacao | SemSolucao)
    assert resultado.estatistica.busca_limitada
    assert resultado.estatistica.cadeias_avaliadas == 20_000
    assert segundos < 3, f"{segundos:.2f}s"


# --------------------------------------------------------------- perguntas


@pytest.mark.parametrize(
    ("mudanca", "motivo"),
    [
        ({"especialidade": None}, MotivoPergunta.FALTA_ESPECIALIDADE),
        ({"duracao_minutos": None}, MotivoPergunta.FALTA_DURACAO),
        ({"dias": ()}, MotivoPergunta.FALTA_DIA),
        (
            {"horario_desejado": None, "horario_minimo": None, "horario_maximo": None},
            MotivoPergunta.FALTA_HORARIO,
        ),
    ],
)
def test_pergunta_o_que_falta_antes_de_buscar(
    mudanca: dict[str, object], motivo: MotivoPergunta
) -> None:
    foto = montar({QUARTA: {CAROL: "AA....BB.. .........."}})
    _pergunta(planejar(foto, replace(_psico_quarta_10h(), **mudanca)), motivo)  # type: ignore[arg-type]


def _semana_do_berilo() -> FotografiaPeriodo:
    # B Primeiro faz psicologia (quarta e quinta) e TO (quarta); B Segundo, fono.
    return montar(
        {
            QUARTA: {
                CAROL: "##CCDDBB## ##########",
                ("assizely", "sala-2", 0): "########## ......BBBB",
            },
            QUINTA: {CAROL: "BB........ ##########", ("bia", "sala-1", 0): "Y......... .........."},
        },
        grupos={"B": ("B Primeiro",), "Y": ("B Segundo",)},
    )


def test_paciente_com_varios_atendimentos_pergunta_qual() -> None:
    foto = _semana_do_berilo()
    pedido = PedidoRealocacao(
        dias=(QUARTA,), paciente="paciente ficticio b", horario_minimo=time(8)
    )
    pergunta = _pergunta(planejar(foto, pedido), MotivoPergunta.NOME_AMBIGUO)
    assert {c.paciente for c in pergunta.candidatas} == {_nome("B Primeiro"), _nome("B Segundo")}

    exato = replace(pedido, paciente="Paciente Ficticio B Primeiro", situacao=Situacao.JA_NA_GRADE)
    pergunta = _pergunta(planejar(foto, exato), MotivoPergunta.QUAL_ATENDIMENTO)
    assert [(c.numero, c.especialidade) for c in pergunta.candidatas] == [(1, PSICO), (2, TO)]

    # Psicologia tem dois dias: pergunta qual horário de origem.
    pergunta = _pergunta(
        planejar(foto, replace(exato, candidata=1)), MotivoPergunta.QUAL_HORARIO_DE_ORIGEM
    )
    assert [_hm(o) for o in pergunta.origens] == ["Qua 10:00", "Qui 07:00"]
    plano = _plano(planejar(foto, replace(exato, candidata=1, dia_origem=QUARTA)))
    assert plano.opcoes[0].pedido.de is not None
    assert plano.opcoes[0].pedido.de.data == QUARTA

    _pergunta(planejar(foto, replace(exato, candidata=9)), MotivoPergunta.CANDIDATA_INVALIDA)


def test_duracao_diferente_da_atual_pergunta() -> None:
    foto = _semana_do_berilo()
    pedido = PedidoRealocacao(
        dias=(QUARTA,),
        paciente="Paciente Ficticio B",
        especialidade=TO,
        duracao_minutos=60,
        horario_minimo=time(8),
    )
    pergunta = _pergunta(planejar(foto, pedido), MotivoPergunta.DURACAO_DIFERENTE)
    assert pergunta.duracao_atual == 120


def test_nome_que_nao_esta_na_grade() -> None:
    foto = _semana_do_berilo()
    pedido = _psico_quarta_10h(paciente="Paciente Ficticio Ninguem")
    # Sem `situacao`: é paciente novo, com o nome dito.
    plano = planejar(foto, pedido)
    assert not isinstance(plano, Pergunta)
    _pergunta(
        planejar(foto, replace(pedido, situacao=Situacao.JA_NA_GRADE)),
        MotivoPergunta.NAO_ENCONTRADO,
    )


def test_paciente_novo_com_nome_da_grade_pergunta_se_e_a_mesma_pessoa() -> None:
    foto = _semana_do_berilo()
    pedido = _psico_quarta_10h(paciente="Paciente Ficticio B", situacao=Situacao.NOVO)
    _pergunta(planejar(foto, pedido), MotivoPergunta.NOME_AMBIGUO)
    exato = replace(pedido, paciente="Paciente Ficticio B Segundo")
    _pergunta(planejar(foto, exato), MotivoPergunta.MESMO_PACIENTE_DA_GRADE)

    # A mesma pessoa: nunca em dois lugares nem duas psicologias no dia.
    mesma = replace(pedido, candidata=1, mesmo_paciente_da_grade=True)
    _sem_solucao(planejar(foto, mesma))
    # Outra pessoa (homônimo): sem as restrições daquele paciente.
    outra = replace(exato, mesmo_paciente_da_grade=False)
    assert isinstance(planejar(foto, outra), PlanoRealocacao | SemSolucao)


def test_paciente_em_dois_lugares_ao_mesmo_tempo_sugere_homonimo() -> None:
    foto = montar(
        {
            QUARTA: {
                CAROL: "##CCDDBB## ##########",
                ("assizely", "sala-2", 0): "######BB## ##########",
            }
        }
    )
    pedido = PedidoRealocacao(
        dias=(QUARTA,), paciente="Paciente Ficticio B", horario_minimo=time(8)
    )
    pergunta = _pergunta(planejar(foto, pedido), MotivoPergunta.HOMONIMO_POSSIVEL)
    assert len(pergunta.candidatas) == 2


def test_sessao_em_grupo_pedida_nao_e_movida() -> None:
    foto = montar({QUARTA: {CAROL: "##CCDDBB## ##########"}}, grupos={"B": ("B", "Z")})
    pedido = PedidoRealocacao(
        dias=(QUARTA,), paciente="Paciente Ficticio B", horario_minimo=time(8)
    )
    assert _sem_solucao(planejar(foto, pedido)).motivo is MotivoSemSolucao.SESSAO_EM_GRUPO
