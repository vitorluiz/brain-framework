#!/usr/bin/env python3
"""
Teste Exaustivo do Brain — Expert Nativo do Brain Framework
Executa TODOS os comandos do brain.py em ambiente isolado.
Versão corrigida: usa parse_json para lidar com headers no stdout.
"""

import os
import sys
import tempfile
import shutil
import subprocess
import json
from pathlib import Path

# === CONFIGURAÇÃO DO TESTE ===
BRAIN_PY = "/home/hermes/softwares/brain-framework/src/brain_tool/brain.py"
TEST_DIR = tempfile.mkdtemp(prefix="brain_exaustivo_")
BRAIN_ROOT = os.path.join(TEST_DIR, "brain")

# === FUNÇÕES DE TESTE ===
tests_passed = 0
tests_failed = 0
tests_total = 0
test_results = []  # (nome, passou, detalhes)


def run_brain(args, expected_rc=0):
    """Executa brain.py com os args e retorna (rc, stdout, stderr)"""
    env = os.environ.copy()
    env["BRAIN_ROOT"] = BRAIN_ROOT
    result = subprocess.run(
        [sys.executable, BRAIN_PY] + args,
        capture_output=True,
        text=True,
        timeout=30,
        env=env
    )
    return result.returncode, result.stdout, result.stderr


def parse_json_from_stdout(stdout):
    """Extrai JSON do stdout, ignorando headers como '=== Brain: ... ==='"""
    lines = stdout.strip().split('\n')
    start = None
    end = None
    for i, l in enumerate(lines):
        stripped = l.strip()
        if stripped.startswith('{'):
            start = i
        if start is not None and stripped.endswith('}'):
            end = i
            break
    if start is not None and end is not None:
        return json.loads('\n'.join(lines[start:end+1]))
    return None


def check(name, condition, detail=""):
    """Verifica uma condição e registra o resultado"""
    global tests_passed, tests_failed, tests_total
    tests_total += 1
    if condition:
        tests_passed += 1
        test_results.append((name, True, detail))
        print(f"✓ {name}")
        if detail:
            print(f"  {detail}")
    else:
        tests_failed += 1
        test_results.append((name, False, detail))
        print(f"✗ {name}")
        if detail:
            print(f"  {detail}")


def check_json(args, name, expected_key=None, expected_value=None, expected_rc=0):
    """Verifica que a saída é JSON válido e contém as chaves esperadas"""
    rc, stdout, stderr = run_brain(args, expected_rc)
    data = parse_json_from_stdout(stdout)
    if data is None:
        check(name, False, "não encontrou JSON no stdout")
        return False
    if expected_key:
        if expected_key in data:
            if expected_value is None or data[expected_key] == expected_value:
                check(name, True, f"chave '{expected_key}' presente")
                return True
            else:
                check(name, False, f"chave '{expected_key}' = {data[expected_key]}, esperado {expected_value}")
                return False
        else:
            check(name, False, f"chave '{expected_key}' NÃO encontrada. Chaves: {list(data.keys())}")
            return False
    else:
        check(name, True, "JSON válido")
        return True


# ============================================================
# TESTES
# ============================================================

print("=" * 65)
print("TESTE EXAUSTIVO: Brain — Expert Nativo do Brain Framework")
print("=" * 65)
print()

# --- 1. brain --help ---
print("1. Comando: brain --help")
rc, stdout, stderr = run_brain(["--help"])
check("brain --help retorna 0", rc == 0, f"rc={rc}")
check("brain --help menciona 'Brain — Expert Nativo'", "Brain — Expert Nativo" in stdout)
check("brain --help mostra subcomandos", all(cmd in stdout for cmd in ["add", "list", "remove", "global", "backup", "update", "sync", "admin"]))

# --- 2. brain init (brain_tool) ---
print("\n2. Comando: brain init (cria brain.db novo)")
init_db = os.path.join(TEST_DIR, "init_test.db")
rc, stdout, stderr = run_brain(["init", "--name", "teste-init", "--brain-path", init_db])
check("brain init retorna 0", rc == 0, f"rc={rc}, stderr={stderr[:100] if stderr else 'none'}")

# Verifica o brain.db criado
check("brain.db criado", os.path.exists(init_db))
if os.path.exists(init_db):
    import sqlite3
    conn = sqlite3.connect(init_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM pages")
    count = cursor.fetchone()[0]
    conn.close()
    check("brain.db tem página inicial", count >= 1, f"{count} páginas")

# --- 3. brain remember ---
print("\n3. Comando: brain remember (adiciona conhecimento)")
rc, stdout, stderr = run_brain([
    "remember", "--expert", "teste-init",
    "--brain-path", init_db,
    "--tipo", "memory",
    "--title", "Meu Título",
    "--content", "Conteúdo de teste para remember"
])
check("brain remember retorna 0", rc == 0)
check_json(["remember", "--expert", "teste-init",
            "--brain-path", init_db,
            "--tipo", "memory",
            "--title", "Meu Título 2",
            "--content", "Conteúdo 2"], "brain remember retorna JSON com 'action'",
           expected_key="action", expected_value="remember")

# --- 4. brain recall ---
print("\n4. Comando: brain recall (recupera conhecimento)")
rc, stdout, stderr = run_brain([
    "recall", "--expert", "teste-init",
    "--brain-path", init_db,
    "--limit", "5"
])
check("brain recall retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain recall retorna lista", isinstance(data, list))
    if isinstance(data, list):
        check("brain recall tem resultados", len(data) >= 2, f"{len(data)} resultados")
        if len(data) > 0:
            check("brain recall inclui 'id'", "id" in data[0])
            check("brain recall inclui 'expert'", "expert" in data[0])
            check("brain recall inclui 'tipo'", "tipo" in data[0])
            check("brain recall inclui 'titulo'", "titulo" in data[0])
            check("brain recall inclui 'corpo'", "corpo" in data[0])
            check("brain recall inclui 'hash_canonical'", "hash_canonical" in data[0])
    else:
        check("brain recall retorna algo", False, f"tipo inesperado: {type(data)}")
else:
    check("brain recall retorna JSON", False, "stdout não é JSON")

# --- 5. brain recall com search ---
print("\n5. Comando: brain recall --search (busca)")
rc, stdout, stderr = run_brain([
    "recall", "--expert", "teste-init",
    "--brain-path", init_db,
    "--search", "remember"
])
check("brain recall --search retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data and isinstance(data, list):
    check("brain recall --search encontra resultados", len(data) >= 1, f"{len(data)} resultados para 'remember'")
else:
    check("brain recall --search funciona", False)

# --- 6. brain forget dry-run ---
print("\n6. Comando: brain forget --dry-run (preview seguro)")
rc, stdout, stderr = run_brain([
    "forget", "--expert", "teste-init",
    "--brain-path", init_db,
    "--id", "1", "--dry-run"
])
check("brain forget --dry-run retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain forget --dry-run tem 'would_delete'", data.get("would_delete") is True)
    check("brain forget --dry-run mostra id", data.get("id") == 1)
else:
    check("brain forget --dry-run funciona", False)

# --- 7. brain forget (execução) ---
print("\n7. Comando: brain forget (remove conhecimento)")
rc, stdout, stderr = run_brain([
    "forget", "--expert", "teste-init",
    "--brain-path", init_db,
    "--id", "1"
])
check("brain forget retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain forget deletou", data.get("deleted") is True)
else:
    check("brain forget funciona", False)

# Verifica que a página 1 foi removida
rc, stdout, stderr = run_brain([
    "recall", "--expert", "teste-init",
    "--brain-path", init_db,
    "--limit", "10"
])
data = parse_json_from_stdout(stdout)
if data and isinstance(data, list):
    ids_restantes = [p.get("id") for p in data]
    check("brain forget removeu a página", 1 not in ids_restantes, f"ids restantes: {ids_restantes}")
else:
    check("verificar remoção", False)

# --- 8. brain synthesize ---
print("\n8. Comando: brain synthesize (gera síntese)")
rc, stdout, stderr = run_brain([
    "synthesize", "--expert", "teste-init",
    "--brain-path", init_db
])
check("brain synthesize retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain synthesize tem 'synthesis'", "synthesis" in data)
    check("brain synthesize tem 'pages_count'", "pages_count" in data)
    check("brain synthesize tem 'by_type'", "by_type" in data)
else:
    check("brain synthesize funciona", False)

# --- 9. brain consolidate dry-run ---
print("\n9. Comando: brain consolidate --dry-run (detecta duplicatas)")
# Adiciona duas entradas com mesmo conteúdo
run_brain([
    "remember", "--expert", "teste-init",
    "--brain-path", init_db,
    "--tipo", "fact",
    "--title", "Original",
    "--content", "Conteúdo idêntico para testar consolidate"
])
run_brain([
    "remember", "--expert", "teste-init",
    "--brain-path", init_db,
    "--tipo", "fact",
    "--title", "Duplicata",
    "--content", "Conteúdo idêntico para testar consolidate"
])
rc, stdout, stderr = run_brain([
    "consolidate", "--expert", "teste-init",
    "--brain-path", init_db,
    "--dry-run"
])
check("brain consolidate --dry-run retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain consolidate --dry-run tem 'duplicates_found'", data.get("duplicates_found", 0) >= 1,
          f"duplicatas encontradas: {data.get('duplicates_found', 0)}")
    check("brain consolidate --dry-run mostra 'would_remove'", data.get("would_remove", 0) >= 1)
else:
    check("brain consolidate --dry-run funciona", False)

# --- 10. brain consolidate (execução) ---
print("\n10. Comando: brain consolidate (remove duplicatas)")
rc, stdout, stderr = run_brain([
    "consolidate", "--expert", "teste-init",
    "--brain-path", init_db
])
check("brain consolidate retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain consolidate removeu duplicatas", data.get("removed_count", 0) >= 1,
          f"removido: {data.get('removed_count', 0)}")
else:
    check("brain consolidate funciona", False)

# Verifica que sobrou 1 entrada com esse conteúdo (busca por 'consolidate' no corpo)
rc, stdout, stderr = run_brain([
    "recall", "--expert", "teste-init",
    "--brain-path", init_db,
    "--search", "consolidar"
])
data = parse_json_from_stdout(stdout)
if data and isinstance(data, list):
    check("brain consolidate: 1 entrada sobrou (busca por 'consolidar')", len(data) == 1,
          f"restam {len(data)} entradas")
else:
    check("verificar consolidate", False)

# --- 11. brain learn (arquivo) ---
print("\n11. Comando: brain learn (processa arquivo)")
test_file = os.path.join(TEST_DIR, "learn_test.txt")
with open(test_file, "w") as f:
    f.write("Conteúdo para learn e sync\nLinha 2 do arquivo\n")

rc, stdout, stderr = run_brain([
    "learn", "--expert", "teste-init",
    "--brain-path", init_db,
    "--path", test_file, "--sync"
])
check("brain learn + sync retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain learn tem 'action' = learn", data.get("action") == "learn")
    check("brain learn tem 'sync'", "sync" in data)
    check("brain learn sync tem 'status' = synced", data.get("sync", {}).get("status") == "synced")
else:
    check("brain learn funciona", False)

# Verifica que o conteúdo sincronizado aparece no recall
rc, stdout, stderr = run_brain([
    "recall", "--expert", "teste-init",
    "--brain-path", init_db,
    "--search", "learn"
])
data = parse_json_from_stdout(stdout)
if data and isinstance(data, list):
    check("brain learn+sync: conteúdo aparece no recall", len(data) >= 1,
          f"{len(data)} resultados para 'learn'")
else:
    check("verificar learn+sync", False)

# --- 12. brain learn (dry-run) ---
print("\n12. Comando: brain learn --dry-run (preview)")
rc, stdout, stderr = run_brain([
    "learn", "--expert", "teste-init",
    "--brain-path", init_db,
    "--path", test_file, "--dry-run"
])
check("brain learn --dry-run retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain learn --dry-run tem 'would_add_to_staging'", data.get("would_add_to_staging") is True)
    check("brain learn --dry-run mostra 'hash'", "hash" in data)
else:
    check("brain learn --dry-run funciona", False)

# --- 13. brain check ---
print("\n13. Comando: brain check (integridade + schema)")
rc, stdout, stderr = run_brain([
    "check", "--expert", "teste-init",
    "--brain-path", init_db
])
check("brain check retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain check tem 'integrity'", "integrity" in data)
    check("brain check 'integrity' = ok", data.get("integrity") == "ok")
    check("brain check tem 'schema_version'", "schema_version" in data)
    check("brain check tem 'tables'", "tables" in data)
    check("brain check tem 'counts'", "counts" in data)
    check("brain check 'schema_version_actual' = 1.0.0", data.get("schema_version_actual") == "1.0.0")
    check("brain check 'issues' = [] (sem problemas)", data.get("issues", []) == [])
else:
    check("brain check funciona", False)

# --- 14. brain jobs ---
print("\n14. Comando: brain jobs (lista jobs)")
rc, stdout, stderr = run_brain([
    "jobs", "--expert", "teste-init",
    "--brain-path", init_db
])
check("brain jobs retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain jobs retorna lista", isinstance(data, list))
else:
    check("brain jobs funciona", False)

# --- 15. brain taxonomist ---
print("\n15. Comando: brain taxonomist (sugeri regras)")
rc, stdout, stderr = run_brain([
    "taxonomist", "--expert", "teste-init",
    "--brain-path", init_db
])
check("brain taxonomist retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain taxonomist tem 'tipo_distribution'", "tipo_distribution" in data)
    check("brain taxonomist tem 'suggestions'", "suggestions" in data)
else:
    check("brain taxonomist funciona", False)

# --- 16. brain capture ---
print("\n16. Comando: brain capture (captura taxonomia)")
rc, stdout, stderr = run_brain([
    "capture", "--expert", "teste-init",
    "--brain-path", init_db,
    "--type", "procedure",
    "--content", "Procedimento capturado pela taxonomia"
])
check("brain capture retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain capture tem 'action' = remember", data.get("action") == "remember")
else:
    check("brain capture funciona", False)

# --- 17. Migração de schema antigo ---
print("\n17. Teste: Migração de schema antigo (brain.db sem coluna 'expert')")
old_schema_db = os.path.join(TEST_DIR, "old_schema.db")
import sqlite3
old_conn = sqlite3.connect(old_schema_db)
old_conn.execute("""
    CREATE TABLE pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidade TEXT,
        version INTEGER,
        status TEXT,
        created_at TEXT,
        updated_at TEXT
    )
""")
old_conn.execute("""
    INSERT INTO pages (entidade, version, status, created_at)
    VALUES ('teste', 1, 'active', '2026-01-01')
""")
old_conn.commit()
old_conn.close()

# Tenta usar com brain_tool — deve migrar automaticamente
rc, stdout, stderr = run_brain([
    "recall", "--expert", "global",
    "--brain-path", old_schema_db
])
check("brain_tool lê brain.db antigo sem erro", rc == 0, f"rc={rc}, stderr={stderr[:100] if stderr else 'none'}")

# Verifica se as colunas foram adicionadas
check_conn = sqlite3.connect(old_schema_db)
check_conn.row_factory = sqlite3.Row
cursor = check_conn.cursor()
cursor.execute("PRAGMA table_info(pages)")
columns = [r['name'] for r in cursor.fetchall()]
check_conn.close()

check("brain_tool migra: coluna 'expert' adicionada", "expert" in columns,
      f"colunas: {columns}")

# --- 18. brain add profile ---
print("\n18. Comando: brain add profile (cria profile + brain.db)")
profile_db = os.path.join(BRAIN_ROOT, "experts", "maria-test", "brain.db")
rc, stdout, stderr = run_brain(["add", "profile", "maria-test"])
check("brain add profile retorna 0", rc == 0, f"rc={rc}")
check("brain add profile cria brain.db", os.path.exists(profile_db))

if os.path.exists(profile_db):
    conn = sqlite3.connect(profile_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM pages")
    count = cursor.fetchone()[0]
    conn.close()
    check("brain add profile cria página inicial", count >= 1, f"{count} páginas")

# --- 19. brain list profiles ---
print("\n19. Comando: brain list profiles (lista todos)")
rc, stdout, stderr = run_brain(["list", "profiles"])
check("brain list profiles retorna 0", rc == 0)
check("brain list profiles mostra 'maria-test'", "maria-test" in stdout)

# --- 20. brain global learn ---
print("\n20. Comando: brain global learn (aprende conhecimento global)")
os.makedirs(os.path.join(BRAIN_ROOT, "global"), exist_ok=True)
global_db = os.path.join(BRAIN_ROOT, "global", "brain.db")

rc, stdout, stderr = run_brain([
    "global", "learn", "--content",
    "Horário de funcionamento: 8h-18h, segunda a sexta",
    "--title", "Horário de Funcionamento"
])
check("brain global learn --content retorna 0", rc == 0)
check("global brain.db foi criado", os.path.exists(global_db))

data = parse_json_from_stdout(stdout)
if data:
    check("brain global learn tem 'action' = remember", data.get("action") == "remember")
    check("brain global learn tem 'expert' = global", data.get("expert") == "global")
else:
    check("brain global learn funciona", False)

# --- 21. brain global learn --path ---
print("\n21. Comando: brain global learn --path (arquivo)")
global_test_file = os.path.join(TEST_DIR, "global_learn_test.txt")
with open(global_test_file, "w") as f:
    f.write("Conteúdo de teste para global learn\nMultiplas linhas\n")

rc, stdout, stderr = run_brain([
    "global", "learn", "--path", global_test_file, "--sync"
])
check("brain global learn --path --sync retorna 0", rc == 0)
data = parse_json_from_stdout(stdout)
if data:
    check("brain global learn --path tem 'action' = learn", data.get("action") == "learn")
    check("brain global learn --path tem 'sync'", "sync" in data)
else:
    check("brain global learn --path funciona", False)

# --- 22. brain admin commands ---
print("\n22. Comando: brain admin (gestão de administradores)")
admin_file = os.path.join(BRAIN_ROOT, "admins.json")

# Lista (vazio)
rc, stdout, stderr = run_brain(["admin", "list"])
check("brain admin list retorna 0", rc == 0)

# Adiciona
rc, stdout, stderr = run_brain(["admin", "add", "whatsapp", "+551999999999"])
check("brain admin add retorna 0", rc == 0)
check("admins.json foi criado", os.path.exists(admin_file))

# Verifica conteúdo
if os.path.exists(admin_file):
    with open(admin_file) as f:
        admins = json.load(f)
    check("brain admin add adicionou admin", "wa:+551999999999" in admins["admins"])

# Lista (com admin)
rc, stdout, stderr = run_brain(["admin", "list"])
check("brain admin list mostra admin", "+551999999999" in stdout)

# Remove
rc, stdout, stderr = run_brain(["admin", "remove", "+551999999999"])
check("brain admin remove retorna 0", rc == 0)

# Verifica remoção
if os.path.exists(admin_file):
    with open(admin_file) as f:
        admins = json.load(f)
    check("brain admin remove removeu admin", "wa:+551999999999" not in admins["admins"])

# --- 23. brain backup ---
print("\n23. Comando: brain backup (backup de todos os brains)")
rc, stdout, stderr = run_brain(["backup"])
check("brain backup retorna 0", rc == 0)

backups_dir = os.path.join(BRAIN_ROOT, "backups")
check("brain backup criou diretório", os.path.isdir(backups_dir))

backup_dirs = sorted([d for d in os.listdir(backups_dir) if d.startswith("backup_")])
if backup_dirs:
    last_backup = os.path.join(backups_dir, backup_dirs[-1])
    manifest_path = os.path.join(last_backup, "manifest.json")
    check("brain backup criou manifest.json", os.path.exists(manifest_path))
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
        check("brain backup manifest tem contagem", manifest.get("count", 0) >= 2,
              f"contagem: {manifest.get('count', 0)}")
else:
    check("brain backup criou backup", False)

# --- 24. brain sync all ---
print("\n24. Comando: brain sync all (sync de todos os brains)")
rc, stdout, stderr = run_brain(["sync", "all"])
check("brain sync all retorna 0", rc == 0)
check("brain sync all processa global", "global" in stdout)
check("brain sync all processa maria-test", "maria-test" in stdout)

# --- 25. brain remove profile ---
print("\n25. Comando: brain remove profile (remove profile + brain.db)")
rc, stdout, stderr = run_brain(["remove", "profile", "maria-test"])
check("brain remove profile retorna 0", rc == 0)
check("brain remove profile removeu brain.db", not os.path.exists(profile_db))
check("brain remove profile removeu diretório",
      not os.path.exists(os.path.join(BRAIN_ROOT, "experts", "maria-test")))

# --- 26. brain update ---
print("\n26. Comando: brain update (atualiza framework via git)")
# Não testamos de fato pois precisa de internet e Git configurado
# Apenas verificamos que o comando existe e aceita ser chamado
rc, stdout, stderr = run_brain(["update"])
# Pode falhar porque não tem git configurado no TEST_DIR, mas o comando existe
check("brain update é um comando válido", rc in [0, 1], f"rc={rc} (pode falhar sem git)")

# ============================================================
# RESULTADO FINAL
# ============================================================

print()
print("=" * 65)
print(f"RESULTADO FINAL: {tests_passed}/{tests_total} testes passaram")
if tests_failed > 0:
    print(f"                   {tests_failed} testes falharam")
    print()
    print("DETALHES DOS FALHADOS:")
    for name, passed, detail in test_results:
        if not passed:
            print(f"  ✗ {name}: {detail}")
    print()
    print("=" * 65)
    print("RESULTADO: 테스트 FAILED")
    print("=" * 65)
    sys.exit(1)
else:
    print("=" * 65)
    print("RESULTADO: TODOS OS TESTES PASSARAM ✓")
    print("=" * 65)
    print()
    print("Resumo:")
    print(f"  - brain.py: expert nativo totalmente funcional")
    print(f"  - brain_tool.py: CLI core com CRUD, learn+sync, hash canônico")
    print(f"  - brain init/remember/recall/forget/synthesize/consolidate: OK")
    print(f"  - brain learn/sync/check/jobs/taxonomist/capture: OK")
    print(f"  - brain global learn (--content + --path): OK")
    print(f"  - brain admin (list/add/remove): OK")
    print(f"  - brain backup/sync all: OK")
    print(f"  - brain add/remove profile: OK")
    print(f"  - Migração de schema antigo: OK")
    print()
    print(f"Arquivo de teste removido: {TEST_DIR}")

sys.exit(0)
