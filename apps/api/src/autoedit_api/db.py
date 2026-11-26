"""Database models and session handling (SQLAlchemy 2.0). Postgres in production, SQLite for tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .settings import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, datetime: DateTime(timezone=True)}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    plan: Mapped[str] = mapped_column(String(16), default="free")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("upl"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # uploading -> complete | failed
    status: Mapped[str] = mapped_column(String(16), default="uploading")
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(512))
    media: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column()

    @property
    def duration_s(self) -> float | None:
        return float(self.media["duration_s"]) if self.media else None


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("prj"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(16))  # edit | motion
    title: Mapped[str] = mapped_column(String(200), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    clips: Mapped[list[ProjectClip]] = relationship(
        back_populates="project", order_by="ProjectClip.position", cascade="all, delete-orphan"
    )


class ProjectClip(Base):
    __tablename__ = "project_clips"
    __table_args__ = (Index("ix_project_clips_project_position", "project_id", "position"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("clp"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id", ondelete="RESTRICT"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    duration_s: Mapped[float] = mapped_column(Float)
    # queued -> processing -> ready | failed
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str | None] = mapped_column(String(32))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="clips")
    upload: Mapped[Upload] = relationship()
    artifacts: Mapped[list[AnalysisArtifact]] = relationship(
        back_populates="clip", cascade="all, delete-orphan"
    )


class AnalysisArtifact(Base):
    """Per-clip analysis outputs (proxy, signals; later transcript, timeline) and project-level story."""

    __tablename__ = "analysis_artifacts"
    __table_args__ = (UniqueConstraint("clip_id", "kind", "version", name="uq_artifact_clip_kind_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("art"))
    clip_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_clips.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # proxy | signals | transcript | timeline | story
    version: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    clip: Mapped[ProjectClip | None] = relationship(back_populates="artifacts")


class UsageEvent(Base):
    """Per-stage cost and timing (PRD 5.8). AI stages will add tokens and dollars here."""

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("use"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), index=True)
    clip_id: Mapped[str | None] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(32))
    wall_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    media_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ------------------------------------------------------------------------------------- sessions


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def session_scope() -> Session:
    return get_sessionmaker()()


def get_db() -> Iterator[Session]:
    db = session_scope()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    """Create tables directly (tests / SQLite dev). Production uses Alembic migrations."""
    Base.metadata.create_all(get_engine())
