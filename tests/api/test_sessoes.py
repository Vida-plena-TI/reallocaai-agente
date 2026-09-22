"""Testes de `ArmazenamentoConversas` (Fase 6b), sem nenhuma chamada de rede."""

import pytest

from app.api.sessoes import ArmazenamentoConversas


def test_criar_conversa_gera_ids_unicos_com_historico_vazio() -> None:
    armazenamento = ArmazenamentoConversas()

    id1 = armazenamento.criar_conversa()
    id2 = armazenamento.criar_conversa()

    assert id1 != id2
    assert armazenamento.obter_historico(id1) == []
    assert armazenamento.obter_historico(id2) == []


def test_registrar_troca_reflete_no_historico_na_ordem_certa() -> None:
    armazenamento = ArmazenamentoConversas()
    conversa_id = armazenamento.criar_conversa()

    armazenamento.registrar_troca(conversa_id, "Oi, tudo bem?", "Tudo ótimo, como posso ajudar?")
    armazenamento.registrar_troca(conversa_id, "Quero saber a ocupação de hoje.", "Está em 80%.")

    historico = armazenamento.obter_historico(conversa_id)

    assert historico is not None
    assert [mensagem.content for mensagem in historico] == [
        "Oi, tudo bem?",
        "Tudo ótimo, como posso ajudar?",
        "Quero saber a ocupação de hoje.",
        "Está em 80%.",
    ]


def test_obter_historico_de_id_inexistente_retorna_none() -> None:
    armazenamento = ArmazenamentoConversas()

    assert armazenamento.obter_historico("id-que-nao-existe") is None


def test_registrar_troca_em_conversa_inexistente_e_nao_op() -> None:
    """Não-op silencioso: quem chama já garantiu a existência da conversa antes
    de rodar o agente, mas ela pode ter expirado nesse meio-tempo."""
    armazenamento = ArmazenamentoConversas()

    armazenamento.registrar_troca("id-que-nao-existe", "Oi?", "Resposta")

    assert armazenamento.obter_historico("id-que-nao-existe") is None


def test_conversa_expirada_retorna_none_e_e_removida_do_armazenamento_interno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.sessoes.time.monotonic", lambda: 1_000.0)
    armazenamento = ArmazenamentoConversas(ttl_horas=1)
    conversa_id = armazenamento.criar_conversa()

    assert conversa_id in armazenamento._conversas

    # Duas horas depois — além do TTL de 1 hora, sem nenhum `time.sleep` real.
    monkeypatch.setattr("app.api.sessoes.time.monotonic", lambda: 1_000.0 + 2 * 3600)

    assert armazenamento.obter_historico(conversa_id) is None
    assert conversa_id not in armazenamento._conversas
