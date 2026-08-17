"""Testes dos endpoints de Profiles (SOUL.md + brain/LLM) do dashboard.

Cobrem listagem, leitura/escrita de SOUL.md, configuração do modelo com
fallback, proteção de auth e anti path-traversal.
"""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from brain_tool import dashboard


@pytest.fixture()
def profile_client(tmp_path, monkeypatch):
    """Dashboard com um profile Hermes isolado (`alentobot`) + expert brain."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))

    profiles = tmp_path / "hermes" / "profiles"
    alentobot = profiles / "alentobot"
    alentobot.mkdir(parents=True)
    (alentobot / "SOUL.md").write_text("Sou o AlentoBot.\n", encoding="utf-8")

    # expert do brain (para _expert_names() listar)
    expert_dir = dashboard.get_brain_root() / "AlentoBot"
    expert_dir.mkdir(parents=True)

    # evita subprocess `hermes config set/get` nos testes
    monkeypatch.setattr(dashboard, "_hermes_config_get", lambda pdir, key: None)
    monkeypatch.setattr(dashboard, "_hermes_config_set", lambda pdir, key, val: [])

    creds = {
        "username": f"user-{secrets.token_hex(3)}",
        "password": secrets.token_urlsafe(12),
    }
    app = dashboard.create_app(secret="test-secret", token="test-token")
    dashboard.add_dashboard_user(creds["username"], creds["password"])
    with TestClient(app) as c:
        c.test_creds = creds
        yield c


def _login(client) -> None:
    creds = client.test_creds
    r = client.post("/login", data={"username": creds["username"], "password": creds["password"]})
    assert r.status_code == 200, r.text


def test_profiles_require_auth(profile_client):
    assert profile_client.get("/api/profiles").status_code == 401
    assert profile_client.get("/api/profiles/alentobot/soul").status_code == 401
    assert profile_client.get("/api/profiles/alentobot/model").status_code == 401


def test_profiles_list(profile_client):
    _login(profile_client)
    data = profile_client.get("/api/profiles").json()
    names = {p["name"] for p in data["profiles"]}
    assert "AlentoBot" in names
    alento = next(p for p in data["profiles"] if p["name"] == "AlentoBot")
    assert alento["hermes_profile"] == "alentobot"
    assert alento["has_soul"] is True


def test_soul_get_and_set(profile_client):
    _login(profile_client)
    assert profile_client.get("/api/profiles/AlentoBot/soul").json()["soul"] \
        == "Sou o AlentoBot.\n"

    r = profile_client.post(
        "/api/profiles/AlentoBot/soul",
        data={"content": "Nova persona do AlentoBot."},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert profile_client.get("/api/profiles/AlentoBot/soul").json()["soul"] \
        == "Nova persona do AlentoBot.\n"


def test_model_get_defaults(profile_client):
    _login(profile_client)
    data = profile_client.get("/api/profiles/AlentoBot/model").json()
    assert data["model"] is None
    assert data["provider"] is None
    assert data["fallback"] == []


def test_model_set_with_fallback(profile_client, tmp_path):
    import yaml

    _login(profile_client)
    r = profile_client.post(
        "/api/profiles/AlentoBot/model",
        data={
            "model": "hermes3:3b",
            "provider": "ollama",
            "fallback_model": "deepseek-v4-pro",
            "fallback_provider": "opencode-go",
        },
    )
    assert r.status_code == 200, r.text

    cfg_path = tmp_path / "hermes" / "profiles" / "alentobot" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert cfg["fallback_providers"] == [{"provider": "opencode-go",
                                          "model": "deepseek-v4-pro"}]


def test_model_fallback_requires_pair(profile_client):
    _login(profile_client)
    r = profile_client.post(
        "/api/profiles/AlentoBot/model",
        data={"fallback_model": "deepseek-v4-pro"},  # sem provider
    )
    assert r.status_code == 400


def test_profile_not_found(profile_client):
    _login(profile_client)
    assert profile_client.get("/api/profiles/Inexistente/soul").status_code == 404
    assert profile_client.get("/api/profiles/Inexistente/model").status_code == 404


def test_profile_path_traversal_blocked(profile_client):
    _login(profile_client)
    # nomes com separador/traversal nunca resolvem um profile
    assert profile_client.get("/api/profiles/../shielddev/soul").status_code == 404
    assert profile_client.get("/api/profiles/a%2Fb/soul").status_code == 404
