"""Testes da autorização admin no core (spec §5).

`require_admin(actor)` e o parâmetro `actor` das funções de escrita garantem
que chamadores remotos (plugin/gateway/dashboard) só gravam se o `actor` for
admin em `admins.json`. `actor=None` = chamador local de confiança (CLI).
"""

from __future__ import annotations

import pytest

from brain_tool import auth
from brain_tool import brain_tool as core


@pytest.fixture
def _admins(monkeypatch):
    auth.save_admins({"admins": ["wa:admin"], "groups": {"adm": ["wa:admin"]}})


def test_require_admin_allows_none():
    # chamador local de confiança — sem checagem
    auth.require_admin(None)


def test_require_admin_allows_listed_admin(_admins):
    auth.require_admin("wa:admin")


def test_require_admin_rejects_unknown(_admins):
    with pytest.raises(PermissionError, match="administradores"):
        auth.require_admin("wa:outsider")


def test_require_admin_rejects_empty_string(_admins):
    with pytest.raises(PermissionError, match="administradores"):
        auth.require_admin("")


def test_is_admin_and_group(_admins):
    assert auth.is_admin("wa:admin") is True
    assert auth.is_admin("wa:x") is False
    assert auth.is_admin(None) is False
    assert auth.is_group_member("wa:admin", "adm") is True
    assert auth.is_group_member("wa:x", "adm") is False


def test_remember_rejects_non_admin_actor(_admins):
    conn = core.get_session(expert="maria")
    try:
        with pytest.raises(PermissionError, match="administradores"):
            core.remember(conn, "maria", "fact", "t", "c", actor="wa:outsider")
    finally:
        conn.close()


def test_remember_allows_admin_actor(_admins):
    conn = core.get_session(expert="maria")
    try:
        res = core.remember(conn, "maria", "fact", "t", "c", actor="wa:admin")
        assert res["action"] == "remember"
    finally:
        conn.close()


def test_remember_trusted_local_by_default():
    # actor=None (default) = CLI local, sem admins.json necessário
    conn = core.get_session(expert="maria")
    try:
        res = core.remember(conn, "maria", "fact", "t", "c")
        assert res["action"] == "remember"
    finally:
        conn.close()


def test_forget_rejects_non_admin_actor(_admins):
    conn = core.get_session(expert="maria")
    try:
        with pytest.raises(PermissionError, match="administradores"):
            core.forget(conn, "maria", 999, actor="wa:outsider")
    finally:
        conn.close()


def test_learn_rejects_non_admin_actor(_admins, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# titulo\ncorpo\n", encoding="utf-8")
    conn = core.get_session(expert="maria")
    try:
        with pytest.raises(PermissionError, match="administradores"):
            core.learn(conn, "maria", str(f), actor="wa:outsider")
    finally:
        conn.close()
