from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from . import database as dbmod
from .database import (
    Delivery, HandoutRequest, MaterialFile, Setting, Subject, User,
    get_setting, init_db, session_scope, set_setting,
)
try:
    from . import bot as botmod
except ModuleNotFoundError as import_error:
    if import_error.name not in {"telegram", "telegram.error", "telegram.ext"}:
        raise
    # Let the website/API start in a reduced local environment without the optional
    # Telegram dependency. The production requirements install the full bot package.
    class _BotModuleUnavailable:
        @staticmethod
        def get_telegram_application():
            return None
        @staticmethod
        def set_telegram_application(_application):
            return None
        @staticmethod
        def build_application(_token):
            raise RuntimeError("python-telegram-bot is not installed; install requirements.txt")
    botmod = _BotModuleUnavailable()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("fourth_scientific")
BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
BOT_USERNAME = os.getenv("BOT_USERNAME", "YIFiabot").lstrip("@")
OWNER_ID = int(os.getenv("OWNER_ID", "7683201905"))
KINDS: dict[str, str] = {"book": "الكتب", "note": "الملازم", "summary": "الملخصات", "exam": "الامتحانات"}
CATEGORY_TO_KIND = {"books": "book", "notes": "note", "summaries": "summary", "exams": "exam"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.telegram_app = None
    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        telegram_app = None
        initialized = False
        try:
            telegram_app = botmod.build_application(token)
            await telegram_app.initialize()
            initialized = True
            if telegram_app.updater is None:
                raise RuntimeError("Telegram polling updater was not created")
            await telegram_app.updater.start_polling(allowed_updates=botmod.Update.ALL_TYPES)
            await telegram_app.start()
            botmod.set_telegram_application(telegram_app)
            app.state.telegram_app = telegram_app
            log.info("Telegram bot polling started for @%s", BOT_USERNAME)
        except Exception:
            log.exception("Telegram bot failed to start; website API remains available")
            if telegram_app is not None:
                try:
                    if telegram_app.updater and telegram_app.updater.running:
                        await telegram_app.updater.stop()
                    if telegram_app.running:
                        await telegram_app.stop()
                    if initialized:
                        await telegram_app.shutdown()
                except Exception:
                    log.exception("Could not clean up a partially-started Telegram bot")
            app.state.telegram_app = None
            botmod.set_telegram_application(None)
    else:
        log.warning("BOT_TOKEN is not set; website is running without Telegram delivery")
    try:
        yield
    finally:
        telegram_app = app.state.telegram_app
        if telegram_app is not None:
            try:
                if telegram_app.updater and telegram_app.updater.running:
                    await telegram_app.updater.stop()
                if telegram_app.running:
                    await telegram_app.stop()
                await telegram_app.shutdown()
            except Exception:
                log.exception("Error while shutting down Telegram bot")
        botmod.set_telegram_application(None)


app = FastAPI(
    title="الرابع العلمي API",
    description="واجهة الموقع وبوت تيليجرام وقاعدة بياناتهما المشتركة",
    version="1.0.0",
    lifespan=lifespan,
)


# ----------------------------
# Public response serializers
# ----------------------------
def subject_json(subject: Subject) -> dict:
    return {
        "id": subject.id,
        "name": subject.name,
        "emoji": subject.emoji,
        "sort_order": subject.sort_order,
    }


def file_json(item: MaterialFile) -> dict:
    subject = item.subject
    return {
        "id": item.id,
        "subject_id": item.subject_id,
        "subject": subject.name if subject else "",
        "subject_emoji": subject.emoji if subject else "📚",
        "kind": item.kind,
        "kind_label": KINDS.get(item.kind, item.kind),
        "title": item.title,
        "teacher": item.teacher or "",
        "description": item.description or "",
        "year": item.year or "2026",
        "file_type": item.file_type,
        "visible": item.visible,
        "downloads": item.downloads,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "has_preview": bool(item.preview_file_id),
        "preview_url": f"/api/files/{item.id}/preview" if item.preview_file_id else None,
        "download_url": f"https://t.me/{BOT_USERNAME}?start=file_{item.id}",
    }


def require_admin(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> bool:
    expected = os.getenv("ADMIN_API_KEY", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="لوحة الإدارة غير مفعلة بعد. عيّن المتغير ADMIN_API_KEY في إعدادات الاستضافة.",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="مفتاح الإدارة غير صحيح.")
    return True


# ----------------------------
# Request models
# ----------------------------
class HandoutRequestIn(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    details: str = Field(min_length=4, max_length=2000)
    contact: str = Field(default="", max_length=180)

    @field_validator("subject", "details", "contact")
    @classmethod
    def strip_strings(cls, value: str) -> str:
        return value.strip()


class FileCreateIn(BaseModel):
    subject_id: int
    kind: Literal["book", "note", "summary", "exam"]
    title: str = Field(min_length=1, max_length=180)
    teacher: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=5000)
    year: str = Field(default="2026", max_length=12)
    telegram_file_id: str = Field(min_length=5, max_length=4096)
    file_type: Literal["document", "photo", "video"] = "document"
    preview_file_id: str | None = Field(default=None, max_length=4096)
    visible: bool = True

    @field_validator("title", "teacher", "description", "year", "telegram_file_id")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("preview_file_id")
    @classmethod
    def strip_preview(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class FileUpdateIn(BaseModel):
    subject_id: int | None = None
    kind: Literal["book", "note", "summary", "exam"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=180)
    teacher: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    year: str | None = Field(default=None, max_length=12)
    telegram_file_id: str | None = Field(default=None, min_length=5, max_length=4096)
    file_type: Literal["document", "photo", "video"] | None = None
    preview_file_id: str | None = Field(default=None, max_length=4096)
    visible: bool | None = None


class SubjectCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    emoji: str = Field(default="📚", max_length=24)
    visible: bool = True

    @field_validator("name", "emoji")
    @classmethod
    def strip_subject_text(cls, value: str) -> str:
        return value.strip()


class SubjectUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    emoji: str | None = Field(default=None, max_length=24)
    visible: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)


class SettingsUpdateIn(BaseModel):
    welcome: str | None = Field(default=None, max_length=2000)
    mandatory_channel: str | None = Field(default=None, max_length=120)
    site_url: str | None = Field(default=None, max_length=500)
    subscription_enabled: bool | None = None

    @field_validator("mandatory_channel")
    @classmethod
    def valid_channel(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not (value.startswith("@") or value.startswith("-100") or value.startswith("https://t.me/")):
            raise ValueError("استخدم معرف القناة مثل @Kii_8i أو رابط t.me")
        return value


# ----------------------------
# Public endpoints
# ----------------------------
@app.get("/health")
def health() -> dict:
    bot_ready = botmod.get_telegram_application() is not None
    return {"status": "ok", "database": "configured", "telegram_bot": "running" if bot_ready else "not_configured_or_stopped"}


@app.get("/api/meta")
def public_meta() -> dict:
    return {
        "site_name": "الرابع العلمي",
        "channel_url": "https://t.me/Kii_8i",
        "bot_username": BOT_USERNAME,
        "categories": [
            {"id": "book", "label": "الكتب", "plural": "books"},
            {"id": "note", "label": "الملازم", "plural": "notes"},
            {"id": "summary", "label": "الملخصات", "plural": "summaries"},
            {"id": "exam", "label": "الامتحانات", "plural": "exams"},
        ],
    }


@app.get("/api/subjects")
def public_subjects() -> list[dict]:
    with session_scope() as db:
        subjects = db.scalars(
            select(Subject).where(Subject.visible.is_(True)).order_by(Subject.sort_order, Subject.id)
        ).all()
        return [subject_json(subject) for subject in subjects]


@app.get("/api/files")
def public_files(
    kind: str | None = Query(default=None),
    category: str | None = Query(default=None),
    subject_id: int | None = Query(default=None, ge=1),
    q: str | None = Query(default=None, max_length=200),
    sort: Literal["latest", "popular", "title"] = "latest",
    limit: int = Query(default=60, ge=1, le=100),
) -> list[dict]:
    requested_kind = CATEGORY_TO_KIND.get(category or "", category) if category else kind
    if requested_kind and requested_kind not in KINDS:
        raise HTTPException(status_code=400, detail="قسم المحتوى غير معروف.")
    with session_scope() as db:
        stmt = (
            select(MaterialFile)
            .join(MaterialFile.subject)
            .options(joinedload(MaterialFile.subject))
            .where(MaterialFile.visible.is_(True), Subject.visible.is_(True))
        )
        if requested_kind:
            stmt = stmt.where(MaterialFile.kind == requested_kind)
        if subject_id is not None:
            stmt = stmt.where(MaterialFile.subject_id == subject_id)
        search = (q or "").strip()
        if search:
            needle = f"%{search}%"
            stmt = stmt.where(or_(
                MaterialFile.title.ilike(needle),
                MaterialFile.teacher.ilike(needle),
                MaterialFile.description.ilike(needle),
                Subject.name.ilike(needle),
            ))
        if sort == "popular":
            stmt = stmt.order_by(MaterialFile.downloads.desc(), MaterialFile.created_at.desc())
        elif sort == "title":
            stmt = stmt.order_by(MaterialFile.title.asc())
        else:
            stmt = stmt.order_by(MaterialFile.created_at.desc(), MaterialFile.id.desc())
        items = db.scalars(stmt.limit(limit)).unique().all()
        return [file_json(item) for item in items]


@app.get("/api/stats")
def public_stats() -> dict:
    with session_scope() as db:
        subject_count = db.scalar(select(func.count(Subject.id)).where(Subject.visible.is_(True))) or 0
        file_count = db.scalar(select(func.count(MaterialFile.id)).where(MaterialFile.visible.is_(True))) or 0
        download_count = db.scalar(select(func.coalesce(func.sum(MaterialFile.downloads), 0))) or 0
        return {"subjects": subject_count, "files": file_count, "downloads": int(download_count)}


@app.get("/api/files/{file_id}/preview")
async def preview_file(file_id: int) -> Response:
    with session_scope() as db:
        item = db.scalar(
            select(MaterialFile).where(MaterialFile.id == file_id, MaterialFile.visible.is_(True))
        )
        preview_id = item.preview_file_id if item else None
    if not preview_id:
        raise HTTPException(status_code=404, detail="ماكو صورة معاينة لهذا الامتحان.")
    tg_app = botmod.get_telegram_application()
    if tg_app is None:
        raise HTTPException(status_code=503, detail="معاينة الصور تحتاج تشغيل البوت على الخادم.")
    try:
        telegram_file = await tg_app.bot.get_file(preview_id)
        binary = await telegram_file.download_as_bytearray()
        file_path = (telegram_file.file_path or "").lower()
        media_type = "image/png" if file_path.endswith(".png") else ("image/webp" if file_path.endswith(".webp") else "image/jpeg")
        return Response(content=bytes(binary), media_type=media_type, headers={"Cache-Control": "public, max-age=300"})
    except Exception as exc:
        log.exception("Could not load preview for file id=%s", file_id)
        raise HTTPException(status_code=502, detail="تعذر تحميل صورة المعاينة.") from exc


@app.post("/api/requests", status_code=201)
async def create_handout_request(payload: HandoutRequestIn, background_tasks: BackgroundTasks) -> dict:
    with session_scope() as db:
        request_item = HandoutRequest(
            subject=payload.subject,
            details=payload.details,
            contact=payload.contact,
            status="new",
        )
        db.add(request_item)
        db.flush()
        request_id = request_item.id
    # A request remains safely saved even if Telegram notification is unavailable.
    async def notify_owner() -> None:
        tg_app = botmod.get_telegram_application()
        if tg_app is None:
            return
        message = (
            f"📨 طلب ملزمة جديد #{request_id}\n"
            f"المادة: {payload.subject}\n"
            f"التواصل: {payload.contact or 'لم يذكر'}\n"
            f"التفاصيل: {payload.details}"
        )
        try:
            await tg_app.bot.send_message(chat_id=OWNER_ID, text=message[:4000])
        except Exception:
            log.exception("Could not notify owner about request #%s", request_id)
    background_tasks.add_task(notify_owner)
    return {"ok": True, "id": request_id, "message": "وصل طلبك وانحفظ."}


# ----------------------------
# Admin endpoints
# ----------------------------
@app.get("/api/admin/dashboard")
def admin_dashboard(_: bool = Depends(require_admin)) -> dict:
    with session_scope() as db:
        subjects = db.scalars(select(Subject).order_by(Subject.sort_order, Subject.id)).all()
        files = db.scalars(
            select(MaterialFile).options(joinedload(MaterialFile.subject)).order_by(MaterialFile.created_at.desc(), MaterialFile.id.desc())
        ).unique().all()
        user_count = db.scalar(select(func.count(User.user_id))) or 0
        request_count = db.scalar(select(func.count(HandoutRequest.id)).where(HandoutRequest.status == "new")) or 0
        settings = {s.key: s.value for s in db.scalars(select(Setting)).all()}
        return {
            "subjects": [subject_json(s) | {"visible": s.visible} for s in subjects],
            "files": [file_json_admin(f) for f in files],
            "stats": {
                "subjects": len(subjects), "files": len(files),
                "visible_files": sum(1 for f in files if f.visible),
                "users": int(user_count), "requests": int(request_count),
                "downloads": int(sum(f.downloads for f in files)),
            },
            "settings": {
                "welcome": settings.get("welcome", dbmod.DEFAULT_WELCOME),
                "mandatory_channel": settings.get("mandatory_channel", "@Kii_8i"),
                "site_url": settings.get("site_url", ""),
                "subscription_enabled": settings.get("subscription_enabled", "1") == "1",
            },
        }


def file_json_admin(item: MaterialFile) -> dict:
    return file_json(item) | {
        "telegram_file_id": item.telegram_file_id,
        "preview_file_id": item.preview_file_id or "",
        "visible": item.visible,
    }


@app.post("/api/admin/files", status_code=201)
def admin_create_file(payload: FileCreateIn, _: bool = Depends(require_admin)) -> dict:
    with session_scope() as db:
        subject = db.get(Subject, payload.subject_id)
        if subject is None:
            raise HTTPException(status_code=404, detail="المادة غير موجودة.")
        item = MaterialFile(**payload.model_dump(), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        db.add(item)
        try:
            db.flush()
            db.refresh(item)
            return file_json_admin(item)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="معرّف تيليجرام هذا مستخدم لملف آخر.") from exc


@app.patch("/api/admin/files/{file_id}")
def admin_update_file(file_id: int, payload: FileUpdateIn, _: bool = Depends(require_admin)) -> dict:
    values = payload.model_dump(exclude_unset=True)
    required_fields = {"subject_id", "kind", "title", "telegram_file_id", "file_type", "visible"}
    if any(key in values and values[key] is None for key in required_fields):
        raise HTTPException(status_code=422, detail="لا يمكن جعل الحقول الأساسية فارغة.")
    with session_scope() as db:
        item = db.scalar(select(MaterialFile).options(joinedload(MaterialFile.subject)).where(MaterialFile.id == file_id))
        if item is None:
            raise HTTPException(status_code=404, detail="الملف غير موجود.")
        if "subject_id" in values and values["subject_id"] is not None and db.get(Subject, values["subject_id"]) is None:
            raise HTTPException(status_code=404, detail="المادة المحددة غير موجودة.")
        for key, value in values.items():
            if key in {"title", "teacher", "description", "year", "telegram_file_id", "file_type", "preview_file_id", "visible", "subject_id", "kind"}:
                setattr(item, key, value.strip() if isinstance(value, str) else value)
        item.updated_at = datetime.now(timezone.utc)
        try:
            db.flush()
            db.refresh(item)
            return file_json_admin(item)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="معرّف تيليجرام هذا مستخدم لملف آخر.") from exc


@app.delete("/api/admin/files/{file_id}")
def admin_delete_file(file_id: int, _: bool = Depends(require_admin)) -> dict:
    with session_scope() as db:
        item = db.get(MaterialFile, file_id)
        if item is None:
            raise HTTPException(status_code=404, detail="الملف غير موجود.")
        db.delete(item)
        return {"ok": True, "message": "تم حذف الملف."}


@app.post("/api/admin/subjects", status_code=201)
def admin_create_subject(payload: SubjectCreateIn, _: bool = Depends(require_admin)) -> dict:
    with session_scope() as db:
        if db.scalar(select(Subject.id).where(Subject.name == payload.name)) is not None:
            raise HTTPException(status_code=409, detail="هذه المادة موجودة مسبقاً.")
        sort_order = (db.scalar(select(func.max(Subject.sort_order))) or 0) + 1
        item = Subject(name=payload.name, emoji=payload.emoji or "📚", visible=payload.visible, sort_order=sort_order)
        db.add(item)
        db.flush()
        return subject_json(item) | {"visible": item.visible}


@app.patch("/api/admin/subjects/{subject_id}")
def admin_update_subject(subject_id: int, payload: SubjectUpdateIn, _: bool = Depends(require_admin)) -> dict:
    values = payload.model_dump(exclude_unset=True)
    with session_scope() as db:
        item = db.get(Subject, subject_id)
        if item is None:
            raise HTTPException(status_code=404, detail="المادة غير موجودة.")
        if "name" in values and values["name"]:
            item.name = values["name"].strip()
        if "emoji" in values and values["emoji"] is not None:
            item.emoji = values["emoji"].strip() or "📚"
        if "visible" in values and values["visible"] is not None:
            item.visible = values["visible"]
        if "sort_order" in values and values["sort_order"] is not None:
            item.sort_order = values["sort_order"]
        try:
            db.flush()
            return subject_json(item) | {"visible": item.visible}
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="اسم المادة مستخدم من قبل.") from exc


@app.delete("/api/admin/subjects/{subject_id}")
def admin_delete_subject(subject_id: int, _: bool = Depends(require_admin)) -> dict:
    with session_scope() as db:
        item = db.get(Subject, subject_id)
        if item is None:
            raise HTTPException(status_code=404, detail="المادة غير موجودة.")
        db.delete(item)  # relationship cascade removes its files in the same transaction
        return {"ok": True, "message": "تم حذف المادة والملفات التابعة لها."}


@app.patch("/api/admin/settings")
def admin_update_settings(payload: SettingsUpdateIn, _: bool = Depends(require_admin)) -> dict:
    values = payload.model_dump(exclude_unset=True)
    with session_scope() as db:
        for key, value in values.items():
            if value is None:
                continue
            setting_key = key
            stored_value = ("1" if value else "0") if key == "subscription_enabled" else str(value).strip()
            item = db.get(Setting, setting_key)
            if item is None:
                db.add(Setting(key=setting_key, value=stored_value))
            else:
                item.value = stored_value
    return {"ok": True, "message": "تم حفظ الإعدادات."}


@app.get("/api/admin/requests")
def admin_requests(_: bool = Depends(require_admin), limit: int = Query(default=50, ge=1, le=100)) -> list[dict]:
    with session_scope() as db:
        items = db.scalars(select(HandoutRequest).order_by(HandoutRequest.created_at.desc()).limit(limit)).all()
        return [{
            "id": item.id, "subject": item.subject, "details": item.details,
            "contact": item.contact, "status": item.status,
            "created_at": item.created_at.isoformat(),
        } for item in items]


@app.patch("/api/admin/requests/{request_id}")
def admin_update_request(request_id: int, payload: dict, _: bool = Depends(require_admin)) -> dict:
    next_status = payload.get("status")
    if next_status not in {"new", "done", "rejected"}:
        raise HTTPException(status_code=422, detail="حالة الطلب غير صحيحة.")
    with session_scope() as db:
        item = db.get(HandoutRequest, request_id)
        if item is None:
            raise HTTPException(status_code=404, detail="الطلب غير موجود.")
        item.status = next_status
        return {"ok": True, "status": item.status}


# Mount static frontend last so API routes keep precedence.
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
