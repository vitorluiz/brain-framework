# Changelog

Todos os cambios notáveis do Brain Framework são documentados aqui.
O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o versionamento segue [SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased] — 1.0.0 (pré-lançamento)

> O projeto ainda **não teve release oficial**. Esta seção consolida tudo o que
> foi construído e será publicada como **v1.0.0** no primeiro lançamento (sem
> bump de versão até lá).

### Adicionado

- **Storage SQLAlchemy 2.0** — SQLite local (um `brain.db` por expert/global) ou
  PostgreSQL compartilhado (`DATABASE_URL`), transparente para o chamador.
- **Processamento assíncrono (Celery + Redis)** — `brain-worker` + task `brain.learn`,
  com fallback síncrono quando não há broker (spec §4.6).
- **Plugin Hermes nativo** — tool `brain` (remember/recall/learn/check/jobs/…) com
  absorção de conhecimento admin-only (spec §5).
- **`brain.py` como expert nativo** — gestor completo (profiles, global, backup,
  update, sync all, admin) + comandos de conhecimento.
- **Dashboard web (FastAPI)** — spec §6.3: learn, jobs, integridade, status dos
  serviços (Redis/Celery/Docker) e autenticação por token/senha.
- **Dashboard em background** — não trava o terminal; `--foreground` opcional,
  subcomandos `stop`/`status`.
- **Token de acesso LAN** e retomada de sessão por token (`brain dashboard token`).
- **Learn por URL (SSRF-safe)** — bloqueio de IPs privados/loopback por padrão.
- **`brain soul` / `brain model`** — gestão de `SOUL.md` e LLM/provider/fallback por
  profile (CLI + edição no dashboard).
- **`brain restore --from <ts>`** — restaura de backup com confirmação e cópia de
  segurança do estado atual (`<brain.db>.pre-restore-<ts>`).
- **Checkpoints assinados — Fase 1** (governança): storage content-addressed
  (`knowledge_objects`), grafo de commits assinados (Ed25519), refs, `brain verify`
  e `brain log`, migração genesis de dados legados.
- **Checkpoints assinados — Fase 2**: `brain diff`, `brain approve` e
  `brain rollback` (não-destrutivo), com resolução de commit por prefixo único.
- **Checkpoints assinados — Fase 3**: quarentena (`learn` propõe um commit
  candidato sem publicar + `brain merge`), detecção heurística de conteúdo
  suspeito (instruções/credenciais/PII) e extração isolada (subprocesso com
  limites de CPU/memória/tempo) com pré-checks de tamanho e MIME.
- **Checkpoints assinados — Fase 4**: `brain promote` (expert → global com
  **dupla aprovação** — exige 2 admins distintos antes do merge), RBAC por
  papéis (`admin`/`approver` via `brain admin role`), `--actor` em
  approve/merge/rollback/promote e `audit_events` encadeado (hash-linked) em
  todos os caminhos de mutação (promote/merge/approve/consolidate).

### Alterado

- **Layout sem `experts/`** — `brain.db` direto em `~/.hermes/brain/<nome>/`,
  espelhando `~/.hermes/profiles/<nome>/` do Hermes.
- **`celebro` → `brain`** — CLI do gestor fundido no expert nativo; `brain-tool`
  virou alias do `brain` (`brain_tool.py` agora é só a camada de domínio).
- **Versão do pacote = 1.0.0** (pré-lançamento; sem bump até release oficial).
- **SOUL.md do Brain** — fonte canônica em `doc/SOUL.md`, implantado em `~/.hermes/SOUL.md`.
- **Backup consistente** — snapshot via SQLite backup API (captura transações em WAL).

### Corrigido

- Entry points quebrados (`brain_tool.cli`/`celebro.cli` inexistentes) — `pyproject.toml`
  correto + `__main__.py`.
- `remove profile` agora remove o profile Hermes de verdade.
- Migração idempotente de schema legado (`ADD COLUMN` quando a coluna falta).
- Backup/restore em modo WAL perdia/contaminava dados — corrigido com SQLite backup
  API + descarte de engine em cache (`dispose_engine_for_path`).
- `brain check` agora recalcula `hash_canonical` e detecta adulteração de conteúdo.

### Segurança

- Autorização admin no **core** (`require_admin(actor)`), não só no plugin — qualquer
  canal futuro (gateway/dashboard) fica protegido por padrão.
- Dashboard: PBKDF2 (100k iterações), lockout (5 tentativas/5 min), CSRF `same_origin`,
  cookie `SameSite=Strict`, secret persistente, cap de upload (50 MB), audit log.
- Learn por URL com proteção anti-SSRF.
- Checkpoints assinados com **Ed25519** — chave privada/pública **fora do banco**
  (âncora de confiança); gerada automaticamente no primeiro uso em
  `$BRAIN_ROOT/.signing/` (override via `BRAIN_SIGNING_KEY`/`BRAIN_SIGNING_KEY_PUB`).
- **Mitigação de prompt injection**: conteúdo importado nasce em quarentena
  (não-publicado), com scan de instruções/credenciais/PII e extração de
  PDF/DOCX/planilhas em subprocesso isolado (rlimits + timeout).
- **Dupla aprovação** para promoção entre scopes (2 admins distintos) e **RBAC**
  por papéis: `approver` só aprova/rejeita; `admin` escreve no conhecimento
  (escritas exigem papel `admin`).

### Removido

- Scripts de teste legados com caminho hardcoded (`test_brain_native.py`,
  `test_brain_exaustivo.py`).
- Referências a `celebro` (código e documentação).
- Subdiretório `experts/` do layout.
- `main()`/`cmd_*` duplicados de `brain_tool.py`.

---

> **Design de governança**: o modelo de checkpoints assinados (fases, modelo de
> dados, fluxo e decisões aprovadas) está em
> [`plan/checkpoints-assinados.md`](plan/checkpoints-assinados.md).
