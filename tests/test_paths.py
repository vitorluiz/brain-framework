from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from sqlalchemy import text

from brain_tool import brain_tool as core


def test_get_brain_root_uses_runtime_environment_precedence(
    tmp_path: Path, monkeypatch,
):
    get_brain_root = getattr(core, "get_brain_root", None)
    assert get_brain_root is not None, "get_brain_root() is not implemented"

    explicit = tmp_path / "explicit"
    home = tmp_path / "user-home"

    monkeypatch.setenv("BRAIN_ROOT", str(explicit))
    monkeypatch.setenv("HOME", str(home))
    assert get_brain_root() == explicit.resolve()

    monkeypatch.delenv("BRAIN_ROOT")
    assert get_brain_root() == (home / ".hermes" / "brain").resolve()


def test_get_brain_db_path_uses_expert_and_global_layout(
    tmp_path: Path, monkeypatch,
):
    root = (tmp_path / "dynamic-root").resolve()
    monkeypatch.setenv("BRAIN_ROOT", str(root))

    expert_db = Path(core.get_brain_db_path(expert="alpha"))
    global_db = Path(core.get_brain_db_path(global_brain=True))

    # spec §3.1 / requirements §2.1: <name>/brain.db + global/brain.db
    assert expert_db == root / "alpha" / "brain.db"
    assert global_db == root / "global" / "brain.db"
    assert expert_db.is_relative_to(root)
    assert global_db.is_relative_to(root)


@pytest.mark.parametrize(
    "expert",
    ["", ".", "..", "../x", "a/b", r"a\b", "/tmp/x", "alpha beta", "-alpha",
     "a" * 65, "global", "backups"],
)
def test_invalid_expert_identifiers_are_rejected_before_filesystem_access(
    expert: str, tmp_path: Path, monkeypatch,
):
    root = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_ROOT", str(root))

    with pytest.raises(ValueError, match="expert identifier"):
        core.get_brain_db_path(expert=expert)

    assert not root.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not enforced")
def test_database_connection_creates_private_storage_with_finite_timeout(
    tmp_path: Path, monkeypatch,
):
    root = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_ROOT", str(root))
    db_path = Path(core.get_brain_db_path(expert="alpha"))

    connection = core.get_db_connection(db_path)
    try:
        busy_timeout_ms = connection.execute(text("PRAGMA busy_timeout")).fetchone()[0]
        assert 0 < busy_timeout_ms <= 10_000
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    finally:
        connection.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not enforced")
def test_database_connection_repairs_existing_sqlite_sidecar_permissions(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    db_path = Path(core.get_brain_db_path(expert="alpha"))
    first = core.get_db_connection(db_path)
    second = None
    try:
        assert first.execute(text("PRAGMA journal_mode=WAL")).fetchone()[0] == "wal"
        core.remember(first, "alpha", "fact", "title", "content")
        sidecars = [Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]
        assert all(path.exists() for path in sidecars)
        for path in sidecars:
            path.chmod(0o644)

        second = core.get_db_connection(db_path)

        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in sidecars)
    finally:
        if second is not None:
            second.close()
        first.close()
