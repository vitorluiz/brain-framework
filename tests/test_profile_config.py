"""Testes dos comandos `brain soul` (SOUL.md) e `brain model` (brain/LLM).

Cobrem a resolução case-insensitive do profile Hermes, escrita/leitura do
SOUL.md e a delegação do LLM a `hermes config set/get` (mocada).
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import brain_tool.brain as brain


@pytest.fixture
def profiles_root(tmp_path, monkeypatch):
    """Profile Hermes isolado: <HERMES_HOME>/profiles/alentobot."""
    root = tmp_path / "hermes" / "profiles"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    return root


def _profile_dir(profiles_root, name="AlentoBot"):
    d = profiles_root / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _args(**kw):
    return SimpleNamespace(**kw)


# --- SOUL.md ---


def test_soul_show_existing(profiles_root, capsys):
    d = _profile_dir(profiles_root)
    (d / "SOUL.md").write_text("Sou o AlentoBot.\n", encoding="utf-8")

    assert brain.cmd_soul(_args(name="AlentoBot")) == 0
    assert "Sou o AlentoBot." in capsys.readouterr().out


def test_soul_set_creates_file(profiles_root, capsys):
    d = _profile_dir(profiles_root)
    text = "Persona do AlentoBot: atende clientes AlentoSoft."

    assert brain.cmd_soul(_args(name="AlentoBot", soul_text=text,
                                file=None, edit=False)) == 0
    content = (d / "SOUL.md").read_text(encoding="utf-8")
    assert content == text + "\n"


def test_soul_set_from_file(profiles_root, capsys, tmp_path):
    d = _profile_dir(profiles_root)
    src = tmp_path / "persona.md"
    src.write_text("Persona longa\ncom duas linhas\n", encoding="utf-8")

    assert brain.cmd_soul(_args(name="AlentoBot", file=str(src),
                                soul_text=None, edit=False)) == 0
    assert (d / "SOUL.md").read_text(encoding="utf-8") == "Persona longa\ncom duas linhas\n"


def test_soul_missing_file(profiles_root, capsys):
    _profile_dir(profiles_root)
    rc = brain.cmd_soul(_args(name="AlentoBot", file="/nao/existe.md",
                              soul_text=None, edit=False))
    assert rc == 1
    assert "nao encontrado" in capsys.readouterr().err


def test_soul_profile_not_found(capsys):
    rc = brain.cmd_soul(_args(name="Inexistente", file=None, soul_text=None, edit=False))
    assert rc == 1
    assert "nao encontrado" in capsys.readouterr().err


def test_profiles_root_when_hermes_home_inside_profile(monkeypatch, tmp_path):
    """HERMES_HOME apontando para um profile ativo sobe para a pasta profiles."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes" / "profiles" / "shielddev"))
    assert brain.hermes_profiles_root() == str(tmp_path / "hermes" / "profiles")


def test_soul_case_insensitive_resolution(profiles_root, capsys):
    # profile Hermes é lowercase 'alentobot', expert é 'AlentoBot'
    d = _profile_dir(profiles_root, name="alentobot")
    (d / "SOUL.md").write_text("ok\n", encoding="utf-8")
    assert brain.cmd_soul(_args(name="AlentoBot", file=None,
                                soul_text=None, edit=False)) == 0
    assert "ok" in capsys.readouterr().out


def test_soul_edit(profiles_root, capsys, monkeypatch):
    d = _profile_dir(profiles_root)
    (d / "SOUL.md").write_text("antes\n", encoding="utf-8")
    monkeypatch.setenv("EDITOR", "true")  # `true` sai 0 sem alterar

    assert brain.cmd_soul(_args(name="AlentoBot", file=None, soul_text=None,
                                edit=True)) == 0
    assert "salvo" in capsys.readouterr().out


# --- brain model (LLM) ---


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _stub_hermes(monkeypatch, values):
    """Substitui `_run_hermes` por um stub que responde `hermes config get`."""

    def fake(profile_dir, *args, timeout=30):
        if args[0] == "config" and args[1] == "get":
            key = args[2]
            return _FakeProc(stdout=values.get(key, ""),
                             stderr="" if key in values else f"Config key not set: {key}")
        if args[0] == "config" and args[1] == "set":
            key, val = args[2], args[3]
            values[key] = val
            return _FakeProc(stdout=f"{key}={val}")
        return _FakeProc(stderr="unknown", returncode=1)

    monkeypatch.setattr(brain, "_run_hermes", fake)
    return values


def test_model_show_unset(profiles_root, capsys, monkeypatch):
    _profile_dir(profiles_root)
    _stub_hermes(monkeypatch, {})
    assert brain.cmd_model(_args(name="AlentoBot", model=None,
                                 provider=None, base_url=None)) == 0
    out = capsys.readouterr().out
    assert "(nao definido)" in out


def test_model_show_set(profiles_root, capsys, monkeypatch):
    _profile_dir(profiles_root)
    _stub_hermes(monkeypatch, {
        "model.default": "deepseek-v4-pro",
        "model.provider": "opencode-go",
    })
    assert brain.cmd_model(_args(name="AlentoBot", model=None,
                                 provider=None, base_url=None)) == 0
    out = capsys.readouterr().out
    assert "deepseek-v4-pro" in out
    assert "opencode-go" in out


def test_model_set_delegates_to_hermes(profiles_root, capsys, monkeypatch):
    d = _profile_dir(profiles_root)
    values = _stub_hermes(monkeypatch, {})

    rc = brain.cmd_model(_args(name="AlentoBot", model="gpt-5.6-sol",
                               provider="openai-codex",
                               base_url="https://chatgpt.com/backend-api/codex"))
    assert rc == 0
    assert values["model.default"] == "gpt-5.6-sol"
    assert values["model.provider"] == "openai-codex"
    assert values["model.base_url"] == "https://chatgpt.com/backend-api/codex"


def test_model_set_propagates_hermes_error(profiles_root, capsys, monkeypatch):
    _profile_dir(profiles_root)

    def fake(profile_dir, *args, timeout=30):
        return _FakeProc(stderr="hermes: comando falhou", returncode=1)

    monkeypatch.setattr(brain, "_run_hermes", fake)
    rc = brain.cmd_model(_args(name="AlentoBot", model="x", provider=None, base_url=None))
    assert rc == 1
    assert "hermes config set" in capsys.readouterr().err


def test_model_profile_not_found(capsys):
    rc = brain.cmd_model(_args(name="Inexistente", model="x", provider=None, base_url=None))
    assert rc == 1
    assert "nao encontrado" in capsys.readouterr().err


def test_model_sets_hermes_home_env(profiles_root, monkeypatch):
    """Garante que `_run_hermes` aponta HERMES_HOME para o profile."""
    d = _profile_dir(profiles_root, name="alentobot")
    calls = []

    def fake(profile_dir, *args, timeout=30):
        calls.append((profile_dir, args))
        if args[0] == "config" and args[1] == "get":
            return _FakeProc(stderr="Config key not set: x")
        return _FakeProc(stdout="ok")

    monkeypatch.setattr(brain, "_run_hermes", fake)
    brain.cmd_model(_args(name="AlentoBot", model="m", provider=None, base_url=None))

    set_calls = [(pd, a) for pd, a in calls if a[:2] == ("config", "set")]
    assert set_calls == [(str(d), ("config", "set", "model.default", "m"))]


# --- fallback (fallback_providers) ---


def test_model_set_fallback_writes_config(profiles_root, capsys, monkeypatch):
    import yaml

    d = _profile_dir(profiles_root)
    _stub_hermes(monkeypatch, {})

    rc = brain.cmd_model(_args(name="AlentoBot", model="hermes3:3b", provider="ollama",
                               base_url=None, fallback="deepseek-v4-pro",
                               fallback_provider="opencode-go"))
    assert rc == 0

    cfg = yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["fallback_providers"] == [{"provider": "opencode-go",
                                          "model": "deepseek-v4-pro"}]
    assert (d / "config.yaml").stat().st_mode & 0o777 == 0o600


def test_model_show_fallback(profiles_root, capsys, monkeypatch):
    import yaml

    d = _profile_dir(profiles_root)
    (d / "config.yaml").write_text(
        yaml.safe_dump({"fallback_providers": [
            {"provider": "opencode-go", "model": "deepseek-v4-pro"}]}),
        encoding="utf-8",
    )
    _stub_hermes(monkeypatch, {"model.default": "hermes3:3b", "model.provider": "ollama"})

    assert brain.cmd_model(_args(name="AlentoBot", model=None, provider=None,
                                 base_url=None, fallback=None,
                                 fallback_provider=None)) == 0
    out = capsys.readouterr().out
    assert "fallback 1: deepseek-v4-pro (via opencode-go)" in out


def test_model_fallback_requires_provider(profiles_root, capsys, monkeypatch):
    _profile_dir(profiles_root)
    _stub_hermes(monkeypatch, {})

    rc = brain.cmd_model(_args(name="AlentoBot", model=None, provider=None, base_url=None,
                               fallback="x", fallback_provider=None))
    assert rc == 1
    assert "juntos" in capsys.readouterr().err


def test_model_fallback_backup_created(profiles_root, capsys, monkeypatch):
    d = _profile_dir(profiles_root)
    (d / "config.yaml").write_text("model:\n  default: antigo\n", encoding="utf-8")
    _stub_hermes(monkeypatch, {})

    brain.cmd_model(_args(name="AlentoBot", model=None, provider=None, base_url=None,
                          fallback="deepseek-v4-pro", fallback_provider="opencode-go"))
    assert (d / "config.yaml.bak").exists()
    assert "model:" in (d / "config.yaml.bak").read_text(encoding="utf-8")
