"""Plugin Hermes nativo — tool `brain` (spec §5: conhecimento admin-only).

A tool expõe as operações do brain.db para o agente (via gateway/mensageria).
Ações que absorvem conhecimento (`remember`, `learn`, `global_learn`) exigem um
`admin_id` autorizado (lista em ~/.hermes/brain/admins.json); ações de leitura
(`recall`, `check`, `jobs`, `synthesize`) são livres.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from brain_tool import brain_tool as core
from brain_tool.brain import is_admin

_ADMIN_ACTIONS = {"remember", "learn", "global_learn"}

BRAIN_TOOL_SCHEMA = {
    "name": "brain",
    "description": (
        "Gerencia a base de conhecimento (brain.db) de um expert: recuperar/"
        "adicionar conhecimento, aprender arquivos, verificar integridade e "
        "listar jobs. Ações que absorvem conhecimento (remember, learn, "
        "global_learn) exigem `admin_id` autorizado."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "remember", "recall", "check", "learn",
                    "global_learn", "jobs", "synthesize",
                ],
            },
            "expert": {"type": "string", "description": "Expert alvo (default: profile ativo)"},
            "tipo": {"type": "string", "description": "memory, fact, entity, procedure, policy, system"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "path": {"type": "string", "description": "Caminho do arquivo/diretório para learn"},
            "search": {"type": "string"},
            "limit": {"type": "integer"},
            "sync": {"type": "boolean", "description": "Sync imediatamente após learn"},
            "admin_id": {"type": "string", "description": "Identificador do remetente (wa:..., tg:...) p/ validação admin"},
        },
        "required": ["action"],
    },
}


def _ok(**payload) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str)


def _merge_result(action: str, res: dict) -> str:
    """Funde o resultado do domínio, definindo `action` no topo (sem conflito)."""
    payload = dict(res)
    payload.pop("action", None)
    return _ok(action=action, **payload)


def _err(action: str, message: str, code: str = "error") -> str:
    return json.dumps(
        {"ok": False, "action": action, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )


def _require_admin(action: str, admin_id: Optional[str]) -> Optional[str]:
    if not admin_id or not is_admin(admin_id):
        return _err(action, "Comando restrito a administradores.", "forbidden")
    return None


def _handle_brain(ctx, args: dict, **kw) -> str:
    action = (args.get("action") or "").strip()
    expert = args.get("expert") or getattr(ctx, "profile_name", None) or "brain"
    admin_id = args.get("admin_id")

    try:
        if action in _ADMIN_ACTIONS:
            denied = _require_admin(action, admin_id)
            if denied:
                return denied

        if action == "remember":
            if not args.get("content"):
                return _err(action, "content é obrigatório para remember.", "validation")
            conn = core.get_session(expert=expert)
            res = core.remember(conn, expert, args.get("tipo") or "memory",
                                args.get("title"), args["content"])
            conn.close()
            return _merge_result("remember", res)

        if action == "recall":
            conn = core.get_session(expert=expert)
            rows = core.recall(conn, expert, args.get("search"), int(args.get("limit") or 10))
            conn.close()
            return _ok(action="recall", count=len(rows), results=rows)

        if action == "check":
            conn = core.get_session(expert=expert)
            res = core.check(conn, expert)
            conn.close()
            return _ok(action="check", **res)

        if action == "jobs":
            conn = core.get_session(expert=expert)
            rows = core.list_jobs(conn, expert, None, int(args.get("limit") or 20))
            conn.close()
            return _ok(action="jobs", count=len(rows), results=rows)

        if action == "synthesize":
            conn = core.get_session(expert=expert)
            res = core.synthesize(conn, expert)
            conn.close()
            return _ok(action="synthesize", **res)

        if action == "learn":
            if not args.get("path"):
                return _err(action, "path é obrigatório para learn.", "validation")
            conn = core.get_session(expert=expert)
            res = core.learn(conn, expert, args["path"], sync_immediately=bool(args.get("sync")))
            conn.close()
            return _merge_result("learn", res)

        if action == "global_learn":
            conn = core.get_session(global_brain=True)
            if args.get("path"):
                res = core.learn(conn, "global", args["path"], sync_immediately=bool(args.get("sync")))
            elif args.get("content"):
                res = core.remember(conn, "global", "global_policy",
                                    args.get("title"), args["content"])
            else:
                conn.close()
                return _err(action, "informe path ou content.", "validation")
            conn.close()
            return _merge_result("global_learn", res)

        return _err(action, f"Ação desconhecida: {action!r}", "validation")
    except Exception as e:  # noqa: BLE001 — tool handler deve nunca vazar traceback
        return _err(action, f"{type(e).__name__}: {e}")


def register(ctx) -> None:
    ctx.register_tool(
        name="brain",
        toolset="brain",
        schema=BRAIN_TOOL_SCHEMA,
        handler=lambda args, **kw: _handle_brain(ctx, args, **kw),
        description="Base de conhecimento (brain.db) — absorção admin-only.",
    )
