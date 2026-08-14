#!/usr/bin/env python3
"""
Brain — Expert Nativo do Brain Framework
O Brain é o expert especial nativo do framework. Ele substitui o agente
default do Hermes e gerencia a infraestrutura de conocimento: cria profiles,
gerencia brains, faz backups, atualizações e administrador.
O Brain NÃO é um agente de atendimento (como Maria ou José). Ele é o
gestor nativo do sistema — o "cerebro" que gerencia os outros cérebros.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# === Importação do brain_tool (CLI core) ===
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from brain_tool import (
        get_brain_db_path, get_db_connection, remember, learn, sync,
        SCHEMA_VERSION
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
        return conn

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


def _get_brain_python() -> str:
    """Retorna o caminho do Python do venv do framework, ou sys.executable se nao encontrado."""
    if os.path.exists(HERMES_VENV_PYTHON):
        return HERMES_VENV_PYTHON
    return sys.executable


# === Comandos do Brain ===

def cmd_add_profile(args) -> int:
    """
    Cria um novo profile no framework.
    O Brain é especial: ele cria o profile e o configura para usar
    o Brain como seu expert nativo (substituindo o default do Hermes).
    """
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
            # Remove só se estiver vazio
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
                from brain_tool import sync as _sync
                result = _sync(conn, name)
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

Comandos:
  add profile NAME     - Cria um novo profile (hermes profile create + brain.db + alias)
  list profiles        - Lista todos os profiles configurados
  remove profile NAME  - Remove um profile e seu brain.db
  global learn         - Aprende conhecimento para o brain global
  backup               - Faz backup de todos os brains (global + experts)
  update               - Atualiza o framework via git pull origin main
  sync all             - Faz sync de todos os brains
  admin list           - Lista administradores configurados
  admin add TYPE ID    - Adiciona administrador (whatsapp/cli/grupo)
  admin remove ID      - Remove administrador

Exemplos:
  brain add profile maria
  brain list profiles
  brain remove profile maria
  brain global learn --path /documentos/gerais/ --sync
  brain backup
  brain update
  brain sync all
  brain admin list
  brain admin add whatsapp +551999999999
        """)

    sp = parser.add_subparsers(dest='command')

    # add profile
    p_add = sp.add_parser('add', help='Adiciona um novo profile')
    p_add_sub = p_add.add_subparsers(dest='subcommand')
    p_add_profile = p_add_sub.add_parser('profile', help='Adiciona um novo profile')
    p_add_profile.add_argument('name', nargs='?', help='Nome do profile')
    p_add.set_defaults(func=lambda args: cmd_add_profile(args))

    # list profiles
    p_list = sp.add_parser('list', help='Lista profiles')
    p_list_sub = p_list.add_subparsers(dest='subcommand')
    p_list_profiles = p_list_sub.add_parser('profiles', help='Lista todos os profiles')
    p_list.set_defaults(func=lambda args: cmd_list_profiles(args))

    # remove profile
    p_rem = sp.add_parser('remove', help='Remove um profile')
    p_rem_sub = p_rem.add_subparsers(dest='subcommand')
    p_rem_profile = p_rem_sub.add_parser('profile', help='Remove um profile')
    p_rem_profile.add_argument('name', nargs='?', help='Nome do profile')
    p_rem.set_defaults(func=lambda args: cmd_remove_profile(args))

    # global learn
    p_global = sp.add_parser('global', help='Operacoes com brain global')
    p_global_sub = p_global.add_subparsers(dest='subcommand')
    p_global_learn = p_global_sub.add_parser('learn', help='Aprende conhecimento global')
    p_global_learn.add_argument('--path', help='Caminho do arquivo/diretorio')
    p_global_learn.add_argument('--content', help='Conteudo direto')
    p_global_learn.add_argument('--title', help='Titulo do conhecimento')
    p_global_learn.add_argument('--sync', action='store_true', help='Sync apos learn')
    p_global_learn.add_argument('--dry-run', action='store_true', help='Preview sem executar')
    p_global.set_defaults(func=lambda args: cmd_global_learn(args))

    # backup
    p_backup = sp.add_parser('backup', help='Backup de todos os brains')
    p_backup.set_defaults(func=lambda args: cmd_backup(args))

    # update
    p_update = sp.add_parser('update', help='Atualiza o framework')
    p_update.set_defaults(func=lambda args: cmd_update(args))

    # sync all
    p_sync = sp.add_parser('sync', help='Sincronizacao')
    p_sync_sub = p_sync.add_subparsers(dest='subcommand')
    p_sync_all = p_sync_sub.add_parser('all', help='Sync de todos os brains')
    p_sync.set_defaults(func=lambda args: cmd_sync_all(args))

    # admin
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

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if hasattr(args, 'func'):
        return args.func(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
