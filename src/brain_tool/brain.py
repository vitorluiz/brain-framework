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
  brain soul <nome>            - Cria/edita o SOUL.md (persona) do profile
  brain model <nome> [modelo]  - Define o brain (LLM) do profile
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
  brain restore --from <ts>    - Restaura brains de um backup
  brain verify                 - Verifica integridade dos checkpoints assinados
  brain log --scope ...        - Histórico de commits de um scope
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
__version__ = "1.0.0"

# === Importação do brain_tool (CLI core de manipulação de conhecimento) ===
from brain_tool import (
    get_brain_db_path, get_db_connection, remember, recall, forget,
    synthesize, consolidate, learn, sync, check, list_jobs,
    suggest_taxonomy_rules, capture_taxonomy, count_pages,
    SCHEMA_VERSION, get_brain_root, list_expert_names,
)
from brain_tool.auth import (
    admin_config_file, load_admins, save_admins, is_admin, is_group_member,
)
from brain_tool.db import dispose_engine_for_path
from brain_tool.checkpoints import verify_scope, history

# === (fallback sqlite3 legado descontinuado — brain_tool sempre importável) ===
if False:
    import sqlite3 as _sqlite3
    import hashlib as _hashlib

    def get_brain_db_path(expert=None, brain_path=None, global_brain=False):
        DEFAULT_BRAIN_ROOT = os.environ.get('BRAIN_ROOT',
            os.path.expanduser("~/.hermes/brain"))
        GLOBAL_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "global")
        if brain_path:
            return brain_path
        if global_brain:
            return os.path.join(GLOBAL_DIR, "brain.db")
        if expert:
            return os.path.join(DEFAULT_BRAIN_ROOT, "experts", expert, "brain.db")
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

# === Configurações globais (resolvidas em tempo de execução) ===
def brain_root() -> str:
    """Raiz compartilhada dos brains (BRAIN_ROOT ou ~/.hermes/brain)."""
    return os.fspath(get_brain_root())


def global_dir() -> str:
    return os.path.join(brain_root(), "global")


def backups_dir() -> str:
    return os.path.join(brain_root(), "backups")


# O "home" do framework (onde o codigo fonte mora, para git pull)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_expert_names() -> List[str]:
    return list_expert_names()


def hermes_profiles_root() -> str:
    """Raiz dos profiles Hermes (onde ficam SOUL.md e config.yaml).

    Profiles vivem em `<hermes_home>/profiles/<nome>`. Se `HERMES_HOME` já
    aponta para dentro de um profile (`.../profiles/<nome>`), sobe para a
    pasta `profiles` real.
    """
    home = os.path.normpath(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
    if os.path.basename(os.path.dirname(home)) == "profiles":
        return os.path.dirname(home)
    return os.path.join(home, "profiles")


def resolve_hermes_profile_dir(name: str) -> Optional[str]:
    """Resolve o diretório do profile Hermes para um expert (case-insensitive).

    `hermes profile create` normaliza o nome para lowercase, então o expert
    `AlentoBot` vive em `~/.hermes/profiles/alentobot/`. Tenta o nome exato e,
    em seguida, varre a pasta por um match case-insensitive.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    root = hermes_profiles_root()
    exact = os.path.join(root, name)
    if os.path.isdir(exact):
        return exact
    if os.path.isdir(root):
        for d in os.scandir(root):
            if d.is_dir() and d.name.lower() == name.lower():
                return os.path.join(root, d.name)
    return None


def _run_hermes(profile_dir: str, *args: str,
                timeout: int = 30) -> subprocess.CompletedProcess:
    """Roda `hermes ...` apontando para o profile via HERMES_HOME."""
    env = dict(os.environ)
    env["HERMES_HOME"] = profile_dir
    return subprocess.run(["hermes", *args], capture_output=True,
                          text=True, timeout=timeout, env=env)


def _hermes_config_get(profile_dir: str, key: str) -> Optional[str]:
    """Lê uma chave do config.yaml do profile via `hermes config get`."""
    result = _run_hermes(profile_dir, "config", "get", key)
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0 or not out or "not set" in out or "not set" in err:
        return None
    return out


def _hermes_config_set(profile_dir: str, key: str, value: str) -> List[str]:
    """Escreve uma chave no config.yaml do profile via `hermes config set`."""
    result = _run_hermes(profile_dir, "config", "set", key, value)
    if result.returncode != 0:
        return [f"hermes config set {key}: {result.stderr.strip()}"]
    return []


def _read_config_yaml(profile_dir: str) -> Dict[str, Any]:
    """Lê o config.yaml do profile (round-trip seguro, sem subprocess)."""
    import yaml

    cfg_path = os.path.join(profile_dir, "config.yaml")
    if not os.path.exists(cfg_path):
        return {}
    with open(cfg_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _write_config_yaml(profile_dir: str, config: Dict[str, Any]) -> List[str]:
    """Escreve o config.yaml com backup prévio e permissões 0600."""
    import yaml

    cfg_path = os.path.join(profile_dir, "config.yaml")
    if os.path.exists(cfg_path):
        try:
            shutil.copy2(cfg_path, cfg_path + ".bak")
        except Exception as e:
            return [f"Erro ao fazer backup de {cfg_path}: {e}"]
    try:
        with open(cfg_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
        os.chmod(cfg_path, 0o600)
    except Exception as e:
        return [f"Erro ao escrever {cfg_path}: {e}"]
    return []


def _read_fallback_chain(profile_dir: str) -> List[Dict[str, Any]]:
    """Lê a cadeia de fallback (`fallback_providers`) do config.yaml."""
    config = _read_config_yaml(profile_dir)
    raw = config.get("fallback_providers")
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _set_fallback_providers(profile_dir: str,
                            entries: List[Dict[str, Any]]) -> List[str]:
    """Define a cadeia de fallback (`fallback_providers`) no config.yaml.

    `hermes config set` não grava listas e `hermes fallback add` é interativo,
    então escrevemos via round-trip YAML seguro (load → modify → dump + .bak).
    """
    config = _read_config_yaml(profile_dir)
    config["fallback_providers"] = entries
    return _write_config_yaml(profile_dir, config)


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

    # 2. Cria brain.db (sempre) — o get_db_connection cria o diretório se needed
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

    # 3. Wrapper/alias: `hermes profile create` já gera o script wrapper `<name>`.
    # Não reinventamos — brain add profile é um alias fino sobre hermes profile create.

    # Resumo final
    print(f"\n  = Profile '{name}' criado")
    print(f"    - Hermes profile: {'sim' if hermes_created else 'nao'}")
    print(f"    - Brain.db: {brain_path}")
    print(f"    - Wrapper/alias: gerenciado pelo Hermes (`hermes profile alias {name}`)")

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
                count = count_pages(conn, name)
                print(f"      brain.db: {brain_path}")
                print(f"      conhecimentos: {count}")
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

    # Confirmação explícita antes de operação destrutiva (não-interativo: prossegue)
    if not getattr(args, 'yes', False) and sys.stdin.isatty():
        answer = input(
            f"Confirmar remoção do profile '{name}' e seu brain.db? [s/N] "
        ).strip().lower()
        if answer not in ('s', 'sim', 'y', 'yes'):
            print("Remoção cancelada.")
            return 0

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

        # Remove o profile Hermes (e seu wrapper/alias) via comando nativo.
        try:
            result = subprocess.run(["hermes", "profile", "delete", name, "-y"],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                print(f"  + Hermes profile '{name}' removido (wrapper/alias incluso)")
            else:
                print(f"  - Hermes profile delete falhou: {result.stderr.strip()}", file=sys.stderr)
        except FileNotFoundError:
            print(f"  - 'hermes' CLI nao encontrado; profile Hermes nao removido.", file=sys.stderr)
        except Exception as e:
            print(f"  - Erro ao remover profile Hermes: {e}", file=sys.stderr)

        print(f"\n= Profile '{name}' removido!")
        return 0
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


def _write_soul(soul_path: str, content: str) -> None:
    """Grava o SOUL.md (garante diretório e newline final)."""
    os.makedirs(os.path.dirname(soul_path), exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"
    with open(soul_path, 'w', encoding='utf-8') as f:
        f.write(content)


def _edit_soul(soul_path: str, name: str) -> int:
    """Abre o SOUL.md no editor do usuário ($VISUAL/$EDITOR, fallback vi)."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    if not os.path.exists(soul_path):
        _write_soul(soul_path, "")
    result = subprocess.run([editor, soul_path])
    if result.returncode != 0:
        print(f"Editor '{editor}' falhou (exit {result.returncode}).", file=sys.stderr)
        return 1
    print(f"+ SOUL.md de '{name}' salvo: {soul_path}")
    return 0


def cmd_soul(args) -> int:
    """Gerencia o SOUL.md (persona) de um profile/expert."""
    name = args.name
    if not name:
        print("Erro: informe o nome do profile. Ex: brain soul maria", file=sys.stderr)
        return 1

    pdir = resolve_hermes_profile_dir(name)
    if pdir is None:
        print(f"Profile Hermes '{name}' nao encontrado em {hermes_profiles_root()}. "
              f"Crie com: brain add profile {name}", file=sys.stderr)
        return 1

    soul_path = os.path.join(pdir, "SOUL.md")

    if getattr(args, "file", None):
        src = args.file
        if not os.path.exists(src):
            print(f"Arquivo nao encontrado: {src}", file=sys.stderr)
            return 1
        with open(src, 'r', encoding='utf-8') as f:
            content = f.read()
        _write_soul(soul_path, content)
        print(f"+ SOUL.md de '{name}' atualizado a partir de {src}")
        print(f"  -> {soul_path}")
        return 0

    if getattr(args, "soul_text", None) is not None:
        _write_soul(soul_path, args.soul_text)
        print(f"+ SOUL.md de '{name}' atualizado")
        print(f"  -> {soul_path}")
        return 0

    if getattr(args, "edit", False):
        return _edit_soul(soul_path, name)

    # default: mostra o conteúdo atual
    if os.path.exists(soul_path):
        with open(soul_path, 'r', encoding='utf-8') as f:
            print(f.read())
    else:
        print(f"SOUL.md nao encontrado em {soul_path}", file=sys.stderr)
        return 1
    return 0


def _show_model(profile_dir: str, name: str) -> int:
    model = _hermes_config_get(profile_dir, "model.default")
    provider = _hermes_config_get(profile_dir, "model.provider")
    base_url = _hermes_config_get(profile_dir, "model.base_url")
    chain = _read_fallback_chain(profile_dir)
    print(f"\nBrain (LLM) de '{name}':")
    print(f"  modelo:   {model or '(nao definido)'}")
    print(f"  provider: {provider or '(nao definido)'}")
    if base_url:
        print(f"  base_url: {base_url}")
    if chain:
        for i, e in enumerate(chain, 1):
            print(f"  fallback {i}: {e.get('model', '?')} (via {e.get('provider', '?')})")
    else:
        print("  fallback: (nenhum)")
    print("\nDefinir: brain model <nome> <modelo> [--provider P] [--base-url URL] "
          "[--fallback M --fallback-provider P]")
    return 0


def cmd_model(args) -> int:
    """Define o brain (LLM) de um profile: model.default + provider + fallback."""
    name = args.name
    if not name:
        print("Erro: informe o nome do profile. Ex: brain model maria hermes3:3b",
              file=sys.stderr)
        return 1

    pdir = resolve_hermes_profile_dir(name)
    if pdir is None:
        print(f"Profile Hermes '{name}' nao encontrado em {hermes_profiles_root()}. "
              f"Crie com: brain add profile {name}", file=sys.stderr)
        return 1

    model = getattr(args, "model", None)
    provider = getattr(args, "provider", None)
    base_url = getattr(args, "base_url", None)
    fallback = getattr(args, "fallback", None)
    fallback_provider = getattr(args, "fallback_provider", None)

    if not model and not provider and not base_url and not fallback and not fallback_provider:
        return _show_model(pdir, name)

    if bool(fallback) != bool(fallback_provider):
        print("Erro: --fallback e --fallback-provider devem ser usados juntos.",
              file=sys.stderr)
        return 1

    errors: List[str] = []
    if model:
        errors += _hermes_config_set(pdir, "model.default", model)
    if provider:
        errors += _hermes_config_set(pdir, "model.provider", provider)
    if base_url:
        errors += _hermes_config_set(pdir, "model.base_url", base_url)
    if fallback:
        errors += _set_fallback_providers(
            pdir, [{"provider": fallback_provider, "model": fallback}])

    if errors:
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"+ Brain (LLM) de '{name}' configurado")
    _show_model(pdir, name)
    return 0


def cmd_global_learn(args) -> int:
    """Aprende conhecimento global (para o brain global)."""
    print(f"\n=== Brain: Global Learn ===")
    brain_path = get_brain_db_path(global_brain=True)
    conn = get_db_connection(brain_path)
    try:
        if args.path:
            result = learn(conn, "global", args.path, args.sync, dry_run=args.dry_run)
        elif args.content:
            result = remember(conn, "global", "global_policy",
                              args.title or "Conteudo global",
                              args.content, dry_run=args.dry_run)
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
    bdir = backups_dir()
    if not os.path.exists(bdir):
        os.makedirs(bdir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(bdir, f"backup_{timestamp}")
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
            _snapshot_sqlite(bp, os.path.join(ndir, "brain.db"))
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


# --- Restore ------------------------------------------------------------------

def _snapshot_sqlite(src: str, dst: str) -> None:
    """Cópia consistente (online backup) de um SQLite, incluindo o WAL.

    `shutil.copy2` sozinho perde transações ainda no `-wal`; o backup API do
    sqlite3 captura tudo o que está commitado.
    """
    import sqlite3 as _sqlite3

    src_conn = _sqlite3.connect(src)
    try:
        dst_conn = _sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _clear_sqlite_sidecars(db_path: str) -> None:
    """Remove sidecars residuais (-wal/-shm/-journal) após sobrescrever um db."""
    for suffix in ("-wal", "-shm", "-journal"):
        p = f"{db_path}{suffix}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _list_available_backups() -> List[Dict[str, Any]]:
    """Backups disponíveis em backups_dir(), ordenados por timestamp (mais novo)."""
    bdir = backups_dir()
    if not os.path.isdir(bdir):
        return []
    found: List[Dict[str, Any]] = []
    for d in sorted(os.listdir(bdir), reverse=True):
        full = os.path.join(bdir, d)
        mp = os.path.join(full, "manifest.json")
        if not (os.path.isdir(full) and os.path.exists(mp)):
            continue
        try:
            with open(mp) as f:
                m = json.load(f)
            brains = [b.get("name") for b in m.get("brains", []) if b.get("name")]
            found.append({
                "timestamp": m.get("timestamp", d),
                "dir": full,
                "brains": brains,
                "count": m.get("count", len(brains)),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return found


def _resolve_backup_dir(from_spec: str) -> str:
    """Resolve o diretório de um backup a partir de caminho ou timestamp."""
    candidate = os.path.abspath(os.path.expanduser(from_spec))
    if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "manifest.json")):
        return candidate

    bdir = backups_dir()
    # Aceita "<timestamp>" puro ou "backup_<timestamp>" como nome de pasta.
    name = from_spec if from_spec.startswith("backup_") else f"backup_{from_spec}"
    full = os.path.join(bdir, name)
    if os.path.isdir(full) and os.path.exists(os.path.join(full, "manifest.json")):
        return full

    raise FileNotFoundError(
        f"Backup nao encontrado para '{from_spec}' em {bdir}"
        " (use `brain restore --list` para listar os disponiveis)"
    )


def _read_backup_manifest(backup_dir: str) -> Dict[str, Any]:
    mpath = os.path.join(backup_dir, "manifest.json")
    if not os.path.exists(mpath):
        raise FileNotFoundError(f"manifest.json ausente em {backup_dir}")
    with open(mpath) as f:
        return json.load(f)


def restore_backup(backup_dir: str, target_expert: Optional[str] = None,
                   global_only: bool = False) -> List[Dict[str, Any]]:
    """Restaura os brain.db de um backup, preservando o estado atual.

    Antes de sobrescrever cada brain.db, faz uma cópia de segurança
    `<brain.db>.pre-restore-<ts>`. Retorna um resultado por brain restaurado.
    """
    manifest = _read_backup_manifest(backup_dir)
    ts = manifest.get("timestamp") or os.path.basename(
        os.path.abspath(backup_dir)
    ).replace("backup_", "")

    results: List[Dict[str, Any]] = []
    for entry in manifest.get("brains", []):
        name = entry.get("name")
        if not name:
            continue
        if name == "global":
            if target_expert:
                continue
            dest = get_brain_db_path(global_brain=True)
        else:
            if global_only or (target_expert and name != target_expert):
                continue
            dest = get_brain_db_path(expert=name)

        src = os.path.join(backup_dir, name, "brain.db")
        if not os.path.exists(src):
            results.append({"name": name, "status": "missing",
                            "error": f"arquivo ausente no backup: {src}"})
            continue

        is_global = (name == "global")
        pre_restore = None
        if os.path.exists(dest):
            # Fecha conexões em cache (checkpoint do WAL) e preserva o estado atual.
            dispose_engine_for_path(dest)
            pre_restore = f"{dest}.pre-restore-{ts}"
            _snapshot_sqlite(dest, pre_restore)

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        _clear_sqlite_sidecars(dest)
        # Garante que o processo atual reabra um estado limpo (engine resetado).
        dispose_engine_for_path(dest)
        results.append({"name": name, "status": "restored",
                        "dest": dest, "pre_restore": pre_restore})
    return results


def cmd_restore(args) -> int:
    """Restaura brains a partir de um backup (brain restore --from <ts>)."""
    if getattr(args, "list_backups", False):
        backups = _list_available_backups()
        print("\n=== Backups disponiveis ===")
        if not backups:
            print("  (nenhum backup encontrado)")
            return 0
        for b in backups:
            print(f"  - {b['timestamp']}  ({b['count']} brains): {', '.join(b['brains'])}")
        return 0

    from_spec = getattr(args, "from_spec", None)
    if not from_spec:
        print("Erro: informe --from <timestamp|diretorio> (ou --list para listar)",
              file=sys.stderr)
        return 1

    backup_dir = _resolve_backup_dir(from_spec)
    manifest = _read_backup_manifest(backup_dir)
    names = [b.get("name") for b in manifest.get("brains", []) if b.get("name")]
    print(f"\n=== Brain: Restore de backup ===")
    print(f"  Backup: {backup_dir}")
    print(f"  Timestamp: {manifest.get('timestamp')}")
    print(f"  Brains: {', '.join(names) or '(vazio)'}")

    target = getattr(args, "expert", None)
    if target:
        print(f"  Escopo: expert '{target}'")
    elif getattr(args, "global_brain", False):
        print("  Escopo: apenas global")

    if not getattr(args, "yes", False):
        try:
            resp = input("\nSobrescrever os brain.db atuais com este backup? [s/N] ")
        except EOFError:
            resp = "n"
        if resp.strip().lower() not in ("s", "sim", "y", "yes"):
            print("Restore cancelado.")
            return 1

    results = restore_backup(
        backup_dir,
        target_expert=target,
        global_only=getattr(args, "global_brain", False),
    )
    restored = 0
    for r in results:
        if r["status"] == "restored":
            restored += 1
            print(f"  + {r['name']}: restaurado -> {r['dest']}")
            if r.get("pre_restore"):
                print(f"      (estado anterior preservado em {r['pre_restore']})")
        else:
            print(f"  - {r['name']}: {r.get('error', 'falha')}")

    print(f"\n= Restore concluido: {restored} brains restaurados")
    return 0 if results else 1


def _open_scope_session(scope: str):
    """Abre uma Session para um scope (`global` ou `expert/<nome>`)."""
    if scope == "global":
        bp = get_brain_db_path(global_brain=True)
    elif scope.startswith("expert/"):
        bp = get_brain_db_path(expert=scope[len("expert/"):])
    else:
        raise ValueError(f"scope invalido: {scope} (use 'global' ou 'expert/<nome>')")
    if not os.path.exists(bp):
        return None
    return get_db_connection(bp)


def cmd_verify(args) -> int:
    """Verifica integridade dos checkpoints assinados (cadeia + assinaturas)."""
    if getattr(args, "scope", None):
        scopes = [args.scope]
    else:
        scopes = ["global"] + [f"expert/{n}" for n in get_expert_names()]

    print("\n=== Brain: Verify (checkpoints assinados) ===")
    all_ok = True
    for scope in scopes:
        conn = _open_scope_session(scope)
        if conn is None:
            print(f"  [skip] {scope}: sem brain.db")
            continue
        try:
            result = verify_scope(conn, scope)
        finally:
            conn.close()
        if result.get("ok"):
            print(f"  [OK] {scope} — {result['commits']} commits")
        else:
            all_ok = False
            print(f"  [FALHOU] {scope} — {result['commits']} commits")
        for issue in result.get("issues", []):
            print(f"      ! {issue}")
        if "note" in result:
            print(f"      ({result['note']})")
    print(f"\n= Verify concluido: {'OK' if all_ok else 'FALHAS ENCONTRADAS'}")
    return 0 if all_ok else 1


def cmd_log(args) -> int:
    """Histórico de commits de um scope (mais novo primeiro)."""
    scope = args.scope
    conn = _open_scope_session(scope)
    if conn is None:
        print(f"Scope {scope} sem brain.db.", file=sys.stderr)
        return 1
    try:
        entries = history(conn, scope)
    finally:
        conn.close()

    print(f"\n=== Histórico de commits — {scope} ===")
    if not entries:
        print("  (nenhum commit)")
        return 0
    for e in entries:
        title = e["message"] or e["policy"]
        print(f"  {e['created_at']}  {e['id'][:12]}  {e['author']}  {title}")
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
            result = sync(conn, name)
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


# === Dashboard web (FastAPI) ===

def _load_dashboard():
    """Importa o módulo do dashboard lazy (evita exigir fastapi no CLI/plugin)."""
    try:
        from brain_tool import dashboard
        return dashboard
    except ImportError:
        print("Dashboard requer o extra 'dashboard'. Instale: "
              "pip install 'brain-framework[dashboard]'", file=sys.stderr)
        return None


def cmd_dashboard_serve(args) -> int:
    """Sobe o dashboard web (spec §6.3) — background por padrão."""
    dash = _load_dashboard()
    if dash is None:
        return 1
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8611)
    foreground = getattr(args, "foreground", False)
    return dash.serve(host=host, port=port, foreground=foreground)


def cmd_dashboard_add_user(args) -> int:
    dash = _load_dashboard()
    if dash is None:
        return 1
    try:
        dash.add_dashboard_user(args.username, args.password)
    except ValueError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    print(f"+ Usuario do dashboard adicionado: {args.username}")
    return 0


def cmd_dashboard_list_users(args) -> int:
    dash = _load_dashboard()
    if dash is None:
        return 1
    users = dash.list_dashboard_users()
    print(f"\n=== Usuarios do dashboard ({len(users)}) ===")
    for u in users:
        print(f"  - {u}")
    if not users:
        print("Nenhum usuario. Crie: brain dashboard add-user <usuario> <senha>")
    return 0


def cmd_dashboard_remove_user(args) -> int:
    dash = _load_dashboard()
    if dash is None:
        return 1
    if dash.remove_dashboard_user(args.username):
        print(f"+ Usuario removido: {args.username}")
        return 0
    print(f"Usuario nao encontrado: {args.username}", file=sys.stderr)
    return 1


def cmd_dashboard_token(args) -> int:
    """Exibe o token de acesso persistente (para retomar a sessão sem restart)."""
    dash = _load_dashboard()
    if dash is None:
        return 1
    print(dash.get_access_token())
    return 0


def cmd_dashboard_stop(args) -> int:
    """Encerra o dashboard em background."""
    dash = _load_dashboard()
    if dash is None:
        return 1
    if dash.stop_dashboard():
        print("+ Dashboard encerrado.")
    else:
        print("Dashboard nao esta rodando (sem PID).")
    return 0


def cmd_dashboard_status(args) -> int:
    """Verifica se o dashboard está rodando em background."""
    dash = _load_dashboard()
    if dash is None:
        return 1
    st = dash.dashboard_status()
    if st.get("running"):
        print(f"Dashboard rodando (PID {st['pid']}).")
    else:
        print("Dashboard nao esta rodando.")
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
  soul NAME            - Cria/edita o SOUL.md (persona) do profile
  model NAME [MODEL]   - Define o brain (LLM) do profile (--fallback para failover)
  global learn         - Aprende conhecimento para o brain global
  backup               - Backup de todos os brains (global + experts)
  restore --from TS    - Restaura brains de um backup (--list lista backups)
  verify [--scope S]   - Verifica integridade dos checkpoints assinados
  log --scope S        - Histórico de commits (global ou expert/<nome>)
  update               - Atualiza o framework via git pull origin main
  sync all             - Faz sync de todos os brains
  admin list           - Lista administradores configurados
  admin add TYPE ID    - Adiciona administrador (whatsapp/cli/grupo)
  admin remove ID      - Remove administrador
  dashboard            - Dashboard web (serve/add-user/list-users/remove-user)

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

    parser.add_argument(
        "--version", action="version", version=f"brain {__version__}",
        help="Exibe a versão e sai",
    )
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
    p_rem_profile.add_argument('--yes', action='store_true', help='Remove sem confirmacao')
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

    p_restore = sp.add_parser('restore', help='Restaura brains a partir de um backup')
    p_restore.add_argument('--from', dest='from_spec',
                           help='Timestamp ou diretorio do backup (ex: backup_20260816_120000)')
    p_restore.add_argument('--expert', help='Restaura apenas este expert')
    p_restore.add_argument('--global', action='store_true', dest='global_brain',
                           help='Restaura apenas o brain global')
    p_restore.add_argument('--yes', action='store_true',
                           help='Restaura sem pedir confirmacao')
    p_restore.add_argument('--list', action='store_true', dest='list_backups',
                           help='Lista os backups disponiveis e sai')
    p_restore.set_defaults(func=lambda args: cmd_restore(args))

    p_verify = sp.add_parser('verify', help='Verifica integridade dos checkpoints assinados')
    p_verify.add_argument('--scope', help='Scope a verificar (global ou expert/<nome>); default: todos')
    p_verify.set_defaults(func=lambda args: cmd_verify(args))

    p_log = sp.add_parser('log', help='Histórico de commits de um scope')
    p_log.add_argument('--scope', required=True, help='Scope (global ou expert/<nome>)')
    p_log.set_defaults(func=lambda args: cmd_log(args))

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

    # --- SOUL.md / brain (LLM) ---
    p_soul = sp.add_parser('soul', help='Gerencia o SOUL.md (persona) de um profile')
    p_soul.add_argument('name', help='Nome do profile/expert')
    p_soul.add_argument('--set', dest='soul_text', help='Define o conteudo do SOUL.md (texto)')
    p_soul.add_argument('--file', help='Define o SOUL.md a partir de um arquivo')
    p_soul.add_argument('--edit', action='store_true', help='Abre o SOUL.md no editor ($EDITOR)')
    p_soul.set_defaults(func=lambda args: cmd_soul(args))

    p_model = sp.add_parser('model', help='Define o brain (LLM) de um profile')
    p_model.add_argument('name', help='Nome do profile/expert')
    p_model.add_argument('model', nargs='?', help='Modelo (ex: gpt-5.6-sol, hermes3:3b)')
    p_model.add_argument('--provider', help='Provider (ex: openai-codex, ollama)')
    p_model.add_argument('--base-url', dest='base_url', help='Base URL do provider (opcional)')
    p_model.add_argument('--fallback', help='Modelo de fallback (ex: deepseek-v4-pro)')
    p_model.add_argument('--fallback-provider', dest='fallback_provider',
                         help='Provider do fallback (ex: opencode-go)')
    p_model.set_defaults(func=lambda args: cmd_model(args))

    # --- Dashboard web ---
    p_dash = sp.add_parser('dashboard', help='Dashboard web do Brain (spec §6.3)')
    p_dash.add_argument('--host', default='127.0.0.1',
                        help='Host de bind (default 127.0.0.1; use 0.0.0.0 para LAN)')
    p_dash.add_argument('--port', type=int, default=8611, help='Porta (default 8611)')
    p_dash.add_argument('--foreground', action='store_true',
                        help='Roda em primeiro plano (default: background)')
    p_dash_sub = p_dash.add_subparsers(dest='subcommand')

    p_dash_serve = p_dash_sub.add_parser('serve', help='Sobe o dashboard web')
    p_dash_serve.add_argument('--host', default='127.0.0.1', help='Host (default 127.0.0.1)')
    p_dash_serve.add_argument('--port', type=int, default=8611, help='Porta (default 8611)')
    p_dash_serve.add_argument('--foreground', action='store_true',
                              help='Roda em primeiro plano (bloqueia o terminal)')
    p_dash_serve.set_defaults(func=lambda args: cmd_dashboard_serve(args))

    p_dash_add = p_dash_sub.add_parser('add-user', help='Adiciona usuario do dashboard')
    p_dash_add.add_argument('username', help='Nome de usuario')
    p_dash_add.add_argument('password', help='Senha')
    p_dash_add.set_defaults(func=lambda args: cmd_dashboard_add_user(args))

    p_dash_list = p_dash_sub.add_parser('list-users', help='Lista usuarios do dashboard')
    p_dash_list.set_defaults(func=lambda args: cmd_dashboard_list_users(args))

    p_dash_remove = p_dash_sub.add_parser('remove-user', help='Remove usuario do dashboard')
    p_dash_remove.add_argument('username', help='Nome de usuario')
    p_dash_remove.set_defaults(func=lambda args: cmd_dashboard_remove_user(args))

    p_dash_stop = p_dash_sub.add_parser('stop', help='Encerra o dashboard em background')
    p_dash_stop.set_defaults(func=lambda args: cmd_dashboard_stop(args))

    p_dash_status = p_dash_sub.add_parser('status', help='Verifica se o dashboard está rodando')
    p_dash_status.set_defaults(func=lambda args: cmd_dashboard_status(args))

    p_dash_token = p_dash_sub.add_parser('token', help='Exibe o token de acesso atual')
    p_dash_token.set_defaults(func=lambda args: cmd_dashboard_token(args))

    # `brain dashboard` sem subcomando sobe o servidor (defaults aplicados no cmd).
    p_dash.set_defaults(func=lambda args: cmd_dashboard_serve(args))

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
