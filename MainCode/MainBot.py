import asyncio
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, date

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = "8573434477:AAHLmz9v_oayas5aCIh1vI3WxoG17LpY76A"
ADMIN_ID = 6034590034

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anon.db")

logging.basicConfig(level=logging.INFO)

COOLDOWN_SEC = 15
DAILY_LIMIT_PER_PAIR = 30
BLOCK_LINKS_DEFAULT = 1

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        anon_enabled INTEGER NOT NULL DEFAULT 1,
        block_links INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS blocks (
        recipient_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (recipient_id, sender_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(recipient_id, sender_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pending (
        user_id INTEGER PRIMARY KEY,
        target_user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS global_bans (
        user_id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS rate_pair (
        sender_id INTEGER NOT NULL,
        recipient_id INTEGER NOT NULL,
        last_ts REAL NOT NULL,
        day TEXT NOT NULL,
        day_count INTEGER NOT NULL,
        PRIMARY KEY (sender_id, recipient_id)
    )
    """)
    con.commit()
    con.close()

def ensure_schema():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        anon_enabled INTEGER NOT NULL DEFAULT 1,
        block_links INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS blocks (
        recipient_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (recipient_id, sender_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(recipient_id, sender_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pending (
        user_id INTEGER PRIMARY KEY,
        target_user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS global_bans (
        user_id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS rate_pair (
        sender_id INTEGER NOT NULL,
        recipient_id INTEGER NOT NULL,
        last_ts REAL NOT NULL,
        day TEXT NOT NULL,
        day_count INTEGER NOT NULL,
        PRIMARY KEY (sender_id, recipient_id)
    )
    """)

    cols_users = {r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
    if "anon_enabled" not in cols_users:
        cur.execute("ALTER TABLE users ADD COLUMN anon_enabled INTEGER NOT NULL DEFAULT 1")
    if "block_links" not in cols_users:
        cur.execute("ALTER TABLE users ADD COLUMN block_links INTEGER NOT NULL DEFAULT 1")
    if "created_at" not in cols_users:
        cur.execute("ALTER TABLE users ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")

    cols_threads = {r[1] for r in cur.execute("PRAGMA table_info(threads)").fetchall()}
    if "created_at" not in cols_threads:
        cur.execute("ALTER TABLE threads ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")

    con.commit()
    con.close()

def now_iso():
    return datetime.utcnow().isoformat()

def today_str():
    return date.today().isoformat()

def is_globally_banned(user_id: int) -> bool:
    con = db()
    row = con.execute("SELECT 1 FROM global_bans WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row is not None

def ban_user(user_id: int):
    con = db()
    con.execute("INSERT OR IGNORE INTO global_bans(user_id, created_at) VALUES(?,?)", (user_id, now_iso()))
    con.commit()
    con.close()

def unban_user(user_id: int):
    con = db()
    con.execute("DELETE FROM global_bans WHERE user_id=?", (user_id,))
    con.commit()
    con.close()

def get_or_create_user(user_id: int) -> str:
    con = db()
    row = con.execute("SELECT code FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        con.close()
        return row["code"]
    code = secrets.token_urlsafe(8)
    con.execute(
        "INSERT INTO users(user_id, code, anon_enabled, block_links, created_at) VALUES(?,?,?,?,?)",
        (user_id, code, 1, BLOCK_LINKS_DEFAULT, now_iso())
    )
    con.commit()
    con.close()
    return code

def get_user_settings(user_id: int):
    con = db()
    row = con.execute("SELECT anon_enabled, block_links, code FROM users WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    if not row:
        code = get_or_create_user(user_id)
        return {"anon_enabled": 1, "block_links": BLOCK_LINKS_DEFAULT, "code": code}
    return {"anon_enabled": int(row["anon_enabled"]), "block_links": int(row["block_links"]), "code": row["code"]}

def set_anon_enabled(user_id: int, enabled: bool):
    con = db()
    con.execute("UPDATE users SET anon_enabled=? WHERE user_id=?", (1 if enabled else 0, user_id))
    con.commit()
    con.close()

def set_block_links(user_id: int, enabled: bool):
    con = db()
    con.execute("UPDATE users SET block_links=? WHERE user_id=?", (1 if enabled else 0, user_id))
    con.commit()
    con.close()

def resolve_recipient_by_code(code: str) -> int | None:
    con = db()
    row = con.execute("SELECT user_id FROM users WHERE code=?", (code,)).fetchone()
    con.close()
    return int(row["user_id"]) if row else None

def is_blocked(recipient_id: int, sender_id: int) -> bool:
    con = db()
    row = con.execute(
        "SELECT 1 FROM blocks WHERE recipient_id=? AND sender_id=?",
        (recipient_id, sender_id)
    ).fetchone()
    con.close()
    return row is not None

def block_sender(recipient_id: int, sender_id: int):
    con = db()
    con.execute(
        "INSERT OR IGNORE INTO blocks(recipient_id, sender_id, created_at) VALUES(?,?,?)",
        (recipient_id, sender_id, now_iso())
    )
    con.commit()
    con.close()

def get_thread_id(recipient_id: int, sender_id: int) -> int:
    con = db()
    row = con.execute(
        "SELECT id FROM threads WHERE recipient_id=? AND sender_id=?",
        (recipient_id, sender_id)
    ).fetchone()
    if row:
        con.close()
        return int(row["id"])
    con.execute(
        "INSERT INTO threads(recipient_id, sender_id, created_at) VALUES(?,?,?)",
        (recipient_id, sender_id, now_iso())
    )
    con.commit()
    tid = con.execute(
        "SELECT id FROM threads WHERE recipient_id=? AND sender_id=?",
        (recipient_id, sender_id)
    ).fetchone()["id"]
    con.close()
    return int(tid)

def get_thread_parties(thread_id: int):
    con = db()
    row = con.execute("SELECT recipient_id, sender_id FROM threads WHERE id=?", (thread_id,)).fetchone()
    con.close()
    if not row:
        return None
    return int(row["recipient_id"]), int(row["sender_id"])

def set_pending(user_id: int, target_user_id: int):
    con = db()
    con.execute(
        "INSERT INTO pending(user_id, target_user_id, created_at) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET target_user_id=excluded.target_user_id, created_at=excluded.created_at",
        (user_id, target_user_id, now_iso())
    )
    con.commit()
    con.close()

def clear_pending(user_id: int):
    con = db()
    con.execute("DELETE FROM pending WHERE user_id=?", (user_id,))
    con.commit()
    con.close()

def get_pending_target(user_id: int) -> int | None:
    con = db()
    row = con.execute("SELECT target_user_id FROM pending WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return int(row["target_user_id"]) if row else None

def is_rate_limited(sender_id: int, recipient_id: int) -> tuple[bool, str]:
    con = db()
    row = con.execute(
        "SELECT last_ts, day, day_count FROM rate_pair WHERE sender_id=? AND recipient_id=?",
        (sender_id, recipient_id)
    ).fetchone()
    now = time.time()
    today = today_str()

    if not row:
        con.execute(
            "INSERT INTO rate_pair(sender_id, recipient_id, last_ts, day, day_count) VALUES(?,?,?,?,?)",
            (sender_id, recipient_id, now, today, 1)
        )
        con.commit()
        con.close()
        return False, ""

    last_ts = float(row["last_ts"])
    day = str(row["day"])
    day_count = int(row["day_count"])

    if now - last_ts < COOLDOWN_SEC:
        con.close()
        return True, f"Слишком часто. Подожди {max(1, int(COOLDOWN_SEC - (now - last_ts)))} сек."

    if day != today:
        day_count = 0
        day = today

    if day_count + 1 > DAILY_LIMIT_PER_PAIR:
        con.close()
        return True, "Дневной лимит на сообщения этому пользователю исчерпан."

    con.execute(
        "UPDATE rate_pair SET last_ts=?, day=?, day_count=? WHERE sender_id=? AND recipient_id=?",
        (now, day, day_count + 1, sender_id, recipient_id)
    )
    con.commit()
    con.close()
    return False, ""

def has_link(text: str) -> bool:
    t = (text or "").lower()
    return ("http://" in t) or ("https://" in t) or ("t.me/" in t)

def main_menu_kb(user_id: int, me_username: str):
    s = get_user_settings(user_id)
    link = f"https://t.me/{me_username}?start=u_{s['code']}"
    kb = InlineKeyboardBuilder()
    kb.button(text="📎 Моя ссылка", callback_data="ui:link")
    kb.button(text=("🔕 Анонимки: выкл" if s["anon_enabled"] == 0 else "🔔 Анонимки: вкл"), callback_data="ui:toggle_anon")
    kb.button(text=("🔗 Ссылки: блок" if s["block_links"] == 1 else "🔗 Ссылки: разреш"), callback_data="ui:toggle_links")
    kb.adjust(1)
    return link, kb.as_markup()

def inbound_msg_kb(thread_id: int, sender_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Ответить", callback_data=f"reply:{thread_id}")
    kb.button(text="🚫 Заблокировать", callback_data=f"block:{sender_id}")
    kb.button(text="⚠️ Пожаловаться", callback_data=f"report:{thread_id}")
    kb.adjust(2, 1)
    return kb.as_markup()

async def main():
    init_db()
    ensure_schema()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(Command("id"))
    async def cmd_id(m: Message):
        if not m.from_user:
            return
        await m.answer(f"Ваш ID: {m.from_user.id}")

    @dp.message(CommandStart())
    async def start(m: Message):
        if not m.from_user:
            return
        user_id = m.from_user.id
        if is_globally_banned(user_id):
            return await m.answer("Доступ ограничен.")
        get_or_create_user(user_id)

        me = await bot.get_me()
        args = (m.text or "").split(maxsplit=1)

        if len(args) == 2 and args[1].startswith("u_"):
            target_code = args[1][2:]
            recipient_id = resolve_recipient_by_code(target_code)
            if not recipient_id:
                return await m.answer("Ссылка недействительна.")
            if recipient_id == user_id:
                link, markup = main_menu_kb(user_id, me.username)
                return await m.answer(f"Ваша ссылка:\n{link}", reply_markup=markup)

            rs = get_user_settings(recipient_id)
            if rs["anon_enabled"] == 0:
                return await m.answer("У пользователя отключены анонимные сообщения.")
            if is_blocked(recipient_id, user_id):
                return await m.answer("Вы не можете отправлять сообщения этому пользователю.")

            set_pending(user_id, recipient_id)
            return await m.answer("Напишите сообщение — я доставлю его анонимно.")

        link, markup = main_menu_kb(user_id, me.username)
        s = get_user_settings(user_id)
        status = "включены" if s["anon_enabled"] == 1 else "отключены"
        await m.answer(f"Анонимные сообщения: {status}\n\nВаша ссылка:\n{link}", reply_markup=markup)

    @dp.message(Command("my"))
    async def my(m: Message):
        if not m.from_user:
            return
        user_id = m.from_user.id
        if is_globally_banned(user_id):
            return await m.answer("Доступ ограничен.")
        me = await bot.get_me()
        link, markup = main_menu_kb(user_id, me.username)
        await m.answer(f"Ваша ссылка:\n{link}", reply_markup=markup)

    @dp.message(Command("settings"))
    async def settings(m: Message):
        if not m.from_user:
            return
        user_id = m.from_user.id
        if is_globally_banned(user_id):
            return await m.answer("Доступ ограничен.")
        me = await bot.get_me()
        link, markup = main_menu_kb(user_id, me.username)
        await m.answer(f"Настройки:\n{link}", reply_markup=markup)

    @dp.callback_query(F.data == "ui:link")
    async def ui_link(c: CallbackQuery):
        user_id = c.from_user.id
        me = await bot.get_me()
        link, markup = main_menu_kb(user_id, me.username)
        await c.answer()
        await c.message.edit_text(f"Ваша ссылка:\n{link}", reply_markup=markup)

    @dp.callback_query(F.data == "ui:toggle_anon")
    async def ui_toggle_anon(c: CallbackQuery):
        user_id = c.from_user.id
        s = get_user_settings(user_id)
        set_anon_enabled(user_id, s["anon_enabled"] == 0)
        me = await bot.get_me()
        link, markup = main_menu_kb(user_id, me.username)
        await c.answer("Готово.")
        await c.message.edit_text(f"Ваша ссылка:\n{link}", reply_markup=markup)

    @dp.callback_query(F.data == "ui:toggle_links")
    async def ui_toggle_links(c: CallbackQuery):
        user_id = c.from_user.id
        s = get_user_settings(user_id)
        set_block_links(user_id, s["block_links"] == 0)
        me = await bot.get_me()
        link, markup = main_menu_kb(user_id, me.username)
        await c.answer("Готово.")
        await c.message.edit_text(f"Ваша ссылка:\n{link}", reply_markup=markup)

    async def deliver(sender_id: int, recipient_id: int, msg: Message):
        if is_globally_banned(sender_id):
            return await msg.answer("Доступ ограничен.")
        if is_globally_banned(recipient_id):
            clear_pending(sender_id)
            return await msg.answer("Сообщение не доставлено.")
        if is_blocked(recipient_id, sender_id):
            clear_pending(sender_id)
            return await msg.answer("Сообщение не доставлено.")
        rs = get_user_settings(recipient_id)
        if rs["anon_enabled"] == 0:
            clear_pending(sender_id)
            return await msg.answer("У пользователя отключены анонимные сообщения.")

        limited, reason = is_rate_limited(sender_id, recipient_id)
        if limited:
            return await msg.answer(reason)

        text = msg.text or msg.caption or ""
        if rs["block_links"] == 1 and text and has_link(text):
            return await msg.answer("Ссылки запрещены у получателя. Уберите ссылку и попробуйте снова.")

        thread_id = get_thread_id(recipient_id, sender_id)
        kb = inbound_msg_kb(thread_id, sender_id)

        await msg.answer("Отправлено ✅")

        if msg.text:
            await bot.send_message(recipient_id, f"📩 Анонимное сообщение:\n\n{msg.text}", reply_markup=kb)
        elif msg.photo:
            await bot.send_photo(recipient_id, msg.photo[-1].file_id, caption=(msg.caption or "📩 Анонимное сообщение"), reply_markup=kb)
        elif msg.video:
            await bot.send_video(recipient_id, msg.video.file_id, caption=(msg.caption or "📩 Анонимное сообщение"), reply_markup=kb)
        elif msg.voice:
            await bot.send_voice(recipient_id, msg.voice.file_id, caption=(msg.caption or "📩 Анонимное сообщение"), reply_markup=kb)
        elif msg.video_note:
            await bot.send_video_note(recipient_id, msg.video_note.file_id, reply_markup=kb)
        elif msg.document:
            await bot.send_document(recipient_id, msg.document.file_id, caption=(msg.caption or "📩 Анонимное сообщение"), reply_markup=kb)
        else:
            await bot.send_message(recipient_id, "📩 Анонимное сообщение", reply_markup=kb)

        clear_pending(sender_id)

    @dp.message(F.content_type.in_({"text", "photo", "video", "voice", "video_note", "document"}))
    async def any_content(m: Message):
        if not m.from_user:
            return
        sender_id = m.from_user.id
        target = get_pending_target(sender_id)
        if not target:
            return await m.answer("Чтобы отправить анонимное сообщение, перейдите по персональной ссылке получателя.")
        await deliver(sender_id, target, m)

    @dp.callback_query(F.data.startswith("block:"))
    async def on_block(c: CallbackQuery):
        recipient_id = c.from_user.id
        sender_id = int(c.data.split(":", 1)[1])
        block_sender(recipient_id, sender_id)
        await c.answer("Заблокировано.")
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("reply:"))
    async def on_reply(c: CallbackQuery):
        user_id = c.from_user.id
        thread_id = int(c.data.split(":", 1)[1])
        parties = get_thread_parties(thread_id)
        if not parties:
            return await c.answer("Тред не найден.", show_alert=True)
        recipient_id, sender_id = parties
        if user_id != recipient_id:
            return await c.answer("Нельзя ответить.", show_alert=True)
        if is_blocked(sender_id, recipient_id) or is_globally_banned(sender_id):
            return await c.answer("Нельзя ответить.", show_alert=True)
        set_pending(recipient_id, sender_id)
        await c.answer()
        await c.message.reply("Напишите ответ — я доставлю его анонимно отправителю.")

    @dp.callback_query(F.data.startswith("report:"))
    async def on_report(c: CallbackQuery):
        reporter_id = c.from_user.id
        thread_id = int(c.data.split(":", 1)[1])
        parties = get_thread_parties(thread_id)
        if not parties:
            return await c.answer("Тред не найден.", show_alert=True)
        recipient_id, sender_id = parties
        if reporter_id != recipient_id:
            return await c.answer("Нельзя.", show_alert=True)
        await c.answer("Жалоба отправлена.")
        if ADMIN_ID and isinstance(ADMIN_ID, int) and ADMIN_ID > 0:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ Жалоба\nthread_id={thread_id}\nrecipient_id={recipient_id}\nsender_id={sender_id}\n"
                    f"Действия: /ban {sender_id} или /unban {sender_id}"
                )
            except Exception:
                pass

    @dp.message(Command("ban"))
    async def cmd_ban(m: Message):
        if not m.from_user or m.from_user.id != ADMIN_ID:
            return
        parts = (m.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            return await m.answer("Использование: /ban <user_id>")
        uid = int(parts[1])
        ban_user(uid)
        await m.answer(f"Забанен: {uid}")

    @dp.message(Command("unban"))
    async def cmd_unban(m: Message):
        if not m.from_user or m.from_user.id != ADMIN_ID:
            return
        parts = (m.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            return await m.answer("Использование: /unban <user_id>")
        uid = int(parts[1])
        unban_user(uid)
        await m.answer(f"Разбанен: {uid}")

    @dp.message(Command("stats"))
    async def cmd_stats(m: Message):
        if not m.from_user or m.from_user.id != ADMIN_ID:
            return
        con = db()
        users = con.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        threads = con.execute("SELECT COUNT(*) AS c FROM threads").fetchone()["c"]
        bans = con.execute("SELECT COUNT(*) AS c FROM global_bans").fetchone()["c"]
        con.close()
        await m.answer(f"users={users}\nthreads={threads}\nbans={bans}")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
