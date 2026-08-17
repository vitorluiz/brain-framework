"""Teste de integração real — Redis + worker Celery (auditoria §6.8).

Roda o fluxo assíncrono de ponta a ponta com um broker real:
    `brain learn` (async) → Redis → worker (pool solo) → candidato (quarentena).

Requer Docker e é **desligado por padrão**. Para rodar:
    BRAIN_INTEGRATION=1 .venv/bin/python -m pytest tests/test_integration.py -v
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from brain_tool import brain_tool as core
from brain_tool import checkpoints

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

requires_integration = pytest.mark.skipif(
    os.environ.get("BRAIN_INTEGRATION") != "1",
    reason="teste de integração real: defina BRAIN_INTEGRATION=1 (requer Docker)",
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10,
                       check=False)
        return True
    except Exception:
        return False


@requires_integration
def test_real_redis_worker_roundtrip(tmp_path: Path, monkeypatch):
    if not _docker_available():
        pytest.skip("Docker indisponível")

    port = _free_port()
    broker = f"redis://127.0.0.1:{port}/0"
    brain_root = tmp_path / "brain"
    container = f"brain-it-redis-{os.getpid()}"

    subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
    up = subprocess.run(
        ["docker", "run", "-d", "--name", container, "-p", f"{port}:6379",
         "redis:7-alpine"],
        capture_output=True, text=True, check=False,
    )
    if up.returncode != 0:
        pytest.skip(f"falha ao subir redis: {up.stderr.strip()}")

    worker = None
    try:
        # worker Celery real, apontando para o broker + mesmo BRAIN_ROOT.
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(SRC) + os.pathsep + env.get("PYTHONPATH", ""),
            "CELERY_BROKER_URL": broker,
            "CELERY_RESULT_BACKEND": broker,
            "REDIS_URL": broker,
            "BRAIN_ROOT": str(brain_root),
        })
        worker = subprocess.Popen(
            [sys.executable, "-m", "celery", "-A", "brain_tool.worker", "worker",
             "--pool=solo", "--concurrency=1", "--loglevel=warning"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # contexto do lado do cliente (mesmo broker + BRAIN_ROOT).
        monkeypatch.setenv("BRAIN_ROOT", str(brain_root))
        monkeypatch.setenv("REDIS_URL", broker)
        monkeypatch.setenv("CELERY_BROKER_URL", broker)

        src = tmp_path / "q.txt"
        src.write_text("quarentena via integração real", encoding="utf-8")
        conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
        try:
            res = core.learn(conn, "maria", str(src))
            assert res["mode"] == "async"
            job_id = res["job_id"]
        finally:
            conn.close()

        # aguarda o worker consumir (job completed) — timeout de 30s.
        deadline = time.time() + 30
        status = None
        while time.time() < deadline:
            conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
            try:
                jobs = core.list_jobs(conn, "maria")
                status = next((j["status"] for j in jobs if j["id"] == job_id), None)
            finally:
                conn.close()
            if status in ("completed", "failed"):
                break
            time.sleep(0.5)
        assert status == "completed", f"job {job_id} terminou como {status}"

        # quarentena: worker propõe candidato, não publica.
        conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
        try:
            assert core.count_pages(conn, "maria") == 0
            cand = checkpoints.get_candidate_commit(conn, "expert/maria", job_id)
            assert cand is not None
            assert checkpoints.verify_scope(conn, "expert/maria")["ok"] is True

            mr = checkpoints.merge_candidate(conn, "expert/maria", job_id, "cli:root")
            assert mr["ok"] is True
            assert core.count_pages(conn, "maria") == 1
        finally:
            conn.close()
    finally:
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
        subprocess.run(["docker", "rm", "-f", container], capture_output=True,
                       check=False)
