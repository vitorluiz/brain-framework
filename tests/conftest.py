from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(autouse=True)
def isolated_storage_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mantém cada teste dentro de um Brain home descartável e em modo síncrono.

    Limpa broker/banco compartilhado para que testes nunca disparem Celery/Redis
    nem toquem um Postgres real — salvo quando o próprio teste os define.
    """
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
