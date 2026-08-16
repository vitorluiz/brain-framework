---

📄 plan/spec.md

# SPEC — Brain Framework

> Especificação do sistema Brain Framework — versão 0.1.0 (draft)
> Este documento descreve o que o sistema deve fazer, o modelo de dados, os
> comandos, e os critérios de aceitação.
> Este documento é iterativo — vai sendo atualizado à medida que o sistema
> é desenvolvido. Não é um contrato fixo; é um guia de desenvolvimento.
> Última atualização: 2026-08-14

---

## 1. Visão Geral

O **Brain Framework** é uma ferramenta feita exclusivamente para auxiliar o hermes agente a gerenciar bases de conhecimento
usando com escrita em SQLAlchemy usando SQLite inicialmente é Postgresql quando o SQLite não suportar a demanda, chamos os 
profiles do hermes de (experts), em que cada experts tem a sua base exclusiva e com um base que chamos de "brain" global
que pode ser compartilhada globalmente entre os experts, tendo um expert nativo chamado **Brain**.

É agnóstico a qualquer sistema específico — pode ser usado por qualquer
usuário que queira reter conhecimento aos seus experts baseado em hermes e ter esses experts
consultando esses conhecimentos durante conversas.

---

## 2. Componentes Principais

### 2.1 Brain

Brain é o perfil mestre nativo do framework — o gestor do sistema.
Ele não é um agente de IA comum; é o gestor que cria, configura, e gerencia
os outros experts.

Responsabilidades:
- Criar novos experts (via `hermes profile create` + configuração)
- Configurar aliases no .bashrc para cada profile
- Gerenciar brains (global + experts)
- Fornecer comandos de gestão (update, add profile, sync, backup)
- Configurar admin list para cada profile (WhatsApp)
- Orquestrar atualizações do framework

### 2.2 Brain Global

Brain global é uma base compartilhada entre todos os agents.
Conhecimento que é comum a todos os experts.

Exemplos de conteúdo do global brain:
- Horário de funcionamento da empresa
- Dias de funcionamento
- Políticas gerais
- Informações que são úteis para múltiplos agents

### 2.3 Expert Brains

Cada expert tem sua próprio base.
Conhecimento específico de um experrt ficará dentro desta base.

Exemplos:
- Maria (atendimento): conhecimento de produtos, estratégias de atendimento, leads
- José (RH): conhecimento de RH, dados de ponto, procedimentos internos

### 2.4 Agente (Expert)

Um expert é um agente de IA configurado como profile no Hermes Agent.
Ele tem:
- Sua propria base.
- Acesso a canais de comunicação (WhatsApp, etc.)
- Permissões de admin (para receber comandos administrativos)
- Acesso a sistemas externos (MCP, API, etc.) se configurado

## 3. Modelo de Dados

### 3.1 Estrutura de Diretórios


~/.hermes/brain/
├── global/
│   └── brain.db          # conhecimento compartilhado
├── experts/
│   ├── maria/
│   │   └── brain.db      # conhecimento específico de Maria
│   ├── jose/
│   │   └── brain.db      # conhecimento específico de José
│   └── <outro_expert>/
│       └── brain.db
└── ...

### 3.2 Schema do brain.db (simplificado)

sql
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expert TEXT NOT NULL,          -- nome do profile ou "global"
    tipo TEXT,                     -- memory, fact, entity, etc.
    titulo TEXT,
    corpo TEXT,
    hash_canonical TEXT,           -- hash do conteúdo processado
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge_staging (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expert TEXT NOT NULL,
    chunk_data TEXT,
    hash_canonical TEXT,
    status TEXT DEFAULT 'pending', -- pending, processed
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    applied_at TEXT DEFAULT (datetime('now')),
    description TEXT
);

## 4. Knowledge Ingestion e Async Processing

### 4.1 Visão Geral

A ingestão de conhecimento (--learn, /admin learn, etc.) processa arquivos
e popula o brain.db. Como esse processo pode ser demorado, ele roda em
**camada assíncrona** via Celery + Redis (ou similar), e depois ocorre um
**sync** que atualiza a tabela principal do brain.db para que o conhecimento
esteja disponível para consulta.

### 4.2 Fluxo de Knowledge Ingestion

Command (--learn) → Queue (Celery/Redis) → Worker (processa arquivos) → Sync (atualiza brain.db)

**Passo a passo:**

1. **Command recebido** (`--learn /caminho/arquivos/` ou `/admin learn maria /caminho/`)
   - Validação de admin: se via WhatsApp/dashboard, verificar admin list
   - Se CLI, não há verificação de admin (acesso ao servidor é o controle)
   - Enqueue job no Celery/Redis com:
     - `expert`: nome do profile (ex: "maria", "jose")
     - `path`: caminho dos arquivos
     - `sync`: boolean (se true, força sync após processamento)
     - `job_id`: identificador único

2. **Queue (Celery/Redis)**
   - Job fica na fila até um worker disponível
   - Command retorna imediatamente com `job_id` e status "enqueued"
   - Não bloqueia a operação do sistema

3. **Worker (processamento assíncrono)**
   - Recebe o job da fila
   - Processa os arquivos:
     - Lê cada arquivo (PDF, DOCX, XLSX, CSV, TXT, Markdown)
     - Converte para texto
     - Chunking: divide em pedaços gerenciáveis
     - (Opcional) Extração de fatos via LLM (se usar learn com LLM)
   - Armazena chunks/fatos em **staging area** dentro do brain.db do expert
   - Calcula **hash canônico** (SHA-256 dos chunks/fatos processados)
   - Status vira "completed" com o hash canônico

4. **Sync (atualização da tabela principal)**
   - Se `sync: true` (ou `--learn sync` chamado explicitamente):
     - Move dados do staging para tabela principal de conhecimento
     - brain.db agora tem conhecimento disponível para consulta
   - Se `sync: false`:
     - Dados ficam no staging até sync explícito
     - Útil para ingestão em lote e sync posterior

5. **Confirmação**
   - Após sync (ou ao final do worker se sync automático):
     - Resposta: `"Conhecimento absorvido. Hash: abc123. Sync concluído."`
     - CLI: stdout
     - WhatsApp admin: resposta no canal administrativo
     - Dashboard: UI mostra status

### 4.3 Hash Canônico

- Cada ingestion gera um **hash canônico** (SHA-256 dos chunks/fatos)
- Hash é armazenado junto com o knowledge no brain.db
- Sync verifica se conteúdo já está syncado via hash — idempotente
- Evita reprocessamento desnecessário e garante consistência

### 4.4 Commands de Sync

bash
Learn com sync automático:
$ maria --learn /caminho/ --sync
$ maria --learn sync   # sync explícito (sem reprocessar)

Global:
$ jose --global-learn "texto" --sync
$ brain global --learn /caminho/ --sync

WhatsApp admin:
/admin learn maria /caminho/ --sync
/admin global-learn "texto" --sync (2/12)
[16/08/2026 01:51] sextafeira: 
### 4.5 Monitoramento de Jobs

- Status de jobs em andamento é visualizável:
  - CLI: `maria --jobs` ou `brain jobs` (lista jobs em andamento/completados)
  - Dashboard: painel de jobs com status, progresso, hash, tempo
  - WhatsApp admin: `/admin jobs` (lista resumida)

- Estados do job: `enqueued`, `processing`, `completed`, `failed`
- Se failed, job é retryável (re-enqueue) ou inspectável para debug

### 4.6 Limitações e Trade-offs

- **Redis/Celery é opcional para v1** — se o ambiente não tiver, ingestão roda em modo síncrono (bloqueante) com aviso. Aceitável para dev ou uso leve.
- **Tamanho de arquivo** — arquivos muito grandes podem ser problemáticos. Limite máximo deve ser configurável.
- **Paralelismo** — múltiplos jobs podem rodar em paralelo se houver workers. Com um worker só, jobs são processados em ordem.

## 5. Security

### 5.1 Princípio de Mínima Privilégio para Knowledge Absorption

O conhecimento no brain.db deve ser confiável. Qualquer pessoa que possa
injetar conhecimento no sistema é uma superfície de ataque. Portanto,
**somente administradores autenticados** podem adicionar conhecimento.

### 5.2 Admin-only Commands

Todos os comandos de knowledge absorption são admin-only, independente do canal.

**No WhatsApp (admin group/channel):**
bash
/admin learn <expert> <caminho>    → administrador autenticado
/admin learn <expert> sync        → força sync após aprendizado
/admin global-learn <texto>       → administra conhecimento global
/admin global-learn sync          → força sync no global

- Se usuário não está na lista de admins → comando ignorado com resposta:
  `"Comando restrito a administradores."`
- Se usuário está no grupo mas NÃO é admin do grupo → comando ignorado.
  Ser admin do grupo (ou participante autorizado com permissão explícita)
  é pré-requisito, além de estar na lista de admins.

**No Dashboard (com autenticação):**
bash
- Login com credencial administrativa
- Apenas admins logados podem acessar /learn, /global-learn, /admin commands
- Leads/clientes sem login não têm acesso à interface de admin

**No CLI (terminal — já seguro por natureza):**
bash
$ maria --learn /caminho/dos/arquivos
$ maria --learn sync
$ jose --global-learn "texto"
$ brain global --learn /caminho/dos/arquivos/gerais/

- CLI não precisa de verificação de admin porque o acesso ao servidor já é
  o controle de acesso. Se você tem acesso ao terminal, é admin do servidor.

### 5.3 Admin List e Grupo Admin

A lista de admins é configurável por profile. Para WhatsApp:

bash
Configuração de admin para um profile:
$ <profile> --configure-admins add <numero_telefone>
$ <profile> --configure-admins list
$ <profile> --configure-admins remove <numero_telefone>

**Grupo administrativo:**
- O admin deve ser:
  1. Cadastrado na lista de admins do profile
  2. Participante do grupo administrativo
  3. Admin do grupo (permisssão de administração no WhatsApp)
- Se falhar qualquer um dos 3 critérios → comando não executado

**Nota de design:** Não basta ser membro do grupo — ser admin do grupo é
verificação adicional de autoridade no contexto WhatsApp. Isso evita que
um participante comum execute comandos administrativos sem a devida autoridade.

### 5.4 Knowledge Integrity

- Conhecimento no brain.db só é adicionado via comandos admin-only
- Não há como um lead ou usuário não autorizado injetar conhecimento
  diretamente no brain.db
- Qualquer tentativa via WhatsApp é ignorada (comando não reconhecido ou
  resposta de "comando restrito a administradores")

### 5.5 Content Validation (futuro)

- Opcional: validar conteúdo antes de armazenar (tamanho máximo, formato,
  palavras-chave bloqueadas)
- Não é requisito para v1, mas deve ser documentado como future improvement

## 6. Admin Commands Reference

### 6.1 CLI (admin-safe por natureza)

bash
Knowledge ingestion (expert-specific):
$ maria --learn <caminho> [--sync]
$ maria --learn sync
$ jose --learn <caminho> [--sync]
 (3/12)
[16/08/2026 01:51] sextafeira: Global knowledge:
$ brain global --learn <caminho> [--sync]
$ jose --global-learn "texto" [--sync]
$ brain global --learn "texto" [--sync]

Job management:
$ maria --jobs
$ brain jobs

Admin configuration (WhatsApp):
$ maria --configure-admins add <numero>
$ maria --configure-admins list
$ maria --configure-admins remove <numero>

### 6.2 WhatsApp (admin-only, grupo com admin do grupo)

bash
Admin autenticado no grupo administrativo:
/admin learn maria /caminho/arquivos/ [--sync]
/admin learn jose /caminho/ [--sync]
/admin global-learn "texto" [--sync]
/admin jobs

Respostas para não-admins:
"Comando restrito a administradores."

### 6.3 Dashboard (com autenticação)

bash
- Login → aglutina admins
- Upload de arquivos ou informa caminho
- Seleciona expert (maria, jose) ou global
- Executa --learn com opção de sync
- Visualiza jobs e status

## 7. Segurança — Resumo Executivo

| Canal | Admin check | Restrição |
|---|---|---|
| CLI | Nenhum (acesso ao servidor = admin) | Somente quem tem acesso ao servidor |
| Dashboard | Login com credencial admin | Somente admins logados |
| WhatsApp (admin group) | 1) Lista de admins + 2) Membro do grupo + 3) Admin do grupo | Comandos ignorados para não-admins |
| WhatsApp (canal de atendimento) | N/A (leads não têm acesso) | Comandos ignorados ou "comando restrito" |

**Princípio:** Knowledge absorption is an administrative operation.
It must never be available to untrusted parties.

## 8. Casos de Uso Exemplo

### 8.1 Maria (Atendimento)

bash
Admin cria Maria:
$ brain add profile Maria
→ hermes profile create Maria
→ brain_tool.py init --name maria
→ alias maria="..." no .bashrc
→ Admin list configurável

Admin popula conhecimento de Maria:
$ maria --learn /caminho/dos/arquivos/atendimento/ --sync

Maria atende no WhatsApp:
Lead: "Qual o horário de funcionamento?"
Maria: consulta global brain → "Segunda a sexta, 8h-18h"

Lead: "Fale sobre o produto X"
Maria: consulta seu brain.db → responde com info de produto

### 8.2 José (RH)

bash
Admin cria José:
$ brain add profile Jose
→ hermes profile create Jose
→ brain_tool.py init --name jose
→ alias jose="..." no .bashrc

Admin popula conhecimento de José:
$ jose --learn /caminho/dos/arquivos/rh/ --sync

Admin popula conhecimento global:
$ brain global --learn /caminho/dos/arquivos/gerais/ --sync

José acessa sistema de ponto (MCP/API):
$ jose --configure-system access-point-system --mcp-endpoint ... --credentials ...

José verifica ponto:
Usuário: "José, verifica o ponto do funcionário Y"
José: acessa sistema de ponto via MCP → processa → reporta

José contribui com conhecimento global:
$ jose --global-learn "Horário de funcionamento: 7h-17h" --sync

### 8.3 Fluxo de Mudança no Conhecimento Global

bash
Empresa muda horário de 8h-18h para 7h-17h

Opção 1 — Via José (se José tem acesso a essa info):
$ jose --global-learn "Horário de funcionamento: 7h-17h (alterado em 2026-08-14)" --sync

Opção 2 — Via usuário diretamente:
$ brain global --learn "Horário de funcionamento: 7h-17h (alterado em 2026-08-14)" --sync

Depois da mudança:
- Maria consulta global brain e vê novo horário
- José também vê
- Qualquer agente novo que consulte global brain vê (4/12)


## 9. Critérios de Aceitação (v1)

- [ ] Brain pode criar novo profile (alias + brain.db vazio)
- [ ] CLI --learn popula brain.db de um expert
- [ ] CLI --learn sync atualiza tabela principal
- [ ] CLI --global-learn popula brain global
- [ ] Admin list configurável por profile (WhatsApp)
- [ ] WhatsApp admin commands apenas para admins autenticados
- [ ] Leads não podem injetar conhecimento via WhatsApp
- [ ] Jobs assíncronos com Celery/Redis (ou fallback síncrono)
- [ ] Hash canônico gerado e verificado no sync
- [ ] Dashboard (se implementado) com autenticação e admin-only

## 10. Roadmap Futuro (não para v1)

- [ ] Content validation (tamanho, formato, palavras-chave bloqueadas)
- [ ] Embeddings para busca semântica no knowledge
- [ ] Versionamento de knowledge (rollback de knowledge ingestion)
- [ ] Audit log de knowledge changes (quem adicionou o quê e quando)
- [ ] Knowledge sharing entre experts (peer-to-peer, não só global)
- [ ] API REST para integração com sistemas externos

📄 plan/requirements.md

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

## 2. Modelo de Dados

### 2.1 Estrutura de Diretórios

~/.hermes/brain/
├── global/
│   └── brain.db          # Conhecimento compartilhado
├── experts/
│   ├── maria/
│   │   └── brain.db      # Conhecimento específico de Maria
│   └── jose/
│       └── brain.db      # Conhecimento específico de José
└── admins.json           # Lista de administradores

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

[16/08/2026 01:51] sextafeira: brain_tool.py learn --expert <expert> --path <caminho> [--sync] [--global] [--dry-run]
- Processa arquivo ou diretório recursivamente
- Formatos suportados: `.txt`, `.md`, `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.csv`
- Converte para texto plano
- Gera hash canônico do conteúdo
- Adiciona ao `knowledge_staging` (status: pending)
- `--sync`: executa sync imediatamente após learn
- `--dry-run`: mostra o que seria processado sem executar
- **Admin**: requerido

### 3.8 `sync` — Sync staging → pages
bash
brain_tool.py sync --expert <expert> [--global]
- Move conhecimentos do staging para a tabela pages
- Verifica hash canônico para evitar duplicatas
- Se hash já existe em pages, skip (idempotente)
- **Admin**: requerido

### 3.9 `check` — Verificar integridade
bash
brain_tool.py check --expert <expert> [--global]
- PRAGMA integrity_check do SQLite
- Verifica versão do schema
- Conta registros por tabela
- Relata issues encontrados
- **Admin**: não necessário (read-only)

### 3.10 `jobs` — Listar jobs
bash
brain_tool.py jobs --expert <expert> [--status <status>] [--limit <n>] [--global]
- Lista jobs do expert (enqueued, processing, completed, failed)
- `--status` para filtrar
- **Admin**: não necessário (read-only)

---

## 4. Comandos do Brain (CLI do Gestor)

### 4.1 `add profile` — Criar novo expert
bash
brain add profile <name>
O que faz:
1. Tenta executar `hermes profile create <name>` (se hermes CLI disponível)
2. Cria brain.db vazio com `brain_tool init --name <name>`
3. Adiciona alias em `~/.bashrc`: `alias <name>='python -m hermes_cli.main --profile <name>'`
4. Confirma criação

### 4.2 `list profiles` — Listar experts
bash
brain list profiles
- Lista todos os diretórios em `experts/`
- Mostra status (existe/não existe) e contagem de conhecimentos

### 4.3 `remove profile` — Remover expert
bash
brain remove profile <name>
- Remove brain.db
- Remove diretório do expert
- Remove alias do `~/.bashrc`

### 4.4 `global learn` — Popular knowledge global
bash
brain global learn --path <caminho> [--sync] [--dry-run]
brain global learn --content <texto> --title <titulo> [--dry-run]
- Funciona como `brain_tool learn` mas para o global brain
- **Admin**: requerido (já é CLI, então quem tem acesso ao servidor é admin)

### 4.5 `backup` — Backup de todos os brains
bash
brain backup
- Cria diretório de backup com timestamp
- Copia brain.db de global + todos os experts
- Cria manifest.json com metadados

### 4.6 `update` — Atualizar framework
bash
brain update
- Executa `git pull origin main` no diretório do framework
- Only atualiza código, não brains

### 4.7 `sync all` — Sync todos os brains
bash
brain sync all
- Executa `brain_tool sync` para global + todos os experts
- Mostra status por expert

### 4.8 `admin` — Gestão de administradores
bash
brain admin list
brain admin add whatsapp <numero>
brain admin add cli <username>
brain admin add grupo <grupo> <membro>
brain admin remove <identificador>
- Admins armazenados em `~/.hermes/brain/admins.json`
- Tipos: `whatsapp`, `cli`, `grupo`
- Para WhatsApp: o admin deve também ser admin do grupo (verificar via WhatsApp API)

---

## 5. Integração com WhatsApp (via Hermes Gateway)

### 5.1 Arquitetura

┌─────────────────────────────────────────────────────────────┐
│                 HERMES AGENT GATEWAY                       │
│  - Conecta ao WhatsApp (API oficial ou não oficial)       │
│  - Gerencia sessões, mensagens, admin list               │
│  - Recebe comandos /admin learn, valida admin,            │
│    chama brain_tool ou brain para processar             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  - brain_tool.py: manipula brain.db                      │
│  - brain: gestor do sistema                            │
│  - Não sabe de WhatsApp — só processa comandos            │
└─────────────────────────────────────────────────────────────┘

### 5.2 Fluxo de Comandos WhatsApp

#### Canal de Atendimento (leads)

Lead: "Qual o horário de funcionamento?"
Maria: "A empresa funciona de segunda a sexta, das 8h às 18h."
- Lead não tem acesso a comandos administrativos
- Maria consulta seu brain.db e o global brain para responder

#### Canal Administrativo (admins)

/admin learn maria /caminho/dos/arquivos/ [--sync]
/admin global-learn "Horário: 8h-18h" [--sync]
/admin jobs
/admin status
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
- Lista de admins configurada via `brain admin add`
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
- `brain backup` cria backup completo
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
- Múltiplos LLMs: cada agent pode usar seu provider (brain é agnóstico)

---

## 10. Exemplos de Uso

### 10.1 Setup Inicial
bash
Criar experts
brain add profile maria
brain add profile jose

Popular global (horários, políticas)
brain global learn --content "Horário de funcionamento: 8h-18h" --title "Horário"
brain global learn --path /documentos/gerais/ --sync

Popular conhecimento específico
brain_tool.py learn --expert maria --path /documentos/atendimento/ --sync
brain_tool.py remember --expert jose --tipo policy --title "Política de férias" --content "..."

### 10.2 Operação Diária
bash
Verificar integridade
brain_tool.py check --expert maria

Adicionar conhecimento pelo CLI
brain_tool.py remember --expert maria --tipo fact --title "Novo produto" --content "..."

Sync após learn
brain_tool.py sync --expert maria

Listar jobs
brain_tool.py jobs --expert maria --status completed 

### 10.3 Segurança em Ação
bash
Admin CLI (seguro por natureza)
brain_tool.py learn --expert maria --path /docs/ --sync

Admin WhatsApp (grupo administrativo)
/admin learn maria /docs/ --sync

Lead no canal de atendimento (comando ignorado)
Lead: /learn /docs/
Maria: "Não entendi. Posso ajudar com algo sobre nossos produtos?"


---

📄 plan/brain.md

# Brain — Plano de Desenvolvimento

> Brain é o **brain inteligente do Hermes Agent** — perfil mestre nativo do brain-framework.
> Este plano define o que é, como funciona, e o que precisa ser implementado.

---

## 1. Visão geral

**Brain** é o brain inteligente do Hermes Agent — perfil mestre nativo do
brain-framework. Ele é o gestor do sistema — não um agente comum, mas a camada
que gerencia instalação, profiles, sincronização, e backup, **integrado com o
próprio Hermes Agent**.

Brain usa o CLI do brain (brain_tool.py) como base para manipular brain.db, e
releva os comandos nativos do Hermes Agent para gerir profiles:

- `sudo brain update` — atualiza o framework
- `sudo brain add profile` — adiciona um novo profile (cria profile Hermes + alias + brain.db)
- `sudo brain sync` — sincroniza brains entre profiles
- `sudo brain backup` — backup de todos os brains

## 2. Brain como brain inteligente do Hermes Agent

### 2.1 Natureza

- **Nativo do framework** — vem com a instalação, não é um "profile de fora"
- **Brain inteligente do Hermes Agent** — ele sabe como criar profiles Hermes,
  configurar aliases, etc. (não um gestor externo)
- **Universal** — não assume nada sobre o sistema do usuário

### 2.2 Como Brain gerencia um profile

Quando o usuário executa `sudo brain add profile <name>`, o Brain:

1. **Cria o profile Hermes** via comando nativo:
   
   hermes profile create <name>
      Isso cria toda a estrutura do profile no diretório de profiles do Hermes.

2. **Configura o alias** para o usuário power usar o profile:
   bash
   printf '\nalias <name>="/$HOME/$USER/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile <name>"\n' >> ~/.bashrc
   source ~/.bashrc
      Exemplo para profile "marketing":
   bash
   printf '\nalias marketing="/$HOME/$USER/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile marketing"\n' >> ~/.bashrc
   source ~/.bashrc
   
3. **Cria o brain.db** do novo profile:
   bash
   brain_tool init --scope <name> --brain-dir ~/.brain/profiles/<name>/
   
4. **Configura provider, LLM, etc.** — pergunta ao usuário ou usa defaults.

   4.1 oferte usar o ollama localmete com o modelo hermes3:3b para o brain

   4.2 ou deixa o usuario escolher o modelo desejado.

   4.5 precisamos criar um SOUL.md e SKILL.md para o agente Brain

---

## 3. Commands do Brain

### 3.1 `sudo brain update`

Atualiza o framework para a versão mais recente.

bash
Uso
sudo brain update

O que faz:
1. Verifica a versão atual (brain version)
2. Conecta ao repo (GitHub ou onde estiver hospedado)
3. Pega a versão mais recente
4. Substitui os arquivos locais
5. Reporta o que mudou

### 3.2 `sudo brain add profile`

Adiciona um novo profile ao sistema.

bash
Uso
sudo brain add profile <name>

O que faz:
1. Cria o profile Hermes: hermes profile create <name>
2. Configura o alias: printf '\nalias <name>="..."' >> ~/.bashrc && source ~/.bashrc
3. Cria o brain.db: brain_tool init --scope <name> --brain-dir ~/.brain/profiles/<name>/
4. Pergunta ao usuário: qual provider usar? (Nous, Ollama, etc.)
5. Pergunta: qual LLM/default usar? (free LLMs por padrão)
6. Configura o profile para usar o provider escolhido
7. Reporta o que foi criado

### 3.3 `sudo brain sync`

Sincroniza brains entre profiles.

bash
Uso
sudo brain sync

O que faz:
1. Para cada profile, lista o conhecimento relevante
2. Pergunta ao usuário: o que sincronizar? (global, specific, tudo)
3. Executa a sincronização via brain_tool
4. Reporta o que foi sincronizado

### 3.4 `sudo brain backup`

Backup de todos os brains.

bash
Uso
sudo brain backup

1. Backup do brain global
2. Backup de cada profile
3. Guarda em ~/.brain/backups/
4. Rotação: mantém últimos N backups
5. Reporta o que foi backupado

---

## 4. Brain como ferramenta de linha de comando

Brain é instalado como um comando de sistema:

bash
Após instalar o framework
sudo brain --version
sudo brain update
sudo brain add profile marketing
sudo brain sync
sudo brain backup

A instalação do brain é feita pelo `pyproject.toml` do framework — ele registered
como um comando de sistema (via `console_scripts` ou similar).

**Exemplo de uso completo (adicionar profile marketing):**

bash
1. Adicionar profile
sudo brain add profile marketing

O Brain executa:
- hermes profile create marketing
- printf '\nalias marketing="/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile marketing"\n' >> ~/.bashrc
- source ~/.bashrc
- brain_tool init --scope marketing --brain-dir ~/.brain/profiles/marketing/
- Pergunta: provider? (Nous, Ollama, etc.) — default: Nous
- Pergunta: LLM/default? (free LLMs) — default: gemma4:31b ou outro free

2. Usar o profile (agora com alias)
marketing --help
ou
marketing <comando>

3. Sincronizar
sudo brain sync

4. Backup
sudo brain backup

## 5. Universal/Genérico — o que NÃO fazer
- **Não assumir que há um "sistema principal"** — Brain é neutro
- **Não assumir caminhos específicos** — perguntar ao usuário ou usar padrões configuráveis
- **Não assumir provider específico** — suportar vários, deixar escolha ao usuário

## 6. O que já existe (reuso)

- `brain_tool.py` — manipulação de brain.db, schema, comandos CRUD
- `backup.sh` — backup/restore (pode ser adaptado para brain backup)
- `schema_pack.yaml` — schema de taxonomia (pode ser usado como template)
- Estrutura de diretórios (global, experts) — convenção, não obrigatoriedade
- Comando `hermes profile create <name>` — cria profiles Hermes
- Alias padrão do Hermes Agent para profiles (exemplo: `marketing`)

## 7. O que precisa ser implementado

### 7.1 Brain CLI

- [ ] `brain/cli.py` — entry point para `sudo brain`
- [ ] `brain/core.py` — lógica de gestão (add profile, sync, backup, update)
- [ ] `brain/config.py` — configuração do Brain (provider, LLMs, paths)

### 7.2 Integração com Hermes Agent

- [ ] Brain chama `hermes profile create <name>` para criar profiles
- [ ] Brain configura alias no ~/.bashrc (exemplo: `marketing`)
- [ ] Brain chama `brain_tool init` para criar brain.db do novo profile

### 7.3 Integração com brain_tool

- [ ] Brain chama `brain_tool` nos bastidores (subprocess ou import)
- [ ] Comandos do Brain mapeados para operações do brain_tool

### 7.4 Instalação

- [ ] `pyproject.toml` registra brain como console_script
- [ ] `pip install` instala brain como comando de sistema

### 7.5 Configuração

- [ ] Arquivo de configuração do Brain (~/.brain/brain.yaml ou similar)
- [ ] Provider default, LLMs free, paths configuráveis

## 8. Critério de aceitação

- [ ] `sudo brain --version` funciona e reporta a versão
- [ ] `sudo brain update` atualiza o framework
- [ ] `sudo brain add profile marketing`:
  - Cria profile Hermes via `hermes profile create marketing`
  - Configura alias `marketing` no ~/.bashrc
  - Cria brain.db via `brain_tool init`
  - Configura provider/LLM
  - Reporta o que foi criado
- [ ] `sudo brain sync` sincroniza brains entre profiles
- [ ] `sudo brain backup` backupa todos os brains
- [ ] Brain é universal — não assume nada sobre o sistema do usuário
- [ ] Brain usa LLMs free por padrão (configurável)

## 9. Próximos passos

1. **Definir estrutura de arquivos** do brain no projeto
2. **Implementar brain/cli.py** — entry point
3. **Implementar brain/core.py** — lógica de gestão
4. **Implementar brain/config.py** — configuração
5. **Integrar com Hermes Agent** — chamar `hermes profile create` e configurar alias
6. **Integrar com brain_tool** — chamar nos bastidores
7. **Testar com um profile de exemplo** (não usar profiles existentes da Granjimmy)
8. **Documentar** em doc/

