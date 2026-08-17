from __future__ import annotations

from pathlib import Path

from brain_tool import brain as manager


def test_admin_list_and_group_helpers():
    admins = {
        "admins": ["wa:+5511999999999", "cli:root"],
        "groups": {"adm": ["wa:+5511999999999"]},
    }

    assert manager.is_admin("wa:+5511999999999", admins) is True
    assert manager.is_admin("cli:root", admins) is True
    assert manager.is_admin("wa:+0000000000000", admins) is False

    assert manager.is_group_member("wa:+5511999999999", "adm", admins) is True
    assert manager.is_group_member("wa:+0000000000000", "adm", admins) is False
    assert manager.is_group_member("wa:+5511999999999", "outro", admins) is False


def test_get_expert_names_scans_root_ignoring_reserved(tmp_path: Path, monkeypatch):
    root = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_ROOT", str(root))

    (root / "maria").mkdir(parents=True)
    (root / "jose").mkdir(parents=True)
    (root / "global").mkdir(parents=True)    # reservado — não aparece
    (root / "backups").mkdir(parents=True)   # reservado — não aparece
    (root / ".uploads").mkdir(parents=True)  # dotfile — não aparece

    assert manager.get_expert_names() == ["jose", "maria"]


def test_get_expert_names_empty_when_no_experts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    assert manager.get_expert_names() == []
