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


# --- RBAC (roles) — Fase 4 ----------------------------------------------------

ROLE_ADMIN = "admin"
ROLE_APPROVER = "approver"

# Identificadores "locais de confiança" — o CLI/gestor nativo. Passam qualquer
# checagem (o operador do host é o dono da chave de assinatura). Chamadores
# remotos (plugin/gateway/dashboard) passam um identificador real e são
# validados contra `admins.json` + `roles`.
_LOCAL_TRUSTED = {"cli:local", "cli:root", "cli:migration"}


def role_for(identifier: Optional[str],
             admins: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Papel de um principal. `None` se não reconhecido.

    Ordem de resolução: `roles` (explícito) → lista `admins` (default `admin`).
    """
    if not identifier:
        return None
    data = admins if admins is not None else load_admins()
    roles = data.get("roles", {})
    if identifier in roles:
        return roles[identifier]
    if identifier in data.get("admins", []):
        return ROLE_ADMIN
    return None


def require_role(actor: Optional[str], roles: Any) -> None:
    """Exige que `actor` tenha um dos `roles`, ou levanta PermissionError.

    `actor=None` (ou um identificador local de confiança) = chamador local,
    sem checagem. Chamadores remotos devem ter o papel exigido.
    """
    if actor is None or actor in _LOCAL_TRUSTED:
        return
    actual = role_for(actor)
    if actual not in roles:
        raise PermissionError(
            f"Permissão insuficiente: requer papel {sorted(roles)}, "
            f"atual {actual or 'nenhum'}. Comando restrito a administradores."
        )


def require_admin(actor: Optional[str]) -> None:
    """Exige papel `admin` (escritas no conhecimento)."""
    require_role(actor, {ROLE_ADMIN})


def require_approver(actor: Optional[str]) -> None:
    """Exige papel `admin` ou `approver` (registrar aprovações/rejeições)."""
    require_role(actor, {ROLE_ADMIN, ROLE_APPROVER})
