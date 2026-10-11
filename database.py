from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:
    pass
_DEFAULT_DB_DIR = BASE_DIR / "data"
_DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or f"sqlite:///{_DEFAULT_DB_DIR / 'fourth_scientific.db'}"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]

_engine_args: dict = {"pool_pre_ping": True, "future": True}
if DATABASE_URL.startswith("sqlite:"):
    _engine_args["connect_args"] = {"check_same_thread": False, "timeout": 30}
engine = create_engine(DATABASE_URL, **_engine_args)
if DATABASE_URL.startswith("sqlite:"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    emoji: Mapped[str] = mapped_column(String(24), default="📚", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    files: Mapped[list["MaterialFile"]] = relationship(back_populates="subject", cascade="all, delete-orphan")


class MaterialFile(Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("kind IN ('book','note','summary','exam')", name="ck_files_kind"),
        CheckConstraint("file_type IN ('document','photo','video')", name="ck_files_type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    teacher: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    year: Mapped[str] = mapped_column(String(12), default="2026", nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), default="document", nullable=False)
    preview_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    downloads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    subject: Mapped[Subject] = relationship(back_populates="files")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class HandoutRequest(Base):
    __tablename__ = "handout_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    contact: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False, index=True)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


SUBJECTS = [
    ("رياضيات", "📐", 1),
    ("كيمياء", "🧪", 2),
    ("فيزياء", "⚡", 3),
    ("أحياء", "🧬", 4),
    ("English", "🇬🇧", 5),
    ("التربية الإسلامية", "📖", 6),
    ("الحاسوب", "💻", 7),
    ("الكردي", "🟩", 8),
    ("اللغة العربية", "📚", 9),
    ("جرائم حزب البعث", "⚖️", 10),
]

DEFAULT_WELCOME = "هلا بيك ببوت الرابع العلمي.\nافتح الموقع حتى تتصفح الكتب والملازم والملخصات والامتحانات."
DEFAULT_SETTINGS = {
    "welcome": DEFAULT_WELCOME,
    "subscription_enabled": "1",
    "mandatory_channel": os.getenv("MANDATORY_CHANNEL", "@Kii_8i"),
    "site_url": os.getenv("SITE_URL", ""),
}


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    Base.metadata.create_all(engine)
    with session_scope() as db:
        for key, value in DEFAULT_SETTINGS.items():
            if db.get(Setting, key) is None:
                db.add(Setting(key=key, value=value))
        for name, emoji, sort_order in SUBJECTS:
            existing = db.scalar(select(Subject).where(Subject.name == name))
            if existing is None:
                db.add(Subject(name=name, emoji=emoji, sort_order=sort_order, visible=True))


def get_setting(key: str, default: str = "") -> str:
    with session_scope() as db:
        item = db.get(Setting, key)
        return default if item is None else item.value


def set_setting(key: str, value: str) -> None:
    with session_scope() as db:
        item = db.get(Setting, key)
        if item is None:
            db.add(Setting(key=key, value=str(value)))
        else:
            item.value = str(value)
