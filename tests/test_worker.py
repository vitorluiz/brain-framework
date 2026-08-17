from __future__ import annotations

from pathlib import Path

from brain_tool import brain_tool as core
from brain_tool import checkpoints


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


class _FakeTask:
    """Stand-in para brain_tool.worker.learn_task (captura .delay)."""

    def __init__(self):
        self.calls = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_learn_enqueues_async_job_when_broker_configured(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    import brain_tool.worker as worker_mod

    fake = _FakeTask()
    monkeypatch.setattr(worker_mod, "learn_task", fake)

    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    try:
        src = tmp_path / "w.txt"
        src.write_text("conteudo async")

        result = core.learn(conn, "maria", str(src))

        assert result["mode"] == "async"
        assert result["status"] == "enqueued"
        job_id = result["job_id"]

        assert len(fake.calls) == 1
        args, _kwargs = fake.calls[0]
        # mensagem = (job_id, scope, path, sync_immediately) — sem database_url
        assert args[0] == job_id
        assert args[1] == "expert/maria"  # scope, não expert cru
        assert args[2] == str(src)
        assert len(args) == 4, "mensagem não deve transportar database_url/credenciais"

        jobs = core.list_jobs(conn, "maria")
        assert any(j["id"] == job_id and j["status"] == "enqueued" for j in jobs)
    finally:
        conn.close()


def test_learn_falls_back_to_sync_without_broker(monkeypatch, tmp_path: Path):
    """Sem broker, learn roda síncrono e o job termina completed."""
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)

    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    try:
        src = tmp_path / "w.txt"
        src.write_text("conteudo sync")

        result = core.learn(conn, "maria", str(src), sync_immediately=True)

        assert result.get("mode") != "async"
        job_id = result["job_id"]
        jobs = core.list_jobs(conn, "maria")
        assert any(j["id"] == job_id and j["status"] == "completed" for j in jobs)
        assert core.count_pages(conn, "maria") == 1
    finally:
        conn.close()


# --- hardening Celery/Redis (§10) --------------------------------------------

def test_propose_learn_candidate_quarantines(tmp_path: Path, monkeypatch):
    """O corpo compartilhado por sync/worker propõe candidato (não publica)."""
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    try:
        src = tmp_path / "w.txt"
        src.write_text("quarentena via worker")

        result = core._propose_learn_candidate(conn, "maria", str(src), "job-x")

        assert result["status"] == "proposed"
        assert core.count_pages(conn, "maria") == 0  # não publicou
        cand = checkpoints.get_candidate_commit(conn, "expert/maria", "job-x")
        assert cand is not None
        assert checkpoints.verify_scope(conn, "expert/maria")["ok"] is True
    finally:
        conn.close()


def test_worker_session_for_scope_resolves(monkeypatch):
    from brain_tool.worker import _session_for_scope

    s = _session_for_scope("global")
    assert s is not None
    s.close()
    s = _session_for_scope("expert/maria")
    assert s is not None
    s.close()


def test_worker_task_idempotent_and_quarantines(tmp_path: Path, monkeypatch):
    """O corpo da task (sem broker) propõe candidato e é idempotente."""
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    import brain_tool.worker as worker_mod

    src = tmp_path / "w.txt"
    src.write_text("conteudo do worker")

    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    job_id = core._new_job(conn, "maria", "learn")
    conn.close()

    # primeira execução → propõe candidato (quarentena) e completa o job
    out = worker_mod.learn_task.run(job_id, "expert/maria", str(src), False)
    assert out["status"] == "completed"

    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    try:
        assert core.count_pages(conn, "maria") == 0  # não publicou
        assert checkpoints.get_candidate_commit(conn, "expert/maria", job_id) is not None
    finally:
        conn.close()

    # segunda execução → idempotente (job já completed, não reprocessa)
    out2 = worker_mod.learn_task.run(job_id, "expert/maria", str(src), False)
    assert out2["status"] == "already_completed"
