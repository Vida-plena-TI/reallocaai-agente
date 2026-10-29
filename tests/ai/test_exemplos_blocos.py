"""Os exemplos de contrato em docs/exemplos-blocos/ acompanham o modelo atual."""

from scripts.gerar_exemplos_blocos import DIRETORIO, gerar_exemplos
from tests.support import agenda_clinica
from tests.support.relatorios import ANA, BIA, NOMES_PACIENTES


def test_exemplos_versionados_estao_atualizados() -> None:
    for arquivo, conteudo in gerar_exemplos().items():
        caminho = DIRETORIO / arquivo
        assert caminho.read_text(encoding="utf-8") == conteudo, (
            f"{caminho.name} desatualizado: rode uv run python scripts/gerar_exemplos_blocos.py"
        )


def test_exemplos_so_tem_dados_ficticios() -> None:
    for conteudo in gerar_exemplos().values():
        assert all(nome not in conteudo for nome in NOMES_PACIENTES)
        assert ANA.id not in conteudo and BIA.id not in conteudo


def test_exemplos_gerais_so_citam_paciente_na_agenda_do_paciente() -> None:
    proibidos = [*agenda_clinica.NOMES_PACIENTES, *agenda_clinica.IDS_PACIENTES]
    for arquivo, conteudo in gerar_exemplos().items():
        if arquivo == "agenda_paciente.json":
            outros = [n for n in proibidos if "Alfa" not in n and "alfa" not in n]
            assert "Paciente Ficticio Alfa" in conteudo
            assert all(nome not in conteudo for nome in outros)
            assert agenda_clinica.IDS_PACIENTES[0] not in conteudo
        else:
            assert all(nome not in conteudo for nome in proibidos), arquivo


def test_ha_exemplo_de_cada_tipo_de_bloco() -> None:
    import json

    tipos = {
        json.loads(conteudo)["tipo"]
        for arquivo, conteudo in gerar_exemplos().items()
        if not arquivo.endswith(".schema.json")
    }
    assert tipos == {
        "ocupacao_profissional",
        "pacientes_por_profissional",
        "ocupacao_agregada",
        "disponibilidade",
        "convenio",
        "agenda_paciente",
        "qualidade_agenda",
        "tabela_generica",
    }


def test_so_os_relatorios_da_grade_semanal_tem_periodo_nulo() -> None:
    import json

    for arquivo, conteudo in gerar_exemplos().items():
        if arquivo.endswith(".schema.json"):
            continue
        bloco = json.loads(conteudo)
        assert "periodo" in bloco, arquivo
        if arquivo in (
            "tabela_generica_plano_semanal.json",
            "tabela_generica_pacientes_por_plano.json",
        ):
            assert bloco["periodo"] is None
        else:
            assert set(bloco["periodo"]) == {"inicio", "fim"}, arquivo


def test_schema_exige_periodo_e_aceita_null() -> None:
    import json

    schema = json.loads(gerar_exemplos()["bloco_relatorio.schema.json"])
    assert "periodo" in schema["required"]
    assert schema["properties"]["periodo"] == {
        "anyOf": [{"$ref": "#/$defs/PeriodoRelatorio"}, {"type": "null"}]
    }


def test_exemplo_de_pacientes_por_plano_so_tem_nomes_ficticios() -> None:
    """O bloco leva nomes de pacientes por definição: no exemplo, só os fictícios
    do cenário `planos_por_cor`, e nenhum dos pretos ou de cor não reconhecida."""
    import json

    bloco = json.loads(gerar_exemplos()["tabela_generica_pacientes_por_plano.json"])
    nomes = {linha["paciente"] for linha in bloco["dados"]["linhas"]}

    assert nomes and all(nome.startswith("Paciente Ficticio ") for nome in nomes)
    assert not nomes & {"Paciente Ficticio Preto", "Paciente Ficticio Magenta"}
    assert bloco["dados"]["relatorio"] == "pacientes_por_plano"
