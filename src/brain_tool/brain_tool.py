#!/usr/bin/env python3
"""Brain Tool CLI — manipulação do brain.db (SQLAlchemy).

Camada de domínio sobre SQLAlchemy: SQLite local (um arquivo por expert/global)
ou PostgreSQL compartilhado (via DATABASE_URL), transparente para o chamador.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text
from sqlalchemy import inspect as sa_inspect

from .db import (
    SCHEMA_VERSION,
    get_brain_db_path,
    get_brain_root,
    get_database_url,
    get_db_connection,
    get_session,
    initialize_schema,
    list_expert_names,
    validate_expert_identifier,
)
from .models import Job, KnowledgeStaging, Page

from .auth import require_admin
from . import checkpoints
from .scan import scan_content, merge_scans

__version__ = "1.0.0"


def generate_canonical_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _iso(value) -> Any:
    return value.isoformat(sep=" ") if isinstance(value, datetime) else value


def _page_dict(p: Page) -> Dict[str, Any]:
    return {
        "id": p.id,
        "expert": p.expert,
        "tipo": p.tipo,
        "titulo": p.titulo,
        "corpo": p.corpo,
        "hash_canonical": p.hash_canonical,
        "created_at": _iso(p.created_at),
        "updated_at": _iso(p.updated_at),
    }


# --- CRUD -------------------------------------------------------------------

def remember(conn, expert, tipo, titulo=None, corpo="", hash_canonical=None, dry_run=False,
             actor=None):
    require_admin(actor)
    if dry_run:
        return {
            "action": "remember (dry-run)",
            "expert": expert, "tipo": tipo, "titulo": titulo,
            "corpo_length": len(corpo),
            "hash": hash_canonical or generate_canonical_hash(corpo),
            "would_create": True,
        }
    if not hash_canonical:
        hash_canonical = generate_canonical_hash(corpo)
    checkpoints.ensure_genesis(conn, expert)
    page = Page(expert=expert, tipo=tipo, titulo=titulo, corpo=corpo,
                hash_canonical=hash_canonical)
    conn.add(page)
    conn.flush()
    commit_id = checkpoints.create_commit(
        conn, checkpoints.scope_for(expert),
        [{"op": "add", "object_hash": hash_canonical, "content": corpo,
          "tipo": tipo, "titulo": titulo}],
        author=actor or "cli:local",
    )
    conn.commit()
    return {
        "action": "remember",
        "id": page.id, "expert": expert, "tipo": tipo, "titulo": titulo,
        "hash": hash_canonical, "created_at": _iso(page.created_at),
        "commit": commit_id,
    }


def recall(conn, expert, search_term=None, limit=10, offset=0):
    q = select(Page).where(Page.expert == expert)
    if search_term:
        like = f"%{search_term}%"
        q = q.where(Page.titulo.like(like) | Page.corpo.like(like))
    q = q.order_by(Page.created_at.desc()).limit(limit).offset(offset)
    return [_page_dict(p) for p in conn.scalars(q).all()]


def forget(conn, expert, page_id, dry_run=False, actor=None):
    require_admin(actor)
    page = conn.get(Page, page_id)
    if dry_run:
        if page and page.expert == expert:
            return {"action": "forget (dry-run)", "id": page.id,
                    "expert": page.expert, "tipo": page.tipo,
                    "titulo": page.titulo, "would_delete": True}
        return {"action": "forget (dry-run)", "would_delete": False,
                "reason": "pagina nao encontrada"}
    if page and page.expert == expert:
        checkpoints.ensure_genesis(conn, expert)
        obj_hash = page.hash_canonical or generate_canonical_hash(page.corpo)
        conn.delete(page)
        checkpoints.create_commit(
            conn, checkpoints.scope_for(expert),
            [{"op": "remove", "object_hash": obj_hash,
              "tipo": page.tipo, "titulo": page.titulo}],
            author=actor or "cli:local",
        )
        conn.commit()
        return {"action": "forget", "id": page_id, "deleted": True}
    return {"action": "forget", "deleted": False, "reason": "pagina nao encontrada"}


def synthesize(conn, expert, synthesis_type="summary", limit=20):
    pages = conn.scalars(
        select(Page).where(Page.expert == expert)
        .order_by(Page.created_at.desc()).limit(limit)
    ).all()
    if not pages:
        return {"synthesis": "Nenhum conhecimento encontrado.", "pages_count": 0}
    by_type: Dict[str, List[Page]] = {}
    for p in pages:
        by_type.setdefault(p.tipo, []).append(p)
    parts = [f"{t}: {len(pl)} entradas" for t, pl in by_type.items()]
    sintese = f"Resumo de {len(pages)} conhecimentos para {expert}:\n" + "\n".join(parts)
    return {"synthesis_type": synthesis_type, "expert": expert,
            "pages_count": len(pages),
            "by_type": {k: len(v) for k, v in by_type.items()},
            "synthesis": sintese}


def consolidate(conn, expert, threshold=0.8, dry_run=False, actor=None):
    require_admin(actor)
    pages = conn.scalars(
        select(Page).where(Page.expert == expert)
        .order_by(Page.hash_canonical, Page.created_at.asc())
    ).all()
    by_hash: Dict[Optional[str], List[Page]] = {}
    for p in pages:
        by_hash.setdefault(p.hash_canonical, []).append(p)
    if dry_run:
        dups = []
        for h, pl in by_hash.items():
            if len(pl) > 1:
                dups.append({"hash": h, "count": len(pl), "ids": [p.id for p in pl]})
        return {"action": "consolidate (dry-run)", "expert": expert,
                "duplicates_found": len(dups), "duplicates": dups[:10],
                "would_remove": sum(len(d["ids"]) - 1 for d in dups)}
    removed = 0
    for pl in by_hash.values():
        for p in pl[1:]:
            conn.delete(p)
            removed += 1
    if removed:
        checkpoints.audit_event(
            conn, "consolidate", checkpoints.scope_for(expert),
            actor or "cli:local", {"removed": removed},
        )
    conn.commit()
    remaining = conn.scalar(
        select(func.count()).select_from(Page).where(Page.expert == expert)
    ) or 0
    return {"action": "consolidate", "expert": expert,
            "removed_count": removed, "remaining_count": remaining}


def count_pages(conn, expert) -> int:
    return conn.scalar(
        select(func.count()).select_from(Page).where(Page.expert == expert)
    ) or 0


# --- Ingestão (learn → staging → sync) --------------------------------------

_RISKY_EXTS = {".pdf", ".docx", ".doc", ".xlsx", ".xls"}
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _extract_isolated(file_path: str, timeout: int = 120) -> str:
    """Extrai texto num subprocesso isolado (rlimits + timeout) — formatos de risco."""
    import subprocess

    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "brain_tool.extract", str(file_path)],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"extrator falhou (saida invalida): {(proc.stdout or '')[:200]}"
        ) from e
    if not data.get("ok"):
        raise ValueError(data.get("error") or "extração falhou")
    return data["text"]


def learn_file(file_path):
    """Lê/extrai texto de um arquivo.

    Formatos de risco (PDF/DOCX/planilhas) rodam em subprocesso isolado com
    limites de CPU/memória/tempo (spec §9.2); texto simples é lido em-processo.
    """
    from . import extract as _extract

    ext = Path(file_path).suffix.lower()
    if ext in _RISKY_EXTS and os.name == "posix":
        return _extract_isolated(file_path)
    return _extract.parse(file_path)


DEFAULT_CHUNK_SIZE = 4000


def _chunk_text(text: str, size: int = DEFAULT_CHUNK_SIZE) -> List[str]:
    """Divide o texto em pedaços gerenciáveis (spec §4.2 — chunking)."""
    if not text:
        return [""]
    if len(text) <= size:
        return [text]

    chunks: List[str] = []
    buffer = ""
    for paragraph in text.split("\n"):
        if buffer and len(buffer) + len(paragraph) + 1 > size:
            chunks.append(buffer.rstrip("\n"))
            buffer = ""
        while len(paragraph) > size:
            chunks.append(paragraph[:size])
            paragraph = paragraph[size:]
        buffer += paragraph + "\n"
    if buffer.strip():
        chunks.append(buffer.rstrip("\n"))
    return chunks or [text]


# --- Assets de ingestão (asset://<id>) ---------------------------------------

_ASSET_SCHEME = "asset://"


def _assets_dir() -> Path:
    d = get_brain_root() / ".uploads"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _assets_index_path() -> Path:
    return _assets_dir() / "assets.json"


def _load_assets_index() -> Dict[str, dict]:
    p = _assets_index_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_assets_index(data: Dict[str, dict]) -> None:
    p = _assets_index_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(p, 0o600)


def register_upload_asset(filename: str, content: bytes) -> str:
    """Persistência estável de upload e retorno de referência `asset://<id>`.

    O dashboard registra o arquivo com um id opaco e passa só o `asset://id`
    para o worker; o caminho físico fica encapsulado no índice local.
    """
    safe_name = Path(filename or "upload").name
    asset_id = uuid.uuid4().hex
    suffix = Path(safe_name).suffix.lower()
    dest = _assets_dir() / f"{asset_id}{suffix}"
    dest.write_bytes(content)
    os.chmod(dest, 0o600)

    idx = _load_assets_index()
    idx[asset_id] = {
        "path": str(dest),
        "name": safe_name,
        "size": len(content),
        "created_at": datetime.utcnow().isoformat(),
    }
    _save_assets_index(idx)
    return f"{_ASSET_SCHEME}{asset_id}"


def resolve_asset_ref(path_or_asset: str) -> str:
    """Resolve `asset://<id>` para caminho físico do upload compartilhado."""
    s = str(path_or_asset)
    if not s.startswith(_ASSET_SCHEME):
        return s
    asset_id = s[len(_ASSET_SCHEME):].strip()
    idx = _load_assets_index()
    meta = idx.get(asset_id)
    if not meta:
        raise ValueError(f"asset nao encontrado: {asset_id}")
    path = str(meta.get("path") or "")
    if not path or not Path(path).is_file():
        raise ValueError(f"asset indisponível no storage compartilhado: {asset_id}")
    return path


# --- Ingestão de URL (SSRF-safe) --------------------------------------------

_URL_TIMEOUT = float(os.environ.get("BRAIN_DASHBOARD_URL_TIMEOUT", "15"))
_URL_MAX_BYTES = int(os.environ.get("BRAIN_DASHBOARD_MAX_URL_BYTES", str(10 * 1024 * 1024)))

_PRIVATE_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _allow_private_urls() -> bool:
    return os.environ.get("BRAIN_DASHBOARD_ALLOW_PRIVATE_URLS", "") in ("1", "true", "yes")


def _host_is_private(host: str) -> bool:
    """True se o host resolve para IP privado/loopback/link-local (bloqueio SSRF)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f"host nao resolve: {host}")
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if any(ip in net for net in _PRIVATE_NETS):
            return True
    return False


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.parse.urlparse(newurl)
        if new.scheme not in ("http", "https"):
            raise ValueError("redirecionamento para esquema nao suportado")
        if not new.hostname:
            raise ValueError("redirecionamento sem host")
        if not _allow_private_urls() and _host_is_private(new.hostname):
            raise ValueError("redirecionamento para rede interna/loopback bloqueado")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_url(url: str) -> str:
    """Busca conteúdo textual de uma URL (http/https) com proteção anti-SSRF.

    Bloqueia IPs privados/loopback/link-local por padrão; libere explicitamente
    com BRAIN_DASHBOARD_ALLOW_PRIVATE_URLS=1 (ex.: docs internos na LAN).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"esquema nao suportado: {parsed.scheme or '(ausente)'}")
    if not parsed.hostname:
        raise ValueError("URL sem host")
    if not _allow_private_urls() and _host_is_private(parsed.hostname):
        raise ValueError(
            "URL aponta para rede interna/loopback — bloqueado por segurança "
            "(use BRAIN_DASHBOARD_ALLOW_PRIVATE_URLS=1 para permitir)"
        )
    opener = urllib.request.build_opener(_ValidatingRedirectHandler())
    req = urllib.request.Request(url, headers={"User-Agent": "brain-framework"})
    with opener.open(req, timeout=_URL_TIMEOUT) as resp:
        content = resp.read(_URL_MAX_BYTES + 1)
        if len(content) > _URL_MAX_BYTES:
            raise ValueError(f"conteudo da URL excede {_URL_MAX_BYTES // (1024 * 1024)}MB")
        charset = resp.headers.get_content_charset() or "utf-8"
        return content.decode(charset, errors="replace")


def _new_job(conn, expert, command, metadata=None) -> str:
    raw = f"{expert}:{command}:{datetime.now().isoformat()}"
    job_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    conn.add(Job(id=job_id, expert=expert, command=command, status="enqueued",
                 job_metadata=json.dumps(metadata or {})))
    conn.commit()
    return job_id


def _set_job_status(conn, job_id, status, error=None):
    job = conn.get(Job, job_id)
    if not job:
        return
    job.status = status
    if status == "processing":
        job.started_at = datetime.utcnow()
    elif status in ("completed", "failed"):
        job.completed_at = datetime.utcnow()
    if error is not None:
        job.error = error
    conn.commit()


def _staging_exists(conn, expert: str, h: str, pipeline_version: str = "1") -> bool:
    return conn.scalars(
        select(KnowledgeStaging.id).where(
            KnowledgeStaging.expert == expert,
            KnowledgeStaging.hash_canonical == h,
            KnowledgeStaging.pipeline_version == pipeline_version,
        )
    ).first() is not None


def _learn_file_into_staging(conn, expert, file_path, sync_immediately=False, dry_run=False):
    path = Path(file_path)
    if path.is_dir():
        raise IsADirectoryError(f"Esperado arquivo, recebido diretorio: {file_path}")
    content = learn_file(file_path)
    chunks = _chunk_text(content)
    hashes = [generate_canonical_hash(c) for c in chunks]
    scan = scan_content(content)
    if dry_run:
        return {
            "action": "learn (dry-run)",
            "expert": expert,
            "file": file_path,
            "content_length": len(content),
            "chunks": len(chunks),
            "hashes": hashes,
            "scan": scan,
            "would_add_to_staging": True,
            "sync_immediately": sync_immediately,
        }
    staging_ids = []
    changes = []
    skipped_existing = 0
    for chunk in chunks:
        h = generate_canonical_hash(chunk)
        if _staging_exists(conn, expert, h, "1"):
            skipped_existing += 1
            continue
        s = KnowledgeStaging(expert=expert, chunk_data=chunk, hash_canonical=h,
                             pipeline_version="1")
        conn.add(s)
        conn.flush()
        staging_ids.append(s.id)
        changes.append({"op": "add", "object_hash": h, "content": chunk,
                        "tipo": "auto_learned",
                        "titulo": f"Arquivo aprendido (staging #{s.id})"})
    conn.commit()
    result = {
        "action": "learn",
        "expert": expert,
        "file": file_path,
        "staging_ids": staging_ids,
        "chunks": len(chunks),
        "hashes": hashes,
        "content_length": len(content),
        "scan": scan,
        "changes": changes,
        "status": "pending",
        "skipped_existing": skipped_existing,
    }
    if sync_immediately:
        result["sync"] = sync(conn, expert)
    return result


def _learn_url_into_staging(conn, expert, url, sync_immediately=False, dry_run=False):
    content = _fetch_url(url)
    chunks = _chunk_text(content)
    hashes = [generate_canonical_hash(c) for c in chunks]
    scan = scan_content(content)
    if dry_run:
        return {
            "action": "learn (dry-run)",
            "expert": expert,
            "url": url,
            "content_length": len(content),
            "chunks": len(chunks),
            "hashes": hashes,
            "scan": scan,
            "would_add_to_staging": True,
            "sync_immediately": sync_immediately,
        }
    staging_ids = []
    changes = []
    skipped_existing = 0
    for chunk in chunks:
        h = generate_canonical_hash(chunk)
        if _staging_exists(conn, expert, h, "1"):
            skipped_existing += 1
            continue
        s = KnowledgeStaging(expert=expert, chunk_data=chunk, hash_canonical=h,
                             pipeline_version="1")
        conn.add(s)
        conn.flush()
        staging_ids.append(s.id)
        changes.append({"op": "add", "object_hash": h, "content": chunk,
                        "tipo": "auto_learned",
                        "titulo": f"Arquivo aprendido (staging #{s.id})"})
    conn.commit()
    result = {
        "action": "learn",
        "expert": expert,
        "url": url,
        "staging_ids": staging_ids,
        "chunks": len(chunks),
        "hashes": hashes,
        "content_length": len(content),
        "scan": scan,
        "changes": changes,
        "status": "pending",
        "skipped_existing": skipped_existing,
    }
    if sync_immediately:
        result["sync"] = sync(conn, expert)
    return result


def learn_directory(conn, expert, dir_path, sync_immediately=False, dry_run=False):
    path = Path(dir_path)
    if not path.exists() or not path.is_dir():
        raise NotADirectoryError(f"Diretorio nao encontrado: {dir_path}")
    results = []
    supported = {".txt", ".md", ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv"}
    for fp in path.rglob("*"):
        if fp.is_file() and fp.suffix.lower() in supported:
            try:
                res = _learn_file_into_staging(conn, expert, str(fp), sync_immediately, dry_run)
                results.append({"file": str(fp), "status": "success", "result": res})
            except Exception as e:
                results.append({"file": str(fp), "status": "error", "error": str(e)})
    return results


def _ingest(conn, expert, path, sync_immediately=False):
    """Executa a ingestão (sem rastreamento de job) — usado no sync e no worker."""
    s = str(path)
    if s.startswith(("http://", "https://")):
        return _learn_url_into_staging(conn, expert, s, sync_immediately)
    s = resolve_asset_ref(s)
    p = Path(s)
    if p.is_dir():
        results = learn_directory(conn, expert, s, sync_immediately)
        all_changes = []
        scans = []
        for r in results:
            if r.get("status") == "success" and isinstance(r.get("result"), dict):
                all_changes.extend(r["result"].get("changes") or [])
                scans.append(r["result"].get("scan") or {})
        return {"action": "learn", "expert": expert, "path": path,
                "type": "directory", "files_processed": len(results), "results": results,
                "changes": all_changes, "scan": merge_scans(scans)}
    elif p.is_file():
        return _learn_file_into_staging(conn, expert, s, sync_immediately)
    else:
        raise ValueError(f"Path nao e arquivo nem diretorio: {path}")


def _async_enabled() -> bool:
    """True se Celery + broker (Redis) estão configurados e importáveis."""
    if not (os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL")):
        return False
    try:
        import celery  # noqa: F401
        return True
    except ImportError:
        return False


def _propose_learn_candidate(conn, expert, path, job_id, sync_immediately=False,
                             actor=None) -> dict:
    """Ingere e propõe um commit **candidato** (quarentena) — não publica.

    Compartilhado pelo caminho síncrono (`learn`) e pelo worker Celery: ambos
    ingerem, criam a branch candidata e só publicam se `sync_immediately`
    (aprovação implícita do admin local). Mantém a trilha de checkpoints
    assinados (Fase 3) também no fluxo assíncrono.
    """
    result = _ingest(conn, expert, path, sync_immediately=False)
    changes = result.get("changes") or []
    scan = result.get("scan") or {}
    checkpoints.ensure_genesis(conn, expert)
    scope = checkpoints.scope_for(expert)
    author = actor or "cli:local"
    candidate = checkpoints.create_commit(
        conn, scope, changes, author=author,
        policy="implicit-admin", ref_name=checkpoints.candidate_ref(job_id),
        validation_results={"scan": scan, "source": str(path)},
    )
    conn.commit()
    result["job_id"] = job_id
    result["status"] = "proposed"
    result["candidate_ref"] = f"{scope}/{checkpoints.candidate_ref(job_id)}"
    result["candidate_commit"] = candidate
    if sync_immediately:
        merge = checkpoints.merge_candidate(conn, scope, job_id, author)
        result["merge"] = merge
        result["status"] = "synced" if merge.get("ok") else "merge_conflict"
    return result


def learn(conn, expert, file_path, sync_immediately=False, dry_run=False, actor=None):
    """Ingere arquivo/diretório/URL no staging (spec §4.2).

    Assíncrono (Celery/Redis) quando o broker está configurado; caso contrário,
    fallback síncrono (spec §4.6).
    """
    require_admin(actor)
    s = str(file_path)
    is_url = s.startswith(("http://", "https://"))

    if dry_run:
        if is_url:
            return _learn_url_into_staging(conn, expert, s, sync_immediately, dry_run=True)
        path = Path(file_path)
        if path.is_dir():
            results = learn_directory(conn, expert, file_path, sync_immediately, dry_run=True)
            return {"action": "learn (dry-run)", "expert": expert, "path": file_path,
                    "type": "directory", "files_processed": len(results), "results": results}
        return _learn_file_into_staging(conn, expert, file_path, sync_immediately, dry_run=True)

    if _async_enabled():
        job_id = _new_job(conn, expert, "learn",
                          metadata={"path": file_path, "sync": sync_immediately,
                                    "mode": "async"})
        from brain_tool.worker import learn_task

        # A mensagem carrega só `(job_id, scope, path, sync_immediately)` —
        # nunca `database_url` (credencial fora do Redis; o worker reconstrói
        # a conexão do próprio ambiente).
        scope = checkpoints.scope_for(expert)
        learn_task.delay(job_id, scope, file_path, sync_immediately)
        return {"action": "learn", "expert": expert, "path": file_path,
                "status": "enqueued", "job_id": job_id, "mode": "async"}

    job_id = _new_job(conn, expert, "learn",
                      metadata={"path": file_path, "sync": sync_immediately, "mode": "sync"})
    _set_job_status(conn, job_id, "processing")
    try:
        result = _propose_learn_candidate(conn, expert, file_path, job_id,
                                          sync_immediately, actor)
    except Exception as e:
        _set_job_status(conn, job_id, "failed", error=str(e))
        raise
    _set_job_status(conn, job_id, "completed")
    return result


def sync(conn, expert, staging_id=None, actor=None):
    require_admin(actor)
    author = actor or "cli:local"
    scope = checkpoints.scope_for(expert)
    if staging_id:
        s = conn.get(KnowledgeStaging, staging_id)
        if not s or s.expert != expert:
            return {"action": "sync", "error": "staging nao encontrado",
                    "staging_id": staging_id}
        existing = conn.scalars(
            select(Page).where(Page.hash_canonical == s.hash_canonical,
                               Page.expert == expert)
        ).first()
        if existing:
            conn.delete(s)
            conn.commit()
            return {"action": "sync", "staging_id": staging_id, "status": "skipped",
                    "reason": "hash ja existe", "hash": s.hash_canonical}
        checkpoints.ensure_genesis(conn, expert)
        page = Page(expert=expert, tipo="auto_learned",
                    titulo=f"Arquivo aprendido (staging #{staging_id})",
                    corpo=s.chunk_data, hash_canonical=s.hash_canonical)
        conn.add(page)
        conn.flush()
        checkpoints.create_commit(
            conn, scope,
            [{"op": "add", "object_hash": s.hash_canonical, "content": s.chunk_data,
              "tipo": "auto_learned", "titulo": page.titulo}],
            author=author,
        )
        conn.delete(s)
        conn.commit()
        return {"action": "sync", "staging_id": staging_id, "page_id": page.id,
                "status": "synced", "hash": s.hash_canonical}

    pending = conn.scalars(
        select(KnowledgeStaging).where(KnowledgeStaging.expert == expert,
                                       KnowledgeStaging.status == "pending")
    ).all()
    if not pending:
        return {"action": "sync", "expert": expert,
                "status": "nothing_to_sync", "pending_count": 0}

    checkpoints.ensure_genesis(conn, expert)
    synced = 0
    skipped = 0
    changes = []
    for e in pending:
        existing = conn.scalars(
            select(Page).where(Page.hash_canonical == e.hash_canonical,
                               Page.expert == expert)
        ).first()
        if existing:
            skipped += 1
            continue
        conn.add(Page(expert=expert, tipo="auto_learned",
                      titulo=f"Arquivo aprendido (staging #{e.id})",
                      corpo=e.chunk_data, hash_canonical=e.hash_canonical))
        changes.append({"op": "add", "object_hash": e.hash_canonical,
                        "content": e.chunk_data, "tipo": "auto_learned",
                        "titulo": f"Arquivo aprendido (staging #{e.id})"})
        synced += 1
    if changes:
        checkpoints.create_commit(conn, scope, changes, author=author)
    for e in pending:
        conn.delete(e)
    conn.commit()
    return {"action": "sync", "expert": expert, "synced": synced,
            "skipped": skipped, "pending_remaining": 0}


# --- Diagnóstico -------------------------------------------------------------

def check(conn, expert):
    result = {"expert": expert, "integrity": "ok", "schema_version": SCHEMA_VERSION,
              "tables": {}, "counts": {}, "issues": []}
    engine = conn.get_bind()
    try:
        if engine.dialect.name == "sqlite":
            r = conn.execute(text("PRAGMA integrity_check")).scalar()
            integrity = "ok" if r == "ok" else str(r)
        else:
            conn.execute(text("SELECT 1"))
            integrity = "ok"
        result["integrity"] = integrity
        if integrity != "ok":
            result["issues"].append(f"Integridade: {integrity}")
    except Exception as e:
        result["integrity"] = "error"
        result["issues"].append(str(e))

    inspector = sa_inspect(engine)
    result["tables"]["present"] = inspector.get_table_names()
    result["tables"]["expected"] = ["pages", "knowledge_staging", "jobs", "schema_version"]

    actual = conn.execute(
        text("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1")
    ).scalar()
    result["schema_version_actual"] = actual or "unknown"
    if actual != SCHEMA_VERSION:
        result["issues"].append(f"Schema: esperado {SCHEMA_VERSION}, atual {actual}")

    for t in ["pages", "knowledge_staging", "jobs"]:
        try:
            result["counts"][t] = conn.execute(
                text(f"SELECT COUNT(*) FROM {t} WHERE expert = :e"), {"e": expert}
            ).scalar() or 0
        except Exception:
            result["counts"][t] = 0

    # Ponte (decisão §13.5): detecta páginas legadas cujo conteúdo foi adulterado.
    tampered = []
    for p in conn.scalars(select(Page).where(Page.expert == expert)).all():
        if p.hash_canonical and p.hash_canonical != generate_canonical_hash(p.corpo):
            tampered.append(p.id)
    if tampered:
        if result["integrity"] == "ok":
            result["integrity"] = "tampered"
        result["issues"].append({
            "type": "hash_mismatch",
            "page_ids": tampered,
            "detail": "hash_canonical não confere com o conteúdo",
        })
    return result


def list_jobs(conn, expert, status=None, limit=20):
    q = select(Job).where(Job.expert == expert)
    if status:
        q = q.where(Job.status == status)
    q = q.order_by(Job.created_at.desc()).limit(limit)
    return [{
        "id": j.id, "expert": j.expert, "command": j.command, "status": j.status,
        "metadata": j.job_metadata,
        "created_at": _iso(j.created_at), "started_at": _iso(j.started_at),
        "completed_at": _iso(j.completed_at), "error": j.error,
    } for j in conn.scalars(q).all()]


def suggest_taxonomy_rules(conn, expert, limit=10):
    rows = conn.execute(
        select(Page.tipo, func.count())
        .where(Page.expert == expert)
        .group_by(Page.tipo).order_by(func.count().desc()).limit(limit)
    ).all()
    stats = [{"tipo": r[0], "count": r[1]} for r in rows]
    return {"action": "taxonomist", "expert": expert, "tipo_distribution": stats,
            "suggestions": ["Categorize por: memory, fact, entity, procedure, policy",
                            "Use hash_canonical para deduplica",
                            "Adicione metadados (tags, origem)"]}


def capture_taxonomy(conn, expert, content, suggested_type, actor=None):
    return remember(conn, expert, suggested_type, None, content, actor=actor)
