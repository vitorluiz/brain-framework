from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from brain_tool import brain as manager
from brain_tool import brain_tool as core


def _args(**kwargs):
    return types.SimpleNamespace(**kwargs)


def _make_expert_brain(expert: str, corpo: str) -> None:
    conn = core.get_db_connection(Path(core.get_brain_db_path(expert=expert)))
    try:
        core.remember(conn, expert, "memory", titulo="pagina", corpo=corpo)
    finally:
        conn.close()


def _recall(expert: str) -> list:
    conn = core.get_db_connection(Path(core.get_brain_db_path(expert=expert)))
    try:
        return core.recall(conn, expert)
    finally:
        conn.close()


def _make_global_brain(corpo: str) -> None:
    conn = core.get_db_connection(Path(core.get_brain_db_path(global_brain=True)))
    try:
        core.remember(conn, "global", "memory", titulo="pagina-global", corpo=corpo)
    finally:
        conn.close()


def _backup_and_get_ts() -> str:
    manager.cmd_backup(_args())
    backups = manager._list_available_backups()
    assert backups, "backup deveria ter sido criado"
    return backups[0]["timestamp"]


def test_restore_roundtrip_reverts_content(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))

    _make_expert_brain("maria", "original")
    assert [p["corpo"] for p in _recall("maria")] == ["original"]

    ts = _backup_and_get_ts()

    # modifica: adiciona uma segunda página que não existia no backup
    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    try:
        core.remember(conn, "maria", "memory", titulo="extra", corpo="conteudo-extra")
    finally:
        conn.close()
    assert len(_recall("maria")) == 2

    rc = manager.cmd_restore(_args(from_spec=ts, expert=None,
                                   global_brain=False, yes=True, list_backups=False))
    assert rc == 0

    after = _recall("maria")
    assert [p["corpo"] for p in after] == ["original"], "restore deveria reverter para o backup"


def test_restore_preserves_pre_restore_copy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))

    _make_expert_brain("maria", "original")
    ts = _backup_and_get_ts()

    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    try:
        core.remember(conn, "maria", "memory", titulo="extra", corpo="conteudo-extra")
    finally:
        conn.close()

    manager.cmd_restore(_args(from_spec=ts, expert=None, global_brain=False,
                              yes=True, list_backups=False))

    dest = core.get_brain_db_path(expert="maria")
    pre = Path(f"{dest}.pre-restore-{ts}")
    assert pre.exists(), "deveria preservar o brain.db anterior antes de sobrescrever"


def test_restore_expert_scoped_only_that_expert(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))

    _make_expert_brain("maria", "maria-original")
    _make_expert_brain("jose", "jose-original")
    ts = _backup_and_get_ts()

    for expert in ("maria", "jose"):
        conn = core.get_db_connection(Path(core.get_brain_db_path(expert=expert)))
        try:
            core.remember(conn, expert, "memory", titulo="extra", corpo=f"extra-{expert}")
        finally:
            conn.close()

    rc = manager.cmd_restore(_args(from_spec=ts, expert="maria",
                                   global_brain=False, yes=True, list_backups=False))
    assert rc == 0

    assert [p["corpo"] for p in _recall("maria")] == ["maria-original"]
    # jose NÃO deve ser restaurado
    assert {p["corpo"] for p in _recall("jose")} == {"jose-original", "extra-jose"}


def test_restore_requires_confirmation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))

    _make_expert_brain("maria", "original")
    ts = _backup_and_get_ts()

    conn = core.get_db_connection(Path(core.get_brain_db_path(expert="maria")))
    try:
        core.remember(conn, "maria", "memory", titulo="extra", corpo="conteudo-extra")
    finally:
        conn.close()

    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    rc = manager.cmd_restore(_args(from_spec=ts, expert=None, global_brain=False,
                                   yes=False, list_backups=False))
    assert rc == 1
    # conteúdo não deve ter sido revertido (cancelado)
    assert len(_recall("maria")) == 2


def test_list_available_backups_empty_when_none(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    assert manager._list_available_backups() == []


def test_resolve_backup_dir_missing_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    with pytest.raises(FileNotFoundError):
        manager._resolve_backup_dir("backup_19000101_000000")


def test_restore_backup_missing_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    d = tmp_path / "brain" / "backups" / "backup_x"
    d.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        manager._read_backup_manifest(str(d))
