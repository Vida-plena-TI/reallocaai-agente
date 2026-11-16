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
            "tabela_generica_pacientes_por_profissional_nomes.json",
            "tabela_generica_plano_realocacao.json",
            "tabela_generica_plano_realocacao_pergunta.json",
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


def test_exemplo_de_pacientes_por_profissional_so_tem_nomes_ficticios() -> None:
    """O bloco leva nomes de pacientes por definição: no exemplo, só os fictícios
    do cenário `nomes_por_profissional`."""
    import json

    bloco = json.loads(gerar_exemplos()["tabela_generica_pacientes_por_profissional_nomes.json"])
    nomes = {linha["paciente"] for linha in bloco["dados"]["linhas"]}

    assert nomes and all(nome.casefold().startswith("paciente fict") for nome in nomes)
    assert bloco["dados"]["relatorio"] == "pacientes_por_profissional_nomes"


def test_exemplos_do_plano_de_realocacao_plano_e_pergunta() -> None:
    """Os dois formatos do mesmo `dados.relatorio`: o plano, agrupado por opção, e a
    pergunta de nome ambíguo, com as candidatas. Só nomes fictícios."""
    import json

    exemplos = gerar_exemplos()
    plano = json.loads(exemplos["tabela_generica_plano_realocacao.json"])
    pergunta = json.loads(exemplos["tabela_generica_plano_realocacao_pergunta.json"])
    for bloco in (plano, pergunta):
        assert bloco["dados"]["relatorio"] == "plano_realocacao"
        assert bloco["contem_dados_pacientes"] is True
        nomes = {linha["paciente"] for linha in bloco["dados"]["linhas"]}
        assert nomes and all(
            nome.startswith("Paciente Ficticio ") or nome == "Paciente novo" for nome in nomes
        )

    assert plano["dados"]["agrupar_por"] == ["opcao"]
    assert plano["dados"]["unidade_grupo"] == {"singular": "passo", "plural": "passos"}
    assert [c["rotulo"] for c in plano["dados"]["colunas"]] == [
        "Opção", "Passo", "Quem", "Terapia", "Profissional", "De", "Para", "Observação",
    ]  # fmt: skip
    # 2 deslocados por opção: 3 passos cada.
    assert len(plano["dados"]["linhas"]) == 9

    assert "agrupar_por" not in pergunta["dados"]
    assert "unidade_grupo" not in pergunta["dados"]
    assert [c["rotulo"] for c in pergunta["dados"]["colunas"]] == [
        "Candidata", "Nome", "Terapia", "Profissional", "Dias",
    ]  # fmt: skip
    assert [linha["paciente"] for linha in pergunta["dados"]["linhas"]] == [
        "Paciente Ficticio Rubi Alva",
        "Paciente Ficticio Rubi Estrela",
    ]
