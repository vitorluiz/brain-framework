from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from brain_tool import dashboard


@pytest.fixture()
def client():
    app = dashboard.create_app(secret="test-secret", token="test-token")
    creds = {
        "username": f"user-{secrets.token_hex(3)}",
        "password": secrets.token_urlsafe(12),
    }
    dashboard.add_dashboard_user(creds["username"], creds["password"])
    with TestClient(app) as c:
        c.test_creds = creds
        yield c


def _login(client) -> None:
    creds = client.test_creds
    r = client.post("/login", data={"username": creds["username"], "password": creds["password"]})
    assert r.status_code == 200, r.text
    assert r.json()["ok"]


# --- autenticação (spec §7: dashboard é admin-only) -------------------------

def test_routes_require_login(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/experts").status_code == 401
    assert client.get("/api/jobs", params={"expert": "maria"}).status_code == 401
    assert client.get("/api/check", params={"expert": "maria"}).status_code == 401
    assert client.post("/api/learn", data={"expert": "maria"}).status_code == 401


def test_index_is_public_login_page(client):
    assert client.get("/").status_code == 200


def test_login_bad_password(client):
    creds = client.test_creds
    r = client.post("/login", data={"username": creds["username"], "password": "wrong-pass"})
    assert r.status_code == 401
    assert r.json()["ok"] is False


def test_login_and_me(client):
    _login(client)
    creds = client.test_creds
    assert client.get("/api/me").json()["user"] == creds["username"]


# --- gestão de usuários -----------------------------------------------------

def test_user_management_roundtrip(client):
    dashboard.add_dashboard_user("vitor", "abc")
    assert "vitor" in dashboard.list_dashboard_users()
    assert dashboard.authenticate("vitor", "abc")
    assert not dashboard.authenticate("vitor", "errada")
    assert dashboard.remove_dashboard_user("vitor")
    assert "vitor" not in dashboard.list_dashboard_users()


def test_password_not_stored_plaintext(client):
    plain = f"test-{secrets.token_hex(8)}"
    dashboard.add_dashboard_user("u", plain)
    raw = dashboard._admins_file().read_text(encoding="utf-8")
    assert plain not in raw
    assert "pbkdf2_sha256" in raw


# --- learn / jobs / check ---------------------------------------------------

def test_experts_includes_global(client):
    _login(client)
    names = [e["name"] for e in client.get("/api/experts").json()["experts"]]
    assert names[0] == "global"


def test_learn_upload_then_jobs(client):
    _login(client)
    r = client.post(
        "/api/learn",
        data={"expert": "maria", "sync_flag": "true"},
        files={"files": ("produto.txt", b"Produto Z: especificacao tecnica", "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["target"] == "maria"

    jobs = client.get("/api/jobs", params={"expert": "maria"}).json()["jobs"]
    assert any(j["command"] == "learn" and j["status"] == "completed" for j in jobs)
    assert any("asset://" in (j.get("metadata") or "") for j in jobs)


def test_learn_path(client):
    _login(client)
    src = dashboard.get_brain_root() / "doc.md"
    src.write_text("# Doc\nconteudo de teste\n")
    r = client.post(
        "/api/learn", data={"expert": "maria", "sync_flag": "true", "path": str(src)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"]


def test_learn_global(client):
    _login(client)
    r = client.post(
        "/api/learn",
        data={"expert": "global", "global_brain": "true", "sync_flag": "true"},
        files={"files": ("global.txt", b"Horario: 8h-18h", "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["target"] == "global"
    jobs = client.get(
        "/api/jobs", params={"expert": "global", "global_brain": "true"}
    ).json()["jobs"]
    assert any(j["status"] == "completed" for j in jobs)


def test_learn_requires_input(client):
    _login(client)
    r = client.post("/api/learn", data={"expert": "maria"})
    assert r.status_code == 400


def test_learn_rejects_invalid_expert(client):
    _login(client)
    r = client.post("/api/learn", data={"expert": "bad/name", "path": "/tmp/x"})
    assert r.status_code == 400


def test_check_endpoint(client):
    _login(client)
    r = client.get("/api/check", params={"expert": "maria"})
    assert r.status_code == 200
    assert r.json()["integrity"] == "ok"


# --- hardening de segurança -------------------------------------------------

def test_login_cross_origin_rejected(client):
    creds = client.test_creds
    r = client.post(
        "/login",
        headers={"Origin": "http://evil.com"},
        data={"username": creds["username"], "password": creds["password"]},
    )
    assert r.status_code == 403


def test_login_lockout_after_failures(client):
    creds = client.test_creds
    for _ in range(5):
        r = client.post("/login", data={"username": creds["username"], "password": "wrong-pass"})
        assert r.status_code == 401
    # 6ª tentativa, mesmo com a senha correta, é bloqueada pelo lockout
    r = client.post("/login", data={"username": creds["username"], "password": creds["password"]})
    assert r.status_code == 429


def test_upload_size_limit(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(dashboard, "MAX_UPLOAD_BYTES", 10)
    r = client.post(
        "/api/learn",
        data={"expert": "maria", "sync_flag": "true"},
        files={"files": ("big.txt", b"x" * 100, "text/plain")},
    )
    assert r.status_code == 413


def test_audit_log_written(client):
    _login(client)
    path = dashboard.get_brain_root() / "audit.log"
    assert path.exists()
    assert '"event": "login_success"' in path.read_text(encoding="utf-8")


def test_secret_persisted_across_apps():
    s1 = dashboard._load_or_create_secret()
    s2 = dashboard._load_or_create_secret()
    assert s1 == s2
    assert (dashboard.get_brain_root() / ".dashboard_secret").exists()


# --- autenticação por token (LAN / single-admin) -----------------------------

def test_token_query_login(client):
    r = client.get("/?token=test-token", follow_redirects=False)
    assert r.status_code == 302
    assert client.get("/api/me").json()["user"] == "admin"


def test_token_bearer_auth(client):
    r = client.get("/api/me", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json()["user"] == "admin"


def test_token_invalid_rejected(client):
    r = client.get("/api/me", headers={"Authorization": "Bearer token-errado"})
    assert r.status_code == 401


def test_token_generated_and_persisted():
    t1 = dashboard._load_or_create_token()
    t2 = dashboard._load_or_create_token()
    assert t1 == t2
    assert (dashboard.get_brain_root() / ".dashboard_token").exists()


# --- learn por URL -----------------------------------------------------------

def test_learn_url_endpoint(client, monkeypatch):
    _login(client)
    from brain_tool import brain_tool as core

    monkeypatch.setattr(core, "_fetch_url", lambda url: "conteudo da url")
    r = client.post(
        "/api/learn",
        data={"expert": "maria", "sync_flag": "true", "url": "https://example.com/x"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["target"] == "maria"
    jobs = client.get("/api/jobs", params={"expert": "maria"}).json()["jobs"]
    assert any(j["status"] == "completed" for j in jobs)


def test_learn_url_rejected_private(client):
    _login(client)
    # sem monkeypatch: URL para loopback é bloqueada (anti-SSRF) → 400
    r = client.post("/api/learn", data={"expert": "maria", "url": "http://127.0.0.1:1/x"})
    assert r.status_code == 400


# --- status dos serviços -----------------------------------------------------

def test_status_endpoint(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(dashboard, "_redis_status", lambda: {"ok": True, "detail": "pong"})
    monkeypatch.setattr(dashboard, "_celery_status", lambda: {"ok": True, "detail": "1 worker"})
    monkeypatch.setattr(dashboard, "_docker_status", lambda: {"ok": True, "detail": "2 contêineres"})
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"redis", "celery", "docker"}
    for k in ("redis", "celery", "docker"):
        assert data[k]["ok"] is True
        assert "detail" in data[k]


def test_status_endpoint_requires_auth(client):
    assert client.get("/api/status").status_code == 401


# --- login por token (retomada de sessão) ------------------------------------

def test_login_token_valid(client):
    r = client.post("/login-token", data={"token": "test-token"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.get("/api/me").json()["user"] == "admin"


def test_login_token_invalid(client):
    r = client.post("/login-token", data={"token": "token-errado"})
    assert r.status_code == 401


def test_get_access_token_returns_persisted():
    assert dashboard.get_access_token() == dashboard._load_or_create_token()


def test_rotate_access_token_replaces_persisted_token():
    previous = dashboard._load_or_create_token()
    rotated = dashboard.rotate_access_token()

    assert rotated != previous
    assert dashboard._load_or_create_token() == rotated


def test_rotated_token_is_used_by_running_app():
    previous = dashboard._load_or_create_token()
    app = dashboard.create_app(secret="test-secret")
    with TestClient(app) as running_client:
        assert dashboard.rotate_access_token() != previous
        assert running_client.post("/login-token", data={"token": previous}).status_code == 401
        assert running_client.post(
            "/login-token", data={"token": dashboard.get_access_token()}
        ).status_code == 200


def test_serve_starts_a_new_token_session(monkeypatch):
    previous = dashboard._load_or_create_token()
    monkeypatch.setattr(dashboard, "_run_server", lambda host, port: 0)

    assert dashboard.serve(foreground=True) == 0
    assert dashboard._load_or_create_token() != previous


# --- background (detached) ---------------------------------------------------

def test_dashboard_status_not_running():
    assert dashboard.dashboard_status() == {"running": False}


def test_stop_dashboard_no_pid():
    assert dashboard.stop_dashboard() is False


def test_stop_dashboard_discards_access_token(monkeypatch):
    dashboard._load_or_create_token()
    pid_path = dashboard._pid_file()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("123", encoding="utf-8")
    monkeypatch.setattr(dashboard.os, "kill", lambda pid, sig: None)

    assert dashboard.stop_dashboard() is True
    assert not (dashboard.get_brain_root() / ".dashboard_token").exists()






