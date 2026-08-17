# Checkpoints Assinados — Design de Governança de Conhecimento

> Estado: **aprovada (17/08/2026) + Fases 1–4 implementadas** — decisões na §13.
> Fase 1: storage content-addressed, commits, refs, Ed25519, `verify`/`log`,
> migração genesis. Fase 2: `diff`/`approve`/`rollback`. Fase 3: quarentena +
> extração isolada + detecção de conteúdo suspeito. Fase 4: `promote` + dupla
> aprovação + RBAC + `audit_events` em todos os caminhos.
> Origem: auditoria técnica de 17/08/2026 — "Falha central do hash atual" e
> "Feature recomendada: checkpoints assinados".
> Objetivo deste documento: definir o modelo de dados, o fluxo e os comandos
> **antes** de implementar. Decisões em aberto na seção 13.

---

## 1. Motivação e problema

O hash atual é:

```python
sha256(conteudo.encode("utf-8"))
```

Ele garante **apenas deduplicação** de conteúdo idêntico. Não garante:

1. **Integridade** — alterar `corpo` sem atualizar `hash_canonical` não é
   detectado (`brain check` verifica só a integridade estrutural do SQLite).
2. **Autoria** — não registra quem adicionou/alterou.
3. **Aprovação** — não registra quem autorizou nem sob qual política.
4. **Histórico** — não há como reconstruir o estado anterior nem auditar mudanças.
5. **Proteção contra prompt injection** — um documento importado pode conter
   "ignore as regras anteriores"; o sistema calcula o hash disso perfeitamente.

> **Hash prova identidade de bytes; não prova que o conhecimento é seguro,
> verdadeiro ou autorizado.**

---

## 2. Objetivos (v1 desta feature)

- **Integridade detectável**: qualquer alteração não autorizada no conteúdo ou
  no histórico é apontada por `brain verify`.
- **Autoria e aprovação registradas**: todo commit tem autor, política e
  assinatura; toda publicação passa por `approve`.
- **Histórico imutável e rollback não destrutivo**: `brain log` e
  `brain rollback` movem a referência sem apagar nada.
- **Prompt injection mitigado por design**: conteúdo importado é não-confiável
  até aprovação; não há publicação automática.

**Fluxo alvo:**

```
learn propõe → approve autoriza → sync faz merge → verify comprova → rollback recupera
```

---

## 3. Princípios

1. **Conteúdo imutável endereçado por hash** (content-addressed): o conteúdo é
   guardado uma única vez, identificado por `sha256(bytes)`. Nunca se edita no
   lugar — edição = novo objeto + novo commit.
2. **Toda mutação gera um commit assinado** (Ed25519), com a chave **fora do
   banco**.
3. **Leitura só enxerga `main`**: branches candidatas são invisíveis para
   `recall`/agentes.
4. **Nada é publicado automaticamente**: `learn` propõe; publicar exige
   aprovação + merge.
5. **A âncora de confiança (chave pública) fica fora do banco** — senão quem
   altera o banco trocaria a chave e reassinaria tudo.
6. **Prompt injection**: não existe filtro infalível; o objetivo é reduzir
   impacto, limitar agência e impedir publicação automática.

---

## 4. Modelo de dados

Novas tabelas (SQLAlchemy). A coluna `scope` assume `global` ou `expert/<nome>`.

### 4.1 `knowledge_objects` — conteúdo imutável

| Coluna | Tipo | Notas |
|---|---|---|
| `hash` | TEXT (PK) | `sha256(bytes(content))` |
| `content` | TEXT | corpo imutável (ou BLOB p/ binário futuro) |
| `created_at` | DateTime | |

### 4.2 `commits` — checkpoint assinado

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | TEXT (PK) | `commit_hash` (hex) |
| `scope` | TEXT | `global` ou `expert/<nome>` |
| `parent_hashes` | TEXT (JSON) | lista de parents (merge = 2) |
| `tree_hash` | TEXT | hash da árvore do scope (ver §5) |
| `author` | TEXT | admin que assinou (ex.: `cli:root`, `wa:+5511...`) |
| `pipeline_version` | TEXT | versão do pipeline de ingestão |
| `policy_version` | TEXT | versão da política de aprovação |
| `validation_results` | TEXT (JSON) | resultado das validações (§9) |
| `message` | TEXT | descrição opcional |
| `created_at` | DateTime | |
| `signature` | TEXT | assinatura Ed25519 (hex) sobre `id` |
| `signing_key_id` | TEXT | qual chave assinou (rotação) |

### 4.3 `commit_items` — o que o commit mudou

| Coluna | Tipo | Notas |
|---|---|---|
| `commit_id` | TEXT (FK→commits) | |
| `op` | TEXT | `add` \| `change` \| `remove` |
| `object_hash` | TEXT (FK→knowledge_objects) | |
| `tipo` | TEXT | `memory/fact/entity/procedure/policy/...` |
| `titulo` | TEXT | metadado mutável por commit |

### 4.4 `refs` — ponteiros

| Coluna | Tipo | Notas |
|---|---|---|
| `name` | TEXT (PK) | `expert/maria/main`, `global/main`, `expert/maria/candidate/<job_id>` |
| `commit_id` | TEXT (FK→commits) | |
| `updated_at` | DateTime | |

### 4.5 `approvals` — aprovação/rejeição

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER (PK) | |
| `scope` | TEXT | |
| `candidate_commit_id` | TEXT | commit candidato sendo avaliado |
| `approver` | TEXT | admin |
| `decision` | TEXT | `approve` \| `reject` |
| `policy` | TEXT | política aplicada (ex.: `policy-approval-v1`) |
| `justification` | TEXT | |
| `created_at` | DateTime | |

### 4.6 `audit_events` — log encadeado

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER (PK) | |
| `prev_hash` | TEXT | hash do evento anterior (log hash-linked) |
| `event` | TEXT | `learn/approve/merge/rollback/verify/...` |
| `scope` | TEXT | |
| `actor` | TEXT | |
| `payload` | TEXT (JSON) | detalhes |
| `hash` | TEXT | hash deste evento |
| `created_at` | DateTime | |

### 4.7 `jobs` (existente, estendido)

Adicionar `candidate_ref` (TEXT) apontando para a branch candidata criada pelo
job — liga o processamento Celery ao checkpoint que ainda aguarda aprovação.

---

## 5. Árvore e hash de commit

### 5.1 `tree_hash`

A "árvore" de um scope num commit é o conjunto ordenado das entradas
`(object_hash, tipo, titulo)`. Para v1:

```text
tree_hash = sha256(
    canonical(sorted_entries)  # serialização canônica (JSON, chaves estáveis)
)
```

Otimização futura (P2): árvore de Merkle para provas parciais em scopes grandes.

### 5.2 `commit_hash`

```text
commit_hash = SHA-256(
    parent_hashes
    + tree_hash
    + scope
    + author
    + pipeline_version
    + policy_version
    + validation_results
    + timestamp
)
```

(canonicalizado com separadores de comprimento — sem ambiguidade de concatenação).

### 5.3 Assinatura

```text
signature = Ed25519.sign(signing_key, commit_hash_bytes)
```

A assinatura é **sobre** o hash; não faz parte dele. Alterar qualquer campo
muda o hash → a assinatura deixa de validar.

---

## 6. Chaves (Ed25519) e âncora de confiança

- **Chave privada** fora do banco: `~/.hermes/brain/.signing/ed25519.key` (0600),
  ou `BRAIN_SIGNING_KEY` (base64). Org mode → KMS/Vault (P2).
- **Chave pública** também fora do banco: `~/.hermes/brain/.signing/ed25519.pub`
  (ou keyring). É a **âncora de confiança**: se vivesse no banco, quem altera o
  banco trocaria a chave e reassinaria tudo — a verificação ficaria inútil.
- **Rotação**: cada commit guarda `signing_key_id`; `verify` valida contra a
  chave pública correspondente àquele id no keyring (arquivo fora do banco).

---

## 7. Fluxos

> Nota: reflete as decisões da §13 — `remember`/`forget` viram commit
> auto-aprovado; `learn --sync` = aprovação implícita do admin local.

### 7.1 `learn` — propõe, não publica

1. Cria `job` (Celery) e uma branch candidata `expert/<nome>/candidate/<job_id>`.
2. Extrai o conteúdo (isolado, §9), divide em objetos → `knowledge_objects`.
3. Monta a árvore candidata (diferença sobre a `main` atual).
4. Roda validações (§9) e grava `validation_results` no commit candidato.
5. **Não** avança a `main`. O conhecimento fica em quarentena, invisível ao recall.

### 7.2 `approve` — autoriza

1. `brain diff` mostra inclusões/alterações/conflitos/exclusões + flags de risco.
2. `brain approve --scope ... --candidate <job|commit> [--policy ...] [--note ...]`
   registra `approvals` (quem, política, justificativa). Pode assinar a decisão.

### 7.3 `sync` — merge atômico + checkpoint assinado

1. Valida que existe aprovação para o candidato.
2. Numa **única transação**: cria o commit (parent = `main` atual, tree = árvore
   aprovada), assina, grava `commit_items`, avança `refs[<scope>/main]`, e
   escreve `audit_events`.
3. Se algo falhar, rollback da transação — nada fica pela metade.

### 7.4 `verify` — comprova integridade

Percorre a cadeia a partir de cada `main`:

1. `object_hash == sha256(content)` para todo objeto referenciado.
2. `tree_hash` recomputado bate com o armazenado.
3. `commit_hash` recomputado bate com o armazenado.
4. `Ed25519.verify(pubkey[key_id], signature, commit_hash)`.
5. Encadeamento de parents fecha (sem buracos).
6. `refs` apontam para commits existentes.

Qualquer falha → relatório com o commit/objeto exato. (Na ponte com o legado,
`verify` também recalcula `hash_canonical` das `pages` antigas — §11.)

### 7.5 `log` / `rollback`

- `brain log --scope ...` — histórico de commits (autor, data, mensagem, hash).
- `brain rollback --scope ... --to <commit>` — move `refs[<scope>/main]` para um
  commit anterior. **Não apaga** commits/objetos; é só mover o ponteiro.
  Exige aprovação e registra `audit_events`.

### 7.6 `promote` — expert → global (aprovação reforçada)

- `brain promote --from expert/maria --to global [--objects ...]` propõe levar
  conhecimento de um expert para o global.
- Exige **dupla aprovação** (dois admins distintos) antes do merge.

---

## 8. Comandos (referência)

```bash
brain learn --expert maria --path /docs/            # propõe (branch candidata)
brain diff --expert maria [--candidate <job_id>]    # mostra o que mudaria
brain approve --expert maria --candidate <job_id> [--policy P] [--note N]
brain expert sync maria                             # merge aprovado → main
brain global sync                                   # merge aprovado → global main
brain verify [--scope ...]                          # recalcula/valida/verifica
brain log --scope ...                               # histórico de commits
brain rollback --scope ... --to <commit>            # move ref, não apaga
brain promote --from expert/maria --to global ...   # propõe promoção (dupla aprovação)
```

---

## 9. Prompt injection — mitigação integrada

1. **Quarentena por padrão**: todo arquivo/URL/texto importado nasce
   não-confiável até `approve`.
2. **Extração isolada**: PDF/DOCX em subprocesso com limites de CPU, memória,
   páginas e tempo (`resource.setrlimit`); container como evolução (P2).
3. **Pré-checagens**: MIME real (magic bytes, não extensão), antivírus opcional
   (via env), tamanho máximo, rejeição de arquivo malformado.
4. **Detecção de conteúdo suspeito**: heurísticas para instruções
   ("ignore as regras anteriores", "desconsidere"), credenciais e PII — gravadas
   em `validation_results` e exibidas no `diff`/`approve`.
5. **Aprovação humana obrigatória** para `policy`, `procedure`, credenciais e
   conhecimento global.
6. **Dupla aprovação** para promoção expert → global.
7. **Recuperação como dado, não instrução**: `recall` entrega conteúdo
   delimitado/estruturado; o plugin/agente deve tratá-lo como **dado**, nunca
   como instrução de sistema (contrato no plugin, não no banco).
8. **Permissões de ferramentas fora da KB**: config, nunca conhecimento.

---

## 10. Integração Celery/Redis (hardening)

- Mensagem transporta **só `(job_id, scope)`** — nunca `database_url`. O worker
  reconstrói a URL do próprio ambiente (elimina credencial no Redis).
- Worker executa extração → branch candidata → validações; **não publica**
  (publicação = `sync` aprovado).
- Task com `acks_late=True`, `autoretry_for`, `max_retries`, `time_limit` /
  `soft_time_limit` (idempotente).
- Merge/checkpoint em transação única.
- `docker-compose`: sem senha padrão (`brain/brain`), portas Redis/Postgres não
  expostas por default, worker `non-root`.

---

## 11. Migração do modelo atual

Modelo atual: `pages` (+ `knowledge_staging`, `jobs`).

1. **Genesis commit**: para cada scope, o conteúdo atual de `pages` vira
   `knowledge_objects` + um commit "genesis" (assinado pela chave bootstrap)
   com `pipeline_version = "migration-v1"` e
   `validation_results = {"migrated_from": "pages", "integrity": "unverified"}`.
   `refs[<scope>/main] = genesis`.
   - **Honestidade**: dados pré-migração **não ganham** garantia de integridade
     retroativamente — só commits novos.
2. **`knowledge_staging` → branches candidatas**: cada item pendente vira
   objeto + branch candidata aguardando aprovação.
3. **`pages` mantida como leitura** durante a transição; escritas passam a ser
   via commits. Depois de estabilizado, `pages` pode virar uma *view* sobre a
   `main` (materializada ou on-the-fly).
4. **`brain check`** ganha recomputação de `hash_canonical` das `pages` legadas
   (ponte até a migração completa).

---

## 12. Fases de implementação

| Fase | Escopo | Entrega |
|---|---|---|
| **0** | Este design + decisões da §13 | spec aprovado ✅ |
| **1** | `knowledge_objects` + `commits` + `refs` + Ed25519 + `verify` + migração (genesis) | integridade real ✅ |
| **2** | `approve`/`diff`/`rollback` (`log` saiu na Fase 1) | governança ✅ (`learn --sync` mantido — decisão §13.4) |
| **3** | Quarentena + extração isolada + detecção de conteúdo suspeito | anti-injection ✅ |
| **4** | `promote` + dupla aprovação + RBAC + `audit_events` em todos os caminhos | org mode ✅ |

Cada fase é um milestone independente e testável.

---

## 13. Decisões aprovadas (17/08/2026)

1. **Chave Ed25519**: via env `BRAIN_SIGNING_KEY` (base64). Sem arquivo de chave
   em disco por padrão; se ausente ao assinar, erro explícito (assinar exige
   chave). `BRAIN_SIGNING_KEY_PUB` (ou derivada) é a âncora de confiança.
2. **Assinatura**: **Ed25519** (assimétrico) — leitores/agentes verificam com a
   chave pública sem poder assinar.
3. **`remember`/`forget` diretos**: continuam, mas viram **commit auto-aprovado**
   do admin (autor = admin local, `policy = "implicit-admin"`), mantendo a
   trilha de integridade.
4. **`learn --sync`**: mantido como **aprovação implícita do admin local**
   (equivale a learn + approve + sync automáticos no mesmo ator).
5. **`verify` na v1**: valida a cadeia nova **e** inclui recomputação das
   `pages` legadas na ponte de migração.
