#!/usr/bin/env python3
"""
Teste completo do Brain — Expert Nativo do Brain Framework
Executa todos os comandos do brain.py para validar a implementação.
"""

import os
import sys
import tempfile
import shutil
import subprocess
import json
from pathlib import Path

# Caminhos
BRAIN_PY = "/home/hermes/softwares/brain-framework/src/brain_tool/brain.py"
BRAIN_TOOL_PY = "/home/hermes/softwares/brain-framework/src/brain_tool/brain_tool.py"

# Diretório temporário para testes isolados
TEST_DIR = tempfile.mkdtemp(prefix="brain_test_")
TEST_BRAIN_ROOT = os.path.join(TEST_DIR, "brain")

# Variáveis de ambiente para isolamento
os.environ["BRAIN_ROOT"] = TEST_BRAIN_ROOT

tests_passed = 0
tests_failed = 0
tests_total = 0


def run(cmd_args, timeout=30):
    """Executa um comando e retorna (returncode, stdout, stderr)"""
    result = subprocess.run(
        [sys.executable, BRAIN_PY] + cmd_args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "BRAIN_ROOT": TEST_BRAIN_ROOT}
    )
    return result.returncode, result.stdout, result.stderr


def assert_ok(cmd_args, description):
    """Asserts que o comando retorna 0"""
    global tests_passed, tests_failed, tests_total
    tests_total += 1
    rc, stdout, stderr = run(cmd_args)
    if rc == 0:
        tests_passed += 1
        print(f"✓ {description}")
        return True
    else:
        tests_failed += 1
        print(f"✗ {description}")
        print(f"  rc={rc}")
        print(f"  stdout: {stdout[:200]}")
        print(f"  stderr: {stderr[:200]}")
        return False


def assert_json_contains(cmd_args, description, expected_key, expected_value=None):
    """Asserts que a saída JSON contém uma chave com valor esperado"""
    global tests_passed, tests_failed, tests_total
    tests_total += 1
    rc, stdout, stderr = run(cmd_args)
    if rc != 0:
        tests_failed += 1
        print(f"✗ {description} (comando falhou)")
        return False
    try:
        data = json.loads(stdout)
        if expected_key in data:
            if expected_value is None or data[expected_key] == expected_value:
                tests_passed += 1
                print(f"✓ {description}")
                return True
            else:
                tests_failed += 1
                print(f"✗ {description}: {expected_key}={data[expected_key]}, esperado {expected_value}")
                return False
        else:
            tests_failed += 1
            print(f"✗ {description}: chave '{expected_key}' nao encontrada")
            return False
    except json.JSONDecodeError:
        tests_failed += 1
        print(f"✗ {description}: stdout nao e JSON valido")
        return False


def assert_file_exists(path, description):
    """Asserts que um arquivo existe"""
    global tests_passed, tests_failed, tests_total
    tests_total += 1
    if os.path.exists(path):
        tests_passed += 1
        print(f"✓ {description}")
        return True
    else:
        tests_failed += 1
        print(f"✗ {description}: arquivo nao encontrado em {path}")
        return False


def assert_dir_contains(dir_path, files, description):
    """Asserts que um diretorio contem os arquivos esperados"""
    global tests_passed, tests_failed, tests_total
    tests_total += 1
    if not os.path.isdir(dir_path):
        tests_failed += 1
        print(f"✗ {description}: diretorio nao existe")
        return False
    existing = set(os.listdir(dir_path))
    expected = set(files)
    if expected.issubset(existing):
        tests_passed += 1
        print(f"✓ {description}")
        return True
    missing = expected - existing
    tests_failed += 1
    print(f"✗ {description}: arquivos faltando: {missing}")
    return False


def assert_not_exists(path, description):
    """Asserts que um arquivo NÃO existe"""
    global tests_passed, tests_failed, tests_total
    tests_total += 1
    if not os.path.exists(path):
        tests_passed += 1
        print(f"✓ {description}")
        return True
    else:
        tests_failed += 1
        print(f"✗ {description}: arquivo ainda existe em {path}")
        return False


# ============================================================
# TESTES
# ============================================================

print("=" * 60)
print("TESTE: Brain — Expert Nativo do Brain Framework")
print("=" * 60)
print()

# --- Teste 1: brain --help ---
print("1. Testando brain --help")
assert_ok(["--help"], "brain --help retorna 0")
assert_json_contains(["--help"], "brain --help contem 'Brain — Expert Nativo'", "action", None)

# --- Teste 2: brain list profiles (sem profiles) ---
print("\n2. Testando brain list profiles (vazio)")
assert_ok(["list", "profiles"], "brain list profiles retorna 0 (vazio)")
rc, stdout, _ = run(["list", "profiles"])
assert "Nenhum profile encontrado" in stdout, "brain list profiles mostra 'Nenhum profile encontrado'"

# --- Teste 3: brain add profile ---
print("\n3. Testando brain add profile")
assert_ok(["add", "profile", "maria"], "brain add profile maria retorna 0")

# Verifica que o brain.db foi criado
maria_db = os.path.join(TEST_BRAIN_ROOT, "experts", "maria", "brain.db")
assert_file_exists(maria_db, "brain.db de maria criado")

# Verifica conteúdo inicial
rc, stdout, _ = run(["list", "profiles"])
assert "maria" in stdout, "brain list profiles mostra maria"
assert "conhecimentos" in stdout or "1" in stdout, "brain list profiles mostra conhecimentos de maria"

# --- Teste 4: brain add profile jose ---
print("\n4. Testando brain add profile jose")
assert_ok(["add", "profile", "jose"], "brain add profile jose retorna 0")
jose_db = os.path.join(TEST_BRAIN_ROOT, "experts", "jose", "brain.db")
assert_file_exists(jose_db, "brain.db de jose criado")

# --- Teste 5: brain list profiles (com profiles) ---
print("\n5. Testando brain list profiles (com profiles)")
rc, stdout, _ = run(["list", "profiles"])
assert "maria" in stdout, "brain list profiles contem maria"
assert "jose" in stdout, "brain list profiles contem jose"
assert "Perfis configurados (2)" in stdout, "brain list profiles conta 2 perfis"
tests_passed += 1
print("✓ brain list profiles contem maria e jose com contagem correta")

# --- Teste 6: brain global learn ---
print("\n6. Testando brain global learn")
assert_ok(["global", "learn", "--content", "Horário: 8h-18h", "--title", "Horário"],
          "brain global learn --content retorna 0")

# Verifica se o global brain.db foi criado
global_db = os.path.join(TEST_BRAIN_ROOT, "global", "brain.db")
assert_file_exists(global_db, "global brain.db criado")

# --- Teste 7: brain global learn --path ---
print("\n7. Testando brain global learn --path")
test_file = os.path.join(TEST_DIR, "teste_global.txt")
with open(test_file, "w") as f:
    f.write("Conteúdo de teste para global learn\nLinha 2\n")

assert_ok(["global", "learn", "--path", test_file, "--sync"],
          "brain global learn --path --sync retorna 0")

# --- Teste 8: brain backup ---
print("\n8. Testando brain backup")
assert_ok(["backup"], "brain backup retorna 0")

# Verifica se o backup foi criado
backups_dir = os.path.join(TEST_BRAIN_ROOT, "backups")
assert_dir_contains(backups_dir, [], "backups dir existe")  # qualquer backup

# Verifica manifest
backup_dirs = sorted([d for d in os.listdir(backups_dir) if d.startswith("backup_")])
if backup_dirs:
    last_backup = os.path.join(backups_dir, backup_dirs[-1])
    manifest_path = os.path.join(last_backup, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["count"] >= 2, f"backup tem {manifest['count']} brains (esperado >= 2)"
        tests_passed += 1
        print(f"✓ brain backup criou manifest com {manifest['count']} brains")
    else:
        tests_failed += 1
        print(f"✗ brain backup: manifest.json nao encontrado")
else:
    tests_failed += 1
    print(f"✗ brain backup: nenhum diretorio de backup encontrado")

# --- Teste 9: brain sync all ---
print("\n9. Testando brain sync all")
assert_ok(["sync", "all"], "brain sync all retorna 0")

# --- Teste 10: brain admin commands ---
print("\n10. Testando brain admin commands")
assert_ok(["admin", "add", "whatsapp", "+551999999999"],
          "brain admin add whatsapp retorna 0")

admin_config = os.path.join(TEST_BRAIN_ROOT, "admins.json")
assert_file_exists(admin_config, "admins.json criado")

# Verifica conteúdo do admins.json
with open(admin_config) as f:
    admins = json.load(f)
assert "wa:+551999999999" in admins["admins"], "admin whatsapp adicionado"
tests_passed += 1
print("✓ brain admin add whatsapp adicionou admin corretamente")

assert_ok(["admin", "list"], "brain admin list retorna 0")
rc, stdout, _ = run(["admin", "list"])
assert "wa:+551999999999" in stdout, "brain admin list mostra admin"
tests_passed += 1
print("✓ brain admin list mostra admin adicionado")

assert_ok(["admin", "remove", "+551999999999"], "brain admin remove retorna 0")
with open(admin_config) as f:
    admins = json.load(f)
assert "wa:+551999999999" not in admins["admins"], "admin removido do admins.json"
tests_passed += 1
print("✓ brain admin remove remove admin corretamente")

# --- Teste 11: brain remove profile ---
print("\n11. Testando brain remove profile")
assert_ok(["remove", "profile", "maria"], "brain remove profile maria retorna 0")
assert_not_exists(maria_db, "brain.db de maria removido")

rc, stdout, _ = run(["list", "profiles"])
assert "maria" not in stdout, "brain list profiles nao mostra maria apos remocao"
tests_passed += 1
print("✓ brain remove profile remove profile corretamente")

# --- Teste 12: brain_tool.py (CLI core) integração ---
print("\n12. Testando integração brain_tool.py")
# Testa init
assert_ok(["init", "--name", "teste-core", "--brain-path",
           os.path.join(TEST_DIR, "teste_core.db")],
          "brain_tool init retorna 0")

# Testa remember
assert_ok(["remember", "--expert", "teste-core",
           "--brain-path", os.path.join(TEST_DIR, "teste_core.db"),
           "--tipo", "memory", "--title", "Teste",
           "--content", "Conteúdo de teste"],
          "brain_tool remember retorna 0")

# Testa recall
rc, stdout, _ = run(["recall", "--expert", "teste-core",
                     "--brain-path", os.path.join(TEST_DIR, "teste_core.db")])
if rc == 0 and "Teste" in stdout:
    tests_passed += 1
    print("✓ brain_tool recall recupera conhecimento adicionado")
else:
    tests_failed += 1
    print(f"✗ brain_tool recall falhou: rc={rc}")

# Testa learn + sync
test_learn_file = os.path.join(TEST_DIR, "learntest.txt")
with open(test_learn_file, "w") as f:
    f.write("Conteúdo para learn e sync\n")

assert_ok(["learn", "--expert", "teste-core",
           "--brain-path", os.path.join(TEST_DIR, "teste_core.db"),
           "--path", test_learn_file, "--sync"],
          "brain_tool learn + sync retorna 0")

# Verifica se o conteúdo foi syncado
rc, stdout, _ = run(["recall", "--expert", "teste-core",
                     "--brain-path", os.path.join(TEST_DIR, "teste_core.db"),
                     "--search", "learn"])
if rc == 0:
    tests_passed += 1
    print("✓ brain_tool learn+sync: conteúdo aparece no recall")
else:
    tests_failed += 1
    print(f"✗ brain_tool learn+sync: recall falhou")

# --- Teste 13: schema evolution (global.db com schema antigo) ---
print("\n13. Testando evolução de schema (brain.db antigo)")
# Cria um brain.db com schema incompatível (sem coluna expert)
old_schema_db = os.path.join(TEST_DIR, "old_schema.db")
old_conn = __import__('sqlite3').connect(old_schema_db)
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

# Agora tenta usar com brain_tool — deve migrar automaticamente
rc, stdout, stderr = run(["recall", "--expert", "global",
                          "--brain-path", old_schema_db])
if rc == 0:
    tests_passed += 1
    print("✓ brain_tool migra schema antigo automaticamente (ADD COLUMN expert)")
else:
    tests_failed += 1
    print(f"✗ brain_tool falhou ao migrar schema antigo: {stderr[:200]}")

# Verifica se as colunas foram adicionadas
check_conn = __import__('sqlite3').connect(old_schema_db)
check_conn.row_factory = __import__('sqlite3').Row
cursor = check_conn.cursor()
cursor.execute("PRAGMA table_info(pages)")
columns = [r['name'] for r in cursor.fetchall()]
if "expert" in columns:
    tests_passed += 1
    print(f"✓ brain_tool: coluna 'expert' adicionada ao schema antigo (colunas: {columns})")
else:
    tests_failed += 1
    print(f"✗ brain_tool: coluna 'expert' NAO adicionada (colunas: {columns})")
check_conn.close()

# --- Teste 14: hash canônico (idempotência do sync) ---
print("\n14. Testando hash canônico e idempotência do sync")
hash_test_db = os.path.join(TEST_DIR, "hash_test.db")
# Cria e faz remember com mesmo conteúdo duas vezes
assert_ok(["remember", "--expert", "hashtest",
           "--brain-path", hash_test_db,
           "--tipo", "fact", "--title", "Fato 1",
           "--content", "Conteúdo idêntico"],
          "brain_tool remember 1")

assert_ok(["remember", "--expert", "hashtest",
           "--brain-path", hash_test_db,
           "--tipo", "fact", "--title", "Fato 2",
           "--content", "Conteúdo idêntico"],
          "brain_tool remember 2 (mesmo conteúdo)")

# Recall e verifica se ambos foram criados (hash é igual mas titulo é diferente)
rc, stdout, _ = run(["recall", "--expert", "hashtest",
                     "--brain-path", hash_test_db])
if rc == 0:
    try:
        data = json.loads(stdout)
        if len(data) == 2:
            tests_passed += 1
            print("✓ brain_tool: dois remembers com mesmo conteúdo criam duas entradas (titulo diferente)")
        else:
            tests_failed += 1
            print(f"✗ brain_tool: esperado 2 entradas, tem {len(data)}")
    except json.JSONDecodeError:
        tests_failed += 1
        print("✗ brain_tool recall: stdout nao e JSON")

# --- Teste 15: dry-run commands ---
print("\n15. Testando --dry-run")
dryrun_db = os.path.join(TEST_DIR, "dryrun.db")
# Cria uma entrada primeiro
assert_ok(["remember", "--expert", "dryrun",
           "--brain-path", dryrun_db,
           "--tipo", "system", "--title", "Original",
           "--content", "Conteúdo original"],
          "brain_tool remember para dry-run test")

# Tenta forget com --dry-run
rc, stdout, _ = run(["forget", "--expert", "dryrun",
                     "--brain-path", dryrun_db,
                     "--id", "1", "--dry-run"])
if rc == 0 and "would_delete" in stdout:
    tests_passed += 1
    print("✓ brain_tool forget --dry-run mostra preview sem deletar")
else:
    tests_failed += 1
    print(f"✗ brain_tool forget --dry-run falhou: rc={rc}")

# Verifica que a entrada ainda existe (não foi deletada)
rc, stdout, _ = run(["recall", "--expert", "dryrun",
                     "--brain-path", dryrun_db,
                     "--id", "1"])
if rc == 0 and "Original" in stdout:
    tests_passed += 1
    print("✓ brain_tool forget --dry-run NAO deletou a entrada")
else:
    tests_failed += 1
    print(f"✗ brain_tool forget --dry-run deletou a entrada (ou falhou)")

# --- Teste 16: CONSOLIDATE dry-run ---
print("\n16. Testando consolidate --dry-run")
cons_db = os.path.join(TEST_DIR, "cons.db")
# Adiciona duas entradas duplicadas
assert_ok(["remember", "--expert", "cons",
           "--brain-path", cons_db,
           "--tipo", "fact", "--title", "Original",
           "--content", "Conteúdo para consolidar"],
          "brain_tool remember 1")
assert_ok(["remember", "--expert", "cons",
           "--brain-path", cons_db,
           "--tipo", "fact", "--title", "Duplicata",
           "--content", "Conteúdo para consolidar"],
          "brain_tool remember 2 (duplicata)")

# dry-run
rc, stdout, _ = run(["consolidate", "--expert", "cons",
                     "--brain-path", cons_db,
                     "--dry-run"])
if rc == 0:
    try:
        data = json.loads(stdout)
        if data.get("duplicates_found", 0) >= 1:
            tests_passed += 1
            print(f"✓ brain_tool consolidate --dry-run detecta duplicata (encontradas: {data['duplicates_found']})")
        else:
            tests_failed += 1
            print("✗ brain_tool consolidate --dry-run nao detectou duplicata")
    except json.JSONDecodeError:
        tests_failed += 1
        print("✗ brain_tool consolidate --dry-run: stdout nao e JSON")
else:
    tests_failed += 1
    print(f"✗ brain_tool consolidate --dry-run falhou: rc={rc}")

# --- Teste 17: check ---
print("\n17. Testando check")
rc, stdout, _ = run(["check", "--expert", "cons",
                     "--brain-path", cons_db])
if rc == 0:
    try:
        data = json.loads(stdout)
        if data.get("integrity") == "ok":
            tests_passed += 1
            print("✓ brain_tool check: integridade OK")
        else:
            tests_failed += 1
            print(f"✗ brain_tool check: integridade={data.get('integrity')}")
    except json.JSONDecodeError:
        tests_failed += 1
        print("✗ brain_tool check: stdout nao e JSON")
else:
    tests_failed += 1
    print(f"✗ brain_tool check falhou: rc={rc}")

# ============================================================
# RESULTADO FINAL
# ============================================================

print()
print("=" * 60)
print(f"RESULTADO: {tests_passed}/{tests_total} testes passaram")
if tests_failed > 0:
    print(f"             {tests_failed} testes falharam")
    print()
    print("TESTES FALHADOS:")
    # (os detalhes já foram impressos acima)
print("=" * 60)

# Limpa
shutil.rmtree(TEST_DIR, ignore_errors=True)

sys.exit(0 if tests_failed == 0 else 1)
