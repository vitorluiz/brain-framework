# SPEC — Brain Framework

> Especificação do sistema Brain Framework — versão 0.1.0 (draft)
> Este documento descreve o que o sistema deve fazer, o modelo de dados, os
> comandos, e os critérios de aceitação.

---

## 1. Visão geral

**Brain Framework** é uma ferramenta CLI para gerenciar bases de conhecimento
SQLite (brain.db). É uma ferramenta **universal** — agnóstica a qualquer sistema
ou agente específico.

O framework tem dois componentes principais:

1. **brain_tool** — CLI para manipular brain.db (CRUD, schema, etc.)
2. **Celebro** — perfil mestre nativo do framework (gerencia instalação, profiles,
   sincronização, backup)

---

## 2. Modelo de dados

### 2.1 brain.db — SQLite

Todas as operações são feitas em um arquivo SQLite único por brain.

#### Tabela `pages`

```sql
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    corpo TEXT NOT NULL,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_archived INTEGER DEFAULT 0,
    hash TEXT
);
```

- `tipo`: taxonomy do schema_pack.yaml (concepts, entities, people, projects, incidents, memories, etc.)
- `titulo`: título da página
- `corpo`: conteúdo da página (markdown ou texto)
- `criado_em`: data de criação
- `atualizado_em`: data da última atualização
- `is_archived`: 0 (ativo) ou 1 (arquivado/forgotten)
- `hash`: hash SHA256 do conteúdo (para deduplicação)

#### Tabela `schema_version`

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);
```

- `version`: versão do schema (ex: "1.1.0")
- `applied_at`: quando foi aplicado
- `description`: descrição da versão

#### Tabela `meta`

```sql
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- Armazena metadados do brain (ex: brain_label, brain_version, etc.)

### 2.2 schema_pack.yaml — taxonomia

Define os tipos/taxonomia que o brain pode usar.

```yaml
schema:
  - type: concepts
    description: "Conceitos e ideias"
  - type: entities
    description: "Entidades nomeadas"
  - type: people
    description: "Pessoas"
  - type: projects
    description: "Projetos"
  - type: incidents
    description: "Incidentes"
  - type: memories
    description: "Memórias"
```

---

## 3. Comandos do brain_tool

### 3.1 `remember`

Salva uma página no brain.

```bash
brain-tool remember --tipo <tipo> --titulo <titulo> --corpo <body>
```

**Critérios de aceitação:**
- [ ] Cria um registro na tabela `pages`
- [ ] Calcula hash SHA256 do conteúdo
- [ ] Se o hash já existe, não cria duplicata (idempotente)
- [ ] Retorna o ID do registro criado

### 3.2 `entity`

Salva uma entidade nomeada.

```bash
brain-tool entity --nome <nome> --descricao <descricao>
```

**Critérios de aceitação:**
- [ ] Cria um registro na tabela `pages` com tipo `entities`
- [ ] O título é o nome da entidade
- [ ] Retorna o ID do registro criado

### 3.3 `recall`

Recupera páginas ativas por tipo/termo.

```bash
brain-tool recall --tipo <tipo> [--termo <termo>]
```

**Critérios de aceitação:**
- [ ] Retorna páginas com `is_archived = 0`
- [ ] Filtra por tipo (opcional)
- [ ] Busca por termo no título ou corpo (opcional)

### 3.4 `synthesize`

Consolida páginas de uma entidade em uma síntese.

```bash
brain-tool synthesize --entity <nome>
```

**Critérios de aceitação:**
- [ ] Busca todas as páginas do tipo `entities` com o nome especificado
- [ ] Gera uma síntese (via LLM ou regra)
- [ ] Retorna a síntese

### 3.5 `forget`

Arquiva uma página (soft delete).

```bash
brain-tool forget --id <id> [--dry-run]
```

**Critérios de aceitação:**
- [ ] `--dry-run`: mostra o que seria arquivado sem fazer a alteração
- [ ] Sem `--dry-run`: define `is_archived = 1` e `atualizado_em = NOW()`

### 3.6 `consolidate`

Deduplica páginas (Jaccard) e aplica tiering.

```bash
brain-tool consolidate [--dry-run]
```

**Critérios de aceitação:**
- [ ] `--dry-run`: mostra o que seria deduplicado sem fazer a alteração
- [ ] Calcula similaridade Jaccard entre páginas
- [ ] Se similaridade > threshold, marca uma como arquivada (keep a melhor)

### 3.7 `taxonomist`

Sugere tipo para conteúdo via schema + LLM.

```bash
brain-tool taxonomist --conteudo <conteudo>
```

**Critérios de aceitação:**
- [ ] Analisa o conteúdo
- [ ] Sugere um tipo do schema_pack.yaml
- [ ] Retorna o tipo sugerido

### 3.8 `capture`

Entrada única com hash dedup (idempotente).

```bash
brain-tool capture --conteudo <conteudo>
```

**Critérios de aceitação:**
- [ ] Calcula hash SHA256 do conteúdo
- [ ] Se o hash já existe, não cria duplicata
- [ ] Salva como tipo `memories` (ou outro padrão)

### 3.9 `learn`

Aprende com jsonl de mensagens (extrai fatos com LLM).

```bash
brain-tool learn --arquivo <arquivo.jsonl>
```

**Critérios de aceitação:**
- [ ] Lê o arquivo jsonl
- [ ] Para cada mensagem, extrai fatos via LLM
- [ ] Salva os fatos no brain (tipo `concepts` ou `memories`)
- [ ] Logs estruturados: what foi extraído, what foi ignorado

### 3.10 `check`

Verifica integridade do brain.

```bash
brain-tool check
```

**Critérios de aceitação:**
- [ ] Executa `PRAGMA integrity_check`
- [ ] Verifica `schema_version` (versão atual vs SCHEMA_VERSION da ferramenta)
- [ ] Reporta: OK, WARN (versão desatualizada), ERR (integridade falhou)

### 3.11 `init`

Inicializa um novo brain.

```bash
brain-tool init --scope <nome> --brain-dir <caminho>
```

**Critérios de aceitação:**
- [ ] Cria o diretório se não existir
- [ ] Cria brain.db com as tabelas
- [ ] Cria schema_pack.yaml (copia do template ou padrão)
- [ ] Registra schema_version = "1.1.0"
- [ ] Insere entrada inicial "em construção"

### 3.12 `migrate`

Aplica migrações do schema.

```bash
brain-tool migrate
```

**Critérios de aceitação:**
- [ ] Verifica schema_version atual vs SCHEMA_VERSION da ferramenta
- [ ] Se desatualizado, aplica delta (ALTER TABLE, etc.)
- [ ] Atualiza schema_version para a versão da ferramenta

---

## 4. Comandos do Celebro

### 4.1 `sudo celebro --version`

Reporta a versão do framework.

**Critérios de aceitação:**
- [ ] Retorna a versão (ex: "Brain Framework v0.1.0")

### 4.2 `sudo celebro update`

Atualiza o framework para a versão mais recente.

```bash
sudo celebro update
```

**Critérios de aceitação:**
- [ ] Verifica a versão atual
- [ ] Conecta ao repo (GitHub ou onde estiver hospedado)
- [ ] Pega a versão mais recente
- [ ] Substitui os arquivos locais
- [ ] Reporta o que mudou

### 4.3 `sudo celebro add profile <name>`

Adiciona um novo profile ao sistema.

```bash
sudo celebro add profile <name>
```

**Critérios de aceitação:**
- [ ] Cria o profile Hermes via `hermes profile create <name>`
- [ ] Configura alias no ~/.bashrc: `alias <name>="/home/hermes/.../python -m hermes_cli.main --profile <name>"`
- [ ] Cria brain.db via `brain-tool init --scope <name> --brain-dir ~/.brain/profiles/<name>/`
- [ ] Pergunta ao usuário: qual provider usar? (Nous, Ollama, etc.)
- [ ] Pergunta: qual LLM/default usar? (free LLMs por padrão)
- [ ] Configura o profile para usar o provider escolhido
- [ ] Reporta o que foi criado

### 4.4 `sudo celebro sync`

Sincroniza brains entre profiles.

```bash
sudo celebro sync
```

**Critérios de aceitação:**
- [ ] Para cada profile, lista o conhecimento relevante
- [ ] Pergunta ao usuário: o que sincronizar? (global, specific, tudo)
- [ ] Executa a sincronização via brain-tool
- [ ] Reporta o que foi sincronizado

### 4.5 `sudo celebro backup`

Backup de todos os brains.

```bash
sudo celebro backup
```

**Critérios de aceitação:**
- [ ] Backup do brain global
- [ ] Backup de cada profile
- [ ] Guarda em ~/.brain/backups/
- [ ] Rotação: mantém últimos N backups
- [ ] Reporta o que foi backupado

---

## 5. TDD — Testes

### 5.1 Estratégia de teste

- **Unit tests:** testam funções isoladas do brain_tool (hash, CRUD, etc.)
- **Integration tests:** testam o brain.db completo (criação, operações, migrações)
- **CLI tests:** testam a interface de linha de comando (argparse)

### 5.2 Testes do brain_tool

#### Teste: `remember` cria página

```python
def test_remember_creates_page(tmp_path):
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    result = subprocess.run([
        "python3", "-m", "brain_tool",
        "--brain", str(brain_dir),
        "remember",
        "--tipo", "concepts",
        "--titulo", "Meu teste",
        "--corpo", "Conteúdo de teste"
    ], capture_output=True, text=True)
    assert result.returncode == 0
    # Verificar que a página foi criada no SQLite
    ...
```

#### Teste: `remember` é idempotente (hash dedup)

```python
def test_remember_idempotent(tmp_path):
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    # Criar página
    subprocess.run([...], ...)
    # Tentar criar a mesma página novamente (mesmo hash)
    result = subprocess.run([...], ...)
    assert result.returncode == 0
    # Verificar que não foi criada duplicata
    ...
```

#### Teste: `recall` retorna páginas ativas

```python
def test_recall_returns_active_pages(tmp_path):
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    # Criar páginas (uma ativa, uma arquivada)
    ...
    result = subprocess.run([
        "python3", "-m", "brain_tool",
        "--brain", str(brain_dir),
        "recall",
        "--tipo", "concepts"
    ], capture_output=True, text=True)
    assert "página ativa" in result.stdout
    assert "página arquivada" not in result.stdout
```

#### Teste: `forget --dry-run` não altera dados

```python
def test_forget_dry_run_does_not_change_data(tmp_path):
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    # Criar página com ID 1
    ...
    # Executar forget --dry-run
    result = subprocess.run([
        "python3", "-m", "brain_tool",
        "--brain", str(brain_dir),
        "forget",
        "--id", "1",
        "--dry-run"
    ], capture_output=True, text=True)
    # Verificar que is_archived ainda é 0
    ...
```

#### Teste: `check` reporta integridade

```python
def test_check_reports_integrity(tmp_path):
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    # Criar brain válido
    ...
    result = subprocess.run([
        "python3", "-m", "brain_tool",
        "--brain", str(brain_dir),
        "check"
    ], capture_output=True, text=True)
    assert result.returncode == 0
    assert "OK" in result.stdout
```

### 5.3 Testes do Celebro

#### Teste: `celebro --version` retorna versão

```python
def test_celebro_version():
    result = subprocess.run([
        "celebro", "--version"
    ], capture_output=True, text=True)
    assert result.returncode == 0
    assert "Brain Framework" in result.stdout
```

#### Teste: `celebro add profile` cria profile Hermes

```python
def test_celebro_add_profile_creates_hermes_profile(tmp_path, monkeypatch):
    # Mock do hermes profile create
    def mock_profile_create(name):
        # Cria um arquivo de configuração simulando o profile
        ...
    monkeypatch.setattr("...", mock_profile_create)
    
    result = subprocess.run([
        "celebro", "add", "profile", "test-profile"
    ], capture_output=True, text=True)
    assert result.returncode == 0
    # Verificar que o profile foi criado
    ...
```

---

## 6. Estrutura de diretórios

```
brain-framework/
├── src/
│   └── brain_tool/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       └── brain_tool.py       # lógica principal
├── celebro/                    # (futuro) Celebro — perfil mestre nativo
│   ├── cli.py
│   ├── core.py
│   └── config.py
├── doc/
│   ├── README.md
│   ├── quickstart.md
│   └── commands.md
├── plan/
│   ├── README.md
│   ├── roadmap.md
│   └── celebro.md
├── tests/
│   ├── test_brain_tool.py
│   └── test_celebro.py
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## 7. Critérios de aceitação gerais

### 7.1 brain_tool

- [ ] `remember` cria página com hash
- [ ] `remember` é idempotente (hash dedup)
- [ ] `recall` retorna páginas ativas
- [ ] `recall` filtra por tipo e termo
- [ ] `forget --dry-run` não altera dados
- [ ] `forget` arquiva página (is_archived = 1)
- [ ] `consolidate --dry-run` mostra o que seria deduplicado
- [ ] `consolidate` deduplica e aplica tiering
- [ ] `check` reporta integridade (PRAGMA + schema_version)
- [ ] `init` cria brain.db com tabelas e schema_version
- [ ] `migrate` aplica deltas de schema
- [ ] `learn` extrai fatos e salva no brain

### 7.2 Celebro

- [ ] `celebro --version` retorna versão
- [ ] `celebro update` atualiza framework
- [ ] `celebro add profile <name>` cria profile Hermes + alias + brain.db
- [ ] `celebro sync` sincroniza brains
- [ ] `celebro backup` backupa todos os brains

### 7.3 Universal

- [ ] Não assume nada sobre o sistema do usuário
- [ ] Não hardcode nomes de profiles ou sistemas
- [ ] Suporta vários providers (Nous, Ollama, etc.)
- [ ] Usa LLMs free por padrão (configurável)

---

## 8. Plano de implementação (TDD)

### Sprint 1 — brain_tool core (TDD)

1. Escrever teste para `remember` (cria página)
2. Implementar `remember` (PASS)
3. Escrever teste para `remember` idempotente (hash dedup)
4. Implementar hash dedup (PASS)
5. Escrever teste para `recall` (retorna páginas ativas)
6. Implementar `recall` (PASS)
7. ... (sequência similar para todos os comandos)

### Sprint 2 — Celebro

1. Escrever teste para `celebro --version`
2. Implementar `celebro --version` (PASS)
3. Escrever teste para `celebro add profile`
4. Implementar `celebro add profile` (PASS)
5. ... (sequência similar para todos os comandos)

### Sprint 3 — Documentação

1. Escrever doc/quickstart.md (primeiro uso)
2. Escrever doc/commands.md (referência de comandos)
3. Escrever README.md (visão geral)

---

## 9. Open questions

- [ ] Como o Celebro vai descobrir onde está o `hermes` binary?
  - Opção 1: hardcode `/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main`
  - Opção 2: usar `which hermes` ou `command -v hermes`
  - Opção 3: perguntar ao usuário no `add profile`
- [ ] Onde o Celebro guarda a configuração?
  - Opção 1: `~/.brain/celebro.yaml`
  - Opção 2: `~/.config/brain-framework/celebro.yaml`
  - Opção 3: variáveis de ambiente
- [ ] Como o Celebro vai fazer o `git pull` para atualizar?
  - Opção 1: usar `git pull origin main` no diretório de instalação
  - Opção 2: baixar um release tarball e extrair
  - Opção 3: usar `pip install --upgrade`

---

## 10. Referências

- [brain_tool.py](../src/brain_tool/brain_tool.py) — implementação atual
- [plan/celebro.md](../plan/celebro.md) — plano de desenvolvimento do Celebro
- [doc/quickstart.md](../doc/quickstart.md) — primeiro uso
- [doc/commands.md](../doc/commands.md) — referência de comandos
