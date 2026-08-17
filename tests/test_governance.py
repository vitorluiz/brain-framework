from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from brain_tool import brain_tool as core
from brain_tool import checkpoints
from brain_tool.models import Approval


def _session(expert: str = "maria"):
    return core.get_db_connection(Path(core.get_brain_db_path(expert=expert)))


def _remember(conn, titulo: str, corpo: str) -> str:
    return core.remember(conn, "maria", "memory", titulo=titulo, corpo=corpo)["commit"]


# --- diff --------------------------------------------------------------------

def test_diff_default_shows_last_commit_add(tmp_path):
    conn = _session()
    try:
        _remember(conn, "a", "um")
        _remember(conn, "b", "dois")
        changes = checkpoints.diff(conn, "expert/maria")
    finally:
        conn.close()
    assert len(changes) == 1
    assert changes[0]["op"] == "add"
    assert changes[0]["titulo"] == "b"


def test_diff_shows_remove_after_forget(tmp_path):
    conn = _session()
    try:
        _remember(conn, "a", "um")
        r2 = core.remember(conn, "maria", "memory", titulo="b", corpo="dois")
        core.forget(conn, "maria", r2["id"])
        changes = checkpoints.diff(conn, "expert/maria")
    finally:
        conn.close()
    assert len(changes) == 1
    assert changes[0]["op"] == "remove"
    assert changes[0]["titulo"] == "b"


# --- approve -----------------------------------------------------------------

def test_approve_records_governance_row(tmp_path):
    conn = _session()
    try:
        cid = _remember(conn, "a", "um")
        aid = checkpoints.approve(conn, "expert/maria", cid, "cli:root",
                                  policy="manual", justification="ok")
    finally:
        conn.close()

    conn = _session()
    try:
        row = conn.scalars(select(Approval)).all()
    finally:
        conn.close()
    assert len(row) == 1
    assert row[0].id == aid
    assert row[0].decision == "approve"
    assert row[0].approver == "cli:root"
    assert row[0].candidate_commit_id == cid


def test_approve_reject_decision(tmp_path):
    conn = _session()
    try:
        cid = _remember(conn, "a", "um")
        checkpoints.approve(conn, "expert/maria", cid, "cli:root",
                            decision="reject", justification="não passou")
    finally:
        conn.close()

    conn = _session()
    try:
        row = conn.scalars(select(Approval)).all()
    finally:
        conn.close()
    assert row[0].decision == "reject"


# --- rollback ----------------------------------------------------------------

def test_rollback_moves_main_and_rebuilds_pages(tmp_path):
    conn = _session()
    try:
        c1 = _remember(conn, "a", "um")
        _remember(conn, "b", "dois")
    finally:
        conn.close()

    conn = _session()
    try:
        result = checkpoints.rollback(conn, "expert/maria", c1, "cli:root")
    finally:
        conn.close()
    assert result["ok"] is True
    assert result["pages"] == 1

    conn = _session()
    try:
        pages = core.recall(conn, "maria")
        assert checkpoints.verify_scope(conn, "expert/maria")["ok"] is True
    finally:
        conn.close()
    assert [p["corpo"] for p in pages] == ["um"]


def test_rollback_is_non_destructive(tmp_path):
    """Commits/objetos abandonados continuam no banco (não apagados)."""
    conn = _session()
    try:
        c1 = _remember(conn, "a", "um")
        c2 = _remember(conn, "b", "dois")
    finally:
        conn.close()

    conn = _session()
    try:
        checkpoints.rollback(conn, "expert/maria", c1, "cli:root")
    finally:
        conn.close()

    conn = _session()
    try:
        # c2 ainda existe como commit (dangling), só não é alcançável pela main
        from brain_tool.models import Commit
        assert conn.get(Commit, c2) is not None
    finally:
        conn.close()


def test_rollback_rejects_unknown_commit(tmp_path):
    conn = _session()
    try:
        _remember(conn, "a", "um")
        result = checkpoints.rollback(conn, "expert/maria", "deadbeef", "cli:root")
    finally:
        conn.close()
    assert result["ok"] is False
    assert "não encontrado" in result["error"]


def test_rollback_rejects_forward_to_dangling(tmp_path):
    conn = _session()
    try:
        c1 = _remember(conn, "a", "um")
        c2 = _remember(conn, "b", "dois")
        checkpoints.rollback(conn, "expert/maria", c1, "cli:root")
        result = checkpoints.rollback(conn, "expert/maria", c2, "cli:root")
    finally:
        conn.close()
    assert result["ok"] is False
    assert "ancestro" in result["error"]
