"""Modelos ORM (SQLAlchemy) do brain.db — spec §3.2 / requirements §2.2.

A coluna `expert` é o eixo de multi-tenant: em SQLite cada expert/global tem um
arquivo próprio; em PostgreSQL todos compartilham um banco e são filtrados por
`expert`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
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
