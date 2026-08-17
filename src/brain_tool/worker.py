"""Worker Celery — processamento assíncrono de learn/sync (spec §4.2).

Roda com:
    celery -A brain_tool.worker worker --loglevel=info
ou, após `pip install .`:
    brain-worker

Config via env: CELERY_BROKER_URL / REDIS_URL (default redis://localhost:6379/0)
e CELERY_RESULT_BACKEND (default = broker).

Hardening (plan/checkpoints-assinados.md §10 / auditoria §6):
- A mensagem transporta **só `(job_id, scope, path, sync_immediately)`** — nunca
  `database_url`. O worker reconstrói a conexão do próprio ambiente (elimina
  credenciais no Redis).
- `acks_late=True` + retry com backoff + time limits (idempotente).
- O worker **propõe** um commit candidato (quarentena); publicação só ocorre com
  `sync_immediately` (aprovação implícita do admin local).
"""

from __future__ import annotations

import os

from celery import Celery
from sqlalchemy.exc import OperationalError

from brain_tool import brain_tool as core
from brain_tool import checkpoints
from brain_tool.db import get_session
from brain_tool.models import Job

_BROKER = (
    os.environ.get("CELERY_BROKER_URL")
    or os.environ.get("REDIS_URL")
    or "redis://localhost:6379/0"
)
_BACKEND = os.environ.get("CELERY_RESULT_BACKEND") or _BROKER

app = Celery("brain", broker=_BROKER, backend=_BACKEND)
app.conf.task_serializer = "json"
app.conf.result_serializer = "json"
app.conf.accept_content = ["json"]
app.conf.timezone = "UTC"
app.conf.enable_utc = True

# Hardening (§10): late ack (só para tarefas idempotentes — a ingestão dedupa
# por hash), rejeição se o worker morrer, limites de tempo e retry com backoff.
app.conf.task_acks_late = True
app.conf.task_reject_on_worker_lost = True
app.conf.task_time_limit = int(os.environ.get("BRAIN_TASK_TIME_LIMIT", "1800"))
app.conf.task_soft_time_limit = int(os.environ.get("BRAIN_TASK_SOFT_TIME_LIMIT", "300"))


def _session_for_scope(scope: str):
    """Abre uma Session para um scope (`global` ou `expert/<nome>`) — sem credencial na mensagem."""
    if scope == "global":
        return get_session(global_brain=True)
    return get_session(expert=scope.removeprefix("expert/"))


@app.task(name="brain.learn", bind=True, acks_late=True,
          autoretry_for=(OperationalError,), max_retries=3,
          retry_backoff=True, retry_backoff_max=60, retry_jitter=True)
def learn_task(self, job_id, scope, path, sync_immediately=False):
    """Worker: marca processing, propõe candidato (quarentena), completed/failed.

    A publicação do conhecimento só acontece com `sync_immediately`; sem isso o
    conteúdo fica em quarentena (commit candidato) até `brain merge`.
    """
    conn = _session_for_scope(scope)
    expert = checkpoints.expert_for(scope)
    try:
        # Idempotência: job já concluído não é reprocessado (retry seguro).
        job = conn.get(Job, job_id)
        if job is not None and job.status == "completed":
            return {"job_id": job_id, "scope": scope, "status": "already_completed"}
        core._set_job_status(conn, job_id, "processing")
        result = core._propose_learn_candidate(conn, expert, path, job_id,
                                               sync_immediately)
        core._set_job_status(conn, job_id, "completed")
        return {"job_id": job_id, "scope": scope, "status": "completed", "result": result}
    except Exception as e:  # noqa: BLE001 — worker deve registrar e propagar
        core._set_job_status(conn, job_id, "failed", error=str(e))
        raise
    finally:
        conn.close()


def main() -> int:
    """Entry point do console script `brain-worker`."""
    import sys

    argv = ["worker", "--loglevel=info"] + [a for a in sys.argv[1:] if a != "--loglevel"]
    app.worker_main(argv)
    return 0


if __name__ == "__main__":
    main()
