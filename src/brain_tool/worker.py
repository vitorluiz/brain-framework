"""Worker Celery — processamento assíncrono de learn/sync (spec §4.2).

Roda com:
    celery -A brain_tool.worker worker --loglevel=info
ou, após `pip install .`:
    brain-worker

Config via env: CELERY_BROKER_URL / REDIS_URL (default redis://localhost:6379/0)
e CELERY_RESULT_BACKEND (default = broker).
"""

from __future__ import annotations

import os

from celery import Celery

from brain_tool import brain_tool as core
from brain_tool.db import get_session, get_session_from_url

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


@app.task(name="brain.learn", bind=True)
def learn_task(self, job_id, expert, path, sync_immediately=False, database_url=None):
    """Worker: marca o job como processing, ingere e marca completed/failed."""
    conn = get_session_from_url(database_url) if database_url else get_session(expert=expert)
    try:
        core._set_job_status(conn, job_id, "processing")
        result = core._ingest(conn, expert, path, sync_immediately)
        core._set_job_status(conn, job_id, "completed")
        return {"job_id": job_id, "status": "completed", "result": result}
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
