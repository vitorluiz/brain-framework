# Brain Framework — Native Hermes Tool MVP Implementation Plan

> **For Hermes:** implement this plan with strict RED→GREEN cycles and independent review. Do not commit or push without Vitor's explicit authorization.

**Goal:** Turn the repository into an installable, profile-scoped native Hermes plugin that exposes one safe `brain` tool for durable non-sensitive knowledge via SQLite.

**Architecture:** Keep SQLite/domain behavior in the installable `brain_tool` Python package and add a thin native Hermes adapter at the repository root. The adapter registers one unified tool through `PluginContext.register_tool`; it captures the active Hermes profile and does not expose arbitrary filesystem paths or profile-management operations. Persistence defaults to `$HERMES_HOME/brain` (or explicit `BRAIN_ROOT` for tests/operators), so named profiles do not silently share databases.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, setuptools, pytest, Hermes native plugin contract (`plugin.yaml`, `register(ctx)`).

---

## Baseline findings

1. `hermes plugins doctor . --ci` fails because there is no plugin manifest or `register(ctx)` entry point.
2. `pip install .` fails because `build-backend = "setuptools.backends._legacy:build"` is invalid in the tested environment.
3. Declared console scripts target missing modules (`brain_tool.cli`, `celebro.cli`); neither `brain`, `brain-tool`, nor `celebro` is installed.
4. Direct scripts work partially, but both root test scripts hard-code `/home/hermes/softwares/brain-framework/...` and break pytest collection.
5. `brain add profile` calls the real `hermes profile create` command. This is deliberately excluded from the native tool because a sandbox database path does not sandbox that side effect.
6. The implementation currently stores an expert at `<BRAIN_ROOT>/<expert>/brain.db`; specification/tests also mention `<BRAIN_ROOT>/experts/<expert>/brain.db`. Wave 1 will preserve the current runtime layout and document it consistently; a data-layout migration is a separate wave.
7. The import-time default ignores named-profile isolation (`~/.hermes/brain`) unless `BRAIN_ROOT` is explicitly set.
8. SQLite files are created with ambient umask and without an explicit private-permission guarantee.
9. There is no real `LICENSE` file despite MIT claims in README/package metadata.
10. The legacy manager is not safe to publish as the default CLI yet: it creates a real Hermes profile without rollback, edits `.bashrc` using a hard-coded venv path, deletes a brain database without confirmation while not deleting the corresponding Hermes profile, runs `git pull origin main`, copies live SQLite files instead of using the backup API, and recursively ingests files without size/symlink boundaries.

## Wave 1 boundaries

### In scope

- Native Hermes plugin manifest and tool registration.
- One tool named `brain` with actions `remember`, `recall`, and `check`.
- Expert scope and optional profile-local global scope.
- Dynamic `$BRAIN_ROOT` / `$HERMES_HOME/brain` resolution.
- Strict expert-name, action, type, limit, and content-size validation.
- Private directory/database permissions (`0700`/`0600`) on supported POSIX systems.
- Package build and working `brain` / `brain-tool` entry points.
- Real pytest suite using only temporary directories and fake plugin context.
- Documentation and MIT license.
- A non-sensitive AlentoSoft dogfood pilot after all gates pass.

### Explicitly out of scope

- Creating, deleting, renaming, or configuring Hermes profiles.
- Arbitrary file ingestion (`learn`) through the agent tool.
- Destructive `forget`/`consolidate` through the agent tool.
- Shared cross-profile global database.
- Celery, Redis, dashboard, WhatsApp commands, embeddings, vector search, LLM extraction, graph relations, backups, or remote synchronization.
- Commit, push, release, PyPI publication, or AlentoSoft runtime changes.

## Tool contract

`brain` receives:

- `action`: required enum `remember | recall | check`.
- `expert`: optional safe identifier; defaults to `ctx.profile_name`.
- `global_scope`: optional boolean; global remains local to the active Hermes profile.
- `tipo`: `memory | fact | entity | procedure | policy | system` for remember.
- `title`: optional short title.
- `content`: required for remember; bounded, non-empty text.
- `search`: optional text for recall.
- `limit`: integer 1–50.

It returns JSON text with a stable top-level object:

- success: `{"ok": true, "action": ..., ...}`
- validation/runtime failure: `{"ok": false, "action": ..., "error": {"code": ..., "message": ...}}`

The adapter exposes no `brain_path`, shell command, or profile-management argument.

---

### Task 1: Create a real test harness and prove current failures

**Objective:** Replace accidental root-script collection with isolated pytest tests that demonstrate missing package/plugin contracts.

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_paths.py`
- Create: `tests/test_plugin.py`
- Create: `tests/test_packaging.py`
- Modify: `pyproject.toml`

**RED steps:**
1. Test root selection precedence: `BRAIN_ROOT` → `HERMES_HOME/brain` → `~/.hermes/brain`.
2. Test invalid expert identifiers (`../x`, absolute paths, separators, empty) are rejected before filesystem access.
3. Test a fake `PluginContext` receives exactly one tool named `brain`, toolset `brain`, with matching schema name.
4. Test handler actions use a `tmp_path` brain root and never call `hermes profile create`.
5. Test package metadata declares working `brain` and `brain-tool` entry points.
6. Run each focused test and confirm failure for the missing behavior rather than syntax/import errors.

### Task 2: Make path resolution and SQLite storage profile-safe

**Objective:** Provide dynamic, validated, private storage for the native tool while preserving existing CLI behavior.

**Files:**
- Modify: `src/brain_tool/brain_tool.py`
- Modify: `src/brain_tool/brain.py`
- Test: `tests/test_paths.py`

**GREEN steps:**
1. Add `get_brain_root()` evaluated at call time.
2. Resolve default root from `BRAIN_ROOT`, then `HERMES_HOME/brain`, then `~/.hermes/brain`.
3. Add a shared expert identifier validator and use it before path construction.
4. Ensure resolved DB paths remain beneath the chosen root.
5. Create parent directories with private POSIX permissions and force DB/sidecar permissions private after connection.
6. Use a finite SQLite timeout/busy timeout; do not add async workers.
7. Fix package-relative imports in `brain.py` while retaining direct-script compatibility.
8. Re-run focused tests, then all tests.

### Task 3: Add the native Hermes plugin adapter

**Objective:** Register and execute one unified safe tool through the public Hermes plugin API.

**Files:**
- Create: `plugin.yaml`
- Create: `__init__.py`
- Create: `src/__init__.py`
- Create: `src/brain_tool/hermes_plugin.py`
- Test: `tests/test_plugin.py`

**RED→GREEN slices:**
1. Registration: fake context records one `brain` tool.
2. Remember: writes one non-sensitive synthetic fact into `tmp_path` and returns its ID/hash.
3. Recall: retrieves the remembered fact and respects `limit`.
4. Check: runs SQLite integrity/schema checks without arbitrary path access.
5. Isolation: two experts and global scope cannot leak into one another.
6. Validation: unknown action, missing/oversized content, invalid type/name/limit return stable errors without tracebacks.
7. Idempotency is not promised in Wave 1; repeated remember calls may produce multiple rows and must be documented honestly.

### Task 4: Repair package installation and CLI entry points

**Objective:** Produce an installable wheel and two working commands.

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_packaging.py`

**RED→GREEN steps:**
1. Replace the invalid build backend with `setuptools.build_meta` and explicit build requirements.
2. Map both `brain` and `brain-tool` to the SQL core `brain_tool.brain_tool:main`; do **not** expose the legacy profile manager as a public entry point in Wave 1.
3. Keep profile-management code importable only as legacy/experimental code until its subprocess, rollback, confirmation, and sandboxing contracts are redesigned.
4. Remove the broken `celebro.cli` entry point; mention compatibility status in release notes/docs.
5. Build/install from a clean temporary clone and prove both `--help` commands return zero.
6. Verify `python -m pytest -q` discovers only the maintained tests.

### Task 5: Align documentation and open-source metadata

**Objective:** Make installation, security boundaries, and current limitations truthful.

**Files:**
- Create: `LICENSE`
- Modify: `README.md`
- Modify: `doc/quickstart.md`
- Modify: `doc/commands.md`
- Modify: `plan/spec.md` only where it contradicts implemented Wave 1 behavior

**Steps:**
1. Document `hermes plugins install vitorluiz/brain-framework --enable` as the published path, with `hermes plugins doctor` as validation.
2. Document that local uncommitted development is validated with `hermes plugins doctor . --ci`; publication requires an authorized commit/push.
3. Clearly distinguish native agent tool operations from administrative CLI operations.
4. State that databases are profile-local by default and must not contain credentials, tokens, patient data, or other secrets.
5. State unsupported features honestly: embeddings, graph relations, cross-profile global, restore, async queue, dashboard.
6. Add the standard MIT license matching existing metadata.

### Task 6: Verification and independent review

**Objective:** Prove the artifact works and introduces no obvious security regression.

**Commands / gates:**

1. `python -m pytest -q` → all maintained tests pass, no collection errors/warnings.
2. Clean temporary venv: `pip install .` → exit 0.
3. Installed `brain --help` and `brain-tool --help` → exit 0 and neither help surface advertises Hermes profile creation/deletion.
4. `hermes plugins doctor . --ci` → exit 0 and exactly one registered tool.
5. Direct fake-context invocation: remember → recall → check in `TemporaryDirectory` → all `ok=true`.
6. Verify no real Hermes profile, alias, config, or default brain DB was created during tests.
7. `git diff --check` → exit 0.
8. Static scan added lines for secrets, shell execution, unsafe deserialization, SQL interpolation, path traversal, and unbounded file reads.
9. Independent reviewer receives exact diff and gates; any security or logic error blocks dogfood.

### Task 7: Non-sensitive AlentoSoft dogfood pilot

**Objective:** Use the validated handler on this development without adding Brain Framework to the AlentoSoft runtime.

**Preconditions:** Tasks 1–6 pass. Database stays under `/home/vitorluiz/.hermes/profiles/shielddev/brain`; no real patient, credential, fiscal XML, preserved-source content, or transient task progress is stored.

**Pilot records:**
1. Stable governance fact: canonical documentation precedence.
2. Stable boundary: `imports/juridico-mvp/` is reference-only, never runtime.
3. Stable domain fact: Tenant is contractual root; Organization is organizational.
4. Each record includes its source path in title/content until provenance columns are designed in a later migration.

**Acceptance:**
1. `remember` reports three successful writes.
2. `recall` queries (`fonte da verdade`, `juridico runtime`, `Tenant Organization`) retrieve the expected records only.
3. `check` reports SQLite integrity `ok` and schema version.
4. File location and permissions are verified without printing the database contents wholesale.
5. A dogfood report records usability friction, missing provenance/namespace fields, retrieval quality, and next-wave recommendations.

## Risks and trade-offs

- **Legacy layout:** preserving `<root>/brain.db` avoids silent migration now but leaves a docs/design choice for Wave 2.
- **LIKE retrieval:** adequate for a tracer bullet, not semantic search; assess through dogfood before adding FTS5/embeddings.
- **No provenance columns:** source is encoded in record text for the pilot only; Wave 2 should add structured provenance and project namespace through a versioned migration.
- **No automatic secret scanner:** Wave 1 relies on strict operating policy and non-sensitive pilot data. Before broad release, add tested secret rejection/redaction and import threat modeling.
- **SQLite concurrency:** timeout/busy handling reduces lock failures but does not replace transactional concurrency tests.
- **Administrative CLI:** profile management remains potentially destructive and must be redesigned separately with explicit dry-run/confirmation and sandboxable adapters.

## Wave 2 candidate backlog (not authorized by this plan)

- Structured `project_namespace`, `source_uri`, `confidence`, `expires_at`, and metadata.
- Idempotent upsert and version history/audit trail.
- FTS5 retrieval and ranking benchmark before embeddings.
- Entities/relations graph only if dogfood proves a real query need.
- Export/restore with manifest and integrity verification.
- Explicit opt-in cross-profile sharing with ACLs.
- Secret/PII detection, retention, deletion, and encrypted backup strategy.
- Safe administrative profile adapter with dependency injection and full rollback tests.
- Transactionally consistent SQLite backup/restore, immutable update verification, bounded ingestion, and explicit confirmation for every destructive legacy CLI operation.
