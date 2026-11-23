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
            "tabela_generica_panorama_enxugamento.json",
            "tabela_generica_panorama_enxugamento_comparativo.json",
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

    # Abas de exportação: nomes esperados, até 31 caracteres e sem \ / ? * [ ] : (Excel).
    assert [t["nome"] for t in plano["tabelas"]] == ["Passos", "Resumo por opção"]
    assert [t["nome"] for t in pergunta["tabelas"]] == ["Candidatas"]
    for tabela in (*plano["tabelas"], *pergunta["tabelas"]):
        assert len(tabela["nome"]) <= 31
        assert not set(tabela["nome"]) & set("\\/?*[]:")


def test_exemplo_do_panorama_de_enxugamento() -> None:
    """Dois blocos com o mesmo `relatorio`: o quadro comparativo (só números) e os passos
    do recomendado, agrupados por cenário, com nomes só fictícios e nenhum código."""
    import json
    import re

    exemplos = gerar_exemplos()
    comparativo = json.loads(exemplos["tabela_generica_panorama_enxugamento_comparativo.json"])
    bloco = json.loads(exemplos["tabela_generica_panorama_enxugamento.json"])
    for exemplo in (comparativo, bloco):
        assert exemplo["dados"]["relatorio"] == "panorama_enxugamento"
        assert exemplo["contem_dados_pacientes"] is True
        assert exemplo["meta"] is None and exemplo["periodo"] is None
        texto = json.dumps(exemplo, ensure_ascii=False)
        assert re.search(r"E-[0-9A-F]{4}", texto) is None
    assert comparativo["titulo"] == "Comparativo de cenários"
    assert "agrupar_por" not in comparativo["dados"]
    assert "Ficticia" not in json.dumps(comparativo, ensure_ascii=False)
    assert [linha["cenario"] for linha in comparativo["dados"]["linhas"]] == [
        "Cenário 1 · equilibrado",
        "Cenário 2 · mais turnos",
        "Cenário 3 · menos mudança",
    ]
    assert [linha["recomendado"] for linha in comparativo["dados"]["linhas"]] == [
        True,
        False,
        False,
    ]

    dados = bloco["dados"]
    assert bloco["titulo"] == "Passos do cenário recomendado"
    assert dados["agrupar_por"] == ["cenario"]
    assert dados["unidade_grupo"] == {"singular": "passo", "plural": "passos"}
    assert [c["rotulo"] for c in dados["colunas"]] == [
        "Cenário", "Turno liberado", "Passo", "Quem", "Terapia", "De", "Para",
        "Tipo de movimento", "Observação",
    ]  # fmt: skip
    nomes = {linha["paciente"] for linha in dados["linhas"]}
    assert nomes and all(nome.startswith("Paciente Ficticio ") for nome in nomes)
    for linha in dados["linhas"]:
        assert linha["cenario"] == "Cenário 1 · equilibrado · libera 3 turnos"
        assert " · Ficticia " in linha["turno_liberado"]
    abas = [t["nome"] for exemplo in (comparativo, bloco) for t in exemplo["tabelas"]]
    assert abas == ["Cenários", "Passos"]
    for aba in abas:
        assert len(aba) <= 31
        assert not set(aba) & set("\\/?*[]:")


def test_resposta_do_panorama_na_doc_da_api_acompanha_os_exemplos() -> None:
    """O exemplo da resposta completa (dois blocos) em docs/api-relatorios.md traz os
    blocos versionados, na ordem da tool, e o resumo determinístico deles."""
    import json
    import re

    from app.ai.blocos_enxugamento import resumo_do_panorama
    from app.ai.relatorios import BlocoRelatorio

    doc = (DIRETORIO.parent / "api-relatorios.md").read_text(encoding="utf-8")
    achado = re.search(r"<!-- exemplo-resposta-panorama -->\n```json\n(.*?)\n```", doc, re.S)
    assert achado is not None
    resposta = json.loads(achado[1])
    exemplos = gerar_exemplos()
    blocos = [
        json.loads(exemplos["tabela_generica_panorama_enxugamento_comparativo.json"]),
        json.loads(exemplos["tabela_generica_panorama_enxugamento.json"]),
    ]
    assert resposta["blocos"] == blocos
    assert resposta["resposta_sensivel"] is True
    assert resposta["resposta"] == resumo_do_panorama(
        [BlocoRelatorio.model_validate(b) for b in blocos]
    )
