# Brain Framework — Especificação de Requisitos

## 1. Visão Geral

### 1.1 Propósito
O Brain Framework é um sistema de gestão de conhecimento para agentes de IA, com foco em suporte à operação de múltiplos "expert agents" (agente especialistas) que atuam em canais como WhatsApp, atendendo diferentes funções dentro de uma organização.

### 1.2 Escopo
- **Não é**: um agente de IA, um chatbot genérico, ou uma plataforma de LLM
- **É**: uma camada de persistência e orquestração de conhecimento estruturado para agentes de IA

### 1.3 Princípios Fundamentais
1. **Conhecimento é admin-only**: apenas administradores podem adicionar/remover conhecimento
2. **Brain.db é a fonte da verdade**: todo conhecimento persistido em SQLite
3. **Hash canônico garante consistência**: sinônimo de conhecimento idêntico
4. **Agent independente**: cada expert tem seu próprio brain.db
5. **Global brain para conhecimento compartilhado**: horários, políticas, etc.
6. **Async processing para não bloquear**: learn via queue é opcional, com fallback síncrono

---

## 2. Modelo de Dados

### 2.1 Estrutura de Diretórios
```
~/.hermes/brain/
├── global/
│   └── brain.db          # Conhecimento compartilhado
├── experts/
│   ├── maria/
│   │   └── brain.db      # Conhecimento específico de Maria
│   └── jose/
│       └── brain.db      # Conhecimento específico de José
└── admins.json           # Lista de administradores
```

### 2.2 Schema do brain.db (v1.0.0)

#### Tabela: `schema_version`
| Coluna | Tipo | Descrição |
|---|---|---|
| version | TEXT PK | Versão do schema aplicada |
| applied_at | TIMESTAMP | Quando foi aplicada |
| description | TEXT | Descrição da migração |

#### Tabela: `pages` (knowledge principal)
| Coluna | Tipo | Descrição |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | Identificador único |
| expert | TEXT NOT NULL | Nome do expert (ou "global") |
| tipo | TEXT NOT NULL | Categoria: memory, fact, entity, procedure, policy, system |
| titulo | TEXT | Título descritivo (opcional) |
| corpo | TEXT NOT NULL | Conteúdo do conhecimento |
| hash_canonical | TEXT | SHA-256 do corpo (para deduplicação) |
| created_at | TIMESTAMP | Quando foi criado |
| updated_at | TIMESTAMP | Quando foi atualizado |

#### Tabela: `knowledge_staging` (pendente de sync)
| Coluna | Tipo | Descrição |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | Identificador do staging |
| expert | TEXT NOT NULL | Expert para qual o conhecimento destina |
| chunk_data | TEXT NOT NULL | Conteúdo processado |
| hash_canonical | TEXT NOT NULL | Hash canônico do conteúdo |
| status | TEXT DEFAULT 'pending' | Status: pending, processed |
| created_at | TIMESTAMP | Quando entrou no staging |
| processed_at | TIMESTAMP | Quando foi processado |

#### Tabela: `jobs` (para async processing)
| Coluna | Tipo | Descrição |
|---|---|---|
| id | TEXT PK | ID único do job (hash truncado) |
| expert | TEXT NOT NULL | Expert associado |
| command | TEXT NOT NULL | Comando executado (ex: "learn") |
| status | TEXT DEFAULT 'enqueued' | Status: enqueued, processing, completed, failed |
| metadata | TEXT | Metadados JSON do job |
| created_at | TIMESTAMP | Quando foi enfileirado |
| started_at | TIMESTAMP | Quando começou a execução |
| completed_at | TIMESTAMP | Quando concluiu |
| error | TEXT | Mensagem de erro (se failed) |

---

## 3. Comandos do Brain Tool (CLI Core)

### 3.1 `init` — Inicializar brain.db
```
brain_tool.py init --name <expert> [--global]
```
- Cria o diretório do expert se não existir
- Cria brain.db com schema v1.0.0
- Adiciona entrada inicial "Brain inicializado" com tipo "system"
- **Admin**: não necessário (init é operação de setup)

### 3.2 `remember` — Adicionar conhecimento
```
brain_tool.py remember --expert <expert> --tipo <tipo> --title <titulo> --content <conteudo> [--global] [--dry-run]
```
- Tipos suportados: `memory`, `fact`, `entity`, `procedure`, `policy`, `system`
- Gera hash canônico automaticamente (SHA-256 do corpo)
- `--dry-run`: mostra o que seria criado sem executar
- **Admin**: requerido para todos os modifies de conhecimento

### 3.3 `recall` — Recuperar conhecimento
```
brain_tool.py recall --expert <expert> [--search <termo>] [--limit <n>] [--offset <n>] [--global]
```
- Busca por similaridade de texto (LIKE no SQLite)
- Retorna JSON com resultados
- `--limit` default: 10, `--offset` default: 0
- **Admin**: não necessário (read-only)

### 3.4 `forget` — Remover conhecimento
```
brain_tool.py forget --expert <expert> --id <id> [--global] [--dry-run]
```
- Remove página pelo ID
- `--dry-run`: mostra o que seria removido sem executar
- **Admin**: requerido (com `--dry-run` para preview seguro)

### 3.5 `synthesize` — Sintetizar conhecimento
```
brain_tool.py synthesize --expert <expert> [--type <tipo>] [--global]
```
- Gera resumo agregado do conhecimento do expert
- Agrupa por tipo e count
- **Admin**: não necessário (read-only)

### 3.6 `consolidate` — Deduplicar conhecimento
```
brain_tool.py consolidate --expert <expert> [--threshold <0.8>] [--global] [--dry-run]
```
- Agrupa por hash_canonical
- Remove duplicatas (mantém o mais antigo)
- `--dry-run`: mostra o que seria removido sem executar
- **Admin**: requerido (com `--dry-run` para preview)

### 3.7 `learn` — Processar arquivo/diretório para staging
```
brain_tool.py learn --expert <expert> --path <caminho> [--sync] [--global] [--dry-run]
```
- Processa arquivo ou diretório recursivamente
- Formatos suportados: `.txt`, `.md`, `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.csv`
- Converte para texto plano
- Gera hash canônico do conteúdo
- Adiciona ao `knowledge_staging` (status: pending)
- `--sync`: executa sync imediatamente após learn
- `--dry-run`: mostra o que seria processado sem executar
- **Admin**: requerido

### 3.8 `sync` — Sync staging → pages
```
brain_tool.py sync --expert <expert> [--global]
```
- Move connaissances do staging para a tabela pages
- Verifica hash canônico para evitar duplicatas
- Se hash já existe em pages, skip (idempotente)
- **Admin**: requerido

### 3.9 `check` — Verificar integridade
```
brain_tool.py check --expert <expert> [--global]
```
- PRAGMA integrity_check do SQLite
- Verifica versão do schema
- Conta registros por tabela
- Relata issues encontrados
- **Admin**: não necessário (read-only)

### 3.10 `jobs` — Listar jobs
```
brain_tool.py jobs --expert <expert> [--status <status>] [--limit <n>] [--global]
```
- Lista jobs do expert (enqueued, processing, completed, failed)
- `--status` para filtrar
- **Admin**: não necessário (read-only)

---

## 4. Comandos do Celebro (CLI do Gestor)

### 4.1 `add profile` — Criar novo expert
```
celebro add profile <name>
```
O que faz:
1. Tenta executar `hermes profile create <name>` (se hermes CLI disponível)
2. Cria brain.db vazio com `brain_tool init --name <name>`
3. Adiciona alias em `~/.bashrc`: `alias <name>='python -m hermes_cli.main --profile <name>'`
4. Confirma criação

### 4.2 `list profiles` — Listar experts
```
celebro list profiles
```
- Lista todos os diretórios em `experts/`
- Mostra status (existe/não existe) e contagem de conhecimentos

### 4.3 `remove profile` — Remover expert
```
celebro remove profile <name>
```
- Remove brain.db
- Remove diretório do expert
- Remove alias do `~/.bashrc`

### 4.4 `global learn` — Popular knowledge global
```
celebro global learn --path <caminho> [--sync] [--dry-run]
celebro global learn --content <texto> --title <titulo> [--dry-run]
```
- Funciona como `brain_tool learn` mas para o global brain
- **Admin**: requerido (já é CLI, então quem tem acesso ao servidor é admin)

### 4.5 `backup` — Backup de todos os brains
```
celebro backup
```
- Cria diretório de backup com timestamp
- Copia brain.db de global + todos os experts
- Cria manifest.json com metadados

### 4.6 `update` — Atualizar framework
```
celebro update
```
- Executa `git pull origin main` no diretório do framework
- Only atualiza código, não brains

### 4.7 `sync all` — Sync todos os brains
```
celebro sync all
```
- Executa `brain_tool sync` para global + todos os experts
- Mostra status por expert

### 4.8 `admin` — Gestão de administradores
```
celebro admin list
celebro admin add whatsapp <numero>
celebro admin add cli <username>
celebro admin add grupo <grupo> <membro>
celebro admin remove <identificador>
```
- Admins armazenados em `~/.hermes/brain/admins.json`
- Tipos: `whatsapp`, `cli`, `grupo`
- Para WhatsApp: o admin deve também ser admin do grupo (verificar via WhatsApp API)

---

## 5. Integração com WhatsApp (via Hermes Gateway)

### 5.1 Arquitetura
```
┌─────────────────────────────────────────────────────────────┐
│                 HERMES AGENT GATEWAY                       │
│  - Conecta ao WhatsApp (API oficial ou não oficial)       │
│  - Gerencia sessões, mensagens, admin list               │
│  - Recebe comandos /admin learn, valida admin,            │
│    chama brain_tool ou celebro para processar             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 BRAIN FRAMEWORK (CLI/celebro)             │
│  - brain_tool.py: manipula brain.db                      │
│  - celebro: gestor do sistema                            │
│  - Não sabe de WhatsApp — só processa comandos            │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Fluxo de Comandos WhatsApp

#### Canal de Atendimento (leads)
```
Lead: "Qual o horário de funcionamento?"
Maria: "A empresa funciona de segunda a sexta, das 8h às 18h."
```
- Lead não tem acesso a comandos administrativos
- Maria consulta seu brain.db e o global brain para responder

#### Canal Administrativo (admins)
```
/admin learn maria /caminho/dos/arquivos/ [--sync]
/admin global-learn "Horário: 8h-18h" [--sync]
/admin jobs
/admin status
```
- Só admins podem executar
- Validação: (1) na lista de admins, (2) membro do grupo, (3) admin do grupo

---

## 6. Segurança

### 6.1 Princípios
1. **Knowledge absorption é admin-only** em todos os canais
2. **CLI é seguro por natureza** (apenas quem tem acesso ao servidor)
3. **Dashboard (futuro) requer autenticação**
4. **WhatsApp admin apenas em grupo administrativo**

### 6.2 Validação de Admin no WhatsApp
- Lista de admins configurada via `celebro admin add`
- Admin deve ser: (1) na lista, (2) membro do grupo, (3) admin do grupo
- Se falhar qualquer critério → comando ignorado com mensagem "Comando restrito a administradores"

### 6.3 Conteúdo Não Confiavel
- Nenhum conhecimento entra no brain.db sem passar por admin
- Leads/clientes não podem injetar conhecimento

---

## 7. Runtime & Deploy

### 7.1 Python venv
- Dependências: `pypdf`, `python-docx`, `pandas`, `openpyxl` (opcionais, para learn)
- SQLite já vem com Python
- Instalação via `pip install -e .`

### 7.2 Docker
- Dockerfile simples com Python 3.11+
- Volumes para persistência: `brain.db`, configurações
- Redis opcional para Celery (async jobs)

### 7.3 Variáveis de Ambiente
- `BRAIN_ROOT`: caminho raiz do brain (default: `~/.hermes/brain`)

---

## 8. Non-Functional Requirements

### 8.1 Performance
- Consultas SQLite: sub-segundo para lookups simples
- Learn via async: não bloqueia operação (opcional)
- Hash canônico: computação rápida (SHA-256)

### 8.2 Observabilidade
- `check` para integridade do brain.db
- `jobs` para status de processamento assíncrono
- `synthesize` para visão geral do conhecimento

### 8.3 Backup & Restore
- `celebro backup` cria backup completo
- Restore: futuro (copiar brain.db de volta manualmente pelo momento)

---

## 9. Roadmap (Futuro)

### 9.1 Próximas Iterações
1. **Dashboard web** (FastAPI + simples UI) para admins gerenciarem remotely
2. **Celery + Redis** para async jobs (opcional, com fallback síncrono)
3. **Embedings + busca semântica** (opcional, para consulta mais inteligente)
4. **MCP integration** para agents como José acessarem sistemas externos

### 9.2 Non-Goals (v1)
- Dashboard: cockpit para admins (pode ser futuro)
- WhatsApp API: managed pelo Hermes gateway, não pelo brain framework
- Múltiplos LLMs: cada agent pode usar seu provider (celebro é agnóstico)

---

## 10. Exemplos de Uso

### 10.1 Setup Inicial
```bash
# Criar experts
celebro add profile maria
celebro add profile jose

# Popular global (horários, políticas)
celebro global learn --content "Horário de funcionamento: 8h-18h" --title "Horário"
celebro global learn --path /documentos/gerais/ --sync

# Popular conhecimento específico
brain_tool.py learn --expert maria --path /documentos/atendimento/ --sync
brain_tool.py remember --expert jose --tipo policy --title "Política de férias" --content "..."
```

### 10.2 Operação Diária
```bash
# Verificar integridade
brain_tool.py check --expert maria

# Adicionar conhecimento pelo CLI
brain_tool.py remember --expert maria --tipo fact --title "Novo produto" --content "..."

# Sync após learn
brain_tool.py sync --expert maria

# Listar jobs
brain_tool.py jobs --expert maria --status completed
```

### 10.3 Segurança em Ação
```bash
# Admin CLI (seguro por natureza)
brain_tool.py learn --expert maria --path /docs/ --sync

# Admin WhatsApp (grupo administrativo)
/admin learn maria /docs/ --sync

# Lead no canal de atendimento (comando ignorado)
Lead: /learn /docs/
Maria: "Não entendi. Posso ajudar com algo sobre nossos produtos?"
```
