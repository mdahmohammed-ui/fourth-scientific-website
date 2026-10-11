from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from .database import (
    Delivery, HandoutRequest, MaterialFile, Setting, Subject, User,
    DEFAULT_WELCOME, get_setting, session_scope, set_setting,
)

log = logging.getLogger("fourth_scientific.bot")
OWNER_ID = int(os.getenv("OWNER_ID", "7683201905"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "YIFiabot").lstrip("@")
MANDATORY_CHANNEL = os.getenv("MANDATORY_CHANNEL", "@Kii_8i")
KINDS = {"book": "كتاب", "note": "ملزمة", "summary": "ملخص", "exam": "امتحان"}
telegram_application: Application | None = None


def get_telegram_application() -> Application | None:
    return telegram_application


def set_telegram_application(application: Application | None) -> None:
    global telegram_application
    telegram_application = application


def is_owner(user_id: int | None) -> bool:
    return user_id == OWNER_ID


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def remember_user(user) -> None:
    if user is None:
        return
    with session_scope() as db:
        record = db.get(User, user.id)
        if record is None:
            db.add(User(
                user_id=user.id, username=user.username, first_name=user.first_name,
                joined_at=now_utc(), last_seen=now_utc(), blocked=False,
            ))
        else:
            record.username = user.username
            record.first_name = user.first_name
            record.last_seen = now_utc()
            record.blocked = False


def get_file(file_pk: int) -> MaterialFile | None:
    with session_scope() as db:
        return db.get(MaterialFile, file_pk)


def list_subjects(visible_only: bool = True) -> list[Subject]:
    with session_scope() as db:
        stmt = select(Subject).order_by(Subject.sort_order, Subject.id)
        if visible_only:
            stmt = stmt.where(Subject.visible.is_(True))
        return db.scalars(stmt).all()


def list_files(kind: str | None = None, limit: int = 30, offset: int = 0) -> list[dict[str, Any]]:
    with session_scope() as db:
        stmt = select(MaterialFile, Subject.name).join(Subject, Subject.id == MaterialFile.subject_id)
        if kind:
            stmt = stmt.where(MaterialFile.kind == kind)
        stmt = stmt.order_by(MaterialFile.id.desc()).offset(max(0, offset)).limit(limit)
        return [
            {"id": item.id, "title": item.title, "kind": item.kind, "visible": item.visible,
             "subject": subject_name}
            for item, subject_name in db.execute(stmt).all()
        ]


def update_user_blocked(user_id: int, blocked: bool = True) -> None:
    with session_scope() as db:
        record = db.get(User, user_id)
        if record:
            record.blocked = blocked


def bot_home_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton("فتح الموقع")]]
    if is_owner(user_id):
        rows[0].append(KeyboardButton("لوحة المالك"))
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def channel_url(channel: str) -> str:
    if channel.startswith("https://t.me/"):
        return channel
    if channel.startswith("@"):
        return "https://t.me/" + channel[1:]
    return "https://t.me/" + channel


def subscription_keyboard(file_pk: int | None = None) -> InlineKeyboardMarkup:
    channel = get_setting("mandatory_channel", MANDATORY_CHANNEL)
    callback = f"checksub:{file_pk or 0}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("الاشتراك بالقناة", url=channel_url(channel))],
        [InlineKeyboardButton("تحقق من الاشتراك", callback_data=callback)],
    ])


async def is_subscribed(bot, user_id: int) -> bool:
    if is_owner(user_id) or get_setting("subscription_enabled", "1") != "1":
        return True
    channel = get_setting("mandatory_channel", MANDATORY_CHANNEL)
    if channel.startswith("https://t.me/"):
        channel_name = channel.rstrip("/").split("/")[-1]
        if channel_name and not channel_name.startswith("+"):
            channel = "@" + channel_name
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        if member.status in {"member", "administrator", "creator"}:
            return True
        if member.status == "restricted":
            return bool(getattr(member, "is_member", False))
        return False
    except TelegramError:
        log.exception("Membership check failed for user %s", user_id)
        return False


async def ask_to_subscribe(message, file_pk: int | None = None) -> None:
    text = (
        "حتى تستلم الملفات، اشترك بقناة رابعيون أولاً، وبعدها اضغط «تحقق من الاشتراك»."
    )
    await message.reply_text(text, reply_markup=subscription_keyboard(file_pk))


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("إضافة ملف", callback_data="a:add")],
        [InlineKeyboardButton("تعديل ملف", callback_data="a:edit"), InlineKeyboardButton("استبدال ملف", callback_data="a:replace")],
        [InlineKeyboardButton("حذف ملف", callback_data="a:delete"), InlineKeyboardButton("إخفاء/إظهار ملف", callback_data="a:hide")],
        [InlineKeyboardButton("إدارة الملخصات", callback_data="a:summaries")],
        [InlineKeyboardButton("إضافة مادة", callback_data="a:addsubject"), InlineKeyboardButton("تعديل مادة", callback_data="a:editsubject")],
        [InlineKeyboardButton("حذف مادة", callback_data="a:delsubject")],
        [InlineKeyboardButton("طلبات الملازم", callback_data="a:requests")],
        [InlineKeyboardButton("الإحصائيات", callback_data="a:stats"), InlineKeyboardButton("إرسال إعلان", callback_data="a:broadcast")],
        [InlineKeyboardButton("إعدادات البوت", callback_data="a:settings")],
        [InlineKeyboardButton("إغلاق", callback_data="a:close")],
    ])


def summary_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("إضافة ملخص", callback_data="sm:add")],
        [InlineKeyboardButton("تعديل ملخص", callback_data="sm:edit"), InlineKeyboardButton("استبدال ملخص", callback_data="sm:replace")],
        [InlineKeyboardButton("حذف ملخص", callback_data="sm:delete"), InlineKeyboardButton("إخفاء/إظهار ملخص", callback_data="sm:hide")],
        [InlineKeyboardButton("عرض الملخصات", callback_data="sm:list")],
        [InlineKeyboardButton("رجوع", callback_data="admin")],
    ])


def set_state(context: ContextTypes.DEFAULT_TYPE, **values) -> None:
    context.user_data["admin_flow"] = values


def state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("admin_flow", {})


def clear_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("admin_flow", None)


def kind_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"addkind:{kind}")] for kind, label in KINDS.items()]
    rows.append([InlineKeyboardButton("رجوع", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def subject_keyboard(prefix: str, visible_only: bool = True) -> InlineKeyboardMarkup:
    subjects = list_subjects(visible_only=visible_only)
    buttons = [
        InlineKeyboardButton(f"{subject.emoji} {subject.name}"[:60], callback_data=f"{prefix}:{subject.id}")
        for subject in subjects
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("رجوع", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def file_picker(prefix: str, kind: str | None = None, page: int = 0) -> InlineKeyboardMarkup:
    per_page = 15
    with session_scope() as db:
        stmt = select(MaterialFile, Subject.name).join(Subject, Subject.id == MaterialFile.subject_id)
        count_stmt = select(func.count(MaterialFile.id))
        if kind:
            stmt = stmt.where(MaterialFile.kind == kind)
            count_stmt = count_stmt.where(MaterialFile.kind == kind)
        total = int(db.scalar(count_stmt) or 0)
        records = db.execute(stmt.order_by(MaterialFile.id.desc()).offset(max(0, page) * per_page).limit(per_page)).all()
        items = [{"id": item.id, "title": item.title, "kind": item.kind, "visible": item.visible, "subject": subject_name} for item, subject_name in records]
    rows = []
    for item in items:
        visible = "✓" if item["visible"] else "×"
        label = f"{visible} {item['subject']} | {KINDS.get(item['kind'], item['kind'])} | {item['title']}"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"{prefix}:{item['id']}")])
    if not rows:
        rows.append([InlineKeyboardButton("لا توجد ملفات بهذا النوع", callback_data="noop")])
    nav = []
    kind_key = kind or "all"
    if page > 0:
        nav.append(InlineKeyboardButton("السابق ←", callback_data=f"pagefiles:{prefix}:{kind_key}:{page-1}"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("التالي →", callback_data=f"pagefiles:{prefix}:{kind_key}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("رجوع", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


async def send_home(message, user_id: int) -> None:
    welcome = get_setting("welcome", DEFAULT_WELCOME)
    await message.reply_text(welcome, reply_markup=bot_home_keyboard(user_id))


async def deliver(message, context: ContextTypes.DEFAULT_TYPE, file_pk: int, user_id: int) -> None:
    item = get_file(file_pk)
    if item is None or not item.visible:
        await message.reply_text("هذا الملف غير متاح حالياً.")
        return
    if not await is_subscribed(context.bot, user_id):
        await ask_to_subscribe(message, file_pk)
        return
    caption = item.title
    if item.kind == "note":
        caption = (
            f"📙: {item.title}\n"
            f"👨‍🏫: {item.teacher or 'غير محدد'}\n"
            "🏷: للصف الرابع الاعدادي\n"
            f"📆: تاريخ الإصدار {item.year or '2026'}"
        )
    elif item.description:
        caption = f"{item.title}\n{item.description}"
    try:
        if item.file_type == "photo":
            await context.bot.send_photo(chat_id=message.chat_id, photo=item.telegram_file_id, caption=caption[:1024])
        elif item.file_type == "video":
            await context.bot.send_video(chat_id=message.chat_id, video=item.telegram_file_id, caption=caption[:1024])
        else:
            await context.bot.send_document(chat_id=message.chat_id, document=item.telegram_file_id, caption=caption[:1024])
        with session_scope() as db:
            stored = db.get(MaterialFile, file_pk)
            if stored:
                stored.downloads += 1
                db.add(Delivery(user_id=user_id, file_id=file_pk, delivered_at=now_utc()))
    except TelegramError:
        log.exception("Unable to deliver Telegram file id=%s", file_pk)
        await message.reply_text("تعذر إرسال الملف حالياً. جرّب مرة ثانية أو أبلغ المالك.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    remember_user(user)
    clear_state(context)
    payload = context.args[0] if context.args else ""
    if payload.startswith("file_"):
        try:
            file_pk = int(payload.removeprefix("file_"))
        except ValueError:
            file_pk = 0
        if file_pk <= 0:
            await message.reply_text("رابط الملف غير صحيح.", reply_markup=bot_home_keyboard(user.id))
            return
        item = get_file(file_pk)
        if item is None or not item.visible:
            await message.reply_text("هذا الملف غير موجود أو مخفي حالياً.", reply_markup=bot_home_keyboard(user.id))
            return
        if not await is_subscribed(context.bot, user.id):
            await ask_to_subscribe(message, file_pk)
            return
        await deliver(message, context, file_pk, user.id)
        await message.reply_text("القائمة الرئيسية", reply_markup=bot_home_keyboard(user.id))
        return
    if not await is_subscribed(context.bot, user.id):
        await ask_to_subscribe(message)
        return
    await send_home(message, user.id)


async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("لوحة المالك مخصصة لصاحب البوت فقط.")
        return
    clear_state(context)
    await update.effective_message.reply_text("لوحة المالك", reply_markup=admin_menu())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_state(context)
    await update.effective_message.reply_text("تم إلغاء العملية.", reply_markup=bot_home_keyboard(update.effective_user.id))


async def show_settings(message) -> None:
    welcome = get_setting("welcome", DEFAULT_WELCOME)
    channel = get_setting("mandatory_channel", MANDATORY_CHANNEL)
    site = get_setting("site_url", "")
    enabled = get_setting("subscription_enabled", "1") == "1"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("تفعيل/تعطيل الاشتراك الإجباري", callback_data="setting:toggle")],
        [InlineKeyboardButton("تغيير القناة", callback_data="setting:channel")],
        [InlineKeyboardButton("تغيير رابط الموقع", callback_data="setting:site")],
        [InlineKeyboardButton("تعديل الترحيب", callback_data="setting:welcome")],
        [InlineKeyboardButton("رجوع", callback_data="admin")],
    ])
    await message.reply_text(
        f"إعدادات البوت\nالاشتراك الإجباري: {'مفعّل' if enabled else 'معطّل'}\nالقناة: {channel}\nالموقع: {site or 'لم يحدد بعد'}\n\nالترحيب الحالي:\n{welcome[:900]}",
        reply_markup=keyboard,
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    user = update.effective_user
    uid = user.id if user else 0
    data = query.data or ""
    message = query.message
    if data == "noop":
        return
    if data.startswith("checksub:"):
        try:
            raw = int(data.split(":", 1)[1])
        except ValueError:
            raw = 0
        if await is_subscribed(context.bot, uid):
            if raw:
                await deliver(message, context, raw, uid)
                await message.reply_text("القائمة الرئيسية", reply_markup=bot_home_keyboard(uid))
            else:
                await send_home(message, uid)
        else:
            await query.edit_message_reply_markup(reply_markup=subscription_keyboard(raw or None))
            await message.reply_text("بعدك غير مشترك بالقناة. اشترك ثم اضغط تحقق مرة ثانية.")
        return
    if data.startswith("download:"):
        try:
            file_pk = int(data.split(":", 1)[1])
        except ValueError:
            return
        await deliver(message, context, file_pk, uid)
        return
    if not is_owner(uid):
        await query.answer("هذا الخيار للمالك فقط.", show_alert=True)
        return

    if data in {"admin", "a:menu"}:
        clear_state(context)
        await query.edit_message_text("لوحة المالك", reply_markup=admin_menu())
        return
    if data == "a:close":
        clear_state(context)
        await query.edit_message_text("تم إغلاق لوحة المالك.")
        return
    if data == "a:add":
        clear_state(context)
        await query.edit_message_text("اختَر نوع الملف:", reply_markup=kind_keyboard())
        return
    if data == "a:summaries":
        clear_state(context)
        await query.edit_message_text("إدارة الملخصات", reply_markup=summary_menu())
        return
    if data.startswith("pagefiles:"):
        try:
            _, prefix, kind_key, page_text = data.split(":", 3)
            page = max(0, int(page_text))
        except (ValueError, TypeError):
            return
        kind = None if kind_key == "all" else kind_key
        title = "اختَر الملخص:" if kind == "summary" else "اختَر الملف:"
        await query.edit_message_text(title, reply_markup=file_picker(prefix, kind=kind, page=page))
        return
    if data.startswith("sm:"):
        action = data.split(":", 1)[1]
        if action == "add":
            set_state(context, action="add_subject", kind="summary")
            await query.edit_message_text("اختَر مادة الملخص:", reply_markup=subject_keyboard("addsub"))
        elif action in {"edit", "replace", "delete", "hide"}:
            prefix = {"edit": "pickedit", "replace": "pickreplace", "delete": "pickdelete", "hide": "pickhide"}[action]
            await query.edit_message_text("اختَر الملخص:", reply_markup=file_picker(prefix, kind="summary"))
        elif action == "list":
            items = list_files(kind="summary", limit=30)
            if not items:
                await query.edit_message_text("ماكو ملخصات مضافة حالياً.", reply_markup=summary_menu())
            else:
                lines = [f"#{x['id']} · {x['subject']} · {x['title']} · {'ظاهر' if x['visible'] else 'مخفي'}" for x in items]
                await query.edit_message_text("الملخصات المسجلة:\n\n" + "\n".join(lines)[:3500], reply_markup=summary_menu())
        return
    if data.startswith("addkind:"):
        kind = data.split(":", 1)[1]
        if kind not in KINDS:
            return
        set_state(context, action="add_subject", kind=kind)
        await query.edit_message_text("اختَر المادة:", reply_markup=subject_keyboard("addsub"))
        return
    if data.startswith("addsub:"):
        try:
            sid = int(data.split(":", 1)[1])
        except ValueError:
            return
        st = state(context)
        st.update(action="add_file", subject_id=sid)
        await query.edit_message_text("أرسل ملف الـ PDF كـ Document. للامتحانات تگدر ترسل صورة أو PDF أيضاً.")
        return
    if data == "a:edit":
        await query.edit_message_text("اختَر الملف لتعديل بياناته:", reply_markup=file_picker("pickedit"))
        return
    if data == "a:replace":
        await query.edit_message_text("اختَر الملف لاستبدال ملف تيليجرام المرتبط به:", reply_markup=file_picker("pickreplace"))
        return
    if data == "a:delete":
        await query.edit_message_text("اختَر الملف للحذف:", reply_markup=file_picker("pickdelete"))
        return
    if data == "a:hide":
        await query.edit_message_text("اختَر الملف لإخفائه أو إظهاره:", reply_markup=file_picker("pickhide"))
        return
    if data.startswith("pickdelete:"):
        file_pk = int(data.split(":", 1)[1])
        item = get_file(file_pk)
        if item is None:
            await query.edit_message_text("الملف غير موجود.", reply_markup=admin_menu())
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("نعم، احذف الملف", callback_data=f"confirmdelete:{file_pk}")],
            [InlineKeyboardButton("إلغاء", callback_data="admin")],
        ])
        await query.edit_message_text(f"تأكيد حذف الملف؟\n{item.title}\nهذا الحذف لا يمكن التراجع عنه.", reply_markup=keyboard)
        return
    if data.startswith("confirmdelete:"):
        file_pk = int(data.split(":", 1)[1])
        with session_scope() as db:
            item = db.get(MaterialFile, file_pk)
            if item:
                db.delete(item)
        clear_state(context)
        await query.edit_message_text("تم حذف الملف.", reply_markup=admin_menu())
        return
    if data.startswith("pickhide:"):
        file_pk = int(data.split(":", 1)[1])
        with session_scope() as db:
            item = db.get(MaterialFile, file_pk)
            if not item:
                await query.edit_message_text("الملف غير موجود.", reply_markup=admin_menu())
                return
            item.visible = not item.visible
            visible = item.visible
            title = item.title
        await query.edit_message_text(f"تم {'إظهار' if visible else 'إخفاء'} الملف: {title}", reply_markup=admin_menu())
        return
    if data.startswith("pickreplace:"):
        file_pk = int(data.split(":", 1)[1])
        set_state(context, action="replace_file", file_id=file_pk)
        await query.edit_message_text("أرسل الملف الجديد كـ PDF/Document أو صورة/فيديو. سيبقى العنوان والوصف كما هما.")
        return
    if data.startswith("pickedit:"):
        file_pk = int(data.split(":", 1)[1])
        item = get_file(file_pk)
        if item is None:
            await query.edit_message_text("الملف غير موجود.", reply_markup=admin_menu())
            return
        set_state(context, action="edit_select", file_id=file_pk)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("الاسم", callback_data="editfield:title"), InlineKeyboardButton("الأستاذ", callback_data="editfield:teacher")],
            [InlineKeyboardButton("الوصف", callback_data="editfield:description"), InlineKeyboardButton("السنة", callback_data="editfield:year")],
            [InlineKeyboardButton("نوع المحتوى", callback_data="editfield:kind"), InlineKeyboardButton("المادة", callback_data="editfield:subject")],
            [InlineKeyboardButton("صورة معاينة الامتحان", callback_data="editfield:preview_file_id")],
            [InlineKeyboardButton("رجوع", callback_data="admin")],
        ])
        await query.edit_message_text(f"تعديل الملف: {item.title}\nاختَر الحقل:", reply_markup=keyboard)
        return
    if data.startswith("editfield:"):
        field = data.split(":", 1)[1]
        file_pk = state(context).get("file_id")
        if not file_pk:
            await query.edit_message_text("انتهت العملية. افتح لوحة المالك مجدداً.", reply_markup=admin_menu())
            return
        if field == "subject":
            set_state(context, action="edit_subject_for_file", file_id=file_pk)
            await query.edit_message_text("اختَر المادة الجديدة:", reply_markup=subject_keyboard("movesub"))
            return
        if field == "kind":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(label, callback_data=f"editkind:{kind}")]
                for kind, label in KINDS.items()
            ] + [[InlineKeyboardButton("رجوع", callback_data="admin")]])
            await query.edit_message_text("اختَر نوع المحتوى الجديد:", reply_markup=keyboard)
            return
        if field not in {"title", "teacher", "description", "year", "preview_file_id"}:
            return
        set_state(context, action="edit_text", file_id=file_pk, field=field)
        if field == "preview_file_id":
            prompt = "أرسل صورة المعاينة كصورة تيليجرام، أو اكتب - لحذف صورة المعاينة الحالية."
        else:
            prompt = "أرسل القيمة الجديدة كنص. اكتب - لمسح الحقل الاختياري."
        await query.edit_message_text(prompt)
        return
    if data.startswith("editkind:"):
        kind = data.split(":", 1)[1]
        file_pk = state(context).get("file_id")
        if kind not in KINDS or not file_pk:
            return
        with session_scope() as db:
            item = db.get(MaterialFile, file_pk)
            if item:
                item.kind = kind
                item.updated_at = now_utc()
        clear_state(context)
        await query.edit_message_text("تم تعديل نوع المحتوى.", reply_markup=admin_menu())
        return
    if data.startswith("movesub:"):
        sid = int(data.split(":", 1)[1])
        file_pk = state(context).get("file_id")
        with session_scope() as db:
            item = db.get(MaterialFile, file_pk) if file_pk else None
            subject = db.get(Subject, sid)
            if item and subject:
                item.subject_id = sid
                item.updated_at = now_utc()
        clear_state(context)
        await query.edit_message_text("تم نقل الملف إلى المادة الجديدة.", reply_markup=admin_menu())
        return
    if data == "a:addsubject":
        set_state(context, action="add_subject_name")
        await query.edit_message_text("أرسل اسم المادة الجديدة.")
        return
    if data == "a:editsubject":
        await query.edit_message_text("اختَر المادة للتعديل:", reply_markup=subject_keyboard("editsub", visible_only=False))
        return
    if data.startswith("editsub:"):
        sid = int(data.split(":", 1)[1])
        set_state(context, action="edit_subject", subject_id=sid)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("تعديل الاسم", callback_data="subfield:name"), InlineKeyboardButton("تعديل الإيموجي", callback_data="subfield:emoji")],
            [InlineKeyboardButton("إظهار/إخفاء المادة", callback_data="subfield:visible")],
            [InlineKeyboardButton("رجوع", callback_data="admin")],
        ])
        await query.edit_message_text("اختَر التعديل:", reply_markup=keyboard)
        return
    if data.startswith("subfield:"):
        field = data.split(":", 1)[1]
        sid = state(context).get("subject_id")
        if not sid:
            return
        if field == "visible":
            with session_scope() as db:
                subject = db.get(Subject, sid)
                if subject:
                    subject.visible = not subject.visible
                    visible = subject.visible
                    name = subject.name
                else:
                    visible, name = False, ""
            clear_state(context)
            await query.edit_message_text(f"تم {'إظهار' if visible else 'إخفاء'} المادة: {name}", reply_markup=admin_menu())
        elif field in {"name", "emoji"}:
            set_state(context, action="edit_subject_text", subject_id=sid, field=field)
            await query.edit_message_text("أرسل القيمة الجديدة كنص.")
        return
    if data == "a:delsubject":
        await query.edit_message_text("اختَر المادة. حذفها يحذف جميع ملفاتها التابعة:", reply_markup=subject_keyboard("delsub", visible_only=False))
        return
    if data.startswith("delsub:"):
        sid = int(data.split(":", 1)[1])
        with session_scope() as db:
            subject = db.get(Subject, sid)
            if not subject:
                await query.edit_message_text("المادة غير موجودة.", reply_markup=admin_menu())
                return
            name = subject.name
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("تأكيد حذف المادة وملفاتها", callback_data=f"confirmdelsub:{sid}")],
            [InlineKeyboardButton("إلغاء", callback_data="admin")],
        ])
        await query.edit_message_text(f"متأكد تريد حذف «{name}» وكل ملفاتها؟", reply_markup=keyboard)
        return
    if data.startswith("confirmdelsub:"):
        sid = int(data.split(":", 1)[1])
        with session_scope() as db:
            subject = db.get(Subject, sid)
            if subject:
                # Explicitly delete children for consistent behaviour across SQLite/PostgreSQL.
                for item in db.scalars(select(MaterialFile).where(MaterialFile.subject_id == sid)).all():
                    db.delete(item)
                db.delete(subject)
        clear_state(context)
        await query.edit_message_text("تم حذف المادة وملفاتها.", reply_markup=admin_menu())
        return
    if data == "a:stats":
        with session_scope() as db:
            users = db.scalar(select(func.count(User.user_id))) or 0
            files = db.scalar(select(func.count(MaterialFile.id))) or 0
            visible = db.scalar(select(func.count(MaterialFile.id)).where(MaterialFile.visible.is_(True))) or 0
            downloads = db.scalar(select(func.coalesce(func.sum(MaterialFile.downloads), 0))) or 0
            pending = db.scalar(select(func.count(HandoutRequest.id)).where(HandoutRequest.status == "new")) or 0
            subs = sum(1 for _ in db.scalars(select(Subject.id)).all())
        await query.edit_message_text(
            f"إحصائيات البوت\nالمستخدمون: {users}\nالمواد: {subs}\nالملفات: {files}\nالملفات الظاهرة: {visible}\nالتنزيلات عبر البوت: {downloads}\nطلبات الملازم الجديدة: {pending}",
            reply_markup=admin_menu(),
        )
        return
    if data == "a:broadcast":
        set_state(context, action="broadcast")
        await query.edit_message_text("أرسل نص الإعلان. راح ينرسل للمستخدمين اللي بدؤوا البوت فقط. للإلغاء اكتب /cancel.")
        return
    if data == "a:settings":
        await show_settings(message)
        return
    if data == "setting:toggle":
        currently = get_setting("subscription_enabled", "1") == "1"
        set_setting("subscription_enabled", "0" if currently else "1")
        await show_settings(message)
        return
    if data in {"setting:channel", "setting:site", "setting:welcome"}:
        action = {"setting:channel": "setting_channel", "setting:site": "setting_site", "setting:welcome": "setting_welcome"}[data]
        set_state(context, action=action)
        await query.edit_message_text("أرسل القيمة الجديدة. لرابط الموقع استخدم الرابط الكامل الذي يبدأ بـ https://")
        return
    if data == "a:requests":
        with session_scope() as db:
            requests = db.scalars(select(HandoutRequest).where(HandoutRequest.status == "new").order_by(HandoutRequest.created_at.desc()).limit(15)).all()
            safe_rows = [{"id": r.id, "subject": r.subject, "details": r.details, "contact": r.contact, "created_at": r.created_at} for r in requests]
        if not safe_rows:
            await query.edit_message_text("ماكو طلبات جديدة حالياً.", reply_markup=admin_menu())
            return
        lines = []
        rows = []
        for item in safe_rows:
            lines.append(f"#{item['id']} | {item['subject']} | {item['details'][:180]} | تواصل: {item['contact'] or 'غير محدد'}")
            rows.append([InlineKeyboardButton(f"تمت معالجة الطلب #{item['id']}", callback_data=f"reqdone:{item['id']}")])
        rows.append([InlineKeyboardButton("رجوع", callback_data="admin")])
        await query.edit_message_text("طلبات الملازم الجديدة:\n\n" + "\n\n".join(lines)[:3500], reply_markup=InlineKeyboardMarkup(rows))
        return
    if data.startswith("reqdone:"):
        request_id = int(data.split(":", 1)[1])
        with session_scope() as db:
            request_item = db.get(HandoutRequest, request_id)
            if request_item:
                request_item.status = "done"
        await query.edit_message_text(f"تم تحديث حالة الطلب #{request_id}.", reply_markup=admin_menu())
        return


async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    remember_user(user)
    text = (message.text or "").strip()

    if is_owner(user.id):
        st = state(context)
        action = st.get("action")
        if action == "add_file":
            file_id, file_type = extract_media(message)
            if not file_id:
                await message.reply_text("أرسل الملف كـ PDF/Document أو صورة أو فيديو.")
                return
            st["new_file_id"] = file_id
            st["new_file_type"] = file_type
            st["action"] = "add_title"
            await message.reply_text("أرسل اسم الملف.")
            return
        if action == "add_title":
            if not text:
                await message.reply_text("أرسل الاسم كنص.")
                return
            st["title"] = text
            st["action"] = "add_teacher"
            await message.reply_text("أرسل اسم الأستاذ، أو اكتب - إذا ماكو أستاذ.")
            return
        if action == "add_teacher":
            st["teacher"] = "" if text == "-" else text
            st["action"] = "add_description"
            await message.reply_text("أرسل وصف الملف، أو اكتب - إذا ماكو وصف.")
            return
        if action == "add_description":
            st["description"] = "" if text == "-" else text
            st["action"] = "add_year"
            await message.reply_text("أرسل سنة الإصدار (مثلاً 2026)، أو اكتب - لاعتماد 2026.")
            return
        if action == "add_year":
            st["year"] = "2026" if text in {"", "-"} else text
            if st.get("kind") == "exam":
                st["action"] = "add_preview"
                await message.reply_text("أرسل صورة معاينة للامتحان (صورة، مو PDF)، أو اكتب - لتخطي المعاينة.")
                return
            await save_new_file(message, context, preview_file_id=None)
            return
        if action == "add_preview":
            preview_id = None
            if message.photo:
                preview_id = message.photo[-1].file_id
            elif text == "-":
                preview_id = None
            else:
                await message.reply_text("أرسل صورة كصورة تيليجرام أو اكتب - للتخطي.")
                return
            await save_new_file(message, context, preview_file_id=preview_id)
            return
        if action == "replace_file":
            file_id, file_type = extract_media(message)
            if not file_id:
                await message.reply_text("أرسل الملف الجديد كـ Document أو صورة أو فيديو.")
                return
            file_pk = st.get("file_id")
            try:
                with session_scope() as db:
                    item = db.get(MaterialFile, file_pk)
                    if not item:
                        await message.reply_text("الملف غير موجود.")
                        clear_state(context)
                        return
                    item.telegram_file_id = file_id
                    item.file_type = file_type
                    item.updated_at = now_utc()
            except IntegrityError:
                await message.reply_text("معرّف تيليجرام هذا مستخدم لملف آخر. أرسل ملفاً مختلفاً أو /cancel.")
                return
            clear_state(context)
            await message.reply_text("تم استبدال الملف مع الإبقاء على بياناته.", reply_markup=bot_home_keyboard(user.id))
            return
        if action == "edit_text":
            file_pk, field = st.get("file_id"), st.get("field")
            with session_scope() as db:
                item = db.get(MaterialFile, file_pk) if file_pk else None
                if not item:
                    clear_state(context)
                    await message.reply_text("الملف غير موجود.", reply_markup=admin_menu())
                    return
                if field == "preview_file_id":
                    if message.photo:
                        item.preview_file_id = message.photo[-1].file_id
                    elif text == "-":
                        item.preview_file_id = None
                    else:
                        await message.reply_text("أرسل صورة أو اكتب - لحذف المعاينة.")
                        return
                else:
                    if not text:
                        await message.reply_text("أرسل قيمة نصية صحيحة.")
                        return
                    value = "" if text == "-" and field in {"teacher", "description"} else text
                    if text == "-" and field == "year":
                        value = "2026"
                    if field == "title" and not value:
                        await message.reply_text("اسم الملف لا يمكن أن يكون فارغاً.")
                        return
                    if field not in {"title", "teacher", "description", "year"}:
                        await message.reply_text("هذا الحقل غير قابل للتعديل هنا.")
                        return
                    setattr(item, field, value)
                item.updated_at = now_utc()
            clear_state(context)
            await message.reply_text("تم تحديث بيانات الملف.", reply_markup=admin_menu())
            return
        if action == "add_subject_name":
            if not text:
                await message.reply_text("أرسل اسم المادة.")
                return
            st["subject_name"] = text
            st["action"] = "add_subject_emoji"
            await message.reply_text("أرسل رمز المادة أو اكتب - لاستخدام 📚.")
            return
        if action == "add_subject_emoji":
            emoji = "📚" if text in {"", "-"} else text
            try:
                with session_scope() as db:
                    sort_order = (db.scalar(select(func.max(Subject.sort_order))) or 0) + 1
                    db.add(Subject(name=st["subject_name"], emoji=emoji, sort_order=sort_order, visible=True))
            except IntegrityError:
                await message.reply_text("هذه المادة موجودة مسبقاً. اكتب اسماً آخر أو /cancel.")
                return
            clear_state(context)
            await message.reply_text("تمت إضافة المادة.", reply_markup=bot_home_keyboard(user.id))
            return
        if action == "edit_subject_text":
            sid, field = st.get("subject_id"), st.get("field")
            if not text:
                await message.reply_text("أرسل قيمة صحيحة.")
                return
            try:
                with session_scope() as db:
                    subject = db.get(Subject, sid)
                    if not subject:
                        clear_state(context)
                        await message.reply_text("المادة غير موجودة.")
                        return
                    if field == "name":
                        subject.name = text
                    elif field == "emoji":
                        subject.emoji = text
            except IntegrityError:
                await message.reply_text("اسم المادة مستخدم من قبل. جرّب اسماً آخر أو /cancel.")
                return
            clear_state(context)
            await message.reply_text("تم تعديل المادة.", reply_markup=admin_menu())
            return
        if action in {"setting_channel", "setting_site", "setting_welcome"}:
            if not text:
                await message.reply_text("أرسل قيمة صحيحة.")
                return
            key = {"setting_channel": "mandatory_channel", "setting_site": "site_url", "setting_welcome": "welcome"}[action]
            if action == "setting_channel" and not (text.startswith("@") or text.startswith("-100") or text.startswith("https://t.me/")):
                await message.reply_text("أرسل معرف القناة مثل @Kii_8i أو رابط t.me.")
                return
            if action == "setting_site" and text and not text.startswith("https://"):
                await message.reply_text("رابط الموقع يجب أن يبدأ بـ https://")
                return
            set_setting(key, text)
            clear_state(context)
            await message.reply_text("تم حفظ الإعداد.", reply_markup=bot_home_keyboard(user.id))
            return
        if action == "broadcast":
            if not text:
                await message.reply_text("أرسل نص الإعلان، أو /cancel للإلغاء.")
                return
            with session_scope() as db:
                user_ids = list(db.scalars(select(User.user_id).where(User.blocked.is_(False))).all())
            sent, failed = 0, 0
            for target_id in user_ids:
                try:
                    await context.bot.send_message(chat_id=target_id, text=text[:4000])
                    sent += 1
                except Forbidden:
                    failed += 1
                    update_user_blocked(target_id, True)
                except TelegramError:
                    failed += 1
                await asyncio.sleep(0.05)
            clear_state(context)
            await message.reply_text(f"انتهى الإعلان.\nوصل: {sent}\nفشل: {failed}", reply_markup=bot_home_keyboard(user.id))
            return

        if text == "لوحة المالك":
            await owner_panel(update, context)
            return
        if text == "فتح الموقع":
            await open_site(message, user.id)
            return
        return

    if text == "فتح الموقع":
        await open_site(message, user.id)


async def save_new_file(message, context: ContextTypes.DEFAULT_TYPE, preview_file_id: str | None) -> None:
    st = state(context)
    try:
        with session_scope() as db:
            subject = db.get(Subject, int(st["subject_id"]))
            if not subject:
                await message.reply_text("المادة لم تعد موجودة. ابدأ الإضافة مجدداً.")
                clear_state(context)
                return
            item = MaterialFile(
                subject_id=subject.id,
                kind=st["kind"],
                title=st["title"],
                teacher=st.get("teacher", ""),
                description=st.get("description", ""),
                year=st.get("year", "2026"),
                telegram_file_id=st["new_file_id"],
                file_type=st["new_file_type"],
                preview_file_id=preview_file_id,
                visible=True,
                downloads=0,
                created_at=now_utc(),
                updated_at=now_utc(),
            )
            db.add(item)
            db.flush()
            file_pk = item.id
    except IntegrityError:
        await message.reply_text("هذا معرّف الملف مسجل مسبقاً. الملف لم تتم إضافته.")
        clear_state(context)
        return
    kind_label = KINDS.get(st.get("kind"), st.get("kind"))
    clear_state(context)
    await message.reply_text(
        f"تمت إضافة {kind_label} بنجاح.\nرقم الملف: {file_pk}\nرابط الاستلام: https://t.me/{BOT_USERNAME}?start=file_{file_pk}\nظهر الملف الآن في الموقع لأن الاثنين يستخدمان قاعدة البيانات نفسها.",
        reply_markup=bot_home_keyboard(OWNER_ID),
    )


def extract_media(message) -> tuple[str | None, str | None]:
    if message.document:
        return message.document.file_id, "document"
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.video:
        return message.video.file_id, "video"
    return None, None


async def open_site(message, user_id: int) -> None:
    site_url = get_setting("site_url", "")
    if site_url.startswith("https://"):
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("فتح الموقع", url=site_url)]])
        await message.reply_text("اضغط لفتح موقع الرابع العلمي:", reply_markup=keyboard)
    else:
        await message.reply_text("رابط الموقع راح يتفعّل بعد نشره وإضافة رابط https في إعدادات البوت.", reply_markup=bot_home_keyboard(user_id))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled Telegram bot error", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("صار خطأ غير متوقع. حاول مرة ثانية أو اكتب /cancel لإلغاء العملية.")
    except Exception:
        pass


def build_application(token: str) -> Application:
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", owner_panel))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    application.add_error_handler(error_handler)
    return application
