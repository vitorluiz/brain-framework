from __future__ import annotations

import pytest

from brain_tool import brain_tool as core


def _conn():
    return core.get_db_connection(core.get_brain_db_path(expert="maria"))


def test_url_blocks_private_by_default():
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            core.learn(conn, "maria", "http://127.0.0.1:1/x", sync_immediately=True)
    finally:
        conn.close()


def test_url_rejects_non_http_scheme():
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            core.learn(conn, "maria", "ftp://example.com/x", sync_immediately=True)
    finally:
        conn.close()


def test_url_rejects_missing_host():
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            core.learn(conn, "maria", "http:///sem-host", sync_immediately=True)
    finally:
        conn.close()


def test_url_fetch_and_sync(monkeypatch):
    monkeypatch.setattr(core, "_fetch_url", lambda url: "conteudo vindo da url")
    conn = _conn()
    try:
        result = core.learn(conn, "maria", "https://example.com/doc", sync_immediately=True)
        assert result["action"] == "learn"
        assert result["url"] == "https://example.com/doc"
        assert core.count_pages(conn, "maria") == 1
    finally:
        conn.close()


def test_url_dry_run_fetches_without_writing(monkeypatch):
    monkeypatch.setattr(core, "_fetch_url", lambda url: "conteudo seco")
    conn = _conn()
    try:
        result = core.learn(conn, "maria", "https://example.com/doc", dry_run=True)
        assert result["action"] == "learn (dry-run)"
        assert core.count_pages(conn, "maria") == 0
    finally:
        conn.close()
