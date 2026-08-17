"""Plugin Hermes nativo — tool `brain` (spec §5: conhecimento admin-only).

A tool expõe as operações do brain.db para o agente (via gateway/mensageria).
Ações que absorvem/mutam conhecimento (`remember`, `learn`, `global_learn`,
`approve`, `merge`, `promote`, `rollback`) exigem um `admin_id` autorizado
(lista em ~/.hermes/brain/admins.json + papéis RBAC); ações de leitura
(`recall`, `check`, `jobs`, `synthesize`, `verify`, `log`, `diff`) são livres.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from brain_tool import brain_tool as core
from brain_tool import checkpoints
from brain_tool.db import get_session

_ADMIN_ACTIONS = {
    "remember", "learn", "global_learn",
    "approve", "merge", "promote", "rollback",
}

BRAIN_TOOL_SCHEMA = {
    "name": "brain",
    "description": (
        "Gerencia a base de conhecimento (brain.db) de um expert: recuperar/"
        "adicionar conhecimento, aprender arquivos, verificar integridade, "
        "listar jobs e executar governança (diff/log/approve/merge/promote/"
        "rollback). Ações que absorvem ou mutam conhecimento exigem `admin_id` "
        "autorizado (com papel adequado)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "remember", "recall", "check", "learn",
                    "global_learn", "jobs", "synthesize",
                    "verify", "log", "diff", "approve", "merge",
                    "promote", "rollback",
                ],
            },
            "expert": {"type": "string", "description": "Expert alvo (default: profile ativo)"},
            "scope": {"type": "string", "description": "Scope de governança (global ou expert/<nome>); default: derivado de expert"},
            "tipo": {"type": "string", "description": "memory, fact, entity, procedure, policy, system"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "path": {"type": "string", "description": "Caminho do arquivo/diretório para learn"},
            "search": {"type": "string"},
            "limit": {"type": "integer"},
            "sync": {"type": "boolean", "description": "Sync imediatamente após learn"},
            "admin_id": {"type": "string", "description": "Identificador do remetente (wa:..., tg:...) p/ validação admin"},
            "candidate": {"type": "string", "description": "Commit/job_id candidato (approve/merge)"},
            "to": {"type": "string", "description": "Commit de destino (rollback)"},
            "from_scope": {"type": "string", "description": "Scope origem (promote)"},
            "to_scope": {"type": "string", "description": "Scope destino (promote)"},
            "objects": {"type": "array", "items": {"type": "string"}, "description": "Hashes específicos a promover"},
            "from_commit": {"type": "string"},
            "to_commit": {"type": "string"},
            "policy": {"type": "string"},
            "note": {"type": "string"},
            "message": {"type": "string"},
            "reject": {"type": "boolean", "description": "Registra rejeição em vez de aprovação"},
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


def _require_admin_id(action: str, admin_id: Optional[str]) -> Optional[str]:
    """Exige a presença de `admin_id`; a validação real é feita pelo core."""
    if not admin_id:
        return _err(action, "Comando restrito a administradores.", "forbidden")
    return None


def _scope_session(scope: str):
    """Abre uma Session para um scope (`global` ou `expert/<nome>`)."""
    if scope == "global":
        return get_session(global_brain=True)
    return get_session(expert=scope.removeprefix("expert/"))


def _handle_brain(ctx, args: dict, **kw) -> str:
    action = (args.get("action") or "").strip()
    expert = args.get("expert") or getattr(ctx, "profile_name", None) or "brain"
    admin_id = args.get("admin_id")
    scope = args.get("scope") or checkpoints.scope_for(expert)

    try:
        if action in _ADMIN_ACTIONS:
            denied = _require_admin_id(action, admin_id)
            if denied:
                return denied
        actor = str(admin_id) if admin_id else "cli:local"

        if action == "remember":
            if not args.get("content"):
                return _err(action, "content é obrigatório para remember.", "validation")
            conn = core.get_session(expert=expert)
            res = core.remember(conn, expert, args.get("tipo") or "memory",
                                args.get("title"), args["content"], actor=actor)
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
            res = core.learn(conn, expert, args["path"], sync_immediately=bool(args.get("sync")),
                             actor=actor)
            conn.close()
            return _merge_result("learn", res)

        if action == "global_learn":
            conn = core.get_session(global_brain=True)
            if args.get("path"):
                res = core.learn(conn, "global", args["path"], sync_immediately=bool(args.get("sync")),
                                 actor=actor)
            elif args.get("content"):
                res = core.remember(conn, "global", "global_policy",
                                    args.get("title"), args["content"], actor=actor)
            else:
                conn.close()
                return _err(action, "informe path ou content.", "validation")
            conn.close()
            return _merge_result("global_learn", res)

        # --- Governança (checkpoints assinados) ---

        if action == "verify":
            conn = _scope_session(scope)
            res = checkpoints.verify_scope(conn, scope)
            conn.close()
            return _ok(action="verify", **res)

        if action == "log":
            conn = _scope_session(scope)
            res = checkpoints.history(conn, scope)
            conn.close()
            return _ok(action="log", count=len(res), commits=res)

        if action == "diff":
            conn = _scope_session(scope)
            res = checkpoints.diff(conn, scope,
                                   from_commit=args.get("from_commit"),
                                   to_commit=args.get("to_commit"))
            conn.close()
            return _ok(action="diff", count=len(res), changes=res)

        if action == "approve":
            if not args.get("candidate"):
                return _err(action, "candidate é obrigatório para approve.", "validation")
            conn = _scope_session(scope)
            decision = "reject" if args.get("reject") else "approve"
            aid = checkpoints.approve(conn, scope, args["candidate"],
                                      approver=actor,
                                      policy=args.get("policy") or "manual",
                                      decision=decision,
                                      justification=args.get("note"))
            conn.close()
            return _ok(action="approve", approval_id=aid, decision=decision)

        if action == "merge":
            if not args.get("candidate"):
                return _err(action, "candidate é obrigatório para merge.", "validation")
            conn = _scope_session(scope)
            res = checkpoints.merge_candidate(conn, scope, args["candidate"],
                                              author=actor)
            conn.close()
            return _merge_result("merge", res)

        if action == "promote":
            from_scope = args.get("from_scope") or scope
            to_scope = args.get("to_scope")
            if not to_scope:
                return _err(action, "to_scope é obrigatório para promote.", "validation")
            if from_scope == to_scope:
                return _err(action, "origem e destino devem ser diferentes.", "validation")
            objects = args.get("objects") or None
            src = _scope_session(from_scope)
            try:
                entries, missing = checkpoints.read_scope_entries(src, from_scope, objects)
            finally:
                src.close()
            if not entries:
                return _err(action, "nenhum objeto a promover.", "validation")
            dst = _scope_session(to_scope)
            try:
                res = checkpoints.promote_into(dst, to_scope, entries, from_scope,
                                               actor, message=args.get("message"))
            finally:
                dst.close()
            if not res.get("ok"):
                return _err(action, res.get("error") or "promote falhou.")
            return _ok(action="promote", missing=missing, **res)

        if action == "rollback":
            if not args.get("to"):
                return _err(action, "to é obrigatório para rollback.", "validation")
            conn = _scope_session(scope)
            res = checkpoints.rollback(conn, scope, args["to"], author=actor)
            conn.close()
            return _merge_result("rollback", res)

        return _err(action, f"Ação desconhecida: {action!r}", "validation")
    except PermissionError as e:
        return _err(action, str(e) or "Comando restrito a administradores.", "forbidden")
    except Exception as e:  # noqa: BLE001 — tool handler deve nunca vazar traceback
        return _err(action, f"{type(e).__name__}: {e}")


def register(ctx) -> None:
    ctx.register_tool(
        name="brain",
        toolset="brain",
        schema=BRAIN_TOOL_SCHEMA,
        handler=lambda args, **kw: _handle_brain(ctx, args, **kw),
        description="Base de conhecimento (brain.db) — absorção admin-only + governança.",
    )
