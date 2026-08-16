from __future__ import annotations

from pathlib import Path

from brain_tool import brain_tool as core


def test_async_disabled_without_broker(monkeypatch):
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert core._async_enabled() is False


def test_async_enabled_when_broker_configured(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert core._async_enabled() is True


def test_ingest_worker_body(tmp_path: Path, monkeypatch):
    """_ingest é a função que o worker executa — sem rastreamento de job."""
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    try:
        src = tmp_path / "w.txt"
        src.write_text("linha unica")

        result = core._ingest(conn, "maria", str(src), sync_immediately=True)

        assert result["action"] == "learn"
        assert result["chunks"] == 1
        assert core.count_pages(conn, "maria") == 1
    finally:
        conn.close()
