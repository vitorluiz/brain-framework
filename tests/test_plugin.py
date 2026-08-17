from __future__ import annotations

import json
from pathlib import Path

from brain_tool.hermes_plugin import register


class FakeCtx:
    def __init__(self, profile_name="maria"):
        self.profile_name = profile_name
        self.tools = {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)


def _registered_handler(profile_name="maria"):
    ctx = FakeCtx(profile_name)
    register(ctx)
    return ctx.tools["brain"][2]


def _write_admins(root: Path, admins):
    admins_file = root / "brain" / "admins.json"
    admins_file.parent.mkdir(parents=True, exist_ok=True)
    admins_file.write_text(json.dumps({"admins": admins, "groups": {}}))


def test_register_exposes_one_brain_tool():
    ctx = FakeCtx()
    register(ctx)
    assert set(ctx.tools) == {"brain"}
    toolset, schema, handler = ctx.tools["brain"]
    assert toolset == "brain"
    assert schema["name"] == "brain"
    assert "action" in schema["parameters"]["properties"]


def test_recall_is_readonly_no_admin_needed(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    handler = _registered_handler()
    out = json.loads(handler({"action": "recall"}))
    assert out["ok"] is True
    assert out["action"] == "recall"
    assert out["count"] == 0


def test_learn_without_admin_is_forbidden(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    handler = _registered_handler()
    out = json.loads(handler({"action": "learn", "path": "/x/y.txt"}))
    assert out["ok"] is False
    assert out["error"]["code"] == "forbidden"
    assert "administradores" in out["error"]["message"]


def test_learn_with_authorized_admin_succeeds(tmp_path, monkeypatch):
    root = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_ROOT", str(root))
    _write_admins(tmp_path, ["wa:+5511999999999"])

    src = tmp_path / "doc.txt"
    src.write_text("conteudo via mensageria")

    handler = _registered_handler()
    out = json.loads(handler({
        "action": "learn",
        "expert": "maria",
        "path": str(src),
        "sync": True,
        "admin_id": "wa:+5511999999999",
    }))
    assert out["ok"] is True
    assert out["action"] == "learn"

    # o conhecimento ficou disponível (modo síncrono, sem broker)
    recall = json.loads(handler({"action": "recall", "expert": "maria", "search": "mensageria"}))
    assert recall["ok"] is True
    assert recall["count"] == 1


# --- governança exposta no plugin (Fase 4) -----------------------------------

def test_governance_readonly_actions_no_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    handler = _registered_handler()
    for action in ("verify", "log", "diff"):
        out = json.loads(handler({"action": action, "expert": "maria"}))
        # não pode ser "forbidden" (são só leitura)
        assert out.get("error", {}).get("code") != "forbidden", (action, out)


def test_governance_mutating_actions_require_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    handler = _registered_handler()
    cases = [
        {"action": "approve", "candidate": "abc"},
        {"action": "merge", "candidate": "abc"},
        {"action": "rollback", "to": "abc"},
        {"action": "promote", "from_scope": "expert/maria", "to_scope": "global"},
    ]
    for args in cases:
        out = json.loads(handler(args))
        assert out["ok"] is False, args
        assert out["error"]["code"] == "forbidden", args


def test_governance_flow_learn_verify_log_merge_via_plugin(tmp_path, monkeypatch):
    root = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_ROOT", str(root))
    _write_admins(tmp_path, ["wa:admin"])

    src = tmp_path / "doc.txt"
    src.write_text("conteudo governado via plugin")

    handler = _registered_handler()
    admin = "wa:admin"

    # learn sem sync → quarentena (candidato proposto)
    learn = json.loads(handler({
        "action": "learn", "expert": "maria", "path": str(src),
        "sync": False, "admin_id": admin,
    }))
    assert learn["ok"] is True
    assert learn["status"] == "proposed"
    job_id = learn["job_id"]

    # verify / log / diff (somente leitura)
    verify = json.loads(handler({"action": "verify", "expert": "maria"}))
    assert verify["ok"] is True
    diff = json.loads(handler({"action": "diff", "expert": "maria"}))
    assert diff["ok"] is True
    # main ainda vazia (quarentena) — log sem commits na main
    log = json.loads(handler({"action": "log", "expert": "maria"}))
    assert log["ok"] is True and log["count"] == 0

    # merge (publica) — admin_id obrigatório
    merge = json.loads(handler({
        "action": "merge", "expert": "maria", "candidate": job_id, "admin_id": admin,
    }))
    assert merge["ok"] is True, merge

    # agora main tem histórico
    log = json.loads(handler({"action": "log", "expert": "maria"}))
    assert log["ok"] is True and log["count"] >= 1

    recall = json.loads(handler({"action": "recall", "expert": "maria"}))
    assert recall["count"] == 1
