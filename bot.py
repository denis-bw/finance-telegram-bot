import os
import logging
import threading
from datetime import date, datetime
from functools import wraps
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
    ContextTypes,
)
from dotenv import load_dotenv

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
MY_TELEGRAM_ID = int(os.getenv("MY_TELEGRAM_ID", "0"))
MISTRAL_KEY    = os.getenv("MISTRAL_API_KEY", "")

import database as db
import ocr
import stats

(
    S_MAIN,
    S_WAITING_PHOTO,
    S_REVIEW_RECEIPT,
    S_REVIEW_EDIT_FIELD,
    S_REVIEW_EDIT_ITEM,
    S_REVIEW_SPLIT,
    S_RECEIPT_LIST,
    S_RECEIPT_DETAIL,
    S_RECEIPT_EDIT_FIELD,
    S_RECEIPT_ITEM_CATS,
    S_RECEIPT_ITEM_OVERVIEW,
    S_CATS,
    S_CAT_ADD_NAME,
    S_CAT_RENAME,
    S_STATS,
    S_STATS_DAY,
) = range(16)

PAGE_SIZE = 7


def owner_only(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        if uid != MY_TELEGRAM_ID:
            if update.message:
                await update.message.reply_text("⛔ Немає доступу.")
            elif update.callback_query:
                await update.callback_query.answer("⛔", show_alert=True)
            return ConversationHandler.END
        return await func(update, ctx)
    return wrapper


_MONTH_SHORT = ["","Січ","Лют","Бер","Кві","Тра","Чер",
                 "Лип","Сер","Вер","Жов","Лис","Гру"]
_MONTH_FULL  = ["","Січень","Лютий","Березень","Квітень","Травень","Червень",
                 "Липень","Серпень","Вересень","Жовтень","Листопад","Грудень"]

def _ms(m: int) -> str: return _MONTH_SHORT[m]
def _ml(m: int) -> str: return _MONTH_FULL[m]

def _fd(d: str) -> str:
    try:    return datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
    except: return d

def _pd(s: str) -> str | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:    return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except: pass
    return None

def _pt(s: str) -> str | None:
    for fmt in ("%H:%M", "%H.%M"):
        try:    return datetime.strptime(s.strip(), fmt).strftime("%H:%M")
        except: pass
    return None

def _pf(s: str) -> float | None:
    try:    return float(s.strip().replace(",", ".").replace(" ", ""))
    except: return None

def _btn(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)

def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))

def _esc(text: str) -> str:
    for ch in ("_", "*", "[", "]", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["📷  Сканувати чек"],
        ["🧾  Мої чеки",    "📊  Статистика"],
        ["📂  Категорії"],
    ], resize_keyboard=True, is_persistent=True)


def kb_with_home(rows: list) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows + [["🏠  Меню"]], resize_keyboard=True, is_persistent=True)


def kb_cancel_home() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["✕  Скасувати", "🏠  Меню"]], resize_keyboard=True, is_persistent=True)


def kb_review(has_items: bool) -> ReplyKeyboardMarkup:
    rows = [["✅  Зберегти"]]
    if has_items:
        rows.append(["🏷  Категорії товарів"])
    rows.append(["✏️  Магазин", "📅  Дата"])
    rows.append(["🕐  Час",     "💰  Сума"])
    if has_items:
        rows.append(["📝  Редагувати товар"])
    rows.append(["🔄  Нове фото"])
    rows.append(["🏠  Меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def kb_detail() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["✏️  Редагувати",        "🗑  Видалити"],
        ["🏷  Категорії товарів"],
        ["◀  До списку",          "🏠  Меню"],
    ], resize_keyboard=True, is_persistent=True)


def kb_receipt_edit() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["🏪  Магазин", "📅  Дата"],
        ["🕐  Час",     "💰  Сума"],
        ["◀  До чека",  "🏠  Меню"],
    ], resize_keyboard=True, is_persistent=True)


def kb_list(offset: int, total: int, cat_filter=None) -> ReplyKeyboardMarkup:
    rows = []
    if cat_filter is not None:
        rows.append(["✕  Скинути фільтр"])
    else:
        rows.append(["🔍  Фільтр за категорією"])
    nav = []
    if offset > 0:
        nav.append("◀  Назад")
    if offset + PAGE_SIZE < total:
        nav.append("Далі  ▶")
    if nav:
        rows.append(nav)
    rows.append(["🏠  Меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def kb_cats() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["➕  Нова категорія"],
        ["🏠  Меню"],
    ], resize_keyboard=True, is_persistent=True)


def kb_confirm_delete() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["🗑  Так, видалити"],
        ["✕  Скасувати",  "🏠  Меню"],
    ], resize_keyboard=True, is_persistent=True)


def kb_stats(months: list, year: int, month: int) -> ReplyKeyboardMarkup:
    idx = months.index((year, month)) if (year, month) in months else 0
    nav = []
    if idx + 1 < len(months):
        py, pm = months[idx + 1]
        nav.append(f"◀  {_ms(pm)} {py}")
    if idx > 0:
        ny, nm = months[idx - 1]
        nav.append(f"{_ms(nm)} {ny}  ▶")
    rows = [nav] if nav else []
    rows.append(["🏠  Меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def kb_stats_day(year: int, month: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [f"◀  {_ms(month)} {year}"],
        ["🏠  Меню"],
    ], resize_keyboard=True, is_persistent=True)


def kb_split(idx: int, total: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [f"⏭  Пропустити  ({idx + 1}/{total})"],
        ["✅  Зберегти без решти"],
        ["🔄  Почати знову",  "🏠  Меню"],
    ], resize_keyboard=True, is_persistent=True)


def kb_item_cats_saved(idx: int, total: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [f"⏭  Далі  ({idx + 1}/{total})"] if idx < total - 1 else ["✅  Готово"],
        ["◀  До чека",  "🏠  Меню"],
    ], resize_keyboard=True, is_persistent=True)


def _fmt_item(it: dict, idx: int | None = None, cat_label: str = "") -> str:
    name  = str(it.get("name", "?"))
    price = float(it.get("price") or 0)
    qty   = int(it.get("qty") or 1)
    if qty > 1:
        line = f"{name} ×{qty} — {price * qty:.2f} ₴  _(по {price:.2f}₴)_"
    else:
        line = f"{name} — {price:.2f} ₴"
    if idx is not None:
        line = f"{idx + 1}. " + line
    if cat_label:
        line += f"  〔{cat_label}〕"
    return line


async def _cats_summary(ic: dict, cats_map: dict, exclude_default: bool = False) -> str:
    default_id = await db.get_default_cat_id()
    seen = set()
    names = []
    for cid in ic.values():
        if not cid:
            continue
        if exclude_default and cid == default_id:
            continue
        if cid not in seen:
            seen.add(cid)
            names.append(cats_map.get(cid, "?"))
    return ", ".join(names) if names else ""


async def _cats_full_summary(ic: dict, cats_map: dict) -> str:
    default_id  = await db.get_default_cat_id()
    default_nm  = db.DEFAULT_CAT_NAME
    seen        = set()
    names       = []
    has_uncat   = False
    for cid in ic.values():
        if not cid:
            has_uncat = True
            continue
        if cid not in seen:
            seen.add(cid)
            if cid == default_id:
                has_uncat = True
            else:
                names.append(cats_map.get(cid, "?"))
    if has_uncat and default_nm not in names:
        names.append(default_nm)
    return ", ".join(names) if names else ""


async def _review_text(data: dict, item_cats: dict | None = None) -> str:
    cats_map: dict[str, str] = {}
    if item_cats:
        cats_map = {c["id"]: c["name"] for c in await db.get_categories()}
    time_str = f"  🕐 {data.get('time','')}" if data.get("time") else ""
    lines = [
        "🧾 *Перевір чек*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🏪 *{data.get('store', 'Невідомо')}*",
        f"📅 {_fd(data.get('date', str(date.today())))}{time_str}",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    items = data.get("items", [])
    for i, it in enumerate(items):
        cid  = (item_cats or {}).get(i)
        clbl = cats_map.get(cid, "") if cid else ""
        lines.append(_fmt_item(it, i, clbl))
    if not items:
        lines.append("_(товари не розпізнані)_")
    lines += ["━━━━━━━━━━━━━━━━━━━━━", f"💰 *Разом: {data.get('total', 0):.2f} ₴*"]
    return "\n".join(lines)


async def _detail_text(p: dict, ic: dict | None = None) -> str:
    cats_map = {c["id"]: c["name"] for c in await db.get_categories()}
    items    = p.get("items", [])
    lines    = []
    for i, it in enumerate(items):
        cid  = (ic or {}).get(i)
        clbl = cats_map.get(cid, "") if cid else ""
        lines.append(_fmt_item(it, i, clbl))

    receipt_cat = p.get("category_name") or db.DEFAULT_CAT_NAME
    if ic:
        summary = await _cats_full_summary(ic, cats_map)
        cat_line = f"🏷 {summary}" if summary else f"📂 {receipt_cat}"
    else:
        cat_line = f"📂 {receipt_cat}"

    time_str = f"  🕐 {p['time'][:5]}" if p.get("time") else ""
    header = [
        f"🏪 *{p['store']}*",
        f"📅 {_fd(p['date'])}{time_str}",
        cat_line,
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    footer = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"💰 *{p['total']:.2f} ₴*",
    ]
    return "\n".join(header + (lines or ["_(деталі відсутні)_"]) + footer)


async def _list_item_cats_summary(pid: str) -> str:
    ic       = await db.get_item_categories(pid)
    if not ic:
        return ""
    cats_map = {c["id"]: c["name"] for c in await db.get_categories()}
    return await _cats_full_summary(ic, cats_map)


async def _cat_inline(prefix: str, current_id: str | None = None, cols: int = 2) -> list:
    cats = await db.get_categories()
    rows, row = [], []
    for cat in cats:
        mark = "✅ " if cat["id"] == current_id else ""
        row.append(_btn(f"{mark}{cat['name']}", f"{prefix}{cat['id']}"))
        if len(row) == cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return rows


MAIN_TEXT = (
    "💳 *Фінансовий бот*\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "Надішли фото чека або вибери розділ:"
)


@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=kb_main())
    return S_MAIN


@owner_only
async def go_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("pending"):
        ctx.user_data.pop("pending", None)
        ctx.user_data.pop("item_cats_new", None)
    await update.message.reply_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=kb_main())
    return S_MAIN


@owner_only
async def noop_cb(update: Update, _: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def _process_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔍 *Розпізнаю чек...*\n_Зазвичай 5–15 секунд_",
        parse_mode="Markdown"
    )
    try:
        photo    = update.message.photo[-1]
        file_obj = await ctx.bot.get_file(photo.file_id)
        raw      = bytes(await file_obj.download_as_bytearray())

        data = await ocr.parse_receipt(raw)

        if not data:
            await msg.edit_text(
                "❌ *Не вдалося розпізнати чек*\n\n"
                "• Фото нечітке або темне\n"
                "• Чек зім'ятий або обрізаний\n"
                "• Це не касовий чек",
                parse_mode="Markdown"
            )
            await update.message.reply_text(
                "Спробуй інше фото:", reply_markup=kb_with_home([["🏠  Меню"]])
            )
            return S_WAITING_PHOTO

        ctx.user_data["pending"]      = data
        ctx.user_data["item_cats_new"] = {}
        has_items = bool(data.get("items"))

        await msg.edit_text(await _review_text(data), parse_mode="Markdown")
        await update.message.reply_text(
            "Перевір дані та збережи:", reply_markup=kb_review(has_items)
        )
        return S_REVIEW_RECEIPT

    except ValueError as e:
        await msg.edit_text(f"⚠️ *Помилка*\n\n{e}", parse_mode="Markdown")
        await update.message.reply_text("Спробуй ще раз:", reply_markup=kb_with_home([["🏠  Меню"]]))
        return S_WAITING_PHOTO
    except Exception as e:
        logger.error(f"Photo processing error: {e}", exc_info=True)
        await msg.edit_text(
            f"❌ *Несподівана помилка*\n`{type(e).__name__}: {str(e)[:150]}`",
            parse_mode="Markdown"
        )
        await update.message.reply_text("Спробуй ще раз:", reply_markup=kb_with_home([["🏠  Меню"]]))
        return S_WAITING_PHOTO


@owner_only
async def handle_photo_any(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("pending", None)
    ctx.user_data.pop("item_cats_new", None)
    return await _process_photo(update, ctx)


@owner_only
async def reply_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("pending", None)
    ctx.user_data.pop("item_cats_new", None)
    await update.message.reply_text(
        "📷 *Надішли фото чека*\n━━━━━━━━━━━━━━━━━━━━━\n"
        "_Просто відправ фото — розпізнаю автоматично_ ✨",
        parse_mode="Markdown",
        reply_markup=kb_with_home([["🏠  Меню"]])
    )
    return S_WAITING_PHOTO


@owner_only
async def reply_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["list_offset"]     = 0
    ctx.user_data["list_cat_filter"] = None
    return await _render_list(update.message, ctx, 0, None)


@owner_only
async def reply_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _render_stats_entry(update.message, ctx)


@owner_only
async def reply_cats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _render_cats(update.message, ctx)


async def _render_list(msg, ctx, offset: int, cat_filter: str | None):
    ctx.user_data["list_offset"]     = offset
    ctx.user_data["list_cat_filter"] = cat_filter

    total = await db.count_all_purchases(cat_filter)
    items = await db.get_all_purchases_paged(offset, PAGE_SIZE, cat_filter)

    active_cat_name: str | None = None
    if cat_filter is not None:
        for c in await db.get_categories():
            if c["id"] == cat_filter:
                active_cat_name = c["name"]
                break

    if not items:
        if active_cat_name:
            text = f"📂 *{active_cat_name}*\n\nЧеків у цій категорії ще немає."
        else:
            text = "🧾 *Мої чеки*\n\nЧеків ще немає. Надішли фото!"
        await msg.reply_text(text, parse_mode="Markdown",
                             reply_markup=kb_with_home([["🏠  Меню"]]))
        return S_RECEIPT_LIST

    if active_cat_name:
        hdr = f"📂 *{active_cat_name}*  ·  {total} чеків"
    else:
        hdr = f"🧾 *Мої чеки*  ·  {total} шт."
    if total > PAGE_SIZE:
        page  = offset // PAGE_SIZE + 1
        pages = max(1, (total - 1) // PAGE_SIZE + 1)
        hdr  += f"\n`Стор. {page} / {pages}`"

    inline_rows = []
    for p in items:
        time_s     = f" {p['time'][:5]}" if p.get("time") else ""
        ic_summary = await _list_item_cats_summary(p["id"])
        cat_s = f"  🏷{ic_summary}" if ic_summary else f"  📂{p.get('category_name','')}"
        lbl = (
            f"🏪 {p['store']}  ·  {_fd(p['date'])}{time_s}\n"
            f"💰 {p['total']:.0f} ₴{cat_s}"
        )
        inline_rows.append([_btn(lbl, f"receipt_{p['id']}")])

    await msg.reply_text(hdr, parse_mode="Markdown",
                         reply_markup=InlineKeyboardMarkup(inline_rows))
    await msg.reply_text("Натисни на чек або:", reply_markup=kb_list(offset, total, cat_filter))
    return S_RECEIPT_LIST


@owner_only
async def list_prev(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    offset = max(0, ctx.user_data.get("list_offset", 0) - PAGE_SIZE)
    cat_f  = ctx.user_data.get("list_cat_filter")
    return await _render_list(update.message, ctx, offset, cat_f)


@owner_only
async def list_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cat_f  = ctx.user_data.get("list_cat_filter")
    total  = await db.count_all_purchases(cat_f)
    offset = min(
        ctx.user_data.get("list_offset", 0) + PAGE_SIZE,
        ((total - 1) // PAGE_SIZE) * PAGE_SIZE
    )
    return await _render_list(update.message, ctx, offset, cat_f)


@owner_only
async def list_filter_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cats = await db.get_categories()
    rows = [[_btn(f"📂  {c['name']}", f"lf_{c['id']}")] for c in cats]
    rows.append([_btn("📋  Усі чеки", "lf_0")])
    await update.message.reply_text(
        "🔍 *Фільтр за категорією чека:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    return S_RECEIPT_LIST


@owner_only
async def list_filter_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    raw_id = q.data[3:]
    cat_id = raw_id if raw_id and raw_id != "0" else None
    ctx.user_data["list_cat_filter"] = cat_id
    return await _render_list(q.message, ctx, 0, cat_id)


@owner_only
async def list_reset_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["list_cat_filter"] = None
    return await _render_list(update.message, ctx, 0, None)


@owner_only
async def receipt_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query; await q.answer()
    pid = q.data[8:]
    ctx.user_data["viewing"] = pid
    return await _show_detail(q.message, ctx, pid)


async def _show_detail(msg, ctx, pid: str):
    p = await db.get_purchase_by_id(pid)
    if not p:
        await msg.reply_text("❌ Чек не знайдено.", reply_markup=kb_main())
        return S_RECEIPT_LIST

    ic = await db.get_item_categories(pid)
    ctx.user_data["viewing"] = pid

    cat_rows = await _cat_inline(f"rc_{pid}_", p.get("category_id"))

    await msg.reply_text(
        await _detail_text(p, ic),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(cat_rows) if cat_rows else None
    )
    await msg.reply_text(
        "📂 Вибери категорію чека вище або обери дію:",
        reply_markup=kb_detail()
    )
    return S_RECEIPT_DETAIL


@owner_only
async def receipt_cat_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query
    parts  = q.data.split("_")
    pid    = parts[1]
    cat_id = parts[2]
    await db.set_purchase_category(pid, cat_id or None)
    await q.answer("✅ Категорію встановлено")
    return await _show_detail(q.message, ctx, pid)


@owner_only
async def receipt_edit_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pid = ctx.user_data.get("viewing")
    if not pid:
        await update.message.reply_text("❌ Чек не знайдено.", reply_markup=kb_main())
        return S_MAIN
    p = await db.get_purchase_by_id(pid)
    if not p:
        await update.message.reply_text("❌ Чек не знайдено.", reply_markup=kb_main())
        return S_MAIN
    t = f"  🕐 {p['time'][:5]}" if p.get("time") else ""
    await update.message.reply_text(
        f"✏️ *Редагування чека*\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏪 {p['store']}\n📅 {_fd(p['date'])}{t}   💰 {p['total']:.2f} ₴\n\n"
        f"Натисни поле яке хочеш змінити:",
        parse_mode="Markdown",
        reply_markup=kb_receipt_edit()
    )
    return S_RECEIPT_EDIT_FIELD


@owner_only
async def receipt_delete_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pid = ctx.user_data.get("viewing")
    if not pid:
        return await go_main(update, ctx)
    p = await db.get_purchase_by_id(pid)
    if not p:
        return await go_main(update, ctx)
    await update.message.reply_text(
        f"🗑 *Видалити чек?*\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏪 {p['store']}\n📅 {_fd(p['date'])}  ·  💰 {p['total']:.2f} ₴\n\n"
        f"_Цю дію не можна скасувати._",
        parse_mode="Markdown",
        reply_markup=kb_confirm_delete()
    )
    return S_RECEIPT_DETAIL


@owner_only
async def receipt_delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pid = ctx.user_data.pop("viewing", None)
    if pid:
        await db.delete_purchase(pid)
    offset = ctx.user_data.get("list_offset", 0)
    cat_f  = ctx.user_data.get("list_cat_filter")
    total  = await db.count_all_purchases(cat_f)
    if offset >= total:
        offset = max(0, offset - PAGE_SIZE)
    await update.message.reply_text("✅ Чек видалено.")
    return await _render_list(update.message, ctx, offset, cat_f)


@owner_only
async def back_to_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    offset = ctx.user_data.get("list_offset", 0)
    cat_f  = ctx.user_data.get("list_cat_filter")
    return await _render_list(update.message, ctx, offset, cat_f)


@owner_only
async def back_to_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pid = ctx.user_data.get("viewing")
    if not pid:
        return await back_to_list(update, ctx)
    return await _show_detail(update.message, ctx, pid)


_EDIT_FIELDS = {
    "🏪  Магазин": ("store",    "🏪 Введи нову назву магазину:"),
    "📅  Дата":    ("date",     "📅 Введи дату у форматі *DD.MM.YYYY*\nНаприклад: `25.12.2024`"),
    "🕐  Час":     ("time",     "🕐 Введи час у форматі *HH:MM*\nНаприклад: `14:30`"),
    "💰  Сума":    ("total",    "💰 Введи нову суму:"),
}


@owner_only
async def receipt_edit_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    btn_text = update.message.text
    if btn_text not in _EDIT_FIELDS:
        return await receipt_edit_input(update, ctx)
    field, prompt = _EDIT_FIELDS[btn_text]
    ctx.user_data["editing_field"] = field
    await update.message.reply_text(prompt, parse_mode="Markdown", reply_markup=kb_cancel_home())
    return S_RECEIPT_EDIT_FIELD


@owner_only
async def receipt_edit_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    field = ctx.user_data.get("editing_field")
    pid   = ctx.user_data.get("viewing")
    text  = update.message.text.strip()
    kw: dict = {}

    if field == "store":
        if not text:
            await update.message.reply_text("❌ Назва не може бути порожньою.")
            return S_RECEIPT_EDIT_FIELD
        kw["store"] = text
    elif field == "date":
        d = _pd(text)
        if not d:
            await update.message.reply_text(
                "❌ Неправильна дата.\nФормат: *DD.MM.YYYY*  наприклад `25.12.2024`",
                parse_mode="Markdown"
            )
            return S_RECEIPT_EDIT_FIELD
        kw["date_str"] = d
    elif field == "time":
        t = _pt(text)
        if not t:
            await update.message.reply_text(
                "❌ Неправильний час.\nФормат: *HH:MM*  наприклад `14:30`",
                parse_mode="Markdown"
            )
            return S_RECEIPT_EDIT_FIELD
        kw["time_str"] = t
    elif field == "total":
        v = _pf(text)
        if v is None or v < 0:
            await update.message.reply_text("❌ Неправильне число.\nНаприклад: `45.80`", parse_mode="Markdown")
            return S_RECEIPT_EDIT_FIELD
        kw["total"] = v
    else:
        return S_RECEIPT_EDIT_FIELD

    if pid and kw:
        await db.edit_purchase(pid, **kw)
        await update.message.reply_text("✅ Збережено!")
        return await _show_detail(update.message, ctx, pid)
    return S_RECEIPT_EDIT_FIELD


def _ic_overview_text(p: dict, ic: dict, cats_map: dict, default_id: str) -> str:
    items = p.get("items", [])
    lines = [
        f"🏷 *Категорії товарів — {p['store']}*",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for i, it in enumerate(items):
        cid  = ic.get(i)
        if cid and cid != default_id:
            cat_lbl = cats_map.get(cid, "?")
        else:
            cat_lbl = "—"
        name  = str(it.get("name", "?"))
        price = float(it.get("price") or 0)
        qty   = int(it.get("qty") or 1)
        lines.append(f"{i+1}. {name} — {price*qty:.2f} ₴  〔{cat_lbl}〕")
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Натисни на товар щоб змінити категорію_")
    return "\n".join(lines)


async def _ic_overview_inline(p: dict, ic: dict, cats_map: dict, pid: str) -> InlineKeyboardMarkup:
    cats = await db.get_categories()
    default_id = await db.get_default_cat_id()
    items = p.get("items", [])

    rows = []
    header_row = []
    for cat in cats:
        header_row.append(_btn(f"📌 Всі → {cat['name']}", f"ica_{pid}_{cat['id']}"))
        if len(header_row) == 2:
            rows.append(header_row)
            header_row = []
    if header_row:
        rows.append(header_row)

    for i, it in enumerate(items):
        cid = ic.get(i)
        if cid and cid != default_id:
            cat_lbl = cats_map.get(cid, "?")
            mark = f"〔{cat_lbl}〕"
        else:
            mark = "〔—〕"
        name = str(it.get("name", "?"))[:28]
        rows.append([_btn(f"{i+1}. {name}  {mark}", f"ici_{pid}_{i}")])

    return InlineKeyboardMarkup(rows)


@owner_only
async def receipt_item_cats_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pid = ctx.user_data.get("viewing")
    if not pid:
        return await go_main(update, ctx)
    p = await db.get_purchase_by_id(pid)
    if not p or not p.get("items"):
        await update.message.reply_text(
            "ℹ️ У цьому чеку немає окремих товарів для категоризації.",
            reply_markup=kb_detail()
        )
        return S_RECEIPT_DETAIL
    return await _show_ic_overview(update.message, ctx, pid)


async def _show_ic_overview(msg, ctx, pid: str):
    p = await db.get_purchase_by_id(pid)
    if not p:
        return await go_main_msg(msg)
    ic       = await db.get_item_categories(pid)
    cats_map = {c["id"]: c["name"] for c in await db.get_categories()}
    default_id = await db.get_default_cat_id()
    ctx.user_data["viewing"] = pid

    await msg.reply_text(
        _ic_overview_text(p, ic, cats_map, default_id),
        parse_mode="Markdown",
        reply_markup=await _ic_overview_inline(p, ic, cats_map, pid)
    )
    await msg.reply_text(
        "Натисни товар щоб змінити категорію, або «Всі →» щоб застосувати до всіх:",
        reply_markup=ReplyKeyboardMarkup([
            ["✅  Готово"],
            ["◀  До чека", "🏠  Меню"],
        ], resize_keyboard=True, is_persistent=True)
    )
    return S_RECEIPT_ITEM_OVERVIEW


@owner_only
async def ic_apply_all_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query
    parts  = q.data.split("_")
    pid    = parts[1]
    cat_id = parts[2]
    p      = await db.get_purchase_by_id(pid)
    if p:
        for i in range(len(p.get("items", []))):
            await db.set_item_category(pid, i, cat_id)
        await db.set_purchase_category(pid, cat_id)
    cat = await db.get_category_by_id(cat_id)
    cat_name = cat["name"] if cat else db.DEFAULT_CAT_NAME
    await q.answer(f"✅ Всі товари → {cat_name}")
    return await _show_ic_overview(q.message, ctx, pid)


@owner_only
async def ic_item_select_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query; await q.answer()
    parts    = q.data.split("_")
    pid      = parts[1]
    item_idx = int(parts[2])
    ctx.user_data["ic_saved_idx"] = item_idx
    return await _show_item_cat_picker(q.message, ctx, pid, item_idx)


async def _show_item_cat_picker(msg, ctx, pid: str, item_idx: int):
    p = await db.get_purchase_by_id(pid)
    if not p:
        return await go_main_msg(msg)
    items = p.get("items", [])
    if item_idx >= len(items):
        return await _show_ic_overview(msg, ctx, pid)

    item    = items[item_idx]
    ic      = await db.get_item_categories(pid)
    cur_cid = ic.get(item_idx)
    cat_rows = await _cat_inline(f"ic_{pid}_{item_idx}_", cur_cid)

    cur_name = db.DEFAULT_CAT_NAME
    if cur_cid:
        cat = await db.get_category_by_id(cur_cid)
        cur_name = cat["name"] if cat else db.DEFAULT_CAT_NAME

    await msg.reply_text(
        f"🏷 *Товар {item_idx + 1} / {len(items)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{_fmt_item(item)}_\n\n"
        f"Поточна: *{cur_name}*\n\n"
        f"_Обери нову категорію:_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(cat_rows)
    )
    await msg.reply_text(
        "Або:",
        reply_markup=ReplyKeyboardMarkup([
            ["◀  До огляду"],
            ["🏠  Меню"],
        ], resize_keyboard=True, is_persistent=True)
    )
    return S_RECEIPT_ITEM_CATS


@owner_only
async def ic_saved_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    parts    = q.data.split("_")
    pid      = parts[1]
    item_idx = int(parts[2])
    cat_id   = parts[3]
    await db.set_item_category(pid, item_idx, cat_id or None)
    cat      = await db.get_category_by_id(cat_id) if cat_id else None
    cat_name = cat["name"] if cat else db.DEFAULT_CAT_NAME
    await q.answer(f"✅ {cat_name}")
    return await _show_ic_overview(q.message, ctx, pid)


@owner_only
async def ic_back_to_overview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pid = ctx.user_data.get("viewing")
    if not pid:
        return await back_to_list(update, ctx)
    return await _show_ic_overview(update.message, ctx, pid)


@owner_only
async def ic_overview_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pid = ctx.user_data.get("viewing")
    if pid:
        return await _show_detail(update.message, ctx, pid)
    return await back_to_list(update, ctx)


@owner_only
async def review_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _do_save(update.message, ctx)


@owner_only
async def review_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("pending", None)
    ctx.user_data.pop("item_cats_new", None)
    await update.message.reply_text(
        "📷 *Надішли нове фото чека*",
        parse_mode="Markdown",
        reply_markup=kb_with_home([["🏠  Меню"]])
    )
    return S_WAITING_PHOTO


async def _split_overview_text(data: dict, ic: dict, cats_map: dict) -> str:
    items      = data.get("items", [])
    default_id = await db.get_default_cat_id()
    lines = [
        f"🏷 *Категорії товарів — {data.get('store','?')}*",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for i, it in enumerate(items):
        cid = ic.get(i)
        if cid and cid != default_id:
            cat = await db.get_category_by_id(cid)
            cat_lbl = cat["name"] if cat else "?"
        else:
            cat_lbl = "—"
        name  = str(it.get("name", "?"))
        price = float(it.get("price") or 0)
        qty   = int(it.get("qty") or 1)
        lines.append(f"{i+1}. {name} — {price*qty:.2f} ₴  〔{cat_lbl}〕")
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Натисни товар щоб встановити категорію_")
    return "\n".join(lines)


async def _split_overview_inline(data: dict, ic: dict) -> InlineKeyboardMarkup:
    cats  = await db.get_categories()
    items = data.get("items", [])
    default_id = await db.get_default_cat_id()
    rows = []

    header_row = []
    for cat in cats:
        header_row.append(_btn(f"📌 Всі → {cat['name']}", f"spa_{cat['id']}"))
        if len(header_row) == 2:
            rows.append(header_row); header_row = []
    if header_row:
        rows.append(header_row)

    cats_map = {c["id"]: c["name"] for c in cats}
    for i, it in enumerate(items):
        cid = ic.get(i)
        if cid and cid != default_id:
            cat_lbl = cats_map.get(cid, "?")
            mark = f"〔{cat_lbl}〕"
        else:
            mark = "〔—〕"
        name = str(it.get("name", "?"))[:28]
        rows.append([_btn(f"{i+1}. {name}  {mark}", f"spi_{i}")])

    return InlineKeyboardMarkup(rows)


async def _show_split_overview(msg, ctx):
    data = ctx.user_data.get("pending", {})
    ic   = ctx.user_data.get("item_cats_new", {})
    cats_map = {c["id"]: c["name"] for c in await db.get_categories()}

    await msg.reply_text(
        await _split_overview_text(data, ic, cats_map),
        parse_mode="Markdown",
        reply_markup=await _split_overview_inline(data, ic)
    )
    await msg.reply_text(
        "Натисни товар щоб змінити категорію, або «Всі →» щоб застосувати до всіх:",
        reply_markup=ReplyKeyboardMarkup([
            ["✅  Зберегти"],
            ["◀  До чека", "🏠  Меню"],
        ], resize_keyboard=True, is_persistent=True)
    )
    return S_REVIEW_SPLIT


@owner_only
async def review_split(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data  = ctx.user_data.get("pending", {})
    items = data.get("items", [])
    if not items:
        await update.message.reply_text("ℹ️ Немає товарів для категоризації.")
        return S_REVIEW_RECEIPT
    return await _show_split_overview(update.message, ctx)


@owner_only
async def split_apply_all_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query
    cat_id = q.data[4:]
    data   = ctx.user_data.get("pending", {})
    items  = data.get("items", [])
    ic     = ctx.user_data.get("item_cats_new", {})
    for i in range(len(items)):
        ic[i] = cat_id or None
    ctx.user_data["item_cats_new"] = ic
    cat      = await db.get_category_by_id(cat_id) if cat_id else None
    cat_name = cat["name"] if cat else db.DEFAULT_CAT_NAME
    await q.answer(f"✅ Всі товари → {cat_name}")
    return await _show_split_overview(q.message, ctx)


@owner_only
async def split_item_select_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query; await q.answer()
    idx  = int(q.data[4:])
    ctx.user_data["split_idx"] = idx
    return await _show_split_item_picker(q.message, ctx, idx)


async def _show_split_item_picker(msg, ctx, idx: int):
    data  = ctx.user_data.get("pending", {})
    items = data.get("items", [])
    ic    = ctx.user_data.get("item_cats_new", {})

    if idx >= len(items):
        return await _show_split_overview(msg, ctx)

    item     = items[idx]
    cur_cid  = ic.get(idx)
    cur_name = db.DEFAULT_CAT_NAME
    if cur_cid:
        cat = await db.get_category_by_id(cur_cid)
        cur_name = cat["name"] if cat else db.DEFAULT_CAT_NAME

    cat_rows = await _cat_inline(f"sp_{idx}_", cur_cid)
    await msg.reply_text(
        f"🏷 *Товар {idx + 1} / {len(items)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"_{_fmt_item(item)}_\n\n"
        f"Поточна: *{cur_name}*\n\n"
        f"_Обери нову категорію:_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(cat_rows)
    )
    await msg.reply_text(
        "Або:",
        reply_markup=ReplyKeyboardMarkup([
            ["◀  До огляду"],
            ["🏠  Меню"],
        ], resize_keyboard=True, is_persistent=True)
    )
    return S_REVIEW_SPLIT


@owner_only
async def split_cat_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query
    parts  = q.data.split("_")
    idx    = int(parts[1])
    cat_id = parts[2]
    ic = ctx.user_data.get("item_cats_new", {})
    ic[idx] = cat_id or None
    ctx.user_data["item_cats_new"] = ic
    cat      = await db.get_category_by_id(cat_id) if cat_id else None
    cat_name = cat["name"] if cat else db.DEFAULT_CAT_NAME
    await q.answer(f"✅ {cat_name}")
    return await _show_split_overview(q.message, ctx)


@owner_only
async def split_back_to_overview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _show_split_overview(update.message, ctx)


@owner_only
async def split_save_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _do_save(update.message, ctx)


@owner_only
async def split_back_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = ctx.user_data.get("pending", {})
    ic   = ctx.user_data.get("item_cats_new", {})
    has_items = bool(data.get("items"))
    await update.message.reply_text(
        await _review_text(data, ic), parse_mode="Markdown"
    )
    await update.message.reply_text(
        "Перевір дані та збережи:", reply_markup=kb_review(has_items)
    )
    return S_REVIEW_RECEIPT


async def _do_save(msg, ctx):
    data = ctx.user_data.get("pending")
    if not data:
        await msg.reply_text("❌ Немає даних для збереження.", reply_markup=kb_main())
        return S_MAIN

    ic  = ctx.user_data.get("item_cats_new", {})
    pid = await db.add_purchase(data)
    for item_idx, cat_id in ic.items():
        if cat_id:
            await db.set_item_category(pid, item_idx, cat_id)

    ctx.user_data.pop("pending", None)
    ctx.user_data.pop("item_cats_new", None)
    ctx.user_data["viewing"] = pid

    p = await db.get_purchase_by_id(pid)
    await msg.reply_text(
        f"✅ *Збережено!*\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏪 *{p['store']}*\n📅 {_fd(p['date'])}   💰 *{p['total']:.2f} ₴*",
        parse_mode="Markdown"
    )
    return await _show_detail(msg, ctx, pid)


_REVIEW_FIELDS = {
    "✏️  Магазин": ("store",  "🏪 Введи нову назву магазину:"),
    "📅  Дата":    ("date",   "📅 Введи дату у форматі *DD.MM.YYYY*\nНаприклад: `25.12.2024`"),
    "🕐  Час":     ("time",   "🕐 Введи час у форматі *HH:MM*\nНаприклад: `14:30`"),
    "💰  Сума":    ("total",  "💰 Введи нову суму:"),
}


@owner_only
async def review_edit_field_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    btn = update.message.text
    if btn not in _REVIEW_FIELDS:
        return S_REVIEW_RECEIPT
    field, prompt = _REVIEW_FIELDS[btn]
    ctx.user_data["epre_field"] = field
    await update.message.reply_text(prompt, parse_mode="Markdown", reply_markup=kb_cancel_home())
    return S_REVIEW_EDIT_FIELD


@owner_only
async def review_edit_field_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    field = ctx.user_data.get("epre_field")
    text  = update.message.text.strip()
    data  = ctx.user_data.get("pending", {})

    if field == "store":
        if not text:
            await update.message.reply_text("❌ Назва не може бути порожньою.")
            return S_REVIEW_EDIT_FIELD
        data["store"] = text
    elif field == "date":
        d = _pd(text)
        if not d:
            await update.message.reply_text(
                "❌ Неправильна дата.\nФормат: *DD.MM.YYYY*", parse_mode="Markdown"
            )
            return S_REVIEW_EDIT_FIELD
        data["date"] = d
    elif field == "time":
        t = _pt(text)
        if not t:
            await update.message.reply_text(
                "❌ Неправильний час.\nФормат: *HH:MM*", parse_mode="Markdown"
            )
            return S_REVIEW_EDIT_FIELD
        data["time"] = t
    elif field == "total":
        v = _pf(text)
        if v is None or v < 0:
            await update.message.reply_text("❌ Неправильне число.", parse_mode="Markdown")
            return S_REVIEW_EDIT_FIELD
        data["total"] = v

    ctx.user_data["pending"] = data
    ic = ctx.user_data.get("item_cats_new", {})
    await update.message.reply_text(await _review_text(data, ic), parse_mode="Markdown")
    await update.message.reply_text(
        "Перевір дані та збережи:", reply_markup=kb_review(bool(data.get("items")))
    )
    return S_REVIEW_RECEIPT


@owner_only
async def review_edit_item_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data  = ctx.user_data.get("pending", {})
    items = data.get("items", [])
    if not items:
        await update.message.reply_text("ℹ️ Немає товарів.")
        return S_REVIEW_RECEIPT
    rows = [
        [_btn(f"{i+1}. {it['name']} — {it.get('price',0):.2f}₴", f"rei_{i}")]
        for i, it in enumerate(items)
    ]
    await update.message.reply_text(
        "📝 *Вибери товар для редагування:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    return S_REVIEW_EDIT_ITEM


@owner_only
async def review_item_select_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query; await q.answer()
    idx = int(q.data[4:])
    ctx.user_data["epre_item_idx"] = idx
    data = ctx.user_data.get("pending", {})
    item = data["items"][idx]
    qty  = item.get("qty", 1)
    qty_note = f" ×{qty}" if qty > 1 else ""
    await q.message.reply_text(
        f"📝 *Товар {idx+1}:* _{item['name']}{qty_note} — {item.get('price',0):.2f} ₴_\n\n"
        f"Формат: `Назва | ціна`  або просто `ціна`  або просто `Назва`",
        parse_mode="Markdown",
        reply_markup=kb_cancel_home()
    )
    return S_REVIEW_EDIT_ITEM


@owner_only
async def review_item_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    idx  = ctx.user_data.get("epre_item_idx")
    if idx is None:
        return S_REVIEW_RECEIPT
    text = update.message.text.strip()
    data = ctx.user_data.get("pending", {})
    item = data["items"][idx]

    if "|" in text:
        parts        = text.split("|", 1)
        item["name"] = parts[0].strip() or item["name"]
        v = _pf(parts[1].strip())
        if v is not None and v >= 0:
            item["price"] = v
    else:
        v = _pf(text)
        if v is not None and v >= 0:
            item["price"] = v
        else:
            item["name"] = text

    data["items"][idx]        = item
    ctx.user_data["pending"]  = data
    ic = ctx.user_data.get("item_cats_new", {})
    await update.message.reply_text(await _review_text(data, ic), parse_mode="Markdown")
    await update.message.reply_text(
        "Перевір дані та збережи:", reply_markup=kb_review(True)
    )
    return S_REVIEW_RECEIPT


async def _render_cats(msg, ctx):
    cats = await db.get_categories()
    if cats:
        lst  = "\n".join(
            f"  {'🔒' if c.get('is_default') else '📁'} {c['name']}"
            for c in cats
        )
        text = (
            f"📂 *Категорії*  ·  {len(cats)} шт.\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{lst}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔒 — захищена від видалення"
        )
    else:
        text = "📂 *Категорії*\n\nЩе немає жодної. Додай першу!"

    inline_rows = []
    for cat in cats:
        if cat.get("is_default"):
            inline_rows.append([_btn(f"🔒  {cat['name']}", "noop")])
        else:
            inline_rows.append([
                _btn(f"✏️  {cat['name']}", f"cat_ren_{cat['id']}"),
                _btn("🗑", f"cat_del_{cat['id']}"),
            ])

    await msg.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_rows) if inline_rows else None
    )
    await msg.reply_text("Обери дію:", reply_markup=kb_cats())
    return S_CATS


@owner_only
async def cats_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    action = q.data

    if action.startswith("cat_del_"):
        cid = action[8:]
        cat = await db.get_category_by_id(cid)
        if cat:
            try:
                await db.delete_category(cid)
                await q.answer(
                    f"✅ «{cat['name']}» видалено. Чеки → «Без категорії».",
                    show_alert=True
                )
            except ValueError as e:
                await q.answer(str(e), show_alert=True)
                return S_CATS
        return await _render_cats(q.message, ctx)

    if action.startswith("cat_ren_"):
        cid = action[8:]
        cat = await db.get_category_by_id(cid)
        if not cat:
            return await _render_cats(q.message, ctx)
        ctx.user_data["renaming_cat_id"]   = cid
        ctx.user_data["renaming_cat_name"] = cat["name"]
        await q.message.reply_text(
            f"✏️ Перейменувати *«{cat['name']}»*\n\nВведи нову назву:",
            parse_mode="Markdown",
            reply_markup=kb_cancel_home()
        )
        return S_CAT_RENAME

    return S_CATS


@owner_only
async def cats_add_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *Нова категорія*\n\nВведи назву:",
        parse_mode="Markdown",
        reply_markup=kb_cancel_home()
    )
    return S_CAT_ADD_NAME


@owner_only
async def cat_add_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Назва не може бути порожньою.")
        return S_CAT_ADD_NAME
    if len(name) > 50:
        await update.message.reply_text("❌ Назва задовга (максимум 50 символів).")
        return S_CAT_ADD_NAME
    if await db.category_exists(name):
        await update.message.reply_text(f"⚠️ Категорія *{name}* вже існує.", parse_mode="Markdown")
        return S_CAT_ADD_NAME
    await db.add_category(name)
    await update.message.reply_text(f"✅ *«{name}» додано!*", parse_mode="Markdown")
    return await _render_cats(update.message, ctx)


@owner_only
async def cat_rename_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    cid      = ctx.user_data.get("renaming_cat_id")
    old_name = ctx.user_data.get("renaming_cat_name", "")
    if not new_name:
        await update.message.reply_text("❌ Назва не може бути порожньою.")
        return S_CAT_RENAME
    if len(new_name) > 50:
        await update.message.reply_text("❌ Назва задовга (максимум 50 символів).")
        return S_CAT_RENAME
    if await db.category_exists(new_name):
        await update.message.reply_text(f"⚠️ *{new_name}* вже існує.", parse_mode="Markdown")
        return S_CAT_RENAME
    try:
        await db.rename_category(cid, new_name)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return S_CAT_RENAME
    await update.message.reply_text(f"✅ *«{old_name}» → «{new_name}»*", parse_mode="Markdown")
    return await _render_cats(update.message, ctx)


async def _get_cat_totals_with_items(year: int, month: int) -> list[dict]:
    purchases  = await db.get_purchases_by_month(year, month)
    cats_map   = {c["id"]: c["name"] for c in await db.get_categories()}
    totals: dict[str, float] = {}

    for p in purchases:
        items   = p.get("items", [])
        ic      = await db.get_item_categories(p["id"])
        receipt_cat = p.get("category_name") or db.DEFAULT_CAT_NAME

        if ic and items:
            item_sum_by_cat: dict[str, float] = {}
            uncategorized_sum = 0.0

            for i, it in enumerate(items):
                price = float(it.get("price") or 0)
                qty   = int(it.get("qty") or 1)
                amount = price * qty
                cid = ic.get(i)
                if cid:
                    cat_name = cats_map.get(cid, db.DEFAULT_CAT_NAME)
                    item_sum_by_cat[cat_name] = item_sum_by_cat.get(cat_name, 0) + amount
                else:
                    uncategorized_sum += amount

            if uncategorized_sum > 0:
                item_sum_by_cat[receipt_cat] = (
                    item_sum_by_cat.get(receipt_cat, 0) + uncategorized_sum
                )

            items_total = sum(item_sum_by_cat.values())
            if items_total > 0:
                ratio = p["total"] / items_total
                for cat_name, amount in item_sum_by_cat.items():
                    totals[cat_name] = totals.get(cat_name, 0) + amount * ratio
            else:
                totals[receipt_cat] = totals.get(receipt_cat, 0) + p["total"]
        else:
            totals[receipt_cat] = totals.get(receipt_cat, 0) + p["total"]

    return [
        {"category_name": name, "total": total}
        for name, total in sorted(totals.items(), key=lambda x: -x[1])
    ]


async def _render_stats_entry(msg, ctx):
    months = await db.get_available_months()
    if not months:
        await msg.reply_text(
            "📊 *Статистика*\n\nЩе немає даних. Надішли перший чек!",
            parse_mode="Markdown",
            reply_markup=kb_with_home([["🏠  Меню"]])
        )
        return S_STATS
    today = date.today()
    year, month = today.year, today.month
    if (year, month) not in months:
        year, month = months[0]
    ctx.user_data["stats_year"]  = year
    ctx.user_data["stats_month"] = month
    return await _render_stats(msg, ctx, year, month, months)


async def _render_stats(msg, ctx, year: int, month: int, months: list | None = None):
    if months is None:
        months = await db.get_available_months()

    wait = await msg.reply_text("⏳ Будую графіки...")

    purchases  = await db.get_purchases_by_month(year, month)
    total_sum  = sum(p["total"] for p in purchases)
    cat_totals = await _get_cat_totals_with_items(year, month)
    daily      = await db.get_daily_totals_by_month(year, month)
    hmap_path  = stats.month_heatmap(year, month, daily=daily)
    bar_path   = stats.category_bar_chart(year, month, cat_totals=cat_totals)

    await wait.delete()

    if not purchases:
        await msg.reply_text(
            f"📅 *{_ml(month)} {year}*\n\nЗа цей місяць чеків немає.",
            parse_mode="Markdown",
            reply_markup=kb_stats(months, year, month)
        )
        return S_STATS

    if hmap_path:
        with open(hmap_path, "rb") as f:
            await msg.reply_photo(f, caption="🗓 Теплова карта витрат")
        os.remove(hmap_path)
    if bar_path:
        with open(bar_path, "rb") as f:
            await msg.reply_photo(f, caption="📊 Витрати по категоріях")
        os.remove(bar_path)

    lines = [
        f"📊 *{_ml(month)} {year}*",
        f"💰 *{total_sum:.2f} ₴*  ·  🧾 {len(purchases)} чеків",
        "",
        "📂 *По категоріях (з урахуванням товарів):*",
    ]
    for r in cat_totals:
        pct = r["total"] / total_sum * 100 if total_sum else 0
        lines.append(f"  · {r['category_name']}: *{r['total']:.2f} ₴*  ({pct:.1f}%)")
    lines += ["", "_Натисни на день щоб побачити деталі_ ⬇️"]

    day_rows, row = [], []
    for d in daily:
        dn = d["date"].split("-")[2].lstrip("0")
        row.append(_btn(f"{dn} · {d['total']:.0f}₴", f"sd_{d['date']}"))
        if len(row) == 4:
            day_rows.append(row); row = []
    if row:
        day_rows.append(row)

    await msg.reply_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(day_rows) if day_rows else None
    )
    await msg.reply_text("Навігація:", reply_markup=kb_stats(months, year, month))
    return S_STATS


@owner_only
async def stats_nav(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text   = update.message.text
    months = await db.get_available_months()
    if not months:
        return S_STATS
    year  = ctx.user_data.get("stats_year",  date.today().year)
    month = ctx.user_data.get("stats_month", date.today().month)

    try:
        idx = months.index((year, month))
    except ValueError:
        idx = 0

    if "▶" in text and idx > 0:
        year, month = months[idx - 1]
    elif "◀" in text and idx + 1 < len(months):
        year, month = months[idx + 1]
    else:
        return S_STATS

    ctx.user_data["stats_year"]  = year
    ctx.user_data["stats_month"] = month
    return await _render_stats(update.message, ctx, year, month, months)


@owner_only
async def stats_day_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query; await q.answer()
    target = q.data[3:]
    wait   = await q.message.reply_text("⏳")

    purchases = await db.get_purchases_by_date(target)
    total_sum = sum(p["total"] for p in purchases)
    cats_map_day = {c["id"]: c["name"] for c in await db.get_categories()}
    day_cat_totals: dict[str, float] = {}
    for p in purchases:
        items = p.get("items", [])
        ic = await db.get_item_categories(p["id"])
        receipt_cat = p.get("category_name") or db.DEFAULT_CAT_NAME
        if ic and items:
            item_sum_by_cat: dict[str, float] = {}
            uncategorized = 0.0
            for i, it in enumerate(items):
                amount = float(it.get("price") or 0) * int(it.get("qty") or 1)
                cid = ic.get(i)
                if cid:
                    name = cats_map_day.get(cid, db.DEFAULT_CAT_NAME)
                    item_sum_by_cat[name] = item_sum_by_cat.get(name, 0) + amount
                else:
                    uncategorized += amount
            if uncategorized > 0:
                item_sum_by_cat[receipt_cat] = item_sum_by_cat.get(receipt_cat, 0) + uncategorized
            items_total = sum(item_sum_by_cat.values())
            if items_total > 0:
                ratio = p["total"] / items_total
                for name, amt in item_sum_by_cat.items():
                    day_cat_totals[name] = day_cat_totals.get(name, 0) + amt * ratio
            else:
                day_cat_totals[receipt_cat] = day_cat_totals.get(receipt_cat, 0) + p["total"]
        else:
            day_cat_totals[receipt_cat] = day_cat_totals.get(receipt_cat, 0) + p["total"]
    day_cat_list = [
        {"category_name": k, "total": v}
        for k, v in sorted(day_cat_totals.items(), key=lambda x: -x[1])
    ]
    bar_path = stats.category_bar_chart(0, 0, target_date=target, cat_totals=day_cat_list)
    y = ctx.user_data.get("stats_year",  date.today().year)
    m = ctx.user_data.get("stats_month", date.today().month)

    await wait.delete()

    if not purchases:
        await q.message.reply_text(
            f"📅 *{_fd(target)}*\n\nЗа цей день чеків немає.",
            parse_mode="Markdown",
            reply_markup=kb_stats_day(y, m)
        )
        return S_STATS_DAY

    if bar_path:
        with open(bar_path, "rb") as f:
            await q.message.reply_photo(f, caption=f"📊 {_fd(target)}")
        os.remove(bar_path)

    lines = [
        f"📅 *{_fd(target)}*",
        f"💰 *{total_sum:.2f} ₴*  ·  🧾 {len(purchases)} чеків",
        "",
    ]
    for p in purchases:
        ic  = await db.get_item_categories(p["id"])
        cat = p.get("category_name") or db.DEFAULT_CAT_NAME
        t   = f" {p['time'][:5]}" if p.get("time") else ""
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🏪 *{p['store']}*  [{cat}]{t}")
        for i, it in enumerate(p.get("items", [])):
            lines.append("  " + _fmt_item(it))
        lines.append(f"💳 *{p['total']:.2f} ₴*")

    await q.message.reply_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=kb_stats_day(y, m)
    )
    return S_STATS_DAY


@owner_only
async def stats_back_month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    y = ctx.user_data.get("stats_year",  date.today().year)
    m = ctx.user_data.get("stats_month", date.today().month)
    return await _render_stats(update.message, ctx, y, m)


@owner_only
async def cancel_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("pending"):
        data = ctx.user_data["pending"]
        ic   = ctx.user_data.get("item_cats_new", {})
        await update.message.reply_text(await _review_text(data, ic), parse_mode="Markdown")
        await update.message.reply_text(
            "Перевір дані та збережи:", reply_markup=kb_review(bool(data.get("items")))
        )
        return S_REVIEW_RECEIPT
    if ctx.user_data.get("viewing"):
        return await back_to_receipt(update, ctx)
    await update.message.reply_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=kb_main())
    return S_MAIN


@owner_only
async def unknown_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📷 Надішли фото чека або вибери розділ у меню нижче.",
        reply_markup=kb_main()
    )


async def go_main_msg(msg):
    await msg.reply_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=kb_main())
    return S_MAIN


F = filters

F_UPLOAD = F.Regex(r"^📷")
F_LIST   = F.Regex(r"^🧾")
F_STATS  = F.Regex(r"^📊")
F_CATS   = F.Regex(r"^📂")

F_MENU   = F.Regex(r"^🏠  Меню$")
F_CANCEL = F.Regex(r"^✕  Скасувати$")

F_SAVE      = F.Regex(r"^✅  Зберегти$")
F_RETRY     = F.Regex(r"^🔄  Нове фото$")
F_SPLIT_BTN = F.Regex(r"^🏷  Категорії товарів$")
F_ED_STORE  = F.Regex(r"^✏️  Магазин$")
F_ED_DATE   = F.Regex(r"^📅  Дата$")
F_ED_TIME   = F.Regex(r"^🕐  Час$")
F_ED_TOTAL  = F.Regex(r"^💰  Сума$")
F_ED_ITEM   = F.Regex(r"^📝  Редагувати товар$")

F_EDIT       = F.Regex(r"^✏️  Редагувати$")
F_DELETE     = F.Regex(r"^🗑  Видалити$")
F_DEL_YES    = F.Regex(r"^🗑  Так, видалити$")
F_BACK_LIST  = F.Regex(r"^◀  До списку$")
F_BACK_RCPT  = F.Regex(r"^◀  До чека$")
F_ITEM_CATS  = F.Regex(r"^🏷  Категорії товарів$")

F_STORE_BTN  = F.Regex(r"^🏪  Магазин$")
F_DATE_BTN   = F.Regex(r"^📅  Дата$")
F_TIME_BTN   = F.Regex(r"^🕐  Час$")
F_TOTAL_BTN  = F.Regex(r"^💰  Сума$")

F_NEXT       = F.Regex(r"^Далі  ▶$")
F_PREV       = F.Regex(r"^◀  Назад$")
F_FILTER_BTN = F.Regex(r"^🔍  Фільтр за категорією$")
F_RESET_F    = F.Regex(r"^✕  Скинути фільтр$")

F_CATS_ADD   = F.Regex(r"^➕  Нова категорія$")

F_STATS_NAV  = F.Regex(r"[◀▶].*[А-ЯҐЄІЇа-яґєії]|[А-ЯҐЄІЇа-яґєії].*[◀▶]")
F_STATS_BACK = F.Regex(r"^◀  [А-ЯҐЄІЇа-яґєії]")

F_SPLIT_SAVE     = F.Regex(r"^✅  Зберегти$")
F_SPLIT_BACK     = F.Regex(r"^◀  До чека$")
F_TO_OVERVIEW    = F.Regex(r"^◀  До огляду$")
F_IC_DONE        = F.Regex(r"^✅  Готово$")

F_ANY_BTN = (
    F_UPLOAD | F_LIST | F_STATS | F_CATS | F_MENU | F_CANCEL |
    F_SAVE | F_RETRY | F_SPLIT_BTN | F_ED_STORE | F_ED_DATE | F_ED_TIME | F_ED_TOTAL | F_ED_ITEM |
    F_EDIT | F_DELETE | F_DEL_YES | F_BACK_LIST | F_BACK_RCPT | F_ITEM_CATS |
    F_STORE_BTN | F_DATE_BTN | F_TIME_BTN | F_TOTAL_BTN |
    F_NEXT | F_PREV | F_FILTER_BTN | F_RESET_F |
    F_CATS_ADD | F_STATS_NAV | F_STATS_BACK |
    F_SPLIT_SAVE | F_SPLIT_BACK | F_TO_OVERVIEW | F_IC_DONE
)

F_TEXT = F.TEXT & ~F.COMMAND & ~F_ANY_BTN


def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не знайдено.")
        return

    async def post_init(application):
        await db.init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    COMMON = [
        MessageHandler(F.PHOTO,      handle_photo_any),
        MessageHandler(F_MENU,       go_main),
        MessageHandler(F_UPLOAD,     reply_upload),
        MessageHandler(F_LIST,       reply_list),
        MessageHandler(F_STATS,      reply_stats),
        MessageHandler(F_CATS,       reply_cats),
        MessageHandler(F_CANCEL,     cancel_btn),
    ]

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            *COMMON,
        ],
        states={
            S_MAIN: [*COMMON],
            S_WAITING_PHOTO: [*COMMON],
            S_REVIEW_RECEIPT: [
                *COMMON,
                MessageHandler(F_SAVE,      review_save),
                MessageHandler(F_RETRY,     review_retry),
                MessageHandler(F_SPLIT_BTN, review_split),
                MessageHandler(F_ED_STORE,  review_edit_field_btn),
                MessageHandler(F_ED_DATE,   review_edit_field_btn),
                MessageHandler(F_ED_TIME,   review_edit_field_btn),
                MessageHandler(F_ED_TOTAL,  review_edit_field_btn),
                MessageHandler(F_ED_ITEM,   review_edit_item_btn),
                CallbackQueryHandler(review_item_select_cb, r"^rei_\d+$"),
            ],
            S_REVIEW_EDIT_FIELD: [
                *COMMON,
                MessageHandler(F_TEXT, review_edit_field_input),
            ],
            S_REVIEW_EDIT_ITEM: [
                *COMMON,
                CallbackQueryHandler(review_item_select_cb, r"^rei_\d+$"),
                MessageHandler(F_TEXT, review_item_input),
            ],
            S_REVIEW_SPLIT: [
                *COMMON,
                CallbackQueryHandler(split_apply_all_cb,   r"^spa_[a-f0-9]+$"),
                CallbackQueryHandler(split_item_select_cb, r"^spi_\d+$"),
                CallbackQueryHandler(split_cat_cb,         r"^sp_\d+_[a-f0-9]+$"),
                MessageHandler(F_SPLIT_SAVE,    split_save_now),
                MessageHandler(F_SPLIT_BACK,    split_back_review),
                MessageHandler(F_TO_OVERVIEW,   split_back_to_overview),
            ],
            S_RECEIPT_LIST: [
                *COMMON,
                CallbackQueryHandler(receipt_open,    r"^receipt_[a-f0-9]+$"),
                CallbackQueryHandler(list_filter_cb,  r"^lf_"),
                MessageHandler(F_NEXT,       list_next),
                MessageHandler(F_PREV,       list_prev),
                MessageHandler(F_FILTER_BTN, list_filter_btn),
                MessageHandler(F_RESET_F,    list_reset_filter),
            ],
            S_RECEIPT_DETAIL: [
                *COMMON,
                CallbackQueryHandler(receipt_cat_cb, r"^rc_[a-f0-9]+_[a-f0-9]+$"),
                CallbackQueryHandler(noop_cb,        r"^noop$"),
                MessageHandler(F_EDIT,      receipt_edit_btn),
                MessageHandler(F_DELETE,    receipt_delete_btn),
                MessageHandler(F_DEL_YES,   receipt_delete_confirm),
                MessageHandler(F_BACK_LIST, back_to_list),
                MessageHandler(F_ITEM_CATS, receipt_item_cats_btn),
            ],
            S_RECEIPT_EDIT_FIELD: [
                *COMMON,
                MessageHandler(F_STORE_BTN, receipt_edit_field),
                MessageHandler(F_DATE_BTN,  receipt_edit_field),
                MessageHandler(F_TIME_BTN,  receipt_edit_field),
                MessageHandler(F_TOTAL_BTN, receipt_edit_field),
                MessageHandler(F_BACK_RCPT, back_to_receipt),
                MessageHandler(F_TEXT,      receipt_edit_input),
            ],
            S_RECEIPT_ITEM_OVERVIEW: [
                *COMMON,
                CallbackQueryHandler(ic_apply_all_cb,   r"^ica_[a-f0-9]+_[a-f0-9]+$"),
                CallbackQueryHandler(ic_item_select_cb, r"^ici_[a-f0-9]+_\d+$"),
                MessageHandler(F_IC_DONE,   ic_overview_done),
                MessageHandler(F_BACK_RCPT, back_to_receipt),
            ],
            S_RECEIPT_ITEM_CATS: [
                *COMMON,
                CallbackQueryHandler(ic_saved_cb,   r"^ic_[a-f0-9]+_\d+_[a-f0-9]+$"),
                MessageHandler(F_TO_OVERVIEW, ic_back_to_overview),
            ],
            S_CATS: [
                *COMMON,
                CallbackQueryHandler(cats_cb, r"^cat_(del|ren)_[a-f0-9]+$"),
                CallbackQueryHandler(noop_cb, r"^noop$"),
                MessageHandler(F_CATS_ADD, cats_add_btn),
            ],
            S_CAT_ADD_NAME: [
                *COMMON,
                MessageHandler(F_TEXT, cat_add_input),
            ],
            S_CAT_RENAME: [
                *COMMON,
                MessageHandler(F_TEXT, cat_rename_input),
            ],
            S_STATS: [
                *COMMON,
                CallbackQueryHandler(stats_day_cb, r"^sd_\d{4}-\d{2}-\d{2}$"),
                CallbackQueryHandler(noop_cb,      r"^noop$"),
                MessageHandler(F_STATS_NAV, stats_nav),
            ],
            S_STATS_DAY: [
                *COMMON,
                CallbackQueryHandler(stats_day_cb, r"^sd_\d{4}-\d{2}-\d{2}$"),
                MessageHandler(F_STATS_BACK, stats_back_month),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            *COMMON,
            MessageHandler(F.ALL, unknown_msg),
        ],
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("Бот запущено.")

    class _Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass

    port = int(os.environ.get("PORT", 10000))
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", port), _Health).serve_forever(),
        daemon=True
    ).start()
    logger.info(f"Health-check сервер запущено на порту {port}")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
