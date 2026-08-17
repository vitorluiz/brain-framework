from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from brain_tool import brain_tool as core
from brain_tool import checkpoints
from brain_tool.auth import (
    ROLE_ADMIN,
    ROLE_APPROVER,
    require_admin,
    require_approver,
    role_for,
    save_admins,
)
from brain_tool.models import Approval, AuditEvent, Commit


def _session(expert: str = "maria"):
    return core.get_db_connection(Path(core.get_brain_db_path(expert=expert)))


def _global_session():
    return core.get_db_connection(Path(core.get_brain_db_path(global_brain=True)))


def _remember(conn, titulo: str, corpo: str) -> dict:
    return core.remember(conn, "maria", "memory", titulo=titulo, corpo=corpo)


# --- RBAC (roles) ------------------------------------------------------------

def _setup_admins():
    save_admins({
        "admins": ["wa:boss"],
        "roles": {"wa:boss": ROLE_ADMIN, "wa:auditor": ROLE_APPROVER},
        "groups": {},
    })


def test_role_for_defaults_admin_for_admins_list():
    _setup_admins()
    assert role_for("wa:boss") == ROLE_ADMIN
    assert role_for("wa:auditor") == ROLE_APPROVER
    assert role_for("wa:unknown") is None


def test_require_admin_rejects_approver_role():
    _setup_admins()
    with pytest.raises(PermissionError):
        require_admin("wa:auditor")
    # admin passa
    require_admin("wa:boss")


def test_require_approver_allows_approver_role():
    _setup_admins()
    require_approver("wa:auditor")
    require_approver("wa:boss")


def test_write_requires_admin_role(tmp_path):
    _setup_admins()
    conn = _session()
    try:
        with pytest.raises(PermissionError):
            core.remember(conn, "maria", "memory", titulo="x", corpo="y",
                          actor="wa:auditor")
        # admin consegue
        res = core.remember(conn, "maria", "memory", titulo="x", corpo="y",
                            actor="wa:boss")
        assert "commit" in res
    finally:
        conn.close()


# --- promote (dupla aprovação) ----------------------------------------------

def _promote_maria_to_global() -> tuple[str, str]:
    """Propõe promoção do conhecimento da maria para o global.

    Retorna (candidate_hash, candidate_id).
    """
    conn = _session()
    try:
        _remember(conn, "procedimento", "procedimento corporativo")
    finally:
        conn.close()

    src = _session()
    try:
        entries, missing = checkpoints.read_scope_entries(src, "expert/maria")
    finally:
        src.close()
    assert entries and not missing

    dst = _global_session()
    try:
        result = checkpoints.promote_into(dst, "global", entries, "expert/maria",
                                          "cli:root")
    finally:
        dst.close()
    assert result["ok"] is True
    return result["candidate"], result["candidate_id"]


def test_promote_creates_candidate_with_dual_approval_flag(tmp_path):
    chash, _ = _promote_maria_to_global()

    dst = _global_session()
    try:
        cand = dst.get(Commit, chash)
        assert cand is not None
        import json
        vr = json.loads(cand.validation_results)
        assert vr["requires_dual_approval"] is True
        assert vr["promotion_from"] == "expert/maria"
        # main global ainda não foi avançada
        assert core.count_pages(dst, "global") == 0
    finally:
        dst.close()


def test_promote_merge_requires_two_distinct_approvers(tmp_path):
    chash, cid = _promote_maria_to_global()

    # 0 aprovações → bloqueado
    dst = _global_session()
    try:
        r = checkpoints.merge_candidate(dst, "global", cid, "cli:root")
        assert r["ok"] is False and "dupla aprovação" in r["error"]
    finally:
        dst.close()

    # 1 aprovação → ainda bloqueado
    dst = _global_session()
    try:
        checkpoints.approve(dst, "global", chash, "cli:root")
        r = checkpoints.merge_candidate(dst, "global", cid, "cli:root")
        assert r["ok"] is False and "apenas 1" in r["error"]
    finally:
        dst.close()

    # 2 aprovações distintas → publica
    dst = _global_session()
    try:
        checkpoints.approve(dst, "global", chash, "cli:local")
        r = checkpoints.merge_candidate(dst, "global", cid, "cli:root")
        assert r["ok"] is True, r
        assert core.count_pages(dst, "global") == 1
        assert checkpoints.verify_scope(dst, "global")["ok"] is True
    finally:
        dst.close()


def test_promote_selects_specific_objects(tmp_path):
    conn = _session()
    try:
        r1 = _remember(conn, "a", "objeto A")
        r2 = _remember(conn, "b", "objeto B")
    finally:
        conn.close()

    # promove apenas o objeto "a" (hash do conteúdo)
    target = core.generate_canonical_hash("objeto A")
    src = _session()
    try:
        entries, missing = checkpoints.read_scope_entries(
            src, "expert/maria", object_hashes=[target])
    finally:
        src.close()
    assert len(entries) == 1
    assert entries[0]["object_hash"] == target
    assert not missing

    # hash inexistente → missing
    src = _session()
    try:
        _, missing = checkpoints.read_scope_entries(
            src, "expert/maria", object_hashes=["deadbeef"])
    finally:
        src.close()
    assert missing == ["deadbeef"]


def test_promote_same_scope_rejected(tmp_path):
    dst = _session()
    try:
        result = checkpoints.promote_into(
            dst, "expert/maria", [], "expert/maria", "cli:root")
        assert result["ok"] is False
        assert "diferentes" in result["error"]
    finally:
        dst.close()


# --- audit_events em todos os caminhos ---------------------------------------

def test_consolidate_emits_audit_event(tmp_path):
    conn = _session()
    try:
        core.remember(conn, "maria", "memory", titulo="dup1", corpo="mesmo corpo")
        core.remember(conn, "maria", "memory", titulo="dup2", corpo="mesmo corpo")
        res = core.consolidate(conn, "maria")
        assert res["removed_count"] == 1
    finally:
        conn.close()

    conn = _session()
    try:
        evts = conn.scalars(
            select(AuditEvent).where(AuditEvent.event == "consolidate")
        ).all()
        assert len(evts) == 1
        assert evts[0].scope == "expert/maria"
    finally:
        conn.close()


def test_promote_and_merge_emit_audit_events(tmp_path):
    chash, cid = _promote_maria_to_global()

    dst = _global_session()
    try:
        checkpoints.approve(dst, "global", chash, "cli:root")
        checkpoints.approve(dst, "global", chash, "cli:local")
        checkpoints.merge_candidate(dst, "global", cid, "cli:root")
        events = [e.event for e in dst.scalars(select(AuditEvent)).all()]
    finally:
        dst.close()
    assert "promote" in events
    assert "merge" in events
    assert events.count("approve") == 2


def test_audit_events_are_hash_linked(tmp_path):
    """A cadeia de auditoria é encadeada por `prev_hash` (sem buracos)."""
    _promote_maria_to_global()

    dst = _global_session()
    try:
        evts = dst.scalars(
            select(AuditEvent).order_by(AuditEvent.id)
        ).all()
    finally:
        dst.close()
    assert len(evts) >= 2
    for prev, cur in zip(evts, evts[1:]):
        assert cur.prev_hash == prev.hash
