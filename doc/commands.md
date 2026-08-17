# Comandos — Referência de Uso

> Referência dos comandos do Brain Framework.
> Para entender o conceito, leia o [README](./README.md).

---

## Executáveis

Após `pip install .`, dois comandos ficam disponíveis (apontando para o mesmo CLI):

| Comando | O que é | Entry point |
|---|---|---|
| `brain` | Gestor nativo do framework (profiles, global, backup, admin) + conhecimento | `brain_tool.brain:main` |
| `brain-tool` | alias do `brain` (mantido por compatibilidade) | `brain_tool.brain:main` |

Ambos aceitam `--version` e `--help`. O `brain_tool.py` é hoje apenas a camada
de domínio (sem CLI próprio) — todo CLI vive em `brain.py`.

---

## Estrutura de diretórios (spec §3.1 / requirements §2.1)

```
~/.hermes/brain/
├── global/
│   └── brain.db          # conhecimento compartilhado entre todos os experts
├── <nome>/
│   └── brain.db          # conhecimento específico de um expert
├── admins.json           # lista de administradores
└── backups/              # backups de `brain backup`
```

A raiz é configurável via `BRAIN_ROOT` (default `~/.hermes/brain`).

---

## Backends de storage (SQLAlchemy)

| Modo | Config | Uso |
|---|---|---|
| SQLite local (default) | `BRAIN_ROOT` (default `~/.hermes/brain`) | 1 pessoa / 1 expert — um `brain.db` por expert/global |
| PostgreSQL (escala) | `DATABASE_URL` (ou `BRAIN_DATABASE_URL`) | organização — todos os experts dividem 1 banco, filtrados pela coluna `expert` |

Exemplo PostgreSQL:

```bash
export DATABASE_URL="postgresql+psycopg://brain:brain@localhost:5432/brain"
pip install -e ".[postgres]"   # driver psycopg
```

Com `DATABASE_URL` definido, `--brain-path`/`--expert` só filtram a coluna `expert`.

---

## Processamento assíncrono (Celery + Redis)

O `learn` enfileira a ingestão num worker Celery quando o broker está
configurado; sem broker, roda em modo síncrono (fallback — spec §4.6).

```bash
export REDIS_URL="redis://localhost:6379/0"     # ou CELERY_BROKER_URL

# producer: retorna "enqueued" + job_id
brain learn --expert maria --path /docs/ --sync

# worker (em outro terminal/container)
brain-worker           # ou: celery -A brain_tool.worker worker --loglevel=info

# acompanhar
brain jobs --expert maria
```

Docker (Redis + PostgreSQL + worker):

```bash
docker compose up -d
# monta ./ingest e passa /ingest/<arquivo> no learn
```

---

## Plugin Hermes (mensageria)

A tool nativa `brain` (ações `remember`, `recall`, `check`, `learn`,
`global_learn`, `jobs`, `synthesize`) é exposta ao agente via qualquer gateway
(Telegram/WhatsApp/…). Ações que absorvem conhecimento exigem `admin_id`
autorizado (`~/.hermes/brain/admins.json`) — spec §5.

```bash
hermes plugins install vitorluiz/brain-framework --enable
hermes plugins doctor . --ci   # validação local
```

---

## `brain` — Gestor do sistema

### Profiles

```bash
brain add profile <nome>       # hermes profile create + brain.db + alias no ~/.bashrc
brain list profiles            # lista experts (pastas na raiz do brain) com contagem
brain remove profile <nome>    # remove brain.db + diretório + alias (confirma em TTY)
brain remove profile <nome> --yes   # remove sem confirmação
```

### Global

```bash
brain global learn --content "Horário: 8h-18h" --title "Horário" [--sync] [--dry-run]
brain global learn --path /documentos/gerais/ [--sync] [--dry-run]
```

### Gestão

```bash
brain backup          # backup de global + experts → backups/backup_<ts>/ + manifest.json
brain update          # git pull origin main no diretório do framework
brain sync all        # sync (staging → pages) de global + todos os experts
```

### Administradores

```bash
brain admin list
brain admin add whatsapp <numero>
brain admin add cli <username>
brain admin add grupo <grupo> <membro>
brain admin remove <identificador>
```

A lista fica em `~/.hermes/brain/admins.json`. No contexto WhatsApp, o Hermes
Gateway valida os 3 critérios (lista + membro do grupo + admin do grupo) usando
as funções `is_admin()` / `is_group_member()` expostas por `brain_tool.brain`.

---

## Comandos de conhecimento (disponíveis em `brain` e `brain-tool`)

Todo comando aceita `--expert <nome>` (ou `--global`) e opcionalmente
`--brain-path <caminho>` para apontar um `brain.db` explícito.

```bash
# Inicializar um brain.db (cria schema + entrada inicial)
brain init --name maria
brain init --name maria --global

# CRUD
brain remember --expert maria --tipo fact --title "Título" --content "Conteúdo" [--dry-run]
brain recall   --expert maria [--search termo] [--limit 10] [--offset 0]
brain forget   --expert maria --id 5 [--dry-run]
brain synthesize   --expert maria [--type summary]
brain consolidate  --expert maria [--dry-run]

# Ingestão (staging → sync)
brain learn --expert maria --path /caminho/arquivo_ou_dir [--sync] [--dry-run]
brain sync-tb --expert maria   # move staging → pages (idempotente por hash)

# Diagnóstico
brain check --expert maria      # PRAGMA integrity_check + schema version + contagens
brain jobs  --expert maria [--status completed] [--limit 20]

# Taxonomia
brain taxonomist --expert maria [--limit 10]
brain capture --expert maria --type fact --content "Texto"
```

### Formatos suportados pelo `learn`

`.txt`, `.md`, `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.csv`.
Para PDF/DOCX/planilhas, instale as dependências opcionais:

```bash
pip install -e ".[learn]"   # pypdf, python-docx, pandas, openpyxl
```

Arquivos grandes são divididos em chunks (spec §4.2) antes de ir ao staging;
cada chunk recebe seu próprio hash canônico (SHA-256).

### Hash canônico e idempotência (spec §4.3)

O `sync` verifica o `hash_canonical` antes de mover um chunk do staging para
`pages`; se o hash já existe, pula. Aprender o mesmo arquivo duas vezes não
duplica conhecimento.

### Jobs (spec §4.5)

O `learn` registra um job em `jobs` com ciclo `enqueued → processing → completed`
(ou `failed`). Em v1 a ingestão é **síncrona** (fallback permitido pelo spec
§4.6 — Celery/Redis é opcional); o job registra o histórico real do processamento.

---

## Segurança (spec §5)

- **Knowledge absorption é admin-only**: no CLI, o acesso ao servidor já é o
  controle de acesso (spec §5.2).
- No WhatsApp, a validação de admin é feita pelo Hermes Gateway usando
  `admins.json` + os helpers `is_admin`/`is_group_member` (spec §5.3).
- O `brain.db` é criado com permissões privadas (`0700` diretórios, `0600` DB).
