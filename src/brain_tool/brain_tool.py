#!/usr/bin/env python3
"""
Brain Tool — CLI evolutive para gerenciar brains do ecossistema Granjimmy.

 versão 1.1.0 — improve: check, schema_version, dry-run, init, learn logs.
Heroku da versão anterior: 1.0.0 (2026-08-12).

Uso:
  python3 brain_tool.py <command> [options] [--brain <path>] [--global]

Commands:
  remember      — salva um registro (conceito/entidade/projetos/grupo/memória)
  entity        — salva uma entidade nomeada
  recall        — recupera registros ativos
  synthesize    — consolida páginas de uma entidade em síntese
  forget        — arquiva uma página (soft delete); use --dry-run para pré-ver
  consolidate   — deduplica (Jaccard) + tiering T1-T4; use --dry-run para pré-ver
  taxonomist    — sugere tipo para conteúdo via schema + LLM
  capture       — entrada única com hash dedup (idempotente)
  learn         — aprende com jsonl de mensagens (extrai fatos com LLM)
  check         — verifica integridade do brain (PRAGMA + schema_version)
  init          — inicializa um novo brain (para expert ou global)
  migrate       — aplica migrações pendentes do schema_version

Brain directories:
  --brain <path>     : usa o brain em <path> (diretório com brain.db + schema_pack.yaml)
  --global           : usa o brain global (~/.hermes/brain/global/)
  (sem --brain/--global) : usa experts/<BRAIN_PROFILE>/ ou experts/jimmy/

Environment:
  BRAIN_PROFILE      : nome do profile (ex: jimmy, gtic). Se setado, brain default = experts/<BRAIN_PROFILE>/
  OLLAMA_API_KEY     : chave da API Ollama (também lida do .env do profile)
"""

import argparse
import hashlib
import json
import os
import sqlite3
import urllib.request
import re
import sys
import time

# ────────────────────────────────────────────────────────── caminhos absolutos
# HOME desta sessão pode ser expandido pelo perfil (verificação sandbox).
# Para evitar caminhos fantasmas, calculamos tudo a partir do local deste script.

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_BRAIN_ROOT = os.path.dirname(_TOOL_DIR)          # ~/.hermes/brain/ (real, absoluto)

GLOBAL_DIR = os.path.join(_BRAIN_ROOT, "global")
EXPERTS_DIR = os.path.join(_BRAIN_ROOT, "experts")

# ────────────────────────────────────────────────────────── module globals


DB = None
SCHEMA = None
BRAIN_DIR = None
OLLAMA_API_KEY_CACHE = None

VALID = ["people", "concepts", "projects", "groups", "memory", "inbox"]
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
MODEL = "gemma4:31b"
VERSION = "1.1.0"
SCHEMA_VERSION = "1.1.0"


# ────────────────────────────────────────────────────────── setup 


def setup_brain(brain_dir: str):
    """Configura o brain para operar no diretório especificado."""
    global DB, SCHEMA, BRAIN_DIR
    BRAIN_DIR = os.path.abspath(brain_dir)
    DB = os.path.join(BRAIN_DIR, "brain.db")
    SCHEMA = os.path.join(BRAIN_DIR, "schema_pack.yaml")
    os.makedirs(BRAIN_DIR, exist_ok=True)


def load_ollama_key() -> str:
    """Carrega a chave Ollama API de múltiplas fontes, da mais específica à mais genérica."""
    global OLLAMA_API_KEY_CACHE
    if OLLAMA_API_KEY_CACHE:
        return OLLAMA_API_KEY_CACHE

    # 1. Variável de ambiente
    k = os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_KEY")
    if k:
        OLLAMA_API_KEY_CACHE = k
        return k

    # 2. .env no diretório da tool
    tool_env = os.path.join(_TOOL_DIR, ".env")
    if os.path.exists(tool_env):
        for line in open(tool_env, encoding="utf-8"):
            line = line.strip()
            if line.startswith("OLLAMA_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
                if k:
                    OLLAMA_API_KEY_CACHE = k
                    return k

    # 3. .env do profile (se BRAIN_PROFILE estiver setado)
    profile = os.environ.get("BRAIN_PROFILE")
    if profile:
        profile_env = os.path.expanduser(f"~/.hermes/profiles/{profile}/.env")
        if os.path.exists(profile_env):
            for line in open(profile_env, encoding="utf-8"):
                line = line.strip()
                if line.startswith("OLLAMA_API_KEY="):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if k:
                        OLLAMA_API_KEY_CACHE = k
                        return k

    # 4. Fallback: .env do jimmy
    jimmy_env = os.path.expanduser("~/.hermes/profiles/jimmy/.env")
    if os.path.exists(jimmy_env):
        for line in open(jimmy_env, encoding="utf-8"):
            line = line.strip()
            if line.startswith("OLLAMA_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
                if k:
                    OLLAMA_API_KEY_CACHE = k
                    return k

    return ""


def call_gemma(prompt: str, temperature: float = 0.2) -> str:
    key = load_ollama_key()
    if not key:
        raise RuntimeError(
            "OLLAMA_API_KEY não configurada. "
            "Sete a variável de ambiente OLLAMA_API_KEY ou configure o .env do profile."
        )
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


# ────────────────────────────────────────────────────────── schema


def ensure_schema_version_table(c: sqlite3.Connection):
    """Cria a tabela schema_version se não existir."""
    c.execute("""CREATE TABLE IF NOT EXISTS schema_version (
        version TEXT PRIMARY KEY,
        applied_at TEXT DEFAULT (datetime('now')),
        description TEXT
    )""")


def get_current_schema_version(c: sqlite3.Connection) -> str | None:
    """Retorna a versão do schema aplicada mais recentemente, ou None se não houver."""
    row = c.execute(
        "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def apply_schema_version(c: sqlite3.Connection, version: str, description: str = ""):
    """Registra uma versão do schema na tabela schema_version."""
    ensure_schema_version_table(c)
    c.execute(
        "INSERT OR REPLACE INTO schema_version (version, applied_at, description) "
        "VALUES (?, datetime('now'), ?)",
        (version, description),
    )
    c.commit()


# ────────────────────────────────────────────────────────── conn


def conn(create_schema_version: bool = True):
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,
        titulo TEXT,
        corpo TEXT,
        entidade TEXT,
        version INTEGER DEFAULT 1,
        status TEXT DEFAULT 'ativo',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    if create_schema_version:
        ensure_schema_version_table(c)
    return c


# ────────────────────────────────────────────────────────── CRUD


def cmd_remember(args):
    c = conn()
    cur = c.execute(
        "INSERT INTO pages (tipo,titulo,corpo,entidade) VALUES (?,?,?,?)",
        (args.tipo, args.titulo, args.corpo, args.entidade))
    c.commit()
    print(f"lembrado [{args.tipo}] id={cur.lastrowid}")


def cmd_entity(args):
    c = conn()
    cur = c.execute(
        "INSERT INTO pages (tipo,titulo,corpo) VALUES (?,?,?)",
        (args.tipo, args.nome, args.notas))
    c.commit()
    print(f"entidade [{args.tipo}] '{args.nome}' id={cur.lastrowid}")


def cmd_recall(args):
    c = conn()
    q = "SELECT id,tipo,titulo,corpo,status FROM pages WHERE status='ativo'"
    params = []
    if args.tipo:
        q += " AND tipo=?"; params.append(args.tipo)
    if args.termo:
        q += " AND (titulo LIKE ? OR corpo LIKE ?)"
        params += [f"%{args.termo}%", f"%{args.termo}%"]
    rows = c.execute(q, params).fetchall()
    if not rows:
        print("nada encontrado")
        return
    for row in rows:
        print(f"[{row[0]}] ({row[1]}) {row[2]}\n   {row[3][:200]}\n")


def cmd_synthesize(args):
    c = conn()
    rows = list(c.execute(
        "SELECT id,tipo,titulo,corpo FROM pages WHERE status='ativo' AND entidade=?",
        (args.entidade,)))
    if not rows:
        print("sem paginas para consolidar"); return
    texto = "\n".join(f"- {r[2]}: {r[3]}" for r in rows)
    cur = c.execute(
        "INSERT INTO pages (tipo,titulo,corpo,entidade) VALUES (?,?,?,?)",
        ("memory", f"Sintese: {args.entidade}", texto, args.entidade))
    c.commit()
    print(f"sintese de '{args.entidade}' id={cur.lastrowid} ({len(rows)} fontes)")


# ────────────────────────────────────────────────────────── forget (com dry-run)


def cmd_forget(args):
    c = conn()
    if args.dry_run:
        row = c.execute(
            "SELECT id, tipo, titulo, status FROM pages WHERE id=?",
            (args.id,)).fetchone()
        if not row:
            print(f"id {args.id}: não encontrado")
            return
        print(f"[DRY-RUN] Arquivaria id={row[0]} ({row[1]}) '{row[2]}' "
              f"status atual: {row[3]}")
        return
    c.execute(
        "UPDATE pages SET status='arquivado', updated_at=datetime('now') WHERE id=?",
        (args.id,))
    c.commit()
    print(f"id {args.id} arquivado")


# ────────────────────────────────────────────────────────── consolidate (com dry-run)


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cmd_consolidate(args):
    """Deduplicação determinística (Jaccard) + tiering T1-T4 por frequência.
    Inspirado no concept-synthesis do Gbrain. Não sintetiza T3/T4 (economiza API).
    Use --dry-run para ver o que seria feito sem aplicar."""
    c = conn()
    rows = list(c.execute(
        "SELECT id, tipo, titulo, corpo FROM pages "
        "WHERE status='ativo' AND tipo IN ('concepts','people','projects')"))
    by_type = {}
    for r in rows:
        by_type.setdefault(r[1], []).append(r)
    merged = 0
    merged_details = []
    for tipo, items in by_type.items():
        canonical = []
        for it in items:
            dup = None
            for cidx, citem in enumerate(canonical):
                if jaccard(it[3], citem[3]) > 0.5 or (it[2] and it[2] in citem[2]):
                    dup = cidx
                    break
            if dup is None:
                canonical.append(it)
            else:
                if args.dry_run:
                    merged_details.append(
                        f"  [DRY-RUN] Arquivaria id={it[0]} ({tipo}) '{it[2]}' "
                        f"(dup de '{canonical[dup][2]}')"
                    )
                else:
                    c.execute("UPDATE pages SET status='arquivado' WHERE id=?", (it[0],))
                merged += 1
        from collections import Counter
        freq = Counter(i[2] for i in canonical if i[2])
        for it in canonical:
            f = freq.get(it[2], 1)
            tier = 1 if f >= 6 else 2 if f >= 3 else 3 if f >= 2 else 4
            if args.dry_run:
                merged_details.append(
                    f"  [DRY-RUN] Definir T{tier} para id={it[0]} ({tipo}) '{it[2]}'"
                )
            else:
                c.execute("UPDATE pages SET version=? WHERE id=?", (tier, it[0]))
    c.commit()
    if args.dry_run:
        print(f"[DRY-RUN] Consolidação simulada: {merged} duplicados arquivados, "
              f"tiering T1-T4 aplicado")
        for d in merged_details:
            print(d)
    else:
        print(f"consolidado: {merged} duplicados arquivados, tiering T1-T4 aplicado")


# ────────────────────────────────────────────────────────── schema load


def load_schema() -> dict:
    """Le o schema pack (inspirado no brain-taxonomist do Gbrain).
    O tipo é decidido POR DADOS (este arquivo), não hardcode no código."""
    try:
        import yaml
        with open(SCHEMA, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return {
            "page_types": [{"name": t} for t in VALID],
            "routing": [{"match": "*", "type": "inbox"}]
        }


def taxonomist(content: str) -> str:
    """Gate de classificação: decide o tipo via schema pack + LLM leve."""
    schema = load_schema()
    types = ", ".join(t["name"] for t in schema.get("page_types", []))
    prompt = (
        f"Classifique o conteúdo abaixo em UMA das categorias: {types}. "
        "Responda só o nome da categoria. Conteúdo: " + content[:500]
    )
    try:
        out = call_gemma(prompt, temperature=0.1)
        out = out.strip().lower()
        for t in schema.get("page_types", []):
            if t["name"] in out:
                return t["name"]
    except Exception:
        pass
    return "inbox"


def cmd_taxonomist(args):
    tipo = taxonomist(args.conteudo)
    print(f"tipo sugerido: {tipo}")


# ────────────────────────────────────────────────────────── capture


def cmd_capture(args):
    """Entrada única (inspirado no capture do Gbrain): salva pensamento com
    hash dedup (idempotente) e roteia via taxonomist gate."""
    content = args.conteudo
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            content = f.read()
    elif args.stdin:
        content = sys.stdin.read()
    if not content:
        print("conteúdo vazio"); return
    h = hashlib.sha256(content.strip().encode()).hexdigest()[:8]
    c = conn()
    existing = c.execute(
        "SELECT id FROM pages WHERE corpo=? AND status='ativo' LIMIT 1",
        (content,)).fetchone()
    if existing:
        print(f"já existe (dedup): id={existing[0]}")
        return
    tipo = args.tipo or taxonomist(content)
    cur = c.execute(
        "INSERT INTO pages (tipo,titulo,corpo) VALUES (?,?,?)",
        (tipo, content[:80], content))
    c.commit()
    print(f"capturado -> tipo={tipo} id={cur.lastrowid} hash={h}")


# ────────────────────────────────────────────────────────── learn (com logs estruturados)


def cmd_learn(args):
    """Aprende com um arquivo jsonl de mensagens. O gemma4 extrai fatos recorrentes
    e entidades (pessoas/conceitos) e grava no brain. Anonimiza: não grava nomes de
    pacientes reais. Processa em chunks. Logs estruturados do que foi extraído."""
    c = conn()
    msgs = []
    with open(args.arquivo, encoding="utf-8") as f:
        for l in f:
            l = l.strip()
            if not l:
                continue
            try:
                m = json.loads(l)
            except Exception:
                continue
            if args.dia and not (m.get("ts") or "").startswith(args.dia + "T"):
                continue
            msgs.append(m)
    if not msgs:
        print("arquivo vazio / dia sem msgs"); return
    texts = [
        f"[{m.get('grupo','')}] {m.get('autor_pushname','')}: {m.get('texto','')}"
        for m in msgs if m.get("texto")
    ]
    chunks = [texts[i:i + 300] for i in range(0, len(texts), 300)]
    t0 = time.time()
    n_concepts = n_people = n_projects = n_chunks = n_errors = 0
    skipped_chunks = 0
    for ch in chunks:
        n_chunks += 1
        prompt = (
            "Você é o agente de aprendizado do Granjimmy. A partir das mensagens abaixo, "
            "EXTRAI aprendizados estruturados em JSON (sem narrativa). Regras:\n"
            "- NÃO exponha nomes de pacientes reais; use 'paciente', 'enfermagem', etc.\n"
            "- só extraia fatos recorrentes/úteis para operação "
            "(rotinas, regras, ocorrências, pessoas da equipe).\n"
            "Retorne JSON: "
            "{\"concepts\":[{\"titulo\":\"...\",\"corpo\":\"...\"}],"
            "\"people\":[{\"nome\":\"...\",\"notas\":\"...\"}],"
            "\"projects\":[{\"titulo\":\"...\",\"corpo\":\"...\"}]}\n\n"
            + "\n".join(ch)
        )
        out = call_gemma(prompt)
        try:
            data = json.loads(out)
        except Exception:
            m = re.search(r"\{.*\}", out, re.S)
            data = json.loads(m.group(0)) if m else {}
            n_errors += 1
        n_concepts += len(data.get("concepts", []))
        n_people += len(data.get("people", []))
        n_projects += len(data.get("projects", []))
        for item in data.get("concepts", []):
            c.execute(
                "INSERT INTO pages (tipo,titulo,corpo) VALUES (?,?,?)",
                ("concepts", item.get("titulo", ""), item.get("corpo", "")))
        for item in data.get("people", []):
            c.execute(
                "INSERT INTO pages (tipo,titulo,corpo) VALUES (?,?,?)",
                ("people", item.get("nome", ""), item.get("notas", "")))
        for item in data.get("projects", []):
            c.execute(
                "INSERT INTO pages (tipo,titulo,corpo) VALUES (?,?,?)",
                ("projects", item.get("titulo", ""), item.get("corpo", "")))
    c.commit()
    elapsed = time.time() - t0
    total = n_concepts + n_people + n_projects
    print(f"aprendido: {total} itens ({n_concepts} concepts, "
          f"{n_people} people, {n_projects} projects) "
          f"de {n_chunks} chunks em {elapsed:.1f}s "
          f"({n_errors} erros de parse de JSON)")


# ────────────────────────────────────────────────────────── check


def cmd_check(args):
    """Verifica a integridade do brain:
    - PRAGMA integrity_check
    - presença das tabelas pages e schema_version
    - versão do schema registrada vs versão da tool
    - contagem de registros ativos
    """
    c = conn(create_schema_version=False)
    print(f"Brain: {DB}")
    print()
    # 1. integridade
    try:
        result = c.execute("PRAGMA integrity_check").fetchone()
        integrity = result[0] if result else "unknown"
        print(f"Integridade: {integrity}")
        if integrity != "ok":
            print("  ⚠️ O PRAGMA integrity_check retornou algo diferente de 'ok'. "
                  "Considere restaurar de backup ou recomputar.")
    except Exception as e:
        print(f"Integridade: ERRO ({e})")
        print("  ⚠️ Não foi possível executar PRAGMA integrity_check.")
    print()
    # 2. tabelas
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    print(f"Tabelas: {', '.join(tables) if tables else 'nenhuma'}")
    for tbl in ("pages", "schema_version"):
        if tbl not in tables:
            print(f"  ⚠️ Tabela '{tbl}' não encontrada.")
    print()
    # 3. schema_version
    sv_row = c.execute(
        "SELECT version, applied_at, description FROM schema_version "
        "ORDER BY applied_at DESC LIMIT 1"
    ).fetchone()
    if sv_row:
        sv_version, sv_at, sv_desc = sv_row
        print(f"Schema version registrado: {sv_version} ({sv_at})")
        if sv_desc:
            print(f"  {sv_desc}")
        print()
        if sv_version != SCHEMA_VERSION:
            print(f"⚠ Versão da tool ({SCHEMA_VERSION}) != versão do brain ({sv_version}).")
            print("  Execute 'migrate' para aplicar atualizações pendentes, ou ignore se "
                  "não há migrações entre essas versões.")
    else:
        print(f"Schema version: não registrado.")
        print(f"  A tool está na versão {SCHEMA_VERSION}.")
        if args.register:
            print("  Registrando versão atual no brain...")
            apply_schema_version(c, SCHEMA_VERSION,
                                 f"Inicializado pela tool v{SCHEMA_VERSION}")
            print(f"  Registrado: {SCHEMA_VERSION}")
    print()
    # 4. contagem de registros
    try:
        ativos = c.execute(
            "SELECT COUNT(*) FROM pages WHERE status='ativo'").fetchone()[0]
        arquivados = c.execute(
            "SELECT COUNT(*) FROM pages WHERE status='arquivado'").fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        print(f"Registros: {total} total ({ativos} ativos, {arquivados} arquivados)")
    except Exception as e:
        print(f"Contagem: ERRO ({e})")


# ────────────────────────────────────────────────────────── init


def cmd_init(args):
    """Inicializa um novo brain (para expert ou global).
    Cria o diretório, brain.db com schema, registra schema_version,
    e copia schema_pack.yaml do global se --schema-pack não for dado."""
    target_dir = os.path.abspath(args.brain_dir)
    os.makedirs(target_dir, exist_ok=True)

    # brain.db
    db_path = os.path.join(target_dir, "brain.db")
    c = sqlite3.connect(db_path)
    c.execute("""CREATE TABLE IF NOT EXISTS pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,
        titulo TEXT,
        corpo TEXT,
        entidade TEXT,
        version INTEGER DEFAULT 1,
        status TEXT DEFAULT 'ativo',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    ensure_schema_version_table(c)
    apply_schema_version(c, SCHEMA_VERSION,
                         f"Inicializado pela tool v{SCHEMA_VERSION} ({args.scope})")
    c.execute(
        "INSERT INTO pages (tipo, titulo, corpo, entidade) VALUES (?, ?, ?, ?)",
        ("memory", f"Brain do {args.scope}",
         f"Knowledge base do {args.scope}. Inicializado pela tool v{SCHEMA_VERSION}.",
         args.scope),
    )
    c.commit()
    c.close()

    # schema_pack.yaml
    schema_dest = os.path.join(target_dir, "schema_pack.yaml")
    if args.schema_pack:
        import shutil
        shutil.copy(os.path.abspath(args.schema_pack), schema_dest)
    else:
        # Copiar do global se existir, senão usar template padrão
        global_schema = os.path.join(GLOBAL_DIR, "schema_pack.yaml")
        if os.path.exists(global_schema):
            import shutil
            shutil.copy(global_schema, schema_dest)
        else:
            default_schema = """# Schema pack — template padrão
page_types:
  - name: people
    primitive: entity
    description: Membros da equipe, admins, contatos (NAO pacientes reais)
  - name: concepts
    primitive: concept
    description: Conceitos do agente
  - name: projects
    primitive: concept
    description: Projetos do agente
  - name: groups
    primitive: entity
    description: Grupos/equipes relevantes
  - name: memory
    primitive: annotation
    description: Sinteses e memorias relacionais
  - name: inbox
    primitive: annotation
    description: Triagem temporaria
"""
            with open(schema_dest, "w", encoding="utf-8") as f:
                f.write(default_schema)

    print(f"✅ Inicializado {args.scope}: {target_dir}")
    print(f"   brain.db: {db_path}")
    print(f"   schema_pack.yaml: {schema_dest}")
    print(f"   schema_version: {SCHEMA_VERSION}")


# ────────────────────────────────────────────────────────── migrate


def cmd_migrate(args):
    """Aplica migrações pendentes do schema_version.
    Migrações são definidas como função version -> bool (True = aplicada).
    Se a versão atual do brain for menor que SCHEMA_VERSION, aplica os deltas.
    """
    c = conn()
    current = get_current_schema_version(c)
    print(f"Versão atual do brain: {current or 'não registrada'}")
    print(f"Versão da tool: {SCHEMA_VERSION}")
    print()
    if current == SCHEMA_VERSION:
        print("✅ Já na versão mais recente. Nada para migrar.")
        return
    if current and current > SCHEMA_VERSION:
        print(f"⚠ A tool (v{SCHEMA_VERSION}) é mais antiga que o brain (v{current}).")
        print("  Ignorando migração — atualize a tool para uma versão mais recente.")
        return

    # Define migrações: versão anterior -> versão alvo
    migrations = [
        # v1.0.0 -> v1.1.0: adiciona tabela schema_version + primeira entrada
        ("1.0.0", "1.1.0", "adiciona schema_version + entrada inicial"),
    ]
    applied = 0
    for from_v, to_v, desc in migrations:
        if current and from_v > (current or ""):
            continue  # skip migrações anteriores à versão atual
        if current and from_v <= current < to_v:
            # aplica a migração
            if to_v == "1.1.0":
                ensure_schema_version_table(c)
                if not get_current_schema_version(c):
                    apply_schema_version(c, to_v, desc)
                    applied += 1
                    print(f"✅ Migrado {from_v} → {to_v}: {desc}")
            else:
                print(f"⚠ Migração {from_v} → {to_v} não implementada.")
    if applied == 0:
        print("Nenhuma migração pendente encontrada.")
    else:
        print(f"\n✅ {applied} migração(ões) aplicada(s). Novo schema_version: {SCHEMA_VERSION}")


# ────────────────────────────────────────────────────────── main


def main():
    ap = argparse.ArgumentParser(
        description=f"Brain Tool v{VERSION} — CLI para gerenciar brains do ecossistema Granjimmy",
        epilog="Use --brain <path> para especificar um diretório de brain, "
               "ou --global para o brain global."
    )
    ap.add_argument("--brain", default=None,
                    help="Caminho para o diretório do brain "
                         "(contém brain.db + schema_pack.yaml)")
    ap.add_argument("--global", action="store_true", dest="use_global",
                    help=f"Usa o brain global ({GLOBAL_DIR})")

    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("remember")
    r.add_argument("--tipo", required=True, choices=VALID)
    r.add_argument("--titulo", required=True)
    r.add_argument("--corpo", required=True)
    r.add_argument("--entidade", default=None)
    r.set_defaults(func=cmd_remember)

    e = sub.add_parser("entity")
    e.add_argument("--tipo", required=True, choices=VALID)
    e.add_argument("--nome", required=True)
    e.add_argument("--notas", default="")
    e.set_defaults(func=cmd_entity)

    rc = sub.add_parser("recall")
    rc.add_argument("--tipo", default=None, choices=VALID)
    rc.add_argument("--termo", default=None)
    rc.set_defaults(func=cmd_recall)

    s = sub.add_parser("synthesize")
    s.add_argument("--entidade", required=True)
    s.set_defaults(func=cmd_synthesize)

    f = sub.add_parser("forget")
    f.add_argument("--id", type=int, required=True)
    f.add_argument("--dry-run", action="store_true",
                   help="Mostra o que seria arquivado sem executar")
    f.set_defaults(func=cmd_forget)

    ln = sub.add_parser("learn")
    ln.add_argument("--arquivo", required=True, help="Arquivo jsonl de mensagens")
    ln.add_argument("--dia", default=None, help="Filtrar só este dia YYYY-MM-DD")
    ln.set_defaults(func=cmd_learn)

    co = sub.add_parser("consolidate")
    co.add_argument("--dry-run", action="store_true",
                    help="Mostra o que seria consolidado sem executar")
    co.set_defaults(func=cmd_consolidate)

    tx = sub.add_parser("taxonomist")
    tx.add_argument("--conteudo", required=True)
    tx.set_defaults(func=cmd_taxonomist)

    cp = sub.add_parser("capture")
    cp.add_argument("--conteudo", default=None)
    cp.add_argument("--file", default=None)
    cp.add_argument("--stdin", action="store_true")
    cp.add_argument("--tipo", default=None, choices=VALID)
    cp.set_defaults(func=cmd_capture)

    chk = sub.add_parser("check")
    chk.add_argument("--register", action="store_true",
                     help="Registra a versão da tool no brain se não houver schema_version")
    chk.set_defaults(func=cmd_check)

    init_p = sub.add_parser("init")
    init_p.add_argument("--scope", required=True,
                        help="Scope do brain (ex: jimmy, gtic, global)")
    init_p.add_argument("--brain-dir", default=None,
                        help="Diretório do brain (se não informado, usa experts/<scope>/)")
    init_p.add_argument("--schema-pack", default=None,
                        help="Caminho para schema_pack.yaml a copiar (se não informado, usa global ou template)")
    init_p.set_defaults(func=cmd_init)

    mig = sub.add_parser("migrate")
    mig.set_defaults(func=cmd_migrate)

    args = ap.parse_args()

    # Determina o diretório do brain para comandos que precisam
    if args.cmd in ("check", "init", "migrate"):
        if args.cmd == "init":
            if args.brain_dir:
                brain_dir = args.brain_dir
            else:
                brain_dir = os.path.join(EXPERTS_DIR, args.scope)
                if args.scope == "global":
                    brain_dir = GLOBAL_DIR
        elif args.cmd == "migrate":
            if args.brain:
                brain_dir = args.brain
            elif args.use_global:
                brain_dir = GLOBAL_DIR
            else:
                profile = os.environ.get("BRAIN_PROFILE", "jimmy")
                brain_dir = os.path.join(EXPERTS_DIR, profile)
        elif args.cmd == "check":
            if args.brain:
                brain_dir = args.brain
            elif args.use_global:
                brain_dir = GLOBAL_DIR
            else:
                profile = os.environ.get("BRAIN_PROFILE", "jimmy")
                brain_dir = os.path.join(EXPERTS_DIR, profile)
    else:
        if args.use_global:
            brain_dir = GLOBAL_DIR
        elif args.brain:
            brain_dir = args.brain
        else:
            profile = os.environ.get("BRAIN_PROFILE", "jimmy")
            brain_dir = os.path.join(EXPERTS_DIR, profile)

    setup_brain(brain_dir)
    args.func(args)


if __name__ == "__main__":
    main()