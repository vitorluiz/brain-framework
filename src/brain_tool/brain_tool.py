#!/usr/bin/env python3
"""
Brain Tool CLI - Manipulacao do brain.db
CLI core do Brain Framework: CRUD de conhecimento, learn com hash canonico,
sync, check de integridade, suporte a jobs e taxonomia.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

SCHEMA_VERSION = "1.0.0"

DEFAULT_BRAIN_ROOT = os.environ.get('BRAIN_ROOT', os.path.expanduser("~/.hermes/brain"))
GLOBAL_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "global")
EXPERTS_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "experts")


def get_brain_db_path(expert=None, brain_path=None, global_brain=False):
    if brain_path:
        return brain_path
    if global_brain:
        return os.path.join(GLOBAL_DIR, "brain.db")
    if expert:
        return os.path.join(EXPERTS_DIR, expert, "brain.db")
    return os.path.join(os.getcwd(), "brain.db")


def _table_has_column(conn, table_name, column_name):
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    for row in cursor.fetchall():
        if row[1] == column_name:
            return True
    return False


def _apply_migration_if_needed(conn):
    cursor = conn.cursor()

    # Tabela schema_version
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
    if cursor.fetchone() is None:
        cursor.execute("""
            CREATE TABLE schema_version (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )
        """)

    # Tabela pages
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pages'")
    pages_exists = cursor.fetchone() is not None

    if not pages_exists:
        cursor.execute("""
            CREATE TABLE pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expert TEXT NOT NULL,
                tipo TEXT NOT NULL,
                titulo TEXT,
                corpo TEXT NOT NULL,
                hash_canonical TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        if not _table_has_column(conn, "pages", "expert"):
            cursor.execute("ALTER TABLE pages ADD COLUMN expert TEXT NOT NULL DEFAULT 'unknown'")
        if not _table_has_column(conn, "pages", "hash_canonical"):
            cursor.execute("ALTER TABLE pages ADD COLUMN hash_canonical TEXT")
        if not _table_has_column(conn, "pages", "tipo"):
            cursor.execute("ALTER TABLE pages ADD COLUMN tipo TEXT NOT NULL DEFAULT 'memory'")
        if not _table_has_column(conn, "pages", "titulo"):
            cursor.execute("ALTER TABLE pages ADD COLUMN titulo TEXT")
        if not _table_has_column(conn, "pages", "updated_at"):
            cursor.execute("ALTER TABLE pages ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # Tabela knowledge_staging
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_staging'")
    staging_exists = cursor.fetchone() is not None

    if not staging_exists:
        cursor.execute("""
            CREATE TABLE knowledge_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expert TEXT NOT NULL,
                chunk_data TEXT NOT NULL,
                hash_canonical TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP
            )
        """)
    else:
        if not _table_has_column(conn, "knowledge_staging", "expert"):
            cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN expert TEXT NOT NULL DEFAULT 'unknown'")
        if not _table_has_column(conn, "knowledge_staging", "hash_canonical"):
            cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN hash_canonical TEXT")
        if not _table_has_column(conn, "knowledge_staging", "status"):
            cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN status TEXT DEFAULT 'pending'")
        if not _table_has_column(conn, "knowledge_staging", "chunk_data"):
            cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN chunk_data TEXT NOT NULL")
        if not _table_has_column(conn, "knowledge_staging", "created_at"):
            cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # Tabela jobs
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    jobs_exists = cursor.fetchone() is not None

    if not jobs_exists:
        cursor.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                expert TEXT NOT NULL,
                command TEXT NOT NULL,
                status TEXT DEFAULT 'enqueued',
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                error TEXT
            )
        """)
    else:
        if not _table_has_column(conn, "jobs", "expert"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN expert TEXT NOT NULL DEFAULT 'unknown'")
        if not _table_has_column(conn, "jobs", "status"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'enqueued'")
        if not _table_has_column(conn, "jobs", "command"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN command TEXT NOT NULL")
        if not _table_has_column(conn, "jobs", "metadata"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN metadata TEXT")
        if not _table_has_column(conn, "jobs", "created_at"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # Schema version
    cursor.execute("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1")
    row = cursor.fetchone()
    current_version = row[0] if row else "0.0.0"
    if current_version < SCHEMA_VERSION:
        cursor.execute("""
            INSERT INTO schema_version (version, description)
            VALUES (?, ?)
        """, (SCHEMA_VERSION, f"Migration for {SCHEMA_VERSION}"))
        conn.commit()


def initialize_schema(conn):
    """Inicializa o schema do brain.db."""
    _apply_migration_if_needed(conn)


def get_db_connection(db_path):
    """Abre conexão com o brain.db, criando diretório e schema se necessário."""
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_migration_if_needed(conn)
    return conn


def generate_canonical_hash(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def remember(conn, expert, tipo, titulo=None, corpo="", hash_canonical=None, dry_run=False):
    if dry_run:
        return {
            "action": "remember (dry-run)",
            "expert": expert, "tipo": tipo, "titulo": titulo,
            "corpo_length": len(corpo),
            "hash": hash_canonical or generate_canonical_hash(corpo),
            "would_create": True
        }
    if not hash_canonical:
        hash_canonical = generate_canonical_hash(corpo)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pages (expert, tipo, titulo, corpo, hash_canonical)
        VALUES (?, ?, ?, ?, ?)
    """, (expert, tipo, titulo, corpo, hash_canonical))
    conn.commit()
    return {
        "action": "remember",
        "id": cursor.lastrowid,
        "expert": expert, "tipo": tipo, "titulo": titulo,
        "hash": hash_canonical,
        "created_at": datetime.now().isoformat()
    }


def recall(conn, expert, search_term=None, limit=10, offset=0):
    cursor = conn.cursor()
    if search_term:
        cursor.execute("""
            SELECT id, expert, tipo, titulo, corpo, hash_canonical, created_at, updated_at
            FROM pages WHERE expert = ? AND (titulo LIKE ? OR corpo LIKE ?)
            ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (expert, f"%{search_term}%", f"%{search_term}%", limit, offset))
    else:
        cursor.execute("""
            SELECT id, expert, tipo, titulo, corpo, hash_canonical, created_at, updated_at
            FROM pages WHERE expert = ? ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (expert, limit, offset))
    return [dict(row) for row in cursor.fetchall()]


def forget(conn, expert, page_id, dry_run=False):
    cursor = conn.cursor()
    if dry_run:
        cursor.execute("SELECT * FROM pages WHERE id = ? AND expert = ?", (page_id, expert))
        page = cursor.fetchone()
        if page:
            return {"action": "forget (dry-run)", "id": page["id"],
                    "expert": page["expert"], "tipo": page["tipo"],
                    "titulo": page["titulo"], "would_delete": True}
        return {"action": "forget (dry-run)", "would_delete": False,
                "reason": "pagina nao encontrada"}
    cursor.execute("DELETE FROM pages WHERE id = ? AND expert = ?", (page_id, expert))
    conn.commit()
    if cursor.rowcount > 0:
        return {"action": "forget", "id": page_id, "deleted": True}
    return {"action": "forget", "deleted": False, "reason": "pagina nao encontrada"}


def synthesize(conn, expert, synthesis_type="summary", limit=20):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, tipo, titulo, corpo FROM pages
        WHERE expert = ? ORDER BY created_at DESC LIMIT ?
    """, (expert, limit))
    pages = [dict(row) for row in cursor.fetchall()]
    if not pages:
        return {"synthesis": "Nenhum conhecimento encontrado.", "pages_count": 0}
    by_type = {}
    for page in pages:
        t = page["tipo"]
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(page)
    parts = [f"{t}: {len(pl)} entradas" for t, pl in by_type.items()]
    sintese = f"Resumo de {len(pages)} conhecimentos para {expert}:\n" + "\n".join(parts)
    return {"synthesis_type": synthesis_type, "expert": expert,
            "pages_count": len(pages), "by_type": {k: len(v) for k, v in by_type.items()},
            "synthesis": sintese}


def consolidate(conn, expert, threshold=0.8, dry_run=False):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, hash_canonical, titulo, corpo FROM pages
        WHERE expert = ? ORDER BY hash_canonical, created_at ASC
    """, (expert,))
    pages = cursor.fetchall()
    by_hash = {}
    for p in pages:
        h = p["hash_canonical"]
        if h not in by_hash:
            by_hash[h] = []
        by_hash[h].append(dict(p))
    if dry_run:
        dups = []
        for h, pl in by_hash.items():
            if len(pl) > 1:
                dups.append({"hash": h, "count": len(pl), "ids": [p["id"] for p in pl]})
        return {"action": "consolidate (dry-run)", "expert": expert,
                "duplicates_found": len(dups), "duplicates": dups[:10],
                "would_remove": sum(len(d["ids"]) - 1 for d in dups)}
    removed = 0
    for h, pl in by_hash.items():
        if len(pl) > 1:
            ids_remove = [p["id"] for p in pl[1:]]
            if ids_remove:
                cursor.execute(f"DELETE FROM pages WHERE id IN ({','.join('?' * len(ids_remove))})", ids_remove)
                removed += len(ids_remove)
    conn.commit()
    remaining = cursor.execute("SELECT COUNT(*) FROM pages WHERE expert = ?", (expert,)).fetchone()[0]
    return {"action": "consolidate", "expert": expert,
            "removed_count": removed, "remaining_count": remaining}


def learn_file(file_path):
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {file_path}")
    ext = path.suffix.lower()
    if ext in ['.txt', '.md']:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    elif ext == '.pdf':
        try:
            from pypdf import PdfReader
            r = PdfReader(path)
            return "\n".join([page.extract_text() or "" for page in r.pages])
        except ImportError:
            raise ImportError("pypdf necessario. pip install pypdf")
    elif ext in ['.docx', '.doc']:
        try:
            from docx import Document
            d = Document(path)
            return "\n".join([p.text for p in d.paragraphs])
        except ImportError:
            raise ImportError("python-docx necessario. pip install python-docx")
    elif ext in ['.xlsx', '.xls', '.csv']:
        try:
            import pandas as pd
            if ext == '.csv':
                df = pd.read_csv(path)
            else:
                df = pd.read_excel(path)
            return df.to_string()
        except ImportError:
            raise ImportError("pandas necessario. pip install pandas openpyxl")
    else:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()


def learn_directory(conn, expert, dir_path, sync_immediately=False, dry_run=False):
    path = Path(dir_path)
    if not path.exists() or not path.is_dir():
        raise NotADirectoryError(f"Diretorio nao encontrado: {dir_path}")
    results = []
    supported = {'.txt', '.md', '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv'}
    for fp in path.rglob('*'):
        if fp.is_file() and fp.suffix.lower() in supported:
            try:
                res = learn(conn, expert, str(fp), sync_immediately, dry_run)
                results.append({"file": str(fp), "status": "success", "result": res})
            except Exception as e:
                results.append({"file": str(fp), "status": "error", "error": str(e)})
    return results


def learn(conn, expert, file_path, sync_immediately=False, dry_run=False):
    path = Path(file_path)
    if path.is_dir():
        results = learn_directory(conn, expert, file_path, sync_immediately, dry_run)
        return {"action": "learn", "expert": expert, "path": file_path,
                "type": "directory", "files_processed": len(results), "results": results}
    elif path.is_file():
        content = learn_file(file_path)
        h = generate_canonical_hash(content)
        if dry_run:
            return {"action": "learn (dry-run)", "expert": expert, "file": file_path,
                    "content_length": len(content), "hash": h,
                    "would_add_to_staging": True, "sync_immediately": sync_immediately}
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO knowledge_staging (expert, chunk_data, hash_canonical)
            VALUES (?, ?, ?)
        """, (expert, content, h))
        sid = cursor.lastrowid
        conn.commit()
        result = {"action": "learn", "expert": expert, "file": file_path,
                  "staging_id": sid, "hash": h, "content_length": len(content), "status": "pending"}
        if sync_immediately:
            result["sync"] = sync(conn, expert, staging_id=sid)
        return result
    else:
        raise ValueError(f"Path nao e arquivo nem diretorio: {file_path}")


def sync(conn, expert, staging_id=None):
    cursor = conn.cursor()
    if staging_id:
        cursor.execute("""
            SELECT id, chunk_data, hash_canonical FROM knowledge_staging
            WHERE id = ? AND expert = ?
        """, (staging_id, expert))
        s = cursor.fetchone()
        if not s:
            return {"action": "sync", "error": "staging nao encontrado", "staging_id": staging_id}
        cursor.execute("SELECT id FROM pages WHERE hash_canonical = ? AND expert = ?",
                       (s["hash_canonical"], expert))
        if cursor.fetchone():
            cursor.execute("DELETE FROM knowledge_staging WHERE id = ?", (staging_id,))
            conn.commit()
            return {"action": "sync", "staging_id": staging_id, "status": "skipped",
                    "reason": "hash ja existe", "hash": s["hash_canonical"]}
        cursor.execute("""
            INSERT INTO pages (expert, tipo, titulo, corpo, hash_canonical)
            VALUES (?, 'auto_learned', ?, ?, ?)
        """, (expert, f"Arquivo aprendido (staging #{staging_id})",
              s["chunk_data"], s["hash_canonical"]))
        cursor.execute("DELETE FROM knowledge_staging WHERE id = ?", (staging_id,))
        conn.commit()
        return {"action": "sync", "staging_id": staging_id, "page_id": cursor.lastrowid,
                "status": "synced", "hash": s["hash_canonical"]}
    else:
        cursor.execute("SELECT COUNT(*) FROM knowledge_staging WHERE expert = ? AND status = 'pending'",
                       (expert,))
        pending = cursor.fetchone()[0]
        if pending == 0:
            return {"action": "sync", "expert": expert, "status": "nothing_to_sync", "pending_count": 0}
        cursor.execute("SELECT id, chunk_data, hash_canonical FROM knowledge_staging WHERE expert = ? AND status = 'pending'",
                       (expert,))
        entries = cursor.fetchall()
        synced = 0
        skipped = 0
        for e in entries:
            cursor.execute("SELECT id FROM pages WHERE hash_canonical = ? AND expert = ?",
                           (e["hash_canonical"], expert))
            if cursor.fetchone():
                skipped += 1
                continue
            cursor.execute("""
                INSERT INTO pages (expert, tipo, titulo, corpo, hash_canonical)
                VALUES (?, 'auto_learned', ?, ?, ?)
            """, (expert, f"Arquivo aprendido (staging #{e['id']})", e["chunk_data"], e["hash_canonical"]))
            synced += 1
        cursor.execute("DELETE FROM knowledge_staging WHERE expert = ? AND status = 'pending'", (expert,))
        conn.commit()
        return {"action": "sync", "expert": expert, "synced": synced,
                "skipped": skipped, "pending_remaining": 0}


def check(conn, expert):
    result = {"expert": expert, "integrity": "ok", "schema_version": SCHEMA_VERSION,
              "tables": {}, "counts": {}, "issues": []}
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        r = cursor.fetchone()[0]
        result["integrity"] = r
        if r != "ok":
            result["issues"].append(f"Integridade: {r}")
    except Exception as e:
        result["integrity"] = "error"
        result["issues"].append(str(e))
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    result["tables"]["present"] = tables
    result["tables"]["expected"] = ["pages", "knowledge_staging", "jobs", "schema_version"]
    cursor.execute("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1")
    row = cursor.fetchone()
    av = row[0] if row else "unknown"
    result["schema_version_actual"] = av
    if av != SCHEMA_VERSION:
        result["issues"].append(f"Schema: esperado {SCHEMA_VERSION}, atual {av}")
    for t in ["pages", "knowledge_staging", "jobs"]:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {t} WHERE expert = ?", (expert,))
            result["counts"][t] = cursor.fetchone()[0]
        except:
            result["counts"][t] = 0
    return result


def list_jobs(conn, expert, status=None, limit=20):
    cursor = conn.cursor()
    q = "SELECT id, expert, command, status, metadata, created_at, started_at, completed_at, error FROM jobs WHERE expert = ?"
    if status:
        q += " AND status = ?"
        cursor.execute(q + " ORDER BY created_at DESC LIMIT ?", (expert, status, limit))
    else:
        cursor.execute(q + " ORDER BY created_at DESC LIMIT ?", (expert, limit))
    return [dict(row) for row in cursor.fetchall()]


def suggest_taxonomy_rules(conn, expert, limit=10):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tipo, COUNT(*) as count FROM pages WHERE expert = ?
        GROUP BY tipo ORDER BY count DESC LIMIT ?
    """, (expert, limit))
    stats = [{"tipo": r[0], "count": r[1]} for r in cursor.fetchall()]
    return {"action": "taxonomist", "expert": expert, "tipo_distribution": stats,
            "suggestions": ["Categorize por: memory, fact, entity, procedure, policy",
                            "Use hash_canonical para deduplica",
                            "Adicione metadados (tags, origem)"]}


def capture_taxonomy(conn, expert, content, suggested_type):
    return remember(conn, expert, suggested_type, None, content)


# === CLI Commands ===

def cmd_init(args):
    db_path = get_brain_db_path(expert=args.name, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.name
    remember(conn, ename, "system", "Brain inicializado",
             f"Brain {ename} inicializado em {datetime.now().isoformat()}")
    conn.close()
    print(json.dumps({"action": "init", "expert": ename, "db_path": db_path,
                      "schema_version": SCHEMA_VERSION, "initialized": True}, indent=2, ensure_ascii=False))
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
    print(json.dumps(recall(conn, ename, args.search, args.limit, args.offset), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_forget(args):
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(forget(conn, ename, args.id, dry_run=args.dry_run), indent=2, ensure_ascii=False))
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
    print(json.dumps(list_jobs(conn, ename, args.status, args.limit), indent=2, ensure_ascii=False))
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
  consolidate    - Duplica conhecimento (use --dry-run primeiro!)
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

    p_con = sp.add_parser('consolidate', help='Duplica conhecimento')
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
