"""Camada de persistência SQLAlchemy.

Backend local (SQLite — um arquivo por expert/global) ou compartilhado
(PostgreSQL) quando `DATABASE_URL`/`BRAIN_DATABASE_URL` está definido. O backend
é transparente para as funções de domínio: elas recebem uma `Session` e usam o
ORM.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, SCHEMA_VERSION

_EXPERT_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# Nomes que não podem ser usados como expert (são escopos/dir especiais da raiz).
_RESERVED_SCOPES = {"global", "backups"}

SQLITE_TIMEOUT_SECONDS = 5.0
SQLITE_BUSY_TIMEOUT_MS = 5_000

# Colunas que precisam ser adicionadas a brain.db legados (migração idempotente).
_MIGRATION_COLUMNS = {
    "pages": [
        ("expert", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("hash_canonical", "TEXT"),
        ("tipo", "TEXT NOT NULL DEFAULT 'memory'"),
        ("titulo", "TEXT"),
        ("corpo", "TEXT NOT NULL DEFAULT ''"),
        ("created_at", "TIMESTAMP"),
        ("updated_at", "TIMESTAMP"),
    ],
    "knowledge_staging": [
        ("expert", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("hash_canonical", "TEXT"),
        ("pipeline_version", "TEXT NOT NULL DEFAULT '1'"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("chunk_data", "TEXT NOT NULL DEFAULT ''"),
        ("created_at", "TIMESTAMP"),
        ("processed_at", "TIMESTAMP"),
    ],
    "jobs": [
        ("expert", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("status", "TEXT DEFAULT 'enqueued'"),
        ("command", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("metadata", "TEXT"),
        ("created_at", "TIMESTAMP"),
        ("started_at", "TIMESTAMP"),
        ("completed_at", "TIMESTAMP"),
        ("error", "TEXT"),
    ],
}


def get_brain_root() -> Path:
    """Raiz compartilhada dos brains (BRAIN_ROOT ou ~/.hermes/brain)."""
    configured = os.environ.get("BRAIN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".hermes" / "brain").resolve()


def validate_expert_identifier(expert: str) -> str:
    if (
        not isinstance(expert, str)
        or not _EXPERT_IDENTIFIER.fullmatch(expert)
        or expert.lower() in _RESERVED_SCOPES
    ):
        raise ValueError(
            "Invalid expert identifier; use 1-64 ASCII letters, digits, '_' or '-'"
        )
    return expert


def get_brain_db_path(expert=None, brain_path=None, global_brain=False) -> str:
    """Caminho físico do brain.db (SQLite) — usado para exibição e como default."""
    if expert is not None and not global_brain:
        expert = validate_expert_identifier(expert)
    if brain_path:
        return os.fspath(Path(brain_path).expanduser())
    if not global_brain and not expert:
        return os.path.join(os.getcwd(), "brain.db")

    root = get_brain_root()
    if global_brain:
        candidate = (root / "global" / "brain.db").resolve()
    else:
        candidate = (root / str(expert) / "brain.db").resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Brain database path escapes the configured root")
    return os.fspath(candidate)


def list_expert_names() -> list[str]:
    """Nomes dos experts — uma pasta por expert, direto na raiz do brain.

    Espelha o layout `~/.hermes/profiles/<nome>/` do Hermes (sem o antigo
    subdiretório `experts/`). Ignora escopos reservados (`global`, `backups`)
    e dotfiles (`.uploads`, `.dashboard_*` etc.).
    """
    root = get_brain_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name not in _RESERVED_SCOPES
    )


def _shared_database_url() -> Optional[str]:
    return os.environ.get("DATABASE_URL") or os.environ.get("BRAIN_DATABASE_URL")


def get_database_url(expert=None, brain_path=None, global_brain=False) -> str:
    """URL SQLAlchemy do scope.

    Se um banco compartilhado estiver configurado, todos os experts o usam
    (filtrados por `expert`). Caso contrário, cada scope tem seu SQLite local.
    """
    shared = _shared_database_url()
    if shared:
        return shared
    return "sqlite:///" + get_brain_db_path(
        expert=expert, brain_path=brain_path, global_brain=global_brain
    )


# --- permissões privadas (SQLite local) -------------------------------------

def _set_private_mode(path, mode: int) -> None:
    if os.name == "posix" and os.path.exists(path):
        os.chmod(path, mode)


def _ensure_private_directory(directory: Path) -> None:
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for p in reversed(missing):
        p.mkdir(mode=0o700, exist_ok=True)
    for p in missing:
        _set_private_mode(p, 0o700)


def _ensure_private_database_file(db_path: Path) -> None:
    if not db_path.exists():
        try:
            fd = os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    _set_private_mode(db_path, 0o600)


def _secure_sqlite_files(db_path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        _set_private_mode(Path(f"{db_path}{suffix}"), 0o600)


def _prepare_sqlite_storage(database: str) -> None:
    if database in (":memory:", ""):
        return
    path = Path(database)
    root = get_brain_root()
    resolved = path.resolve()
    if resolved.is_relative_to(root):
        _ensure_private_directory(root)
        _set_private_mode(root, 0o700)
    _ensure_private_directory(path.parent)
    _set_private_mode(path.parent, 0o700)
    _ensure_private_database_file(path)


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()


# --- engine / session -------------------------------------------------------

_engines: dict[str, Engine] = {}
_initialized: set[int] = set()


def _create_engine(database_url: str) -> Engine:
    backend = database_url.split(":", 1)[0]
    if backend == "sqlite":
        from sqlalchemy.engine import make_url

        database = make_url(database_url).database
        _prepare_sqlite_storage(database or "")
        engine = create_engine(
            database_url,
            connect_args={"timeout": SQLITE_TIMEOUT_SECONDS},
            future=True,
        )
        _configure_sqlite(engine)
    else:
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
    return engine


def get_engine(database_url: str) -> Engine:
    if database_url not in _engines:
        _engines[database_url] = _create_engine(database_url)
    return _engines[database_url]


def dispose_engine_for_path(db_path: str) -> None:
    """Descarta engines SQLite que apontam para `db_path` (restore/backup).

    Depois de sobrescrever um brain.db no disco, qualquer engine em cache
    (pool de conexões) ainda pode ler estado antigo (WAL). Descarta esses
    engines para que a próxima sessão reabra um estado limpo.
    """
    from sqlalchemy.engine import make_url

    target = os.path.abspath(os.fspath(Path(db_path).expanduser().resolve()))
    for url, engine in list(_engines.items()):
        if not url.startswith("sqlite:///"):
            continue
        db = make_url(url).database
        if db and os.path.abspath(db) == target:
            _engines.pop(url, None)
            engine.dispose()


def initialize_schema(engine: Engine) -> None:
    """Cria tabelas (se ausentes), migra brain.db legados e grava a versão."""
    if id(engine) in _initialized:
        return
    Base.metadata.create_all(engine)
    _apply_column_migrations(engine)
    _ensure_staging_unique_index(engine)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1")
        ).fetchone()
        current = row[0] if row else "0.0.0"
        if current < SCHEMA_VERSION:
            conn.execute(
                text(
                    "INSERT INTO schema_version (version, description, applied_at) "
                    "VALUES (:v, :d, CURRENT_TIMESTAMP)"
                ),
                {"v": SCHEMA_VERSION, "d": f"Migration for {SCHEMA_VERSION}"},
            )
    _initialized.add(id(engine))


def _apply_column_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _MIGRATION_COLUMNS.items():
            if table not in existing_tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _ensure_staging_unique_index(engine: Engine) -> None:
    """Hardening Celery/Redis: impede duplicação de staging por retry.

    Garante o índice único `(expert, hash_canonical, pipeline_version)`.
    Em bases legadas, remove duplicatas antigas (mantém o menor id) antes de
    criar o índice para evitar falha em ambientes com dados já repetidos.
    """
    with engine.begin() as conn:
        conn.execute(text(
            """
            DELETE FROM knowledge_staging
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM knowledge_staging
                GROUP BY expert, hash_canonical, COALESCE(pipeline_version, '1')
            )
            """
        ))
        conn.execute(text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_staging_expert_hash_pipeline
            ON knowledge_staging (expert, hash_canonical, pipeline_version)
            """
        ))


def get_session_from_url(database_url: str) -> Session:
    """Abre uma Session para uma URL explícita (usada pelo worker Celery)."""
    engine = get_engine(database_url)
    initialize_schema(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    # Eager: força abertura da conexão para disparar os PRAGMAs (SQLite) e,
    # em seguida, re-aplica permissões privadas nos arquivos (db + sidecars).
    session.connection()
    if database_url.startswith("sqlite:///"):
        from sqlalchemy.engine import make_url

        db_file = make_url(database_url).database
        if db_file not in (":memory:", ""):
            _secure_sqlite_files(Path(db_file))
    return session


def get_session(expert=None, global_brain=False, brain_path=None) -> Session:
    return get_session_from_url(
        get_database_url(expert=expert, global_brain=global_brain, brain_path=brain_path)
    )


def get_db_connection(db_path) -> Session:
    """Compat com a API anterior: recebe caminho (ou URL) e devolve uma Session."""
    if db_path == ":memory:":
        return _memory_session()
    return get_session(brain_path=db_path)


def _memory_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    initialize_schema(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()
