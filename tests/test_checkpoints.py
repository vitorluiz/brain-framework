from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from brain_tool import brain_tool as core
from brain_tool import checkpoints
from brain_tool import crypto
from brain_tool.models import Page


def _db_path(expert: str = "maria") -> str:
    return core.get_brain_db_path(expert=expert)


def _session(expert: str = "maria"):
    return core.get_db_connection(Path(_db_path(expert)))


# --- crypto ------------------------------------------------------------------

def test_crypto_sign_verify_roundtrip(monkeypatch):
    priv, pub = crypto.generate_keypair()
    monkeypatch.setenv("BRAIN_SIGNING_KEY", priv)
    monkeypatch.setenv("BRAIN_SIGNING_KEY_PUB", pub)
    key = crypto.load_or_create_signing_key()
    public = crypto.load_public_key()
    assert public is not None

    data = b"conteudo qualquer"
    sig = crypto.sign(key, data)
    assert crypto.verify(public, sig, data) is True
    assert crypto.verify(public, sig, b"outro") is False


def test_signing_key_auto_generated_first_use(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAIN_SIGNING_KEY", raising=False)
    monkeypatch.delenv("BRAIN_SIGNING_KEY_PUB", raising=False)
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    key1 = crypto.load_or_create_signing_key()
    key2 = crypto.load_or_create_signing_key()
    assert crypto.load_public_key() is not None
    # mesma chave persistida (não gera outra a cada chamada)
    assert crypto.sign(key1, b"x") == crypto.sign(key2, b"x")
    assert (tmp_path / "brain" / ".signing" / "ed25519.key").exists()


# --- commits / verify --------------------------------------------------------

def test_remember_creates_signed_commit_and_verify(tmp_path):
    conn = _session()
    try:
        r = core.remember(conn, "maria", "memory", titulo="t1", corpo="conteudo")
        assert r["commit"]
        result = checkpoints.verify_scope(conn, "expert/maria")
        assert result["ok"] is True, result["issues"]
        assert result["commits"] == 1
    finally:
        conn.close()


def test_verify_detects_tampered_content(tmp_path):
    bp = _db_path()
    conn = _session()
    try:
        core.remember(conn, "maria", "memory", titulo="t", corpo="original")
    finally:
        conn.close()

    # adultera o conteúdo diretamente no banco (fora do ORM)
    conn = _session()
    try:
        conn.execute(text("UPDATE knowledge_objects SET content = 'hacked'"))
        conn.commit()
    finally:
        conn.close()

    conn = _session()
    try:
        result = checkpoints.verify_scope(conn, "expert/maria")
    finally:
        conn.close()
    assert result["ok"] is False
    assert any("adulterado" in i.get("error", "") for i in result["issues"])


def test_verify_detects_tampered_commit_field(tmp_path):
    conn = _session()
    try:
        core.remember(conn, "maria", "memory", titulo="t", corpo="original")
    finally:
        conn.close()

    conn = _session()
    try:
        conn.execute(text("UPDATE commits SET author = 'evil'"))
        conn.commit()
    finally:
        conn.close()

    conn = _session()
    try:
        result = checkpoints.verify_scope(conn, "expert/maria")
    finally:
        conn.close()
    assert result["ok"] is False
    assert any("commit_hash não confere" in i.get("error", "") for i in result["issues"])


def test_forget_creates_remove_commit_and_empties_tree(tmp_path):
    conn = _session()
    try:
        r = core.remember(conn, "maria", "memory", titulo="t", corpo="c")
        core.forget(conn, "maria", r["id"])
    finally:
        conn.close()

    conn = _session()
    try:
        assert checkpoints.verify_scope(conn, "expert/maria")["ok"] is True
        assert checkpoints._read_tree(conn, "expert/maria") == {}
    finally:
        conn.close()


def test_check_detects_tampered_legacy_page(tmp_path):
    conn = _session()
    try:
        core.remember(conn, "maria", "memory", titulo="t", corpo="original")
    finally:
        conn.close()

    conn = _session()
    try:
        conn.execute(text("UPDATE pages SET corpo = 'hacked'"))
        conn.commit()
    finally:
        conn.close()

    conn = _session()
    try:
        result = core.check(conn, "maria")
    finally:
        conn.close()
    assert result["integrity"] == "tampered"
    assert any(i.get("type") == "hash_mismatch" for i in result["issues"])


def test_genesis_migration_from_legacy_pages(tmp_path):
    conn = _session()
    try:
        corpo = "legado"
        h = core.generate_canonical_hash(corpo)
        conn.add(Page(expert="maria", tipo="memory", titulo="legado",
                      corpo=corpo, hash_canonical=h))
        conn.commit()
    finally:
        conn.close()

    conn = _session()
    try:
        core.remember(conn, "maria", "fact", titulo="novo", corpo="novo-conteudo")
    finally:
        conn.close()

    conn = _session()
    try:
        hist = checkpoints.history(conn, "expert/maria")  # mais novo primeiro
        assert hist[0]["policy"] == "implicit-admin"
        assert hist[-1]["policy"] == "migration-genesis"
        result = checkpoints.verify_scope(conn, "expert/maria")
        assert result["ok"] is True, result["issues"]
    finally:
        conn.close()


def test_history_orders_newest_first(tmp_path):
    conn = _session()
    try:
        core.remember(conn, "maria", "memory", titulo="a", corpo="um")
        core.remember(conn, "maria", "memory", titulo="b", corpo="dois")
    finally:
        conn.close()

    conn = _session()
    try:
        hist = checkpoints.history(conn, "expert/maria")
        tree = checkpoints._read_tree(conn, "expert/maria")
    finally:
        conn.close()
    assert len(hist) == 2
    assert len(tree) == 2


def test_verify_no_key_reports_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    conn = _session()
    try:
        core.remember(conn, "maria", "memory", titulo="t", corpo="c")
    finally:
        conn.close()
    # remove privada E pública (âncora) para forçar o caminho "sem chave"
    import os
    for f in ("ed25519.key", "ed25519.pub"):
        p = tmp_path / "brain" / ".signing" / f
        if p.exists():
            os.remove(p)
    conn = _session()
    try:
        result = checkpoints.verify_scope(conn, "expert/maria")
    finally:
        conn.close()
    assert result["ok"] is False
    assert any("sem chave pública" in i.get("error", "") for i in result["issues"])
