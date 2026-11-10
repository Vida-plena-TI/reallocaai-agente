"""Campos opcionais da versão 1 (adição de 2026-11-10): `agrupar_por` e
`unidade_grupo` em `dados` da `tabela_generica`, e `contem_dados_pacientes` no
envelope.

Regras: os três só saem quando têm valor (blocos antigos idênticos byte a byte);
`agrupar_por` não vazio, sem repetição e só com colunas existentes, sempre junto
com `unidade_grupo`; linhas contíguas por grupo; `contem_dados_pacientes` só
`true`, obrigatório nos três blocos com nomes de pacientes e proibido nos demais.
Nomes fictícios.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.ai.relatorios import BlocoRelatorio, DadosTabelaGenerica
from scripts.gerar_exemplos_blocos import ARQUIVO_SCHEMA, DIRETORIO, gerar_exemplos

#: Exemplos cujo bloco traz nomes de pacientes.
COM_NOMES = {
    "agenda_paciente.json",
    "tabela_generica_pacientes_por_plano.json",
    "tabela_generica_pacientes_por_profissional_nomes.json",
}
CAMPOS_NOVOS_NO_ENVELOPE = {"contem_dados_pacientes"}
CAMPOS_NOVOS_EM_DADOS = {"agrupar_por", "unidade_grupo"}


def _exemplos() -> dict[str, dict[str, Any]]:
    return {
        arquivo: json.loads(conteudo)
        for arquivo, conteudo in gerar_exemplos().items()
        if arquivo != ARQUIVO_SCHEMA
    }


def _dados(**extra: Any) -> dict[str, Any]:
    return {
        "relatorio": "pacientes_por_profissional_nomes",
        "dimensoes": [],
        "metricas": [],
        "colunas": [
            {"chave": "especialidade", "rotulo": "Especialidade", "formato": "texto"},
            {"chave": "profissional", "rotulo": "Profissional", "formato": "texto"},
            {"chave": "paciente", "rotulo": "Paciente", "formato": "texto"},
        ],
        "linhas": [
            {"especialidade": "Fono", "profissional": "Bia", "paciente": "Paciente Ficticio Um"},
            {"especialidade": "Fono", "profissional": "Bia", "paciente": "Paciente Ficticio Dois"},
            {"especialidade": "TO", "profissional": "Elo", "paciente": "Paciente Ficticio Tres"},
        ],
        "total": {},
        "linhas_omitidas": 0,
        **extra,
    }


AGRUPAMENTO = {
    "agrupar_por": ["especialidade", "profissional"],
    "unidade_grupo": {"singular": "paciente", "plural": "pacientes"},
}


# --- agrupar_por e unidade_grupo ---------------------------------------------


def test_agrupamento_valido_sai_no_json() -> None:
    dados = DadosTabelaGenerica.model_validate(_dados(**AGRUPAMENTO))

    dump = dados.model_dump(mode="json")
    assert dump["agrupar_por"] == ["especialidade", "profissional"]
    assert dump["unidade_grupo"] == {"singular": "paciente", "plural": "pacientes"}


def test_sem_agrupamento_os_campos_nao_saem_nem_como_null() -> None:
    dump = DadosTabelaGenerica.model_validate(_dados()).model_dump(mode="json")

    assert not CAMPOS_NOVOS_EM_DADOS & set(dump)


@pytest.mark.parametrize(
    ("extra", "trecho"),
    [
        ({**AGRUPAMENTO, "agrupar_por": []}, "at least 1 item"),
        ({**AGRUPAMENTO, "agrupar_por": ["profissional", "profissional"]}, "repetir coluna"),
        ({**AGRUPAMENTO, "agrupar_por": ["sala"]}, "não existem: sala"),
        ({"agrupar_por": ["profissional"]}, "vêm juntos"),
        ({"unidade_grupo": {"singular": "paciente", "plural": "pacientes"}}, "vêm juntos"),
        ({**AGRUPAMENTO, "unidade_grupo": {"singular": "paciente"}}, "plural"),
        ({**AGRUPAMENTO, "unidade_grupo": {"singular": "", "plural": "pacientes"}}, "singular"),
    ],
)
def test_agrupamento_invalido_e_recusado(extra: dict[str, Any], trecho: str) -> None:
    with pytest.raises(ValidationError, match=trecho):
        DadosTabelaGenerica.model_validate(_dados(**extra))


def test_linhas_de_um_grupo_tem_de_ser_contiguas() -> None:
    dados = _dados(**AGRUPAMENTO)
    # Bia, Elo, Bia: o grupo da Bia reaparece depois de outro.
    dados["linhas"] = [dados["linhas"][0], dados["linhas"][2], dados["linhas"][1]]

    with pytest.raises(ValidationError, match="contíguas") as erro:
        DadosTabelaGenerica.model_validate(dados)
    # A mensagem não repete valores das linhas (podem ser dados da planilha).
    assert "Bia" not in str(erro.value) and "Ficticio" not in str(erro.value)


def test_grupo_e_a_combinacao_das_colunas() -> None:
    """Mesmo profissional em duas especialidades seguidas são dois grupos válidos."""
    dados = _dados(**AGRUPAMENTO)
    dados["linhas"][2]["profissional"] = "Bia"

    DadosTabelaGenerica.model_validate(dados)


# --- contem_dados_pacientes --------------------------------------------------


def _bloco(dados: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "tipo": "tabela_generica",
        "titulo": "Título",
        "periodo": None,
        "meta": None,
        "parcial": False,
        "dias_nao_lidos": [],
        "avisos": [],
        "resumo": [],
        "dados": {"tipo": "tabela_generica", **dados},
        "tabelas": [],
        **extra,
    }


def test_bloco_com_nomes_exige_a_marca() -> None:
    with pytest.raises(ValidationError, match="exige contem_dados_pacientes"):
        BlocoRelatorio.model_validate(_bloco(_dados(**AGRUPAMENTO)))

    bloco = BlocoRelatorio.model_validate(
        _bloco(_dados(**AGRUPAMENTO), contem_dados_pacientes=True)
    )
    assert bloco.model_dump(mode="json")["contem_dados_pacientes"] is True


@pytest.mark.parametrize("valor", [False, None, "true", 1])
def test_marca_so_aceita_true(valor: Any) -> None:
    with pytest.raises(ValidationError):
        BlocoRelatorio.model_validate(_bloco(_dados(), contem_dados_pacientes=valor))


def test_marca_e_proibida_nos_blocos_sem_nomes() -> None:
    sem_nomes = {**_dados(), "relatorio": "ranking_ocupacao"}

    with pytest.raises(ValidationError, match="só vale nos blocos com nomes"):
        BlocoRelatorio.model_validate(_bloco(sem_nomes, contem_dados_pacientes=True))
    dump = BlocoRelatorio.model_validate(_bloco(sem_nomes)).model_dump(mode="json")
    assert "contem_dados_pacientes" not in dump


# --- onde os campos aparecem -------------------------------------------------


def test_marca_so_nos_tres_blocos_com_nomes() -> None:
    exemplos = _exemplos()

    com_marca = {a for a, bloco in exemplos.items() if "contem_dados_pacientes" in bloco}
    assert com_marca == COM_NOMES
    assert all(exemplos[a]["contem_dados_pacientes"] is True for a in COM_NOMES)


def test_agrupamento_so_em_pacientes_por_profissional_nomes() -> None:
    com_agrupamento = {
        arquivo
        for arquivo, bloco in _exemplos().items()
        if CAMPOS_NOVOS_EM_DADOS & set(bloco["dados"])
    }

    assert com_agrupamento == {"tabela_generica_pacientes_por_profissional_nomes.json"}


def test_blocos_sem_nomes_nao_ganharam_nenhum_campo() -> None:
    """Os exemplos dos blocos sem nomes não mudaram: nenhum campo novo, nem `null`."""
    for arquivo, bloco in _exemplos().items():
        if arquivo in COM_NOMES:
            continue
        assert not CAMPOS_NOVOS_NO_ENVELOPE & set(bloco), arquivo
        assert not CAMPOS_NOVOS_EM_DADOS & set(bloco["dados"]), arquivo


def test_schema_declara_os_campos_como_opcionais_e_continua_fechado() -> None:
    schema = json.loads((DIRETORIO / ARQUIVO_SCHEMA).read_text(encoding="utf-8"))
    dados = schema["$defs"]["DadosTabelaGenerica"]

    assert schema["additionalProperties"] is False and dados["additionalProperties"] is False
    assert "contem_dados_pacientes" in schema["properties"]
    assert "contem_dados_pacientes" not in schema["required"]
    assert schema["properties"]["contem_dados_pacientes"]["const"] is True
    assert "default" not in schema["properties"]["contem_dados_pacientes"]
    assert set(dados["properties"]) >= CAMPOS_NOVOS_EM_DADOS
    assert not CAMPOS_NOVOS_EM_DADOS & set(dados["required"])
    assert schema["$defs"]["UnidadeGrupo"]["required"] == ["singular", "plural"]
    assert schema["properties"]["versao"]["maximum"] == 1
