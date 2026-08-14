# Comandos — Referência de Uso

> Esta é a referência de todos os comandos do Brain Framework.
> Para entender o conceito, leia [Visão geral](./README.md).

---

## brain_tool CLI

O `brain_tool` é a CLI principal para manipular brain.db.

```bash
# Especificar qual brain usar
python3 -m brain_tool --brain /caminho/para/brain/ <command> [options]
python3 -m brain_tool --global <command> [options]

# Sem especificar: usa BRAIN_PROFILE ou experts/jimmy/
python3 -m brain_tool <command> [options]
```

### Comandos de CRUD

#### `remember`

Salva um registro (tipo, título, corpo).

```bash
python3 -m brain_tool --brain /caminho/para/brain/ remember \
  --tipo concepts \
  --titulo "Meu conceito" \
  --corpo "Descrição do meu conceito"
```

#### `entity`

Salva uma entidade nomeada.

```bash
python3 -m brain_tool --brain /caminho/para/brain/ entity \
  --nome "João" \
  --descricao "Responsável pelo sistema"
```

#### `recall`

Recupera registros ativos por tipo/termo.

```bash
# Listar todos os conceitos
python3 -m brain_tool --brain /caminho/para/brain/ recall --tipo concepts

# Buscar por termo
python3 -m brain_tool --brain /caminho/para/brain/ recall --tipo concepts --termo "conceito"
```

#### `synthesize`

Consolida páginas de uma entidade em uma síntese.

```bash
python3 -m brain_tool --brain /caminho/para/brain/ synthesize --entity "João"
```

#### `forget`

Arquiva uma página (soft delete). Use `--dry-run` para pré-ver.

```bash
# Arquivar sem dor
python3 -m brain_tool --brain /caminho/para/brain/ forget --id 5 --dry-run

# Arquivar de verdade
python3 -m brain_tool --brain /caminho/para/brain/ forget --id 5
```

#### `consolidate`

Deduplica (Jaccard) + tiering. Use `--dry-run` para pré-ver.

```bash
# Ver o que seria deduplicado
python3 -m brain_tool --brain /caminho/para/brain/ consolidate --dry-run

# Deduplicar de verdade
python3 -m brain_tool --brain /caminho/para/brain/ consolidate
```

### Comandos de Classificação

#### `taxonomist`

Sugere tipo para conteúdo via schema + LLM.

```bash
python3 -m brain_tool --brain /caminho/para/brain/ taxonomist --conteudo "Texto para classificar"
```

#### `capture`

Entrada única com hash dedup (idempotente).

```bash
python3 -m brain_tool --brain /caminho/para/brain/ capture --conteudo "Texto para capturar"
```

### Comandos de Aprendizado

#### `learn`

Aprende com jsonl de mensagens (extrai fatos com LLM).

```bash
python3 -m brain_tool --brain /caminho/para/brain/ learn --arquivo /caminho/para/mensagens.jsonl
```

### Comandos de Diagnóstico

#### `check`

Verifica integridade (PRAGMA + schema_version).

```bash
# Verificar um brain
python3 -m brain_tool --brain /caminho/para/brain/ check

# Verificar o brain global
python3 -m brain_tool --global check
```

#### `init`

Inicializa um novo brain.

```bash
# Inicializar um brain para um scope
python3 -m brain_tool init --scope meu-sistema --brain-dir /caminho/para/brain/
```

#### `migrate`

Aplica migrações pendentes do schema_version.

```bash
# Verificar se há migrações pendentes
python3 -m brain_tool --brain /caminho/para/brain/ check

# Aplicar migrações
python3 -m brain_tool --brain /caminho/para/brain/ migrate
```

---

## Celebro CLI

O `celebro` é o perfil mestre nativo do framework.

```bash
# Verificar a versão
sudo celebro --version

# Atualizar o framework
sudo celebro update

# Adicionar um profile
sudo celebro add profile nome-do-profile

# Sincronizar brains
sudo celebro sync

# Backup de todos os brains
sudo celebro backup
```

### `sudo celebro update`

Atualiza o framework para a versão mais recente.

```bash
sudo celebro update
```

O que faz:
1. Verifica a versão atual (`celebro version`)
2. Conecta ao repo (GitHub ou onde estiver hospedado)
3. Pega a versão mais recente
4. Substitui os arquivos locais
5. Reporta o que mudou

### `sudo celebro add profile`

Adiciona um novo profile ao sistema.

```bash
sudo celebro add profile nome-do-profile
```

O que faz:
1. Pergunta ao usuário: qual provider usar? (Nous, Ollama, etc.)
2. Pergunta: qual LLM/default usar? (free LLMs por padrão)
3. Cria ~/.brain/profiles/nome-do-profile/
4. Cria brain.db com schema
5. Cria schema_pack.yaml
6. Configura o profile para usar o provider escolhido
7. Reporta o que foi criado

### `sudo celebro sync`

Sincroniza brains entre profiles.

```bash
sudo celebro sync
```

O que faz:
1. Para cada profile, lista o conhecimento relevante
2. Pergunta ao usuário: o que sincronizar? (global, specific, tudo)
3. Executa a sincronização via brain_tool
4. Reporta o que foi sincronizado

### `sudo celebro backup`

Backup de todos os brains.

```bash
sudo celebro backup
```

O que faz:
1. Backup do brain global
2. Backup de cada profile
3. Guarda em ~/.brain/backups/
4. Rotação: mantém últimos N backups
5. Reporta o que foi backupado

---

## Diferença entre brain_tool e Celebro

| | brain_tool | Celebro |
|---|---|---|
| **O que é** | CLI para manipular brain.db | Perfil mestre nativo do framework |
| **Para quem** | Qualquer um que queira usar um brain.db | Usuários do sistema |
| **O que faz** | CRUD de conhecimento, schema, etc. | Gerencia instalação, profiles, sync, backup |
| **Como usar** | `python3 -m brain_tool <command>` | `sudo celebro <command>` |
| **Universal?** | Sim — agnóstico a qualquer sistema | Sim — não assume nada sobre o sistema |

Celebro usa brain_tool nos bastidores — não reimplementa a manipulação de brain.db.
