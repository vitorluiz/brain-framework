# Brain Framework — CLI evolutive para gerenciar brains do ecossistema Granjimmy

> **Fonte da verdade:** Este repositório (vitorluiz/brain-framework).

O **Brain Framework** é uma ferramenta CLI independente — agnóstica a qualquer
profile ou agente — que gerencia bases de conhecimento SQLite (brain.db) para
sistemas multi-agente.

## Instalação

```bash
# Instalar direto do GitHub
pip install git+ssh://git@github.com/vitorluiz/brain-framework.git

# Ou clonar e instalar localmente
git clone git@github.com:vitorluiz/brain-framework.git
cd brain-framework
pip install -e .
```

## Uso

```bash
# CLI principal
python3 -m brain_tool <command> [options]

# Comandos disponíveis:
brain_tool remember      — salva um registro
brain_tool entity        — salva uma entidade nomeada
brain_tool recall        — recupera registros ativos
brain_tool synthesize    — consolida páginas em síntese
brain_tool forget        — arquiva uma página (soft delete); use --dry-run para pré-ver
brain_tool consolidate   — deduplica (Jaccard) + tiering T1-T4; use --dry-run para pré-ver
brain_tool taxonomist    — sugere tipo para conteúdo via schema + LLM
brain_tool capture       — entrada única com hash dedup (idempotente)
brain_tool learn         — aprende com jsonl de mensagens (extrai fatos com LLM)
brain_tool check         — verifica integridade do brain (PRAGMA + schema_version)
brain_tool init          — inicializa um novo brain (para expert ou global)
brain_tool migrate       — aplica migrações pendentes do schema_version

# Diretórios do brain:
#   --brain <path>     : usa o brain em <path> (diretório com brain.db + schema_pack.yaml)
#   --global           : usa o brain global (~/.hermes/brain/global/)
#   (sem --brain/--global) : usa experts/<BRAIN_PROFILE>/ ou experts/jimmy/
```

## Arquitetura

```
~/.hermes/brain/                        # Brain raiz (backup aqui)
├── tool/
│   └── brain_tool.py                   # CLI canônica (v1.1.0)
├── global/
│   ├── brain.db                        # Knowledge base compartilhada
│   ├── schema_pack.yaml                # Schema de taxonomia
│   └── schema_version (tabela)         # Versão do schema registrada
├── experts/
│   ├── jimmy/
│   │   ├── brain.db                    # Knowledge base do jimmy
│   │   └── schema_pack.yaml
│   ├── gtic/
│   │   ├── brain.db
│   │   └── schema_pack.yaml
│   ├── sextafeira/
│   │   ├── brain.db
│   │   └── schema_pack.yaml
│   ├── marketing/
│   │   ├── brain.db
│   │   └── schema_pack.yaml
│   └── default/
│       ├── brain.db
│       └── schema_pack.yaml
└── backup/                             # Backups periódicos (últimos 7 por label)
    ├── global/
    │   └── brain_YYYYMMDD_HHMMSS.db
    └── expert_<profile>/
        └── brain_YYYYMMDD_HHMMSS.db
```

## Schema compartilhado

Todos os brains usam o mesmo `schema_pack.yaml` (tipos: people, concepts,
projects, groups, memory, inbox), inspirado no Gbrain do garrytan.

- **people** — Membros da equipe, admins, contatos (NÃO pacientes reais)
- **concepts** — Conceitos/protocolos/regras de operação
- **projects** — Tarefas, melhorias, operação diária do agente
- **groups** — Perfil dos grupos monitorados e regras
- **memory** — Sínteses e memórias relacionais
- **inbox** — Triagem temporária antes de virar página tipada

O `schema_pack.yaml` é decidido por dados (o arquivo), não hardcode no código.
O `taxonomist` e `capture` usam o schema para classificar conteúdo.

## Versão do schema

Cada brain.db registra sua versão no `schema_version`. A ferramenta compara a
versão registrada com `SCHEMA_VERSION` (atualmente `1.1.0`) no `check`. O
comando `migrate` aplica deltas entre versões. Isso garante que brains antigos
possam ser atualizados sem perder dados ou quebrar.

## LLM para aprendizado e taxonomia

A ferramenta usa gemma4 (Ollama Cloud) para:
- `learn` — extrai fatos dos jsonl de mensagens (anonimiza pacientes)
- `taxonomist` — classifica conteúdo em tipos via schema + LLM
- `consolidate` — deduplicação Jaccard + tiering T1-T4 (sem LLM, determinístico)

## Chave da API

O `brain_tool.py` busca a chave Ollama Cloud de:
1. `OLLAMA_API_KEY` ou `OLLAMA_KEY` (variável de ambiente)
2. `.env` na pasta da ferramenta (`~/.hermes/brain/tool/.env`)
3. `~/.hermes/profiles/<BRAIN_PROFILE>/.env`
4. `~/.hermes/profiles/jimmy/.env` (fallback)

Cada profile deve ter sua `.env` com a chave se for usar `learn` ou
`taxonomist`. Operações determinísticas (recall, remember, consolidare,
forget, check, init, migrate) não precisam de chave.

## Backup

```bash
# Backup de TODOS os brains (global + todos os experts)
bash brain-framework/backup.sh

# Backup apenas do brain global
bash brain-framework/backup.sh --global

# Backup apenas do brain de um expert
bash brain-framework/backup.sh --expert jimmy
bash brain-framework/backup.sh --expert gtic

# Restauração
bash brain-framework/backup.sh restore --global 20260812_030000
bash brain-framework/backup.sh restore --expert jimmy 20260812_030000
```

## Adicionar um novo agente

```bash
# 1. Inicializar o brain do novo agente
python3 -m brain_tool init --scope novo-agente
# → cria ~/.hermes/brain/experts/novo-agente/ com brain.db + schema_pack.yaml

# 2. Se o agente usa LLM no brain, configurar .env
echo "OLLAMA_API_KEY=***" >> ~/.hermes/profiles/novo-agente/.env

# 3. Usar o brain do novo agente
BRAIN_PROFILE=novo-agente python3 -m brain_tool recall
# ou explicitamente:
python3 -m brain_tool --brain ~/.hermes/brain/experts/novo-agente/ recall
```

## Concorrência no brain global

SQLite usa bloqueio de escrita — se dois agents (ou duas operações) tentarem
`remember` (ou qualquer escrita) no mesmo brain.global ao mesmo time, o SQLite
serializa as escritas (bloqueio). Para uso leve — poucos `remember` por hora —
isso é aceitável e não há perda de dados.

Para uso intensivo do brain global (muitas escritas concorrentes de múltiplos
agents), considere:

1. **Filas/lock explícito:** usar um arquivo de lock (`flock`) ou uma fila
   (Redis, fila do Celery) para serializar as escritas no global
2. **Migração para Postgres:** se o global brain crescer além de ~1GB ou
   necessitar acesso concorrente intenso, migrar para Postgres+pgvector

A decisão de usar ou não o brain global para escritas concorrentes é uma
decisão de design — não técnica. Documente quando o brain global começar a
ser usado intensivamente.

## Versionamento e migração

A ferramenta tem `VERSION` (versão da CLI) e `SCHEMA_VERSION` (versão do
schema do brain.db). Quando a ferramenta evolui (novos comandos, campos, etc.),
a SCHEMA_VERSION é incrementada e uma migração é adicionada em `migrate`.

```bash
# Verificar estado atual
python3 -m brain_tool check

# Se houver migrações pendentes, aplicar
python3 -m brain_tool migrate
```

### SQLite → Postgres (futuro)

Se um brain crescer além de ~1GB ou necessitar acesso concorrente intenso
(provavelmente o brain do jimmy primeiro, que aprende com muitos jsonl),
considerar migrar para Postgres+pgvector. O `brain_tool.py` seria adaptado para
usar SQLAlchemy ou psycopg2, e o `schema_pack.yaml` seria mantido.

## Testes e verificação

```bash
# Checar integridade de um brain
python3 -m brain_tool check
python3 -m brain_tool --global check
python3 -m brain_tool --brain ~/.hermes/brain/experts/jimmy/ check

# Simular operações destrutivas antes de executar
python3 -m brain_tool --global forget --id 5 --dry-run
python3 -m brain_tool --global consolidate --dry-run
```

## Links relacionados

- **CONTRATO_AGENTES.md** — Contrato de comportamento do ecossistema
  (`~/Projetos/gtic/CONTRATO_AGENTES.md`)
- **ARQUITETURA.md** — Mapa mestre do ecossistema (`~/Projetos/gtic/ARQUITETURA.md`)
- **brain_tool.py** — Ferramenta de instrução do brain
  (`~/Projetos/gtic/jimmy/brain.py`)
