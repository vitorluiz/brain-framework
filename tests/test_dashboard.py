from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brain_tool import dashboard


@pytest.fixture()
def client():
    app = dashboard.create_app(secret="test-secret")
    dashboard.add_dashboard_user("admin", "s3cret")
    with TestClient(app) as c:
        yield c


def _login(client) -> None:
    r = client.post("/login", data={"username": "admin", "password": "s3cret"})
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
    r = client.post("/login", data={"username": "admin", "password": "errada"})
    assert r.status_code == 401
    assert r.json()["ok"] is False


def test_login_and_me(client):
    _login(client)
    assert client.get("/api/me").json()["user"] == "admin"


# --- gestão de usuários -----------------------------------------------------

def test_user_management_roundtrip(client):
    dashboard.add_dashboard_user("vitor", "abc")
    assert "vitor" in dashboard.list_dashboard_users()
    assert dashboard.authenticate("vitor", "abc")
    assert not dashboard.authenticate("vitor", "errada")
    assert dashboard.remove_dashboard_user("vitor")
    assert "vitor" not in dashboard.list_dashboard_users()


def test_password_not_stored_plaintext(client):
    dashboard.add_dashboard_user("u", "senha-secreta-123")
    raw = dashboard._admins_file().read_text(encoding="utf-8")
    assert "senha-secreta-123" not in raw
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
