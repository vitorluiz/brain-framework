"""Modelos ORM (SQLAlchemy) do brain.db — spec §3.2 / requirements §2.2.

A coluna `expert` é o eixo de multi-tenant: em SQLite cada expert/global tem um
arquivo próprio; em PostgreSQL todos compartilham um banco e são filtrados por
`expert`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_VERSION = "1.0.0"


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    version: Mapped[str] = mapped_column(String, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    expert: Mapped[str] = mapped_column(String, nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String, nullable=False)
    titulo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corpo: Mapped[str] = mapped_column(Text, nullable=False)
    hash_canonical: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow()
    )


class KnowledgeStaging(Base):
    __tablename__ = "knowledge_staging"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    expert: Mapped[str] = mapped_column(String, nullable=False, index=True)
    chunk_data: Mapped[str] = mapped_column(Text, nullable=False)
    hash_canonical: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    expert: Mapped[str] = mapped_column(String, nullable=False, index=True)
    command: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="enqueued")
    # "metadata" é atributo reservado do declarative Base — mapear via nome de coluna.
    job_metadata: Mapped[Optional[str]] = mapped_column("metadata", Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# --- Checkpoints assinados (governança) — plan/checkpoints-assinados.md §4 ---

class KnowledgeObject(Base):
    """Conteúdo imutável endereçado por hash (content-addressed)."""
    __tablename__ = "knowledge_objects"

    hash: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())


class Commit(Base):
    """Checkpoint assinado (nó do grafo de commits)."""
    __tablename__ = "commits"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # commit_hash
    scope: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parent_hashes: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    tree_hash: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str] = mapped_column(String, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    validation_results: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # String ISO (não DateTime) — é a exata string que entra no commit_hash.
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String, nullable=False)


class CommitItem(Base):
    """Conhecimentos incluídos/removidos por um commit."""
    __tablename__ = "commit_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commit_id: Mapped[str] = mapped_column(String, ForeignKey("commits.id"), index=True)
    op: Mapped[str] = mapped_column(String, nullable=False)  # add | remove
    object_hash: Mapped[str] = mapped_column(String, ForeignKey("knowledge_objects.hash"))
    tipo: Mapped[str] = mapped_column(String, nullable=False)
    titulo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Ref(Base):
    """Ponteiro (ex.: expert/maria/main, global/main)."""
    __tablename__ = "refs"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    commit_id: Mapped[str] = mapped_column(String, ForeignKey("commits.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.utcnow(), onupdate=lambda: datetime.utcnow()
    )


class Approval(Base):
    """Aprovações/rejeições e justificativas."""
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String, index=True)
    candidate_commit_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approver: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)  # approve | reject
    policy: Mapped[str] = mapped_column(String, nullable=False)
    justification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())


class AuditEvent(Base):
    """Log de operações (encadeado por hash — verificação em fases futuras)."""
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prev_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
