"""Autorização admin (spec §5) — admins.json + helpers de checagem.

O core (`brain_tool`) usa `require_admin(actor)` nas funções de escrita para
exigir autorização explícita quando o chamador não é o CLI local (trusted).
`actor=None` = chamador local de confiança (CLI) — sem checagem. Qualquer
chamador remoto/atribuído (plugin, gateway, dashboard) DEVE passar um `actor`
identificador, validado contra `admins.json`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .db import get_brain_root


def admin_config_file() -> Path:
    return get_brain_root() / "admins.json"


def load_admins() -> Dict[str, Any]:
    path = admin_config_file()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"admins": [], "groups": {}}


def save_admins(data: Dict[str, Any]) -> None:
    path = admin_config_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o600)


def is_admin(identifier: Optional[str],
             admins: Optional[Dict[str, Any]] = None) -> bool:
    """True se `identifier` está na lista de administradores (spec §5.3)."""
    if not identifier:
        return False
    data = admins if admins is not None else load_admins()
    return identifier in data.get("admins", [])


def is_group_member(identifier: str, group: str,
                    admins: Optional[Dict[str, Any]] = None) -> bool:
    """True se `identifier` é membro do grupo administrativo (spec §5.3)."""
    data = admins if admins is not None else load_admins()
    return identifier in data.get("groups", {}).get(group, [])


def require_admin(actor: Optional[str]) -> None:
    """Exige que `actor` seja admin, ou levanta PermissionError.

    `actor=None` significa chamador local de confiança (CLI) — sem checagem.
    Chamadores remotos devem passar um identificador; este é validado contra
    `admins.json`.
    """
    if actor is not None and not is_admin(actor):
        raise PermissionError("Comando restrito a administradores.")
