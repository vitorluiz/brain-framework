#!/usr/bin/env python3
"""
Brain — Expert Nativo do Brain Framework
O Brain é o expert especial nativo do framework. Ele substitui o agente
default do Hermes e gerencia a infraestrutura de conocimiento: cria profiles,
gerencia brains, faz backups, atualizações, administrador, e todos os
comandos de manipulação de conhecimento (via brain_tool interno).
O Brain NÃO é um agente de atendimento (como Maria ou José). Ele é o
gestor nativo — o "cerebro" que gerencia os outros cérebros.

Uso:
  brain add profile <nome>     - Cria um novo profile
  brain list profiles           - Lista todos os profiles
  brain remove profile <nome>  - Remove um profile
  brain init --name <nome>     - Inicializa um brain.db (via brain_tool)
  brain remember --expert ...  - Adiciona conhecimento (via brain_tool)
  brain recall --expert ...    - Recupera conhecimento
  brain forget --expert ...    - Remove conhecimento
  brain synthesize --expert ...- Gera síntese
  brain consolidate --expert ...- Deduplica conhecimento
  brain learn --expert ...     - Processa arquivo/diretorio
  brain sync --expert ...      - Move staging para principal
  brain check --expert ...     - Verifica integridade
  brain jobs --expert ...      - Lista jobs
  brain taxonomist --expert ...- Sugeri regras de taxonomia
  brain capture --expert ...   - Captura classificação
  brain global learn --path ...- Aprende conhecimento global
  brain backup                 - Backup de todos os brains
  brain update                  - Atualiza framework
  brain sync all               - Sync de todos os brains
  brain admin list             - Lista administradores
  brain admin add TYPE ID      - Adiciona administrador
  brain admin remove ID        - Remove administrador
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

SCHEMA_VERSION = "1.0.0"

# === Importação do brain_tool (CLI core de manipulação de conhecimento) ===
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from brain_tool import (
        get_brain_db_path, get_db_connection, remember, recall, forget,
        synthesize, consolidate, learn, sync, check, list_jobs,
        suggest_taxonomy_rules, capture_taxonomy,
        SCHEMA_VERSION, initialize_schema
    )
    BRAIN_TOOL_AVAILABLE = True
except ImportError as e:
    print(f"AVISO: brain_tool nao importavel: {e}", file=sys.stderr)
    print("Alguns comandos podem nao funcionar.", file=sys.stderr)
    BRAIN_TOOL_AVAILABLE = False

# === Se brain_tool nao disponivel, fallback minimo ===
if not BRAIN_TOOL_AVAILABLE:
    import sqlite3 as _sqlite3
    import hashlib as _hashlib

    def get_brain_db_path(expert=None, brain_path=None, global_brain=False):
        DEFAULT_BRAIN_ROOT = os.environ.get('BRAIN_ROOT',
            os.path.expanduser("~/.hermes/brain"))
        GLOBAL_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "global")
        EXPERTS_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "experts")
        if brain_path:
            return brain_path
        if global_brain:
            return os.path.join(GLOBAL_DIR, "brain.db")
        if expert:
            return os.path.join(EXPERTS_DIR, expert, "brain.db")
        return os.path.join(os.getcwd(), "brain.db")

    def get_db_connection(db_path):
        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row
        _apply_migration_if_needed(conn)
        return conn

    def _apply_migration_if_needed(conn):
        cursor = conn.cursor()
        _create_schema_version_table(conn)
        # Tabela pages
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pages'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(pages)")
            cols = [r[1] for r in cursor.fetchall()]
            if "expert" not in cols:
                cursor.execute("ALTER TABLE pages ADD COLUMN expert TEXT NOT NULL DEFAULT 'unknown'")
            if "hash_canonical" not in cols:
                cursor.execute("ALTER TABLE pages ADD COLUMN hash_canonical TEXT")
            if "tipo" not in cols:
                cursor.execute("ALTER TABLE pages ADD COLUMN tipo TEXT NOT NULL DEFAULT 'memory'")
            if "titulo" not in cols:
                cursor.execute("ALTER TABLE pages ADD COLUMN titulo TEXT")
            if "updated_at" not in cols:
                cursor.execute("ALTER TABLE pages ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        else:
            cursor.execute("""
                CREATE TABLE pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expert TEXT NOT NULL, tipo TEXT NOT NULL,
                    titulo TEXT, corpo TEXT NOT NULL,
                    hash_canonical TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        # knowledge_staging
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_staging'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(knowledge_staging)")
            cols = [r[1] for r in cursor.fetchall()]
            if "expert" not in cols:
                cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN expert TEXT NOT NULL DEFAULT 'unknown'")
            if "hash_canonical" not in cols:
                cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN hash_canonical TEXT")
            if "status" not in cols:
                cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN status TEXT DEFAULT 'pending'")
            if "chunk_data" not in cols:
                cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN chunk_data TEXT NOT NULL")
            if "created_at" not in cols:
                cursor.execute("ALTER TABLE knowledge_staging ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        else:
            cursor.execute("""
                CREATE TABLE knowledge_staging (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expert TEXT NOT NULL, chunk_data TEXT NOT NULL,
                    hash_canonical TEXT NOT NULL, status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            """)
        # jobs
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(jobs)")
            cols = [r[1] for r in cursor.fetchall()]
            if "expert" not in cols:
                cursor.execute("ALTER TABLE jobs ADD COLUMN expert TEXT NOT NULL DEFAULT 'unknown'")
            if "status" not in cols:
                cursor.execute("ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'enqueued'")
            if "command" not in cols:
                cursor.execute("ALTER TABLE jobs ADD COLUMN command TEXT NOT NULL")
            if "metadata" not in cols:
                cursor.execute("ALTER TABLE jobs ADD COLUMN metadata TEXT")
            if "created_at" not in cols:
                cursor.execute("ALTER TABLE jobs ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        else:
            cursor.execute("""
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY, expert TEXT NOT NULL,
                    command TEXT NOT NULL, status TEXT DEFAULT 'enqueued',
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP, completed_at TIMESTAMP, error TEXT
                )
            """)
        cursor.execute("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1")
        row = cursor.fetchone()
        current_version = row[0] if row else "0.0.0"
        if current_version < SCHEMA_VERSION:
            cursor.execute("""
                INSERT INTO schema_version (version, description)
                VALUES (?, ?)
            """, (SCHEMA_VERSION, f"Migration for {SCHEMA_VERSION}"))
            conn.commit()

    def _create_schema_version_table(conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )
        """)

    def remember(conn, expert, tipo, titulo=None, corpo="", hash_canonical=None, dry_run=False):
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expert TEXT NOT NULL, tipo TEXT NOT NULL,
                titulo TEXT, corpo TEXT NOT NULL,
                hash_canonical TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if not hash_canonical:
            hash_canonical = _hashlib.sha256(corpo.encode('utf-8')).hexdigest()
        cursor.execute("""
            INSERT INTO pages (expert, tipo, titulo, corpo, hash_canonical)
            VALUES (?, ?, ?, ?, ?)
        """, (expert, tipo, titulo, corpo, hash_canonical))
        conn.commit()
        return {"action": "remember", "id": cursor.lastrowid, "expert": expert}

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

    def learn(conn, expert, file_path, sync_immediately=False, dry_run=False):
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expert TEXT NOT NULL, chunk_data TEXT NOT NULL,
                hash_canonical TEXT NOT NULL, status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except:
            content = f"Arquivo: {file_path}"
        h = _hashlib.sha256(content.encode('utf-8')).hexdigest()
        if dry_run:
            return {"action": "learn (dry-run)", "expert": expert, "file": file_path, "hash": h}
        cursor.execute("""
            INSERT INTO knowledge_staging (expert, chunk_data, hash_canonical)
            VALUES (?, ?, ?)
        """, (expert, content, h))
        conn.commit()
        return {"action": "learn", "expert": expert, "staging_id": cursor.lastrowid, "hash": h}

    def sync(conn, expert, staging_id=None):
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expert TEXT NOT NULL, tipo TEXT NOT NULL,
                titulo TEXT, corpo TEXT NOT NULL,
                hash_canonical TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if staging_id:
            cursor.execute("SELECT id, chunk_data, hash_canonical FROM knowledge_staging WHERE id = ? AND expert = ?",
                           (staging_id, expert))
            s = cursor.fetchone()
            if not s:
                return {"action": "sync", "error": "staging nao encontrado"}
            cursor.execute("SELECT id FROM pages WHERE hash_canonical = ? AND expert = ?",
                           (s["hash_canonical"], expert))
            if cursor.fetchone():
                cursor.execute("DELETE FROM knowledge_staging WHERE id = ?", (staging_id,))
                conn.commit()
                return {"action": "sync", "status": "skipped", "reason": "hash ja existe"}
            cursor.execute("""
                INSERT INTO pages (expert, tipo, titulo, corpo, hash_canonical)
                VALUES (?, 'auto_learned', ?, ?, ?)
            """, (expert, f"Arquivo aprendido (staging #{staging_id})", s["chunk_data"], s["hash_canonical"]))
            cursor.execute("DELETE FROM knowledge_staging WHERE id = ?", (staging_id,))
            conn.commit()
            return {"action": "sync", "status": "synced", "page_id": cursor.lastrowid}
        cursor.execute("SELECT COUNT(*) FROM knowledge_staging WHERE expert = ? AND status = 'pending'", (expert,))
        p = cursor.fetchone()[0]
        if p == 0:
            return {"action": "sync", "status": "nothing_to_sync", "pending_count": 0}
        cursor.execute("SELECT id, chunk_data, hash_canonical FROM knowledge_staging WHERE expert = ? AND status = 'pending'", (expert,))
        entries = cursor.fetchall()
        synced = 0
        for e in entries:
            cursor.execute("SELECT id FROM pages WHERE hash_canonical = ? AND expert = ?", (e["hash_canonical"], expert))
            if cursor.fetchone():
                continue
            cursor.execute("""
                INSERT INTO pages (expert, tipo, titulo, corpo, hash_canonical)
                VALUES (?, 'auto_learned', ?, ?, ?)
            """, (expert, f"Arquivo aprendido (staging #{e['id']})", e["chunk_data"], e["hash_canonical"]))
            synced += 1
        cursor.execute("DELETE FROM knowledge_staging WHERE expert = ? AND status = 'pending'", (expert,))
        conn.commit()
        return {"action": "sync", "synced": synced, "skipped": len(entries) - synced}

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

# === Configurações globais ===
DEFAULT_BRAIN_ROOT = os.environ.get('BRAIN_ROOT',
    os.path.expanduser("~/.hermes/brain"))
GLOBAL_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "global")
EXPERTS_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "experts")
BACKUPS_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "backups")
ADMIN_CONFIG_FILE = os.path.join(DEFAULT_BRAIN_ROOT, "admins.json")

# O "home" do framework (onde o codigo fonte mora, para git pull)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Caminho do venv do Hermes Agent (para criar alias)
HERMES_VENV_PYTHON = "/home/hermes/.hermes/hermes-agent/venv/bin/python"
HERMES_CLI_MAIN = "-m hermes_cli.main"


def load_admins() -> Dict[str, Any]:
    if os.path.exists(ADMIN_CONFIG_FILE):
        with open(ADMIN_CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {"admins": [], "groups": {}}


def save_admins(admins: Dict[str, Any]) -> None:
    with open(ADMIN_CONFIG_FILE, 'w') as f:
        json.dump(admins, f, indent=2)


def get_expert_names() -> List[str]:
    if not os.path.exists(EXPERTS_DIR):
        return []
    return sorted([d.name for d in os.scandir(EXPERTS_DIR) if d.is_dir()])


# === Comandos do Brain ===

def cmd_add_profile(args) -> int:
    """Cria um novo profile no framework."""
    name = args.name
    if not name:
        print("Erro: informe o nome do profile. Ex: brain add profile maria", file=sys.stderr)
        return 1

    print(f"\n{'='*50}")
    print(f"Brain: Adicionando profile '{name}'")
    print(f"{'='*50}")

    # 1. Tenta executar hermes profile create (se disponível)
    hermes_created = False
    try:
        result = subprocess.run(
            ["hermes", "profile", "create", name],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print(f"  + Hermes profile '{name}' criado")
            hermes_created = True
        else:
            print(f"  - Hermes profile falhou: {result.stderr.strip()}", file=sys.stderr)
    except FileNotFoundError:
        print(f"  - 'hermes' CLI nao encontrado no PATH. Profile Hermes nao criado.", file=sys.stderr)
    except Exception as e:
        print(f"  - Erro ao criar profile Hermes: {e}", file=sys.stderr)

    # 2. Cria brain.db (sempre)
    expert_dir = os.path.join(EXPERTS_DIR, name)
    if not os.path.exists(expert_dir):
        os.makedirs(expert_dir, exist_ok=True)

    brain_path = get_brain_db_path(expert=name)
    try:
        conn = get_db_connection(brain_path)
        remember(conn, name, "system", "Brain inicializado",
                 f"Brain '{name}' inicializado em {datetime.now().isoformat()}")
        conn.close()
        print(f"  + Brain.db criado: {brain_path}")
    except Exception as e:
        print(f"  - Erro ao criar brain.db: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    # 3. Adiciona alias no bashrc
    alias_created = False
    try:
        bashrc = os.path.expanduser("~/.bashrc")
        alias_cmd = f"alias {name}='{HERMES_VENV_PYTHON} {HERMES_CLI_MAIN} --profile {name}'"
        if os.path.exists(bashrc):
            with open(bashrc, 'r') as f:
                content = f.read()
            if alias_cmd not in content:
                with open(bashrc, 'a') as f:
                    f.write(f"\n{alias_cmd}\n")
                alias_created = True
                print(f"  + Alias adicionado ao ~/.bashrc: {alias_cmd}")
        else:
            print(f"  - ~/.bashrc nao encontrado. Alias nao criado.", file=sys.stderr)
    except Exception as e:
        print(f"  - Erro ao adicionar alias: {e}", file=sys.stderr)

    # Resumo final
    print(f"\n  = Profile '{name}' criado")
    print(f"    - Hermes profile: {'sim' if hermes_created else 'nao'}")
    print(f"    - Brain.db: {brain_path}")
    print(f"    - Alias: {'sim' if alias_created else 'nao'}")

    return 0


def cmd_list_profiles(args) -> int:
    """Lista todos os profiles configurados no framework."""
    experts = get_expert_names()

    print(f"\n=== Brain: Perfis configurados ({len(experts)}) ===\n")

    if not experts:
        print("Nenhum profile encontrado.")
        print("\nPara criar um profile: brain add profile <nome>")
        return 0

    for name in experts:
        brain_path = get_brain_db_path(expert=name)
        exists = os.path.exists(brain_path)
        status = "✓" if exists else "✗"
        print(f"  {status} {name}")
        if exists:
            try:
                conn = get_db_connection(brain_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pages'")
                if cursor.fetchone():
                    cursor.execute("PRAGMA table_info(pages)")
                    columns = [r[1] for r in cursor.fetchall()]
                    if "expert" in columns:
                        cursor.execute("SELECT COUNT(*) FROM pages WHERE expert = ?", (name,))
                        count = cursor.fetchone()[0]
                        print(f"      brain.db: {brain_path}")
                        print(f"      conhecimentos: {count}")
                    else:
                        print(f"      brain.db: {brain_path}")
                        print(f"      schema: antigo (migrar para v{SCHEMA_VERSION})")
                else:
                    print(f"      brain.db: {brain_path} (sem tabela pages)")
                conn.close()
            except Exception as e:
                print(f"      brain.db: {brain_path}")
                print(f"      erro ao ler: {e}", file=sys.stderr)

    return 0


def cmd_remove_profile(args) -> int:
    """Remove um profile e seu brain.db."""
    name = args.name
    if not name:
        print("Erro: informe o nome do profile.", file=sys.stderr)
        return 1

    experts = get_expert_names()
    if name not in experts:
        print(f"Profile '{name}' nao encontrado.", file=sys.stderr)
        return 1

    brain_path = get_brain_db_path(expert=name)
    expert_dir = os.path.dirname(brain_path)

    print(f"\nRemovendo profile: {name}")
    print(f"  - brain.db: {brain_path}")
    print(f"  - diretorio: {expert_dir}")

    try:
        if os.path.exists(brain_path):
            os.remove(brain_path)
            print(f"  + brain.db removido")
        if os.path.exists(expert_dir):
            if not os.listdir(expert_dir):
                os.rmdir(expert_dir)
                print(f"  + diretorio removido")
            else:
                print(f"  - diretorio nao vazio, nao removido")

        # Remove alias do bashrc
        bashrc = os.path.expanduser("~/.bashrc")
        alias_cmd = f"alias {name}='{HERMES_VENV_PYTHON} {HERMES_CLI_MAIN} --profile {name}'"
        if os.path.exists(bashrc):
            with open(bashrc, 'r') as f:
                lines = f.readlines()
            with open(bashrc, 'w') as f:
                for line in lines:
                    if line.strip() != alias_cmd:
                        f.write(line)
            print(f"  + alias removido do ~/.bashrc")

        print(f"\n= Profile '{name}' removido!")
        return 0
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


def cmd_global_learn(args) -> int:
    """Aprende conhecimento global (para o brain global)."""
    print(f"\n=== Brain: Global Learn ===")
    brain_path = get_brain_db_path(global_brain=True)
    conn = get_db_connection(brain_path)
    try:
        if args.path:
            if not BRAIN_TOOL_AVAILABLE:
                print("Erro: brain_tool nao disponivel para learn.", file=sys.stderr)
                conn.close()
                return 1
            result = learn(conn, "global", args.path, args.sync, dry_run=args.dry_run)
        elif args.content:
            if BRAIN_TOOL_AVAILABLE:
                result = remember(conn, "global", "global_policy",
                                  args.title or "Conteudo global",
                                  args.content, dry_run=args.dry_run)
            else:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pages'")
                if not cursor.fetchone():
                    cursor.execute("""
                        CREATE TABLE pages (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            expert TEXT NOT NULL, tipo TEXT NOT NULL,
                            titulo TEXT, corpo TEXT NOT NULL,
                            hash_canonical TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                else:
                    cursor.execute("PRAGMA table_info(pages)")
                    cols = [r[1] for r in cursor.fetchall()]
                    if "expert" not in cols:
                        cursor.execute("ALTER TABLE pages ADD COLUMN expert TEXT NOT NULL DEFAULT 'unknown'")
                    if "hash_canonical" not in cols:
                        cursor.execute("ALTER TABLE pages ADD COLUMN hash_canonical TEXT")
                cursor.execute("""
                    INSERT INTO pages (expert, tipo, titulo, corpo, hash_canonical)
                    VALUES (?, 'global_policy', ?, ?, ?)
                """, ("global", args.title or "Conteudo global", args.content, None))
                conn.commit()
                result = {"action": "remember", "expert": "global", "status": "ok"}
        else:
            print("Erro: especifique --path ou --content", file=sys.stderr)
            conn.close()
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        conn.close()
        return 0
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        traceback.print_exc()
        conn.close()
        return 1


def cmd_backup(args) -> int:
    """Faz backup de todos os brains (global + experts)."""
    print(f"\n=== Brain: Backup de todos os brains ===")
    if not os.path.exists(BACKUPS_DIR):
        os.makedirs(BACKUPS_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUPS_DIR, f"backup_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)

    brains = []
    global_path = get_brain_db_path(global_brain=True)
    if os.path.exists(global_path):
        brains.append(("global", global_path))
    for name in get_expert_names():
        ep = get_brain_db_path(expert=name)
        if os.path.exists(ep):
            brains.append((name, ep))

    count = 0
    for name, bp in brains:
        try:
            ndir = os.path.join(backup_dir, name)
            os.makedirs(ndir, exist_ok=True)
            shutil.copy2(bp, os.path.join(ndir, "brain.db"))
            sp_src = os.path.join(os.path.dirname(bp), ".brain_schema_template.yaml")
            if os.path.exists(sp_src):
                shutil.copy2(sp_src, os.path.join(ndir, ".brain_schema_template.yaml"))
            count += 1
            print(f"  + {name}")
        except Exception as e:
            print(f"  - {name}: {e}", file=sys.stderr)

    manifest = {
        "timestamp": timestamp,
        "brains": [{"name": n, "path": p} for n, p in brains],
        "backup_dir": backup_dir,
        "count": count
    }
    with open(os.path.join(backup_dir, "manifest.json"), 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f"\n= Backup concluido: {count} brains")
    print(f"  Diretorio: {backup_dir}")
    return 0


def cmd_update(args) -> int:
    """Atualiza o Brain Framework via git pull."""
    print(f"\n=== Brain: Atualizando framework ===")
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode == 0:
            print("= Framework atualizado!")
            if result.stdout and "Already up to date" not in result.stdout:
                print(result.stdout)
            return 0
        else:
            print(f"Erro: {result.stderr}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1


def cmd_sync_all(args) -> int:
    """Faz sync de todos os brains (global + experts)."""
    print(f"\n=== Brain: Sync de todos os brains ===")
    all_experts = ["global"] + get_expert_names()
    results = []

    for name in all_experts:
        bp = get_brain_db_path(global_brain=(name == "global"),
                               expert=name if name != "global" else None)
        if not os.path.exists(bp):
            results.append({"name": name, "status": "not_found"})
            continue
        try:
            conn = get_db_connection(bp)
            if BRAIN_TOOL_AVAILABLE:
                result = sync(conn, name)
            else:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM knowledge_staging WHERE expert = ? AND status = 'pending'",
                    (name,))
                pending = cursor.fetchone()[0]
                if pending == 0:
                    result = {"action": "sync", "status": "nothing_to_sync", "pending_count": 0}
                else:
                    result = {"action": "sync", "status": "skipped",
                              "reason": "sync nao disponivel sem brain_tool"}
            results.append({"name": name, **result})
            conn.close()
        except Exception as e:
            results.append({"name": name, "status": "error", "error": str(e)})

    for r in results:
        if r["status"] == "nothing_to_sync":
            print(f"  + {r['name']}: nada para sync")
        elif r["status"] == "not_found":
            print(f"  - {r['name']}: nao encontrado")
        elif r.get("synced", 0) > 0:
            print(f"  + {r['name']}: {r['synced']} syncados")
        elif r.get("status") == "error":
            print(f"  - {r['name']}: {r['error']}")
        else:
            print(f"  ? {r['name']}: {r}")

    print(f"\n= Sync concluido: {len(results)} brains")
    return 0


def cmd_admin_list(args) -> int:
    """Lista administradores configurados."""
    admins = load_admins()
    print(f"\n=== Administradores ===")
    print(f"Admins globais ({len(admins.get('admins', []))}):")
    for a in admins.get("admins", []):
        print(f"  - {a}")
    print(f"\nGrupos administrativos:")
    for g, m in admins.get("groups", {}).items():
        if m:
            print(f"  Grupo '{g}': {len(m)} membros")
        else:
            print(f"  Grupo '{g}': vazio")
    return 0


def cmd_admin_add(args) -> int:
    """Adiciona um administrador."""
    admins = load_admins()
    identifier = args.identifier

    if args.type == "whatsapp":
        key = f"wa:{identifier}"
        admins["admins"].append(key)
        print(f"+ Admin WhatsApp adicionado: {identifier}")
    elif args.type == "cli":
        key = f"cli:{identifier}"
        admins["admins"].append(key)
        print(f"+ Admin CLI adicionado: {identifier}")
    elif args.type == "grupo":
        gn = args.group
        if gn not in admins["groups"]:
            admins["groups"][gn] = []
        if identifier not in admins["groups"][gn]:
            admins["groups"][gn].append(identifier)
            print(f"+ Membro '{identifier}' adicionado ao grupo '{gn}'")
        else:
            print(f"Ja membro do grupo '{gn}': {identifier}")
    else:
        print(f"Tipo desconhecido: {args.type}", file=sys.stderr)
        return 1

    save_admins(admins)
    return 0


def cmd_admin_remove(args) -> int:
    """Remove um administrador."""
    admins = load_admins()
    identifier = args.identifier

    orig = len(admins["admins"])
    admins["admins"] = [
        a for a in admins["admins"]
        if a.replace("wa:", "").replace("cli:", "") != identifier
    ]
    if len(admins["admins"]) < orig:
        save_admins(admins)
        print(f"+ Admin removido: {identifier}")
        return 0

    for g, m in admins["groups"].items():
        if identifier in m:
            m.remove(identifier)
            save_admins(admins)
            print(f"+ Membro removido do grupo '{g}': {identifier}")
            return 0

    print(f"Admin nao encontrado: {identifier}", file=sys.stderr)
    return 1


# === Comandos do brain_tool (integrados no Brain) ===

def cmd_init(args) -> int:
    """Inicializa um novo brain.db."""
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
                      "schema_version": SCHEMA_VERSION, "initialized": True},
                     indent=2, ensure_ascii=False))
    return 0


def cmd_remember(args) -> int:
    """Adiciona conhecimento."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(remember(conn, ename, args.tipo, args.title, args.content,
                              dry_run=args.dry_run), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_recall(args) -> int:
    """Recupera conhecimento."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(recall(conn, ename, args.search, args.limit, args.offset),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_forget(args) -> int:
    """Remove conhecimento."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(forget(conn, ename, args.id, dry_run=args.dry_run),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_synthesize(args) -> int:
    """Gera síntese do conhecimento."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(synthesize(conn, ename, args.type), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_consolidate(args) -> int:
    """Deduplica conhecimento."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(consolidate(conn, ename, args.threshold, dry_run=args.dry_run),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_learn(args) -> int:
    """Processa arquivo/diretorio para staging."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(learn(conn, ename, args.path, args.sync, dry_run=args.dry_run),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_sync(args) -> int:
    """Move staging para tabela principal."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(sync(conn, ename), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_check(args) -> int:
    """Verifica integridade do brain.db."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(check(conn, ename), indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_jobs(args) -> int:
    """Lista jobs."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(list_jobs(conn, ename, args.status, args.limit),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_taxonomist(args) -> int:
    """Sugeri regras de taxonomia."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(suggest_taxonomy_rules(conn, ename, args.limit),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


def cmd_capture(args) -> int:
    """Captura classificação de taxonomia."""
    db_path = get_brain_db_path(expert=args.expert, global_brain=args.global_brain,
                               brain_path=args.brain_path)
    conn = get_db_connection(db_path)
    ename = "global" if args.global_brain else args.expert
    print(json.dumps(capture_taxonomy(conn, ename, args.content, args.type),
                     indent=2, ensure_ascii=False))
    conn.close()
    return 0


# === Main ===

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="brain",
        description="Brain — Expert Nativo do Brain Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
O Brain é o expert especial nativo do framework. Ele substitui o agente
default do Hermes e gerencia a infraestrutura de conhecimento.

Brain NÃO é um agente de atendimento (como Maria ou José).
Brain é o gestor nativo — o "cerebro" que gerencia os outros cérebros.

Comandos de Gestão:
  add profile NAME     - Cria um novo profile (hermes profile create + brain.db + alias)
  list profiles        - Lista todos os profiles configurados
  remove profile NAME  - Remove um profile e seu brain.db
  global learn         - Aprende conhecimento para o brain global
  backup               - Backup de todos os brains (global + experts)
  update               - Atualiza o framework via git pull origin main
  sync all             - Faz sync de todos os brains
  admin list           - Lista administradores configurados
  admin add TYPE ID    - Adiciona administrador (whatsapp/cli/grupo)
  admin remove ID      - Remove administrador

Comandos de Conhecimento (via brain_tool):
  init --name NAME     - Inicializa um novo brain.db
  remember --expert ...-- Adiciona conhecimento
  recall --expert ...  - Recupera conhecimento
  forget --expert ...  - Remove conhecimento (use --dry-run!)
  synthesize --expert ..- Gera síntese do conhecimento
  consolidate --expert ..- Deduplica conhecimento (use --dry-run!)
  learn --expert ...   - Processa arquivo/diretorio para staging
  sync-tb --expert ...    - Move staging para tabela principal
  check --expert ...   - Verifica integridade + schema version
  jobs --expert ...    - Lista jobs
  taxonomist --expert ..- Sugeri regras de taxonomia
  capture --expert ... - Captura classificação de taxonomia

Para conhecer mais:
  brain <comando> --help
        """)

    sp = parser.add_subparsers(dest='command')

    # --- Gestão ---
    p_add = sp.add_parser('add', help='Adiciona um novo profile')
    p_add_sub = p_add.add_subparsers(dest='subcommand')
    p_add_profile = p_add_sub.add_parser('profile', help='Adiciona um novo profile')
    p_add_profile.add_argument('name', nargs='?', help='Nome do profile')
    p_add.set_defaults(func=lambda args: cmd_add_profile(args))

    p_list = sp.add_parser('list', help='Lista profiles')
    p_list_sub = p_list.add_subparsers(dest='subcommand')
    p_list_profiles = p_list_sub.add_parser('profiles', help='Lista todos os profiles')
    p_list.set_defaults(func=lambda args: cmd_list_profiles(args))

    p_rem = sp.add_parser('remove', help='Remove um profile')
    p_rem_sub = p_rem.add_subparsers(dest='subcommand')
    p_rem_profile = p_rem_sub.add_parser('profile', help='Remove um profile')
    p_rem_profile.add_argument('name', nargs='?', help='Nome do profile')
    p_rem.set_defaults(func=lambda args: cmd_remove_profile(args))

    p_global = sp.add_parser('global', help='Operacoes com brain global')
    p_global_sub = p_global.add_subparsers(dest='subcommand')
    p_global_learn = p_global_sub.add_parser('learn', help='Aprende conhecimento global')
    p_global_learn.add_argument('--path', help='Caminho do arquivo/diretorio')
    p_global_learn.add_argument('--content', help='Conteudo direto')
    p_global_learn.add_argument('--title', help='Titulo do conhecimento')
    p_global_learn.add_argument('--sync', action='store_true', help='Sync apos learn')
    p_global_learn.add_argument('--dry-run', action='store_true', help='Preview sem executar')
    p_global.set_defaults(func=lambda args: cmd_global_learn(args))

    p_backup = sp.add_parser('backup', help='Backup de todos os brains')
    p_backup.set_defaults(func=lambda args: cmd_backup(args))

    p_update = sp.add_parser('update', help='Atualiza o framework')
    p_update.set_defaults(func=lambda args: cmd_update(args))

    p_sync = sp.add_parser('sync', help='Sincronizacao')
    p_sync_sub = p_sync.add_subparsers(dest='subcommand')
    p_sync_all = p_sync_sub.add_parser('all', help='Sync de todos os brains')
    p_sync.set_defaults(func=lambda args: cmd_sync_all(args))

    p_admin = sp.add_parser('admin', help='Gestao de administradores')
    p_admin_sub = p_admin.add_subparsers(dest='subcommand')

    p_admin_list = p_admin_sub.add_parser('list', help='Lista administradores')
    p_admin_list.set_defaults(func=lambda args: cmd_admin_list(args))

    p_admin_add = p_admin_sub.add_parser('add', help='Adiciona administrador')
    p_admin_add.add_argument('type', choices=['whatsapp', 'cli', 'grupo'],
                             help='Tipo de administrador')
    p_admin_add.add_argument('identifier', help='Identificador')
    p_admin_add.add_argument('--group', help='Nome do grupo (para tipo grupo)')
    p_admin_add.set_defaults(func=lambda args: cmd_admin_add(args))

    p_admin_remove = p_admin_sub.add_parser('remove', help='Remove administrador')
    p_admin_remove.add_argument('identifier', help='Identificador')
    p_admin_remove.set_defaults(func=lambda args: cmd_admin_remove(args))

    # --- Conhecimento (brain_tool) ---
    # Init
    p_init = sp.add_parser('init', help='Inicializa um novo brain.db (brain_tool)')
    p_init.add_argument('--name', required=True, help='Nome do expert')
    p_init.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_init.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_init.set_defaults(func=lambda args: cmd_init(args))

    # Remember
    p_rem = sp.add_parser('remember', help='Adiciona conhecimento (brain_tool)')
    p_rem.add_argument('--expert', required=True, help='Nome do expert')
    p_rem.add_argument('--tipo', required=True, help='Tipo: memory, fact, entity, procedure, policy, system')
    p_rem.add_argument('--title', help='Titulo da entrada')
    p_rem.add_argument('--content', required=True, help='Conteudo do conhecimento')
    p_rem.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_rem.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_rem.add_argument('--dry-run', action='store_true', help='Preview sem executar')
    p_rem.set_defaults(func=lambda args: cmd_remember(args))

    # Recall
    p_rec = sp.add_parser('recall', help='Recupera conhecimento (brain_tool)')
    p_rec.add_argument('--expert', required=True, help='Nome do expert')
    p_rec.add_argument('--search', help='Termo de busca')
    p_rec.add_argument('--limit', type=int, default=10, help='Limite de resultados')
    p_rec.add_argument('--offset', type=int, default=0, help='Offset de resultados')
    p_rec.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_rec.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_rec.set_defaults(func=lambda args: cmd_recall(args))

    # Forget
    p_for = sp.add_parser('forget', help='Remove conhecimento (brain_tool)')
    p_for.add_argument('--expert', required=True, help='Nome do expert')
    p_for.add_argument('--id', type=int, required=True, help='ID da entrada')
    p_for.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_for.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_for.add_argument('--dry-run', action='store_true', help='Preview sem executar')
    p_for.set_defaults(func=lambda args: cmd_forget(args))

    # Synthesize
    p_syn = sp.add_parser('synthesize', help='Gera sintese do conhecimento (brain_tool)')
    p_syn.add_argument('--expert', required=True, help='Nome do expert')
    p_syn.add_argument('--type', default='summary', help='Tipo de sintese')
    p_syn.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_syn.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_syn.set_defaults(func=lambda args: cmd_synthesize(args))

    # Consolidate
    p_con = sp.add_parser('consolidate', help='Deduplica conhecimento (brain_tool)')
    p_con.add_argument('--expert', required=True, help='Nome do expert')
    p_con.add_argument('--threshold', type=float, default=0.8, help='Limite de similaridade')
    p_con.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_con.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_con.add_argument('--dry-run', action='store_true', help='Preview sem executar')
    p_con.set_defaults(func=lambda args: cmd_consolidate(args))

    # Learn
    p_learn = sp.add_parser('learn', help='Processa arquivo/diretorio para staging (brain_tool)')
    p_learn.add_argument('--expert', required=True, help='Nome do expert')
    p_learn.add_argument('--path', required=True, help='Caminho do arquivo ou diretorio')
    p_learn.add_argument('--sync', action='store_true', help='Sync imediatamente apos learn')
    p_learn.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_learn.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_learn.add_argument('--dry-run', action='store_true', help='Preview sem executar')
    p_learn.set_defaults(func=lambda args: cmd_learn(args))

    # Sync (brain_tool) — usa sync-tb para evitar conflito com sync all
    p_sync_bt = sp.add_parser('sync-tb', help='Move staging para principal (brain_tool)')
    p_sync_bt.add_argument('--expert', required=True, help='Nome do expert')
    p_sync_bt.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_sync_bt.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_sync_bt.set_defaults(func=lambda args: cmd_sync(args))

    # Check
    p_chk = sp.add_parser('check', help='Verifica integridade (brain_tool)')
    p_chk.add_argument('--expert', required=True, help='Nome do expert')
    p_chk.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_chk.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_chk.set_defaults(func=lambda args: cmd_check(args))

    # Jobs
    p_job = sp.add_parser('jobs', help='Lista jobs (brain_tool)')
    p_job.add_argument('--expert', required=True, help='Nome do expert')
    p_job.add_argument('--status', help='Filtra por status (enqueued, processing, completed, failed)')
    p_job.add_argument('--limit', type=int, default=20, help='Limite de resultados')
    p_job.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_job.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_job.set_defaults(func=lambda args: cmd_jobs(args))

    # Taxonomist
    p_tax = sp.add_parser('taxonomist', help='Sugeri regras de taxonomia (brain_tool)')
    p_tax.add_argument('--expert', required=True, help='Nome do expert')
    p_tax.add_argument('--limit', type=int, default=10, help='Limite de resultados')
    p_tax.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_tax.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_tax.set_defaults(func=lambda args: cmd_taxonomist(args))

    # Capture
    p_cap = sp.add_parser('capture', help='Captura classificacao de taxonomia (brain_tool)')
    p_cap.add_argument('--expert', required=True, help='Nome do expert')
    p_cap.add_argument('--type', required=True, help='Tipo sugerido pela taxonomia')
    p_cap.add_argument('--content', required=True, help='Conteudo a capturar')
    p_cap.add_argument('--brain-path', help='Caminho explicito para brain.db')
    p_cap.add_argument('--global', action='store_true', dest='global_brain', help='Usa brain global')
    p_cap.set_defaults(func=lambda args: cmd_capture(args))

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if hasattr(args, 'func'):
        return args.func(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
