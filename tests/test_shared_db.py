from __future__ import annotations

from pathlib import Path

from brain_tool import brain_tool as core


def test_shared_database_url_separates_experts_by_column(tmp_path: Path, monkeypatch):
    """Quando DATABASE_URL está definido, todos os experts dividem um banco
    (filtrados pela coluna `expert`) — mesmo caminho do PostgreSQL."""
    shared = tmp_path / "shared.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{shared}")
    monkeypatch.delenv("BRAIN_ROOT", raising=False)

    conn_a = core.get_session(expert="maria")
    conn_b = core.get_session(expert="jose")
    try:
        core.remember(conn_a, "maria", "fact", "titulo a", "conteudo a")
        core.remember(conn_b, "jose", "fact", "titulo b", "conteudo b")

        assert core.count_pages(conn_a, "maria") == 1
        assert core.count_pages(conn_b, "jose") == 1
        # Isolamento: maria não vê o conhecimento de jose.
        assert [p["expert"] for p in core.recall(conn_a, "maria")] == ["maria"]
        assert [p["expert"] for p in core.recall(conn_b, "jose")] == ["jose"]
    finally:
        conn_a.close()
        conn_b.close()


def test_database_url_overrides_brain_path(tmp_path: Path, monkeypatch):
    """Com DATABASE_URL definido, o --brain-path local é ignorado."""
    shared = tmp_path / "shared.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{shared}")
    monkeypatch.delenv("BRAIN_ROOT", raising=False)

    url = core.get_database_url(brain_path="/algum/lugar/ignorado.db")
    assert url == f"sqlite:///{shared}"
