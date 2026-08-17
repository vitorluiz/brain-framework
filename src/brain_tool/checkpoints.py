"""Checkpoints assinados — content-addressed + grafo de commits + Ed25519.

Implementa a Fase 1 de `plan/checkpoints-assinados.md`: objetos imutáveis por
hash, commits assinados, refs e verificação de integridade. As funções recebem
uma `Session` (o chamador abre/commita); `create_commit` só adiciona ao
session — o chamador faz `conn.commit()` para atomicidade.

Semântica (decisões aprovadas §13):
- `remember`/`forget`/`sync` viram commit **auto-aprovado** (`implicit-admin`).
- `learn --sync` = aprovação implícita do admin local.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import delete, select

from . import crypto
from .models import (
    Approval,
    AuditEvent,
    Commit,
    CommitItem,
    KnowledgeObject,
    Page,
    Ref,
)

PIPELINE_VERSION = "1"
POLICY_IMPLICIT = "implicit-admin"
POLICY_MIGRATION = "migration-genesis"


def scope_for(expert: str) -> str:
    return "global" if expert == "global" else f"expert/{expert}"


def expert_for(scope: str) -> str:
    return "global" if scope == "global" else scope.removeprefix("expert/")


def object_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_tree_hash(entries: List[Tuple[str, str, Optional[str]]]) -> str:
    """Hash da árvore: conjunto ordenado de (object_hash, tipo, titulo)."""
    canonical = json.dumps(
        sorted(
            [{"h": h, "t": tipo, "ti": titulo or ""} for h, tipo, titulo in entries],
            key=lambda e: (e["h"], e["t"], e["ti"]),
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_commit_hash(
    parent_hashes: List[str],
    tree_hash: str,
    scope: str,
    author: str,
    pipeline_version: str,
    policy_version: str,
    validation_results: dict,
    timestamp: str,
) -> str:
    """Fórmula da §5.2 (length-prefixed — sem ambiguidade de concatenação)."""
    parts = [
        json.dumps(sorted(parent_hashes), sort_keys=True, separators=(",", ":")),
        tree_hash,
        scope,
        author,
        pipeline_version,
        policy_version,
        json.dumps(validation_results, sort_keys=True, separators=(",", ":")),
        timestamp,
    ]
    canonical = "".join(f"{len(p.encode('utf-8'))}:{p}" for p in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sign_commit(key, commit_hash: str) -> str:
    return crypto.sign(key, bytes.fromhex(commit_hash)).hex()


def verify_commit_signature(pub, commit_hash: str, signature_hex: str) -> bool:
    try:
        return crypto.verify(pub, bytes.fromhex(signature_hex), bytes.fromhex(commit_hash))
    except (ValueError, TypeError):
        return False


# --- internals ---------------------------------------------------------------

def _ensure_object(conn, obj_hash: str, content: str) -> None:
    if conn.get(KnowledgeObject, obj_hash) is None:
        conn.add(KnowledgeObject(hash=obj_hash, content=content))


def _ordered_commits(conn, tip_commit_id: str) -> List[Commit]:
    """Commits do mais antigo ao mais novo, a partir de um tip."""
    ordered: List[Commit] = []
    seen: set = set()
    stack = [tip_commit_id]
    while stack:
        cid = stack.pop()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        c = conn.get(Commit, cid)
        if c is None:
            continue
        ordered.append(c)
        stack.extend(json.loads(c.parent_hashes or "[]"))
    ordered.reverse()
    return ordered


def _tree_at_commit(conn, tip_commit_id: str) -> Dict[str, Tuple[str, Optional[str]]]:
    """Reconstrói a árvore (object_hash -> (tipo, titulo)) a partir de um commit."""
    tree: Dict[str, Tuple[str, Optional[str]]] = {}
    for c in _ordered_commits(conn, tip_commit_id):
        for it in conn.scalars(
            select(CommitItem).where(CommitItem.commit_id == c.id)
        ).all():
            if it.op == "add":
                tree[it.object_hash] = (it.tipo, it.titulo)
            elif it.op == "remove":
                tree.pop(it.object_hash, None)
    return tree


def _read_tree(conn, scope: str) -> Dict[str, Tuple[str, Optional[str]]]:
    """Reconstrói a árvore (object_hash -> (tipo, titulo)) a partir da main."""
    ref = conn.get(Ref, f"{scope}/main")
    if ref is None:
        return {}
    return _tree_at_commit(conn, ref.commit_id)


def _current_parents(conn, scope: str) -> List[str]:
    ref = conn.get(Ref, f"{scope}/main")
    return [ref.commit_id] if ref else []


def resolve_commit_id(conn, scope: str, spec: Optional[str]) -> Optional[str]:
    """Resolve um commit por id completo ou prefixo único dentro do scope."""
    if not spec:
        return None
    c = conn.get(Commit, spec)
    if c is not None and c.scope == scope:
        return c.id
    matches = conn.scalars(
        select(Commit.id).where(Commit.scope == scope, Commit.id.like(f"{spec}%"))
    ).all()
    return matches[0] if len(matches) == 1 else None


def _set_ref(conn, name: str, commit_id: str) -> None:
    ref = conn.get(Ref, name)
    if ref is None:
        conn.add(Ref(name=name, commit_id=commit_id))
    else:
        ref.commit_id = commit_id
        ref.updated_at = datetime.utcnow()


def _audit(conn, event: str, scope: str, actor: str, payload: dict) -> None:
    prev = conn.scalars(
        select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
    ).first()
    prev_hash = prev.hash if prev else None
    raw = json.dumps(
        {
            "prev": prev_hash,
            "event": event,
            "scope": scope,
            "actor": actor,
            "payload": payload,
            "ts": datetime.utcnow().isoformat(),
        },
        sort_keys=True,
    )
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    conn.add(AuditEvent(prev_hash=prev_hash, event=event, scope=scope, actor=actor,
                        payload=json.dumps(payload), hash=h))


# --- API pública -------------------------------------------------------------

def create_commit(
    conn,
    scope: str,
    changes: List[dict],
    author: str,
    policy: str = POLICY_IMPLICIT,
    message: Optional[str] = None,
    validation_results: Optional[dict] = None,
) -> str:
    """Cria o commit assinado e avança a ref main (sem commit — o chamador commita).

    `changes`: lista de dicts `{"op": "add"|"remove", "object_hash", "content"?,
    "tipo"?, "titulo"?}`.
    """
    key = crypto.load_or_create_signing_key()
    parents = _current_parents(conn, scope)
    tree = _read_tree(conn, scope)

    for ch in changes:
        if ch["op"] == "add":
            _ensure_object(conn, ch["object_hash"], ch["content"])
            tree[ch["object_hash"]] = (ch["tipo"], ch.get("titulo"))
        elif ch["op"] == "remove":
            existing = tree.pop(ch["object_hash"], None)
            if existing is not None:
                ch.setdefault("tipo", existing[0])
                ch.setdefault("titulo", existing[1])

    tree_hash = compute_tree_hash(
        [(h, t, ti) for h, (t, ti) in tree.items()]
    )
    ts = datetime.utcnow().isoformat()
    vr = validation_results or {}
    chash = compute_commit_hash(
        parents, tree_hash, scope, author, PIPELINE_VERSION, policy, vr, ts
    )
    signature = sign_commit(key, chash)

    conn.add(Commit(
        id=chash, scope=scope, parent_hashes=json.dumps(parents),
        tree_hash=tree_hash, author=author, pipeline_version=PIPELINE_VERSION,
        policy_version=policy, validation_results=json.dumps(vr),
        message=message, created_at=ts, signature=signature,
        signing_key_id=crypto.key_id(),
    ))
    for ch in changes:
        conn.add(CommitItem(commit_id=chash, op=ch["op"],
                            object_hash=ch["object_hash"],
                            tipo=ch.get("tipo", ""), titulo=ch.get("titulo")))
    _set_ref(conn, f"{scope}/main", chash)
    _audit(conn, "commit", scope, author, {"commit": chash, "changes": len(changes)})
    return chash


def ensure_genesis(conn, expert: str) -> None:
    """Garante um commit genesis para o scope (snapshot das `pages` legadas).

    Comitta em transação própria (migração idempotente). Páginas legadas ganham
    `integrity: unverified` — não há garantia retroativa sobre dados antigos.
    """
    scope = scope_for(expert)
    if conn.get(Ref, f"{scope}/main") is not None:
        return
    pages = conn.scalars(select(Page).where(Page.expert == expert)).all()
    if not pages:
        return  # escopo novo: o primeiro commit já nasce como genesis (parent=[])
    changes = [
        {
            "op": "add",
            "object_hash": p.hash_canonical or object_hash(p.corpo),
            "content": p.corpo,
            "tipo": p.tipo,
            "titulo": p.titulo,
        }
        for p in pages
    ]
    create_commit(
        conn, scope, changes,
        author="cli:migration", policy=POLICY_MIGRATION,
        message="genesis (migração de páginas legadas)",
        validation_results={"migrated_from": "pages", "integrity": "unverified"},
    )
    conn.commit()


def verify_scope(conn, scope: str) -> dict:
    """Verifica cadeia de commits, assinaturas e integridade de conteúdo (§7.4)."""
    pub = crypto.load_public_key()
    if pub is None:
        return {
            "scope": scope, "ok": False, "commits": 0,
            "issues": [{"error": "sem chave pública para verificar "
                                  "(defina BRAIN_SIGNING_KEY_PUB ou gere a chave)"}],
        }
    ref = conn.get(Ref, f"{scope}/main")
    if ref is None:
        return {"scope": scope, "ok": True, "commits": 0, "issues": [],
                "note": "sem main ref (scope nunca commitado)"}

    ordered = _ordered_commits(conn, ref.commit_id)
    issues: List[dict] = []
    tree: Dict[str, Tuple[str, Optional[str]]] = {}
    for c in ordered:
        try:
            parents = json.loads(c.parent_hashes)
            vr = json.loads(c.validation_results)
        except (json.JSONDecodeError, TypeError):
            parents, vr = [], {}

        chash = compute_commit_hash(
            parents, c.tree_hash, c.scope, c.author, c.pipeline_version,
            c.policy_version, vr, c.created_at,
        )
        if chash != c.id:
            issues.append({"commit": c.id, "error": "commit_hash não confere (campos alterados)"})
        if not verify_commit_signature(pub, c.id, c.signature):
            issues.append({"commit": c.id, "error": "assinatura inválida"})

        for it in conn.scalars(
            select(CommitItem).where(CommitItem.commit_id == c.id)
        ).all():
            if it.op == "add":
                obj = conn.get(KnowledgeObject, it.object_hash)
                if obj is None:
                    issues.append({"commit": c.id, "object": it.object_hash,
                                   "error": "objeto ausente"})
                elif object_hash(obj.content) != it.object_hash:
                    issues.append({"commit": c.id, "object": it.object_hash,
                                   "error": "conteúdo adulterado (hash não confere)"})
                tree[it.object_hash] = (it.tipo, it.titulo)
            elif it.op == "remove":
                tree.pop(it.object_hash, None)

        recomputed = compute_tree_hash([(h, t, ti) for h, (t, ti) in tree.items()])
        if recomputed != c.tree_hash:
            issues.append({"commit": c.id, "error": "tree_hash não confere"})

    return {"scope": scope, "ok": not issues, "commits": len(ordered), "issues": issues}


def history(conn, scope: str) -> List[dict]:
    """Histórico de commits (mais novo primeiro) — para `brain log`."""
    ref = conn.get(Ref, f"{scope}/main")
    if ref is None:
        return []
    ordered = _ordered_commits(conn, ref.commit_id)
    ordered.reverse()
    return [
        {
            "id": c.id,
            "scope": c.scope,
            "author": c.author,
            "created_at": c.created_at,
            "message": c.message,
            "policy": c.policy_version,
        }
        for c in ordered
    ]


def diff(conn, scope: str, from_commit: Optional[str] = None,
         to_commit: Optional[str] = None) -> List[dict]:
    """Diferença de árvore entre dois commits (default: main vs seu parent)."""
    ref = conn.get(Ref, f"{scope}/main")
    if to_commit is None:
        to_commit = ref.commit_id if ref else None
    else:
        to_commit = resolve_commit_id(conn, scope, to_commit)
    if to_commit is None:
        return []
    to_tree = _tree_at_commit(conn, to_commit)

    if from_commit is None:
        c = conn.get(Commit, to_commit)
        parents = json.loads(c.parent_hashes) if c else []
        from_tree = _tree_at_commit(conn, parents[0]) if parents else {}
    else:
        resolved = resolve_commit_id(conn, scope, from_commit)
        from_tree = _tree_at_commit(conn, resolved) if resolved else {}

    changes: List[dict] = []
    for h in sorted(set(from_tree) | set(to_tree)):
        if h not in from_tree:
            changes.append({"op": "add", "object_hash": h,
                            "tipo": to_tree[h][0], "titulo": to_tree[h][1]})
        elif h not in to_tree:
            changes.append({"op": "remove", "object_hash": h,
                            "tipo": from_tree[h][0], "titulo": from_tree[h][1]})
        elif from_tree[h] != to_tree[h]:
            changes.append({"op": "change", "object_hash": h,
                            "tipo": to_tree[h][0], "titulo": to_tree[h][1]})
    return changes


def approve(conn, scope: str, candidate_commit_id: str, approver: str,
            policy: str = "manual", decision: str = "approve",
            justification: Optional[str] = None) -> int:
    """Registra uma aprovação/rejeição (governança). Retorna o id da approval."""
    cid = resolve_commit_id(conn, scope, candidate_commit_id)
    if cid is None:
        raise ValueError(f"commit não encontrado no scope {scope}")
    a = Approval(scope=scope, candidate_commit_id=cid,
                 approver=approver, decision=decision, policy=policy,
                 justification=justification)
    conn.add(a)
    conn.flush()
    _audit(conn, decision, scope, approver,
           {"candidate": candidate_commit_id, "policy": policy})
    conn.commit()
    return a.id


def rollback(conn, scope: str, to_commit_id: str, author: str) -> dict:
    """Move a ref main para um commit anterior e reconstrói `pages` (não-destrutivo)."""
    target_id = resolve_commit_id(conn, scope, to_commit_id)
    if target_id is None:
        return {"ok": False, "error": "commit não encontrado no scope"}
    ref = conn.get(Ref, f"{scope}/main")
    if ref is None:
        return {"ok": False, "error": "sem main ref"}
    target = conn.get(Commit, target_id)
    if target is None or target.scope != scope:
        return {"ok": False, "error": "commit não encontrado no scope"}
    current_ids = {c.id for c in _ordered_commits(conn, ref.commit_id)}
    if target_id not in current_ids:
        return {"ok": False, "error": "commit não é ancestro do main atual"}

    tree = _tree_at_commit(conn, target_id)
    expert = expert_for(scope)
    conn.execute(delete(Page).where(Page.expert == expert))
    for h, (tipo, titulo) in tree.items():
        obj = conn.get(KnowledgeObject, h)
        if obj is None:
            continue
        conn.add(Page(expert=expert, tipo=tipo, titulo=titulo,
                      corpo=obj.content, hash_canonical=h))
    _set_ref(conn, f"{scope}/main", target_id)
    _audit(conn, "rollback", scope, author,
           {"to": target_id, "from": ref.commit_id})
    conn.commit()
    return {"ok": True, "to": target_id, "pages": len(tree)}
