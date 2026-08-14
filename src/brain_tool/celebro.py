#!/usr/bin/env python3
"""
Celebro CLI - Gestor do Brain Framework
Gerencia profiles, brains, backups, atualizacoes e administracao.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importa apenas o que precisa do brain_tool
def _import_brain_tool():
    try:
        from brain_tool import (
            get_brain_db_path, get_db_connection, remember, learn, sync,
            SCHEMA_VERSION
        )
        return True
    except ImportError as e:
        print(f"AVISO: brain_tool nao importavel: {e}", file=sys.stderr)
        print("Alguns comandos podem nao funcionar corretamente.", file=sys.stderr)
        return False

_BRAIN_TOOL_AVAILABLE = _import_brain_tool()

# Se brain_tool nao esta disponivel, definimos fallback minimo
if not _BRAIN_TOOL_AVAILABLE:
    def get_brain_db_path(expert=None, brain_path=None, global_brain=False):
        DEFAULT_BRAIN_ROOT = os.environ.get('BRAIN_ROOT', os.path.expanduser("~/.hermes/brain"))
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
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

DEFAULT_BRAIN_ROOT = os.environ.get('BRAIN_ROOT', os.path.expanduser("~/.hermes/brain"))
GLOBAL_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "global")
EXPERTS_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "experts")
BACKUPS_DIR = os.path.join(DEFAULT_BRAIN_ROOT, "backups")
ADMIN_CONFIG_FILE = os.path.join(DEFAULT_BRAIN_ROOT, "admins.json")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_admins():
    if os.path.exists(ADMIN_CONFIG_FILE):
        with open(ADMIN_CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {"admins": [], "groups": {}}


def save_admins(admins):
    with open(ADMIN_CONFIG_FILE, 'w') as f:
        json.dump(admins, f, indent=2)


def get_expert_names():
    if not os.path.exists(EXPERTS_DIR):
        return []
    return [d.name for d in os.scandir(EXPERTS_DIR) if d.is_dir()]


def create_profile_hermes(name):
    try:
        result = subprocess.run(
            ["hermes", "profile", "create", name],
            capture_output=True, text=True, timeout=60
        )
        return result.returncode == 0
    except FileNotFoundError:
        print(f"AVISO: 'hermes' nao encontrado no PATH. Profile Hermes nao criado.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Erro ao criar profile Hermes: {e}", file=sys.stderr)
        return False


def create_brain_db(name):
    try:
        brain_path = get_brain_db_path(expert=name)
        brain_dir = os.path.dirname(brain_path)
        if brain_dir and not os.path.exists(brain_dir):
            os.makedirs(brain_dir, exist_ok=True)
        
        if _BRAIN_TOOL_AVAILABLE:
            conn = get_db_connection(brain_path)
            remember(conn, name, "system", "Brain inicializado",
                     f"Brain {name} inicializado em {datetime.now().isoformat()}")
            conn.close()
        else:
            conn = sqlite3.connect(brain_path) if 'sqlite3' in globals() else get_db_connection(brain_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pages (
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
            cursor.execute("""
                INSERT INTO pages (expert, tipo, titulo, corpo, hash_canonical)
                VALUES (?, 'system', ?, ?, ?)
            """, (name, "Brain inicializado", f"Brain {name} criado em {datetime.now().isoformat()}", None))
            conn.commit()
            conn.close()
        return True
    except Exception as e:
        print(f"Erro ao criar brain.db: {e}", file=sys.stderr)
        traceback.print_exc()
        return False


def create_alias(name):
    try:
        alias_line = f"alias {name}='/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile {name}'"
        bashrc_path = os.path.expanduser("~/.bashrc")
        
        with open(bashrc_path, 'r') as f:
            content = f.read()
        
        if alias_line not in content:
            with open(bashrc_path, 'a') as f:
                f.write(f"\n{alias_line}\n")
            print(f"Alias adicionado ao ~/.bashrc: {alias_line}")
        
        return True
    except Exception as e:
        print(f"Erro ao criar alias: {e}", file=sys.stderr)
        return False


def setup_hermes_profile(name):
    print(f"\n{'='*50}")
    print(f"Configurando profile Hermes: {name}")
    print(f"{'='*50}")
    
    # 1. Profile Hermes
    if create_profile_hermes(name):
        print(f"  + Profile Hermes criado")
    else:
        print(f"  - Profile Hermes falhou (continua com brain.db)", file=sys.stderr)
    
    # 2. Brain.db
    if create_brain_db(name):
        print(f"  + Brain.db criado: {get_brain_db_path(expert=name)}")
    else:
        print(f"  - Brain.db falhou", file=sys.stderr)
        return False
    
    # 3. Alias
    if create_alias(name):
        print(f"  + Alias configurado")
    else:
        print(f"  - Alias falhou", file=sys.stderr)
    
    return True


def cmd_add_profile(args):
    name = args.name
    if not name:
        print("Erro: informe o nome do profile. Ex: celebro add profile maria", file=sys.stderr)
        return 1
    
    print(f"\n{'='*50}")
    print(f"Adicionando profile: {name}")
    print(f"{'='*50}")
    
    if setup_hermes_profile(name):
        print(f"\n= Profile '{name}' criado com sucesso!")
        print(f"  - Hermes profile: configurado")
        print(f"  - Brain.db: {get_brain_db_path(expert=name)}")
        print(f"  - Alias: execute '{name}' para usar")
        return 0
    else:
        print(f"\n= Falha parcial na criacao do profile.", file=sys.stderr)
        return 1


def cmd_list_profiles(args):
    experts = get_expert_names()
    print(f"\n=== Perfis configurados ({len(experts)}) ===\n")
    
    if not experts:
        print("Nenhum profile encontrado.")
        print("\nPara criar: celebro add profile <nome>")
        return 0
    
    for name in sorted(experts):
        brain_path = get_brain_db_path(expert=name)
        exists = os.path.exists(brain_path)
        status = "+" if exists else "-"
        print(f"  {status} {name}")
        if exists:
            conn = get_db_connection(brain_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM pages WHERE expert = ?", (name,))
            count = cursor.fetchone()[0]
            conn.close()
            print(f"      brain.db: {brain_path}")
            print(f"      conhecimentos: {count}")
    
    return 0


def cmd_remove_profile(args):
    name = args.name
    if not name:
        print("Erro: informe o nome do profile.", file=sys.stderr)
        return 1
    
    if name not in get_expert_names():
        print(f"Profile '{name}' nao encontrado.")
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
            os.rmdir(expert_dir)
            print(f"  + diretorio removido")
        
        bashrc_path = os.path.expanduser("~/.bashrc")
        alias_line = f"alias {name}='/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile {name}'"
        
        if os.path.exists(bashrc_path):
            with open(bashrc_path, 'r') as f:
                lines = f.readlines()
            with open(bashrc_path, 'w') as f:
                for line in lines:
                    if line.strip() != alias_line:
                        f.write(line)
            print(f"  + alias removido do ~/.bashrc")
        
        print(f"\n= Profile '{name}' removido!")
        return 0
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


def cmd_global_learn(args):
    print(f"\n=== Global Learn ===")
    brain_path = get_brain_db_path(global_brain=True)
    conn = get_db_connection(brain_path)
    try:
        if args.path:
            if not _BRAIN_TOOL_AVAILABLE:
                print("Erro: brain_tool nao disponivel para learn.", file=sys.stderr)
                conn.close()
                return 1
            result = learn(conn, "global", args.path, args.sync, dry_run=args.dry_run)
        elif args.content:
            if _BRAIN_TOOL_AVAILABLE:
                result = remember(conn, "global", "global_policy", args.title or "Conteudo global",
                                  args.content, dry_run=args.dry_run)
            else:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS pages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        expert TEXT NOT NULL,
                        tipo TEXT NOT NULL,
                        titulo TEXT,
                        corpo TEXT NOT NULL,
                        hash_canonical TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
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


def cmd_backup(args):
    print(f"\n=== Backup de todos os brains ===")
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
    
    manifest = {"timestamp": timestamp, "brains": [{"name": n, "path": p} for n, p in brains],
                "backup_dir": backup_dir, "count": count}
    with open(os.path.join(backup_dir, "manifest.json"), 'w') as f:
        json.dump(manifest, f, indent=2)
    
    print(f"\n= Backup concluido: {count} brains")
    print(f"  Diretorio: {backup_dir}")
    return 0


def cmd_update(args):
    print(f"\n=== Atualizando Brain Framework ===")
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120
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


def cmd_sync_all(args):
    print(f"\n=== Sync de todos os brains ===")
    all_experts = ["global"] + get_expert_names()
    results = []
    
    for name in all_experts:
        bp = get_brain_db_path(global_brain=(name == "global"), expert=name if name != "global" else None)
        if not os.path.exists(bp):
            results.append({"name": name, "status": "not_found"})
            continue
        try:
            conn = get_db_connection(bp)
            if _BRAIN_TOOL_AVAILABLE:
                from brain_tool import sync as _sync
                result = _sync(conn, name)
            else:
                # Fallback minimo
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM knowledge_staging WHERE expert = ? AND status = 'pending'", (name,))
                pending = cursor.fetchone()[0]
                if pending == 0:
                    result = {"action": "sync", "status": "nothing_to_sync", "pending_count": 0}
                else:
                    result = {"action": "sync", "status": "skipped", "reason": "sync nao disponivel sem brain_tool"}
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


def cmd_admin_list(args):
    admins = load_admins()
    print(f"\n=== Administradores =========================================")
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


def cmd_admin_add(args):
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


def cmd_admin_remove(args):
    admins = load_admins()
    identifier = args.identifier
    orig = len(admins["admins"])
    admins["admins"] = [a for a in admins["admins"] if a.replace("wa:", "").replace("cli:", "") != identifier]
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


def main():
    parser = argparse.ArgumentParser(
        description="Celebro CLI - Gestor do Brain Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Comandos:
  add profile NAME     - Cria profile Hermes + brain.db + alias
  list profiles        - Lista todos os profiles
  remove profile NAME  - Remove profile e brain.db
  global learn         - Aprende conhecimento global
  backup              - Backup de todos os brains
  update              - Atualiza framework via git pull
  sync all            - Sync de todos os brains
  admin list          - Lista administradores
  admin add TYPE ID   - Adiciona administrador
  admin remove ID     - Remove administrador

Exemplos:
  celebro add profile maria
  celebro list profiles
  celebro global learn --path /caminho/dos/arquivos/ --sync
  celebro backup
  celebro update
        """)
    sp = parser.add_subparsers(dest='command')
    
    # add profile
    p_add = sp.add_parser('add', help='Adiciona profile')
    p_add_sub = p_add.add_subparsers(dest='subcommand')
    p_add_prof = p_add_sub.add_parser('profile', help='Adiciona profile')
    p_add_prof.add_argument('name', nargs='?', help='Nome do profile')
    p_add.set_defaults(func=cmd_add_profile)
    
    # list profiles
    p_list = sp.add_parser('list', help='Lista profiles')
    p_list_sub = p_list.add_subparsers(dest='subcommand')
    p_list_prof = p_list_sub.add_parser('profiles', help='Lista profiles')
    p_list.set_defaults(func=cmd_list_profiles)
    
    # remove profile
    p_rem = sp.add_parser('remove', help='Remove profile')
    p_rem_sub = p_rem.add_subparsers(dest='subcommand')
    p_rem_prof = p_rem_sub.add_parser('profile', help='Remove profile')
    p_rem_prof.add_argument('name', nargs='?', help='Nome do profile')
    p_rem.set_defaults(func=cmd_remove_profile)
    
    # global learn
    p_glob = sp.add_parser('global', help='Operacoes globais')
    p_glob_sub = p_glob.add_subparsers(dest='subcommand')
    p_glob_learn = p_glob_sub.add_parser('learn', help='Aprende conhecimento global')
    p_glob_learn.add_argument('--path', help='Caminho do arquivo/diretorio')
    p_glob_learn.add_argument('--content', help='Conteudo direto')
    p_glob_learn.add_argument('--title', help='Titulo')
    p_glob_learn.add_argument('--sync', action='store_true')
    p_glob_learn.add_argument('--dry-run', action='store_true')
    p_glob.set_defaults(func=cmd_global_learn)
    
    # backup
    p_backup = sp.add_parser('backup', help='Backup de todos os brains')
    p_backup.set_defaults(func=cmd_backup)
    
    # update
    p_update = sp.add_parser('update', help='Atualiza framework')
    p_update.set_defaults(func=cmd_update)
    
    # sync all
    p_sync = sp.add_parser('sync', help='Sincronizacao')
    p_sync_sub = p_sync.add_subparsers(dest='subcommand')
    p_sync_all = p_sync_sub.add_parser('all', help='Sync todos os brains')
    p_sync.set_defaults(func=cmd_sync_all)
    
    # admin
    p_admin = sp.add_parser('admin', help='Gestao de admins')
    p_admin_sub = p_admin.add_subparsers(dest='subcommand')
    p_admin_list = p_admin_sub.add_parser('list', help='Lista admins')
    p_admin_list.set_defaults(func=cmd_admin_list)
    p_admin_add = p_admin_sub.add_parser('add', help='Adiciona admin')
    p_admin_add.add_argument('type', choices=['whatsapp', 'cli', 'grupo'], help='Tipo')
    p_admin_add.add_argument('identifier', help='Identificador')
    p_admin_add.add_argument('--group', help='Nome do grupo (para tipo grupo)')
    p_admin_add.set_defaults(func=cmd_admin_add)
    p_admin_rem = p_admin_sub.add_parser('remove', help='Remove admin')
    p_admin_rem.add_argument('identifier', help='Identificador')
    p_admin_rem.set_defaults(func=cmd_admin_remove)
    
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
