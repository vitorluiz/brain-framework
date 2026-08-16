#!/usr/bin/env python3
"""Brain Tool CLI — manipulação do brain.db (SQLAlchemy).

Camada de domínio sobre SQLAlchemy: SQLite local (um arquivo por expert/global)
ou PostgreSQL compartilhado (via DATABASE_URL), transparente para o chamador.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
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
    validate_expert_identifier,
)
from .models import Job, KnowledgeStaging, Page

__version__ = "1.1.0"


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

def remember(conn, expert, tipo, titulo=None, corpo="", hash_canonical=None, dry_run=False):
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
    page = Page(expert=expert, tipo=tipo, titulo=titulo, corpo=corpo,
                hash_canonical=hash_canonical)
    conn.add(page)
    conn.commit()
    return {
        "action": "remember",
        "id": page.id, "expert": expert, "tipo": tipo, "titulo": titulo,
        "hash": hash_canonical, "created_at": _iso(page.created_at),
    }


def recall(conn, expert, search_term=None, limit=10, offset=0):
    q = select(Page).where(Page.expert == expert)
    if search_term:
        like = f"%{search_term}%"
        q = q.where(Page.titulo.like(like) | Page.corpo.like(like))
    q = q.order_by(Page.created_at.desc()).limit(limit).offset(offset)
    return [_page_dict(p) for p in conn.scalars(q).all()]


def forget(conn, expert, page_id, dry_run=False):
    page = conn.get(Page, page_id)
    if dry_run:
        if page and page.expert == expert:
            return {"action": "forget (dry-run)", "id": page.id,
                    "expert": page.expert, "tipo": page.tipo,
                    "titulo": page.titulo, "would_delete": True}
        return {"action": "forget (dry-run)", "would_delete": False,
                "reason": "pagina nao encontrada"}
    if page and page.expert == expert:
        conn.delete(page)
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


def consolidate(conn, expert, threshold=0.8, dry_run=False):
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

def learn_file(file_path):
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {file_path}")
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ImportError("pypdf necessario. pip install 'brain-framework[learn]'") from e
        r = PdfReader(path)
        return "\n".join([page.extract_text() or "" for page in r.pages])
    elif ext in (".docx", ".doc"):
        try:
            from docx import Document
        except ImportError as e:
            raise ImportError("python-docx necessario. pip install 'brain-framework[learn]'") from e
        d = Document(path)
        return "\n".join([p.text for p in d.paragraphs])
    elif ext in (".xlsx", ".xls", ".csv"):
        try:
            import pandas as pd
        except ImportError as e:
            raise ImportError("pandas necessario. pip install 'brain-framework[learn]'") from e
        if ext == ".csv":
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
        return df.to_string()
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()


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


def _learn_file_into_staging(conn, expert, file_path, sync_immediately=False, dry_run=False):
    path = Path(file_path)
    if path.is_dir():
        raise IsADirectoryError(f"Esperado arquivo, recebido diretorio: {file_path}")
    content = learn_file(file_path)
    chunks = _chunk_text(content)
    hashes = [generate_canonical_hash(c) for c in chunks]
    if dry_run:
        return {
            "action": "learn (dry-run)",
            "expert": expert,
            "file": file_path,
            "content_length": len(content),
            "chunks": len(chunks),
            "hashes": hashes,
            "would_add_to_staging": True,
            "sync_immediately": sync_immediately,
        }
    staging_ids = []
    for chunk in chunks:
        s = KnowledgeStaging(expert=expert, chunk_data=chunk,
                             hash_canonical=generate_canonical_hash(chunk))
        conn.add(s)
        conn.flush()
        staging_ids.append(s.id)
    conn.commit()
    result = {
        "action": "learn",
        "expert": expert,
        "file": file_path,
        "staging_ids": staging_ids,
        "chunks": len(chunks),
        "hashes": hashes,
        "content_length": len(content),
        "status": "pending",
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
    p = Path(path)
    if p.is_dir():
        results = learn_directory(conn, expert, path, sync_immediately)
        return {"action": "learn", "expert": expert, "path": path,
                "type": "directory", "files_processed": len(results), "results": results}
    elif p.is_file():
        return _learn_file_into_staging(conn, expert, path, sync_immediately)
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


def learn(conn, expert, file_path, sync_immediately=False, dry_run=False):
    """Ingere um arquivo/diretório no staging (spec §4.2).

    Assíncrono (Celery/Redis) quando o broker está configurado; caso contrário,
    fallback síncrono (spec §4.6).
    """
    path = Path(file_path)

    if dry_run:
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

        database_url = str(conn.get_bind().url)
        learn_task.delay(job_id, expert, file_path, sync_immediately, database_url)
        return {"action": "learn", "expert": expert, "path": file_path,
                "status": "enqueued", "job_id": job_id, "mode": "async"}

    job_id = _new_job(conn, expert, "learn",
                      metadata={"path": file_path, "sync": sync_immediately, "mode": "sync"})
    _set_job_status(conn, job_id, "processing")
    try:
        result = _ingest(conn, expert, file_path, sync_immediately)
        result["job_id"] = job_id
    except Exception as e:
        _set_job_status(conn, job_id, "failed", error=str(e))
        raise
    _set_job_status(conn, job_id, "completed")
    return result


def sync(conn, expert, staging_id=None):
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
        page = Page(expert=expert, tipo="auto_learned",
                    titulo=f"Arquivo aprendido (staging #{staging_id})",
                    corpo=s.chunk_data, hash_canonical=s.hash_canonical)
        conn.add(page)
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

    synced = 0
    skipped = 0
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
        synced += 1
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


def capture_taxonomy(conn, expert, content, suggested_type):
    return remember(conn, expert, suggested_type, None, content)


# === CLI Commands ============================================================

def cmd_init(args):
    db_path = get_brain_db_path(expert=args.name, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.name
    remember(conn, ename, "system", "Brain inicializado",
             f"Brain {ename} inicializado em {datetime.now().isoformat()}")
    conn.close()
    print(json.dumps({"action": "init", "expert": ename, "db_path": db_path,
                      "schema_version": SCHEMA_VERSION, "initialized": True},
                     indent=2, ensure_ascii=False))
    return 0


def cmd_remember(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(remember(conn, ename, args.tipo, args.title, args.content,
                              dry_run=args.dry_run), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_recall(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(recall(conn, ename, args.search, args.limit, args.offset),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_forget(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(forget(conn, ename, args.id, dry_run=args.dry_run),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_synthesize(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(synthesize(conn, ename, args.type), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_consolidate(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(consolidate(conn, ename, args.threshold, dry_run=args.dry_run),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_learn(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(learn(conn, ename, args.path, args.sync, dry_run=args.dry_run),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_sync(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(sync(conn, ename), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_check(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(check(conn, ename), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_jobs(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(list_jobs(conn, ename, args.status, args.limit),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_taxonomist(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(suggest_taxonomy_rules(conn, ename, args.limit),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_capture(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                                brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(capture_taxonomy(conn, ename, args.content, args.type),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Brain Tool CLI - Manipulacao do brain.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Comandos:
  init           - Inicializa um novo brain.db
  remember       - Adiciona conhecimento
  recall         - Recupera conhecimento
  forget         - Remove conhecimento (use --dry-run primeiro!)
  synthesize     - Gera sintese
  consolidate    - Deduplica conhecimento (use --dry-run primeiro!)
  learn          - Processa arquivo/diretorio para staging
  sync           - Move staging para tabela principal
  check          - Verifica integridade + schema version
  jobs           - Lista jobs
  taxonomist     - Sugeri regras de taxonomia
  capture        - Captura classificacao de taxonomia

Targeting:
  --expert NAME     Operar sobre brain.db de um expert
  --brain-path PATH Caminho explicito para o brain.db
  --global          Operar sobre brain.db global
  --dry-run         Preview sem executar
        """)
    parser.add_argument(
        "--version", action="version", version=f"brain-tool {__version__}",
        help="Exibe a versão e sai",
    )
    sp = parser.add_subparsers(dest='command')

    p_init = sp.add_parser('init', help='Inicializa brain.db')
    p_init.add_argument('--name', required=True)
    p_init.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_init.add_argument('--global', action='store_true', dest='global_brain')

    p_rem = sp.add_parser('remember', help='Adiciona conhecimento')
    p_rem.add_argument('--expert', required=True)
    p_rem.add_argument('--tipo', required=True)
    p_rem.add_argument('--title')
    p_rem.add_argument('--content', required=True)
    p_rem.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_rem.add_argument('--global', action='store_true', dest='global_brain')
    p_rem.add_argument('--dry-run', action='store_true')

    p_rec = sp.add_parser('recall', help='Recupera conhecimento')
    p_rec.add_argument('--expert', required=True)
    p_rec.add_argument('--search')
    p_rec.add_argument('--limit', type=int, default=10)
    p_rec.add_argument('--offset', type=int, default=0)
    p_rec.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_rec.add_argument('--global', action='store_true', dest='global_brain')

    p_for = sp.add_parser('forget', help='Remove conhecimento')
    p_for.add_argument('--expert', required=True)
    p_for.add_argument('--id', type=int, required=True)
    p_for.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_for.add_argument('--global', action='store_true', dest='global_brain')
    p_for.add_argument('--dry-run', action='store_true')

    p_syn = sp.add_parser('synthesize', help='Gera sintese')
    p_syn.add_argument('--expert', required=True)
    p_syn.add_argument('--type', default='summary')
    p_syn.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_syn.add_argument('--global', action='store_true', dest='global_brain')

    p_con = sp.add_parser('consolidate', help='Deduplica conhecimento')
    p_con.add_argument('--expert', required=True)
    p_con.add_argument('--threshold', type=float, default=0.8)
    p_con.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_con.add_argument('--global', action='store_true', dest='global_brain')
    p_con.add_argument('--dry-run', action='store_true')

    p_learn = sp.add_parser('learn', help='Processa arquivo/diretorio')
    p_learn.add_argument('--expert', required=True)
    p_learn.add_argument('--path', required=True)
    p_learn.add_argument('--sync', action='store_true')
    p_learn.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_learn.add_argument('--global', action='store_true', dest='global_brain')
    p_learn.add_argument('--dry-run', action='store_true')

    p_sync = sp.add_parser('sync', help='Sync staging para principal')
    p_sync.add_argument('--expert', required=True)
    p_sync.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_sync.add_argument('--global', action='store_true', dest='global_brain')

    p_chk = sp.add_parser('check', help='Verifica integridade')
    p_chk.add_argument('--expert', required=True)
    p_chk.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_chk.add_argument('--global', action='store_true', dest='global_brain')

    p_job = sp.add_parser('jobs', help='Lista jobs')
    p_job.add_argument('--expert', required=True)
    p_job.add_argument('--status')
    p_job.add_argument('--limit', type=int, default=20)
    p_job.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_job.add_argument('--global', action='store_true', dest='global_brain')

    p_tax = sp.add_parser('taxonomist', help='Sugeri taxonomia')
    p_tax.add_argument('--expert', required=True)
    p_tax.add_argument('--limit', type=int, default=10)
    p_tax.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_tax.add_argument('--global', action='store_true', dest='global_brain')

    p_cap = sp.add_parser('capture', help='Captura taxonomia')
    p_cap.add_argument('--expert', required=True)
    p_cap.add_argument('--type', required=True)
    p_cap.add_argument('--content', required=True)
    p_cap.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_cap.add_argument('--global', action='store_true', dest='global_brain')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cmds = {'init': cmd_init, 'remember': cmd_remember, 'recall': cmd_recall,
            'forget': cmd_forget, 'synthesize': cmd_synthesize, 'consolidate': cmd_consolidate,
            'learn': cmd_learn, 'sync': cmd_sync, 'check': cmd_check,
            'jobs': cmd_jobs, 'taxonomist': cmd_taxonomist, 'capture': cmd_capture}
    return cmds[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
