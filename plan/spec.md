# SPEC — Brain Framework

> Especificação do sistema Brain Framework — versão 0.1.0 (draft)
> Este documento descreve o que o sistema deve fazer, o modelo de dados, os
> comandos, e os critérios de aceitação.
> Este documento é iterativo — vai sendo atualizado à medida que o sistema
> é desenvolvido. Não é um contrato fixo; é um guia de desenvolvimento.
> Última atualização: 2026-08-14

---

## 1. Visão Geral

O **Brain Framework** é um sistema para gerenciar bases de conhecimento
(SQLite brain.db) para agentes de IA (experts), com um brain global
compartilhado entre todos os agents, e um gestor chamado **Brain**.

É agnóstico a qualquer sistema específico — pode ser usado por qualquer
usuário que queira dar conhecimento aos seus agents de IA e ter esses agents
consultarem esse conhecimento durante conversas.

---

## 2. Componentes Principais

### 2.1 Brain

Brain é o perfil mestre nativo do framework — o gestor do sistema.
Ele não é um agente de IA comum; é o gestor que cria, configura, e gerencia
os outros profiles/agents.

Responsabilidades:
- Criar novos profiles/agents (via `hermes profile create` + configuração)
- Configurar aliases no .bashrc para cada profile
- Gerenciar brains (global + experts)
- Fornecer comandos de gestão (update, add profile, sync, backup)
- Configurar admin list para cada profile (WhatsApp)
- Orquestrar atualizações do framework

### 2.2 Brain Global

Brain global é uma base SQLite compartilhada entre todos os agents.
Conhecimento que é comum a todos os agents deve ser armazenado aqui.

Exemplos de conteúdo do global brain:
- Horário de funcionamento da empresa
- Dias de funcionamento
- Políticas gerais
- Informações que são úteis para múltiplos agents

### 2.3 Expert Brains

Cada agente (expert) tem seu próprio brain.db.
Conhecimento específico de um agente fica no brain.db desse agente.

Exemplos:
- Maria (atendimento): conhecimento de produtos, estratégias de atendimento, leads
- José (RH): conhecimento de RH, dados de ponto, procedimentos internos

### 2.4 Agente (Expert)

Um expert é um agente de IA configurado como profile no Hermes Agent.
Ele tem:
- Seu próprio brain.db (SQLite)
- Acesso a canais de comunicação (WhatsApp, etc.)
- Permissões de admin (para receber comandos administrativos)
- Acesso a sistemas externos (MCP, API, etc.) se configurado

---

## 3. Modelo de Dados

### 3.1 Estrutura de Diretórios

```
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
```

### 3.2 Schema do brain.db (simplificado)

```sql
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
```

---

## 4. Knowledge Ingestion e Async Processing

### 4.1 Visão Geral

A ingestão de conhecimento (--learn, /admin learn, etc.) processa arquivos
e popula o brain.db. Como esse processo pode ser demorado, ele roda em
**camada assíncrona** via Celery + Redis (ou similar), e depois ocorre um
**sync** que atualiza a tabela principal do brain.db para que o conhecimento
esteja disponível para consulta.

### 4.2 Fluxo de Knowledge Ingestion

```
Command (--learn) → Queue (Celery/Redis) → Worker (processa arquivos) → Sync (atualiza brain.db)
```

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

```
# Learn com sync automático:
$ maria --learn /caminho/ --sync
$ maria --learn sync   # sync explícito (sem reprocessar)

# Global:
$ jose --global-learn "texto" --sync
$ brain global --learn /caminho/ --sync

# WhatsApp admin:
/admin learn maria /caminho/ --sync
/admin global-learn "texto" --sync
```

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

---

## 5. Security

### 5.1 Princípio de Mínima Privilégio para Knowledge Absorption

O conhecimento no brain.db deve ser confiável. Qualquer pessoa que possa
injetar conhecimento no sistema é uma superfície de ataque. Portanto,
**somente administradores autenticados** podem adicionar conhecimento.

### 5.2 Admin-only Commands

Todos os comandos de knowledge absorption são admin-only, independente do canal.

**No WhatsApp (admin group/channel):**
```
/admin learn <expert> <caminho>    → administrador autenticado
/admin learn <expert> sync        → força sync após aprendizado
/admin global-learn <texto>       → administra conhecimento global
/admin global-learn sync          → força sync no global
```

- Se usuário não está na lista de admins → comando ignorado com resposta:
  `"Comando restrito a administradores."`
- Se usuário está no grupo mas NÃO é admin do grupo → comando ignorado.
  Ser admin do grupo (ou participante autorizado com permissão explícita)
  é pré-requisito, além de estar na lista de admins.

**No Dashboard (com autenticação):**
```
- Login com credencial administrativa
- Apenas admins logados podem acessar /learn, /global-learn, /admin commands
- Leads/clientes sem login não têm acesso à interface de admin
```

**No CLI (terminal — já seguro por natureza):**
```
$ maria --learn /caminho/dos/arquivos
$ maria --learn sync
$ jose --global-learn "texto"
$ brain global --learn /caminho/dos/arquivos/gerais/
```

- CLI não precisa de verificação de admin porque o acesso ao servidor já é
  o controle de acesso. Se você tem acesso ao terminal, é admin do servidor.

### 5.3 Admin List e Grupo Admin

A lista de admins é configurável por profile. Para WhatsApp:

```bash
# Configuração de admin para um profile:
$ <profile> --configure-admins add <numero_telefone>
$ <profile> --configure-admins list
$ <profile> --configure-admins remove <numero_telefone>
```

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

### 5.5 Content Validation (Opcional — futuro)

- Opcional: validar conteúdo antes de armazenar (tamanho máximo, formato,
  palavras-chave bloqueadas)
- Não é requisito para v1, mas deve ser documentado como future improvement

---

## 6. Admin Commands Reference

### 6.1 CLI (admin-safe por natureza)

```bash
# Knowledge ingestion (expert-specific):
$ maria --learn <caminho> [--sync]
$ maria --learn sync
$ jose --learn <caminho> [--sync]

# Global knowledge:
$ brain global --learn <caminho> [--sync]
$ jose --global-learn "texto" [--sync]
$ brain global --learn "texto" [--sync]

# Job management:
$ maria --jobs
$ brain jobs

# Admin configuration (WhatsApp):
$ maria --configure-admins add <numero>
$ maria --configure-admins list
$ maria --configure-admins remove <numero>
```

### 6.2 WhatsApp (admin-only, grupo com admin do grupo)

```
# Admin autenticado no grupo administrativo:
/admin learn maria /caminho/arquivos/ [--sync]
/admin learn jose /caminho/ [--sync]
/admin global-learn "texto" [--sync]
/admin jobs

# Respostas para não-admins:
"Comando restrito a administradores."
```

### 6.3 Dashboard (com autenticação)

```
- Login → aglutura admins
- Upload de arquivos ou informa caminho
- Seleciona expert (maria, jose) ou global
- Executa --learn com opção de sync
- Visualiza jobs e status
```

---

## 7. Segurança — Resumo Executivo

| Canal | Admin check | Restrição |
|---|---|---|
| CLI | Nenhum (acesso ao servidor = admin) | Somente quem tem acesso ao servidor |
| Dashboard | Login com credencial admin | Somente admins logados |
| WhatsApp (admin group) | 1) Lista de admins + 2) Membro do grupo + 3) Admin do grupo | Comandos ignorados para não-admins |
| WhatsApp (canal de atendimento) | N/A (leads não têm acesso) | Comandos ignorados ou "comando restrito" |

**Princípio:** Knowledge absorption is an administrative operation.
It must never be available to untrusted parties.

---

## 8. Casos de Uso Exemplo

### 8.1 Maria (Atendimento)

```
# Admin cria Maria:
$ brain add profile Maria
→ hermes profile create Maria
→ brain_tool.py init --name maria
→ alias maria="..." no .bashrc
→ Admin list configurável

# Admin popula conhecimento de Maria:
$ maria --learn /caminho/dos/arquivos/atendimento/ --sync

# Maria atende no WhatsApp:
Lead: "Qual o horário de funcionamento?"
Maria: consulta global brain → "Segunda a sexta, 8h-18h"

Lead: "Fale sobre o produto X"
Maria: consulta seu brain.db → responde com info de produto
```

### 8.2 José (RH)

```
# Admin cria José:
$ brain add profile Jose
→ hermes profile create Jose
→ brain_tool.py init --name jose
→ alias jose="..." no .bashrc

# Admin popula conhecimento de José:
$ jose --learn /caminho/dos/arquivos/rh/ --sync

# Admin popula conhecimento global:
$ brain global --learn /caminho/dos/arquivos/gerais/ --sync

# José acessa sistema de ponto (MCP/API):
$ jose --configure-system access-point-system --mcp-endpoint ... --credentials ...

# José verifica ponto:
Usuário: "José, verifica o ponto do funcionário Y"
José: acessa sistema de ponto via MCP → processa → reporta

# José contribui com conhecimento global:
$ jose --global-learn "Horário de funcionamento: 7h-17h" --sync
```

### 8.3 Fluxo de Mudança no Conhecimento Global

```
# Empresa muda horário de 8h-18h para 7h-17h

# Opção 1 — Via José (se José tem acesso a essa info):
$ jose --global-learn "Horário de funcionamento: 7h-17h (alterado em 2026-08-14)" --sync

# Opção 2 — Via usuário diretamente:
$ brain global --learn "Horário de funcionamento: 7h-17h (alterado em 2026-08-14)" --sync

# Depois da mudança:
# - Maria consulta global brain e vê novo horário
# - José também vê
# - Qualquer agente novo que consulte global brain vê
```

---

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

---

## 10. Roadmap Futuro (não para v1)

- [ ] Content validation (tamanho, formato, palavras-chave bloqueadas)
- [ ] Embeddings para busca semântica no knowledge
- [ ] Versionamento de knowledge (rollback de knowledge ingestion)
- [ ] Audit log de knowledge changes (quem adicionou o quê e quando)
- [ ] Knowledge sharing entre experts (peer-to-peer, não só global)
- [ ] API REST para integração com sistemas externos
