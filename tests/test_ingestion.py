from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from brain_tool import brain_tool as core


def test_learn_tracks_job_chunks_and_syncs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    db_path = Path(core.get_brain_db_path(expert="maria"))
    conn = core.get_db_connection(db_path)
    try:
        big = "\n".join(f"linha {i} " + "x" * 120 for i in range(120))
        src = tmp_path / "big.txt"
        src.write_text(big)

        result = core.learn(conn, "maria", str(src), sync_immediately=True)

        assert result["action"] == "learn"
        assert result.get("job_id"), "learn deve criar um job"
        assert result["chunks"] > 1, "arquivo grande deve ser dividido em chunks"

        rows = core.list_jobs(conn, "maria")
        assert rows, "job deve estar registrado"
        assert rows[0]["status"] == "completed"
        assert rows[0]["command"] == "learn"

        assert core.count_pages(conn, "maria") == result["chunks"], \
            "sync deve mover todos os chunks para pages"
    finally:
        conn.close()


def test_learn_dry_run_writes_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    db_path = Path(core.get_brain_db_path(expert="jose"))
    conn = core.get_db_connection(db_path)
    try:
        src = tmp_path / "a.txt"
        src.write_text("hello world")

        result = core.learn(conn, "jose", str(src), dry_run=True)

        assert result["action"].startswith("learn (dry-run)")
        staging = conn.execute(
            text("SELECT COUNT(*) FROM knowledge_staging WHERE expert = :e"), {"e": "jose"}
        ).scalar()
        jobs = conn.execute(
            text("SELECT COUNT(*) FROM jobs WHERE expert = :e"), {"e": "jose"}
        ).scalar()
        assert staging == 0
        assert jobs == 0
    finally:
        conn.close()


def test_sync_is_idempotent_by_hash(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    db_path = Path(core.get_brain_db_path(expert="maria"))
    conn = core.get_db_connection(db_path)
    try:
        src = tmp_path / "dup.txt"
        src.write_text("conteudo unico para deduplicacao")

        core.learn(conn, "maria", str(src), sync_immediately=True)
        core.learn(conn, "maria", str(src), sync_immediately=True)

        assert core.count_pages(conn, "maria") == 1, \
            "hash canônico deve evitar duplicatas no sync"
    finally:
        conn.close()


def test_module_exposes_version():
    assert isinstance(getattr(core, "__version__", None), str)
