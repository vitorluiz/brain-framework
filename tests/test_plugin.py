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
