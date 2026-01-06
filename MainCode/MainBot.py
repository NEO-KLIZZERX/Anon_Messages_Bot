import asyncio
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple, Iterable

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)


@dataclass(frozen=True)
class Config:
    token: str
    admin_id: int
    db_path: str
    cooldown_sec: int = 15
    daily_limit_per_pair: int = 30
    default_block_links: int = 1
    inbox_limit: int = 12


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def today_str() -> str:
    return date.today().isoformat()


def has_link(text: str) -> bool:
    t = (text or "").lower()
    return ("http://" in t) or ("https://" in t) or ("t.me/" in t)


class Repo:
    def __init__(self, path: str):
        self.path = path

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def init(self) -> None:
        con = self._con()
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
            updated_at TEXT NOT NULL DEFAULT '',
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
        if "updated_at" not in cols_threads:
            cur.execute("ALTER TABLE threads ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        if "created_at" not in cols_threads:
            cur.execute("ALTER TABLE threads ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")

        con.commit()
        con.close()

    def is_banned(self, user_id: int) -> bool:
        con = self._con()
        row = con.execute("SELECT 1 FROM global_bans WHERE user_id=?", (user_id,)).fetchone()
        con.close()
        return row is not None

    def ban(self, user_id: int) -> None:
        con = self._con()
        con.execute("INSERT OR IGNORE INTO global_bans(user_id, created_at) VALUES(?,?)", (user_id, utc_now_iso()))
        con.commit()
        con.close()

    def unban(self, user_id: int) -> None:
        con = self._con()
        con.execute("DELETE FROM global_bans WHERE user_id=?", (user_id,))
        con.commit()
        con.close()

    def ensure_user(self, user_id: int, default_block_links: int) -> str:
        con = self._con()
        row = con.execute("SELECT code FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row:
            con.close()
            return str(row["code"])
        code = secrets.token_urlsafe(8)
        con.execute(
            "INSERT INTO users(user_id, code, anon_enabled, block_links, created_at) VALUES(?,?,?,?,?)",
            (user_id, code, 1, default_block_links, utc_now_iso()),
        )
        con.commit()
        con.close()
        return code

    def settings(self, user_id: int, default_block_links: int) -> dict:
        con = self._con()
        row = con.execute(
            "SELECT anon_enabled, block_links, code FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        con.close()
        if not row:
            code = self.ensure_user(user_id, default_block_links)
            return {"anon_enabled": 1, "block_links": default_block_links, "code": code}
        return {"anon_enabled": int(row["anon_enabled"]), "block_links": int(row["block_links"]), "code": str(row["code"])}

    def set_anon(self, user_id: int, enabled: bool) -> None:
        con = self._con()
        con.execute("UPDATE users SET anon_enabled=? WHERE user_id=?", (1 if enabled else 0, user_id))
        con.commit()
        con.close()

    def set_block_links(self, user_id: int, enabled: bool) -> None:
        con = self._con()
        con.execute("UPDATE users SET block_links=? WHERE user_id=?", (1 if enabled else 0, user_id))
        con.commit()
        con.close()

    def user_by_code(self, code: str) -> Optional[int]:
        con = self._con()
        row = con.execute("SELECT user_id FROM users WHERE code=?", (code,)).fetchone()
        con.close()
        return int(row["user_id"]) if row else None

    def is_blocked(self, recipient_id: int, sender_id: int) -> bool:
        con = self._con()
        row = con.execute(
            "SELECT 1 FROM blocks WHERE recipient_id=? AND sender_id=?",
            (recipient_id, sender_id),
        ).fetchone()
        con.close()
        return row is not None

    def block(self, recipient_id: int, sender_id: int) -> None:
        con = self._con()
        con.execute(
            "INSERT OR IGNORE INTO blocks(recipient_id, sender_id, created_at) VALUES(?,?,?)",
            (recipient_id, sender_id, utc_now_iso()),
        )
        con.commit()
        con.close()

    def set_pending(self, user_id: int, target_user_id: int) -> None:
        con = self._con()
        con.execute(
            "INSERT INTO pending(user_id, target_user_id, created_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET target_user_id=excluded.target_user_id, created_at=excluded.created_at",
            (user_id, target_user_id, utc_now_iso()),
        )
        con.commit()
        con.close()

    def clear_pending(self, user_id: int) -> None:
        con = self._con()
        con.execute("DELETE FROM pending WHERE user_id=?", (user_id,))
        con.commit()
        con.close()

    def pending_target(self, user_id: int) -> Optional[int]:
        con = self._con()
        row = con.execute("SELECT target_user_id FROM pending WHERE user_id=?", (user_id,)).fetchone()
        con.close()
        return int(row["target_user_id"]) if row else None

    def thread_id(self, recipient_id: int, sender_id: int) -> int:
        con = self._con()
        row = con.execute(
            "SELECT id FROM threads WHERE recipient_id=? AND sender_id=?",
            (recipient_id, sender_id),
        ).fetchone()
        if row:
            tid = int(row["id"])
            con.execute("UPDATE threads SET updated_at=? WHERE id=?", (utc_now_iso(), tid))
            con.commit()
            con.close()
            return tid
        con.execute(
            "INSERT INTO threads(recipient_id, sender_id, created_at, updated_at) VALUES(?,?,?,?)",
            (recipient_id, sender_id, utc_now_iso(), utc_now_iso()),
        )
        con.commit()
        tid = int(con.execute(
            "SELECT id FROM threads WHERE recipient_id=? AND sender_id=?",
            (recipient_id, sender_id),
        ).fetchone()["id"])
        con.close()
        return tid

    def thread_parties(self, thread_id: int) -> Optional[Tuple[int, int]]:
        con = self._con()
        row = con.execute("SELECT recipient_id, sender_id FROM threads WHERE id=?", (thread_id,)).fetchone()
        con.close()
        if not row:
            return None
        return int(row["recipient_id"]), int(row["sender_id"])

    def inbox_threads(self, recipient_id: int, limit: int) -> list[sqlite3.Row]:
        con = self._con()
        rows = con.execute(
            "SELECT id, sender_id, updated_at, created_at FROM threads WHERE recipient_id=? "
            "ORDER BY COALESCE(NULLIF(updated_at,''), created_at) DESC LIMIT ?",
            (recipient_id, limit),
        ).fetchall()
        con.close()
        return list(rows)

    def rate_check_and_touch(self, sender_id: int, recipient_id: int, cooldown_sec: int, daily_limit: int) -> Tuple[bool, str]:
        con = self._con()
        row = con.execute(
            "SELECT last_ts, day, day_count FROM rate_pair WHERE sender_id=? AND recipient_id=?",
            (sender_id, recipient_id),
        ).fetchone()

        now = time.time()
        today = today_str()

        if not row:
            con.execute(
                "INSERT INTO rate_pair(sender_id, recipient_id, last_ts, day, day_count) VALUES(?,?,?,?,?)",
                (sender_id, recipient_id, now, today, 1),
            )
            con.commit()
            con.close()
            return False, ""

        last_ts = float(row["last_ts"])
        day = str(row["day"])
        day_count = int(row["day_count"])

        if now - last_ts < cooldown_sec:
            con.close()
            wait = max(1, int(cooldown_sec - (now - last_ts)))
            return True, f"Слишком часто. Подожди {wait} сек."

        if day != today:
            day = today
            day_count = 0

        if day_count + 1 > daily_limit:
            con.close()
            return True, "Дневной лимит на сообщения этому пользователю исчерпан."

        con.execute(
            "UPDATE rate_pair SET last_ts=?, day=?, day_count=? WHERE sender_id=? AND recipient_id=?",
            (now, day, day_count + 1, sender_id, recipient_id),
        )
        con.commit()
        con.close()
        return False, ""

    def stats(self) -> Tuple[int, int, int]:
        con = self._con()
        users = int(con.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
        threads = int(con.execute("SELECT COUNT(*) AS c FROM threads").fetchone()["c"])
        bans = int(con.execute("SELECT COUNT(*) AS c FROM global_bans").fetchone()["c"])
        con.close()
        return users, threads, bans


def kb_main(settings: dict) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="📎 Моя ссылка", callback_data="ui:link")
    kb.button(
        text=("🔕 Анонимки: выкл" if settings["anon_enabled"] == 0 else "🔔 Анонимки: вкл"),
        callback_data="ui:toggle_anon",
    )
    kb.button(
        text=("🔗 Ссылки: блок" if settings["block_links"] == 1 else "🔗 Ссылки: разреш"),
        callback_data="ui:toggle_links",
    )
    kb.button(text="📥 Инбокс", callback_data="ui:inbox")
    kb.adjust(1)
    return kb


def kb_inbound(thread_id: int, sender_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Ответить", callback_data=f"reply:{thread_id}")
    kb.button(text="🚫 Заблокировать", callback_data=f"block:{sender_id}")
    kb.button(text="⚠️ Пожаловаться", callback_data=f"report:{thread_id}")
    kb.adjust(2, 1)
    return kb


def kb_inbox_list(threads: Iterable[sqlite3.Row]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for r in threads:
        tid = int(r["id"])
        kb.button(text=f"💬 Диалог #{tid}", callback_data=f"inbox:{tid}")
    kb.adjust(1)
    return kb


async def run_bot(cfg: Config) -> None:
    repo = Repo(cfg.db_path)
    repo.init()

    bot = Bot(cfg.token)
    dp = Dispatcher()

    async def me_username() -> str:
        me = await bot.get_me()
        return me.username or ""

    async def render_home(user_id: int) -> Tuple[str, InlineKeyboardBuilder]:
        s = repo.settings(user_id, cfg.default_block_links)
        username = await me_username()
        link = f"https://t.me/{username}?start=u_{s['code']}"
        return link, kb_main(s)

    async def deliver(sender_id: int, recipient_id: int, msg: Message) -> None:
        if repo.is_banned(sender_id):
            await msg.answer("Доступ ограничен.")
            return

        if repo.is_banned(recipient_id) or repo.is_blocked(recipient_id, sender_id):
            repo.clear_pending(sender_id)
            await msg.answer("Сообщение не доставлено.")
            return

        rs = repo.settings(recipient_id, cfg.default_block_links)
        if rs["anon_enabled"] == 0:
            repo.clear_pending(sender_id)
            await msg.answer("У пользователя отключены анонимные сообщения.")
            return

        limited, reason = repo.rate_check_and_touch(
            sender_id, recipient_id, cfg.cooldown_sec, cfg.daily_limit_per_pair
        )
        if limited:
            await msg.answer(reason)
            return

        text = msg.text or msg.caption or ""
        if rs["block_links"] == 1 and text and has_link(text):
            await msg.answer("Ссылки запрещены у получателя. Уберите ссылку и попробуйте снова.")
            return

        thread_id = repo.thread_id(recipient_id, sender_id)
        markup = kb_inbound(thread_id, sender_id).as_markup()

        await msg.answer("Отправлено ✅")

        if msg.text:
            await bot.send_message(recipient_id, f"📩 Анонимное сообщение:\n\n{msg.text}", reply_markup=markup)
        elif msg.photo:
            await bot.send_photo(
                recipient_id, msg.photo[-1].file_id,
                caption=(msg.caption or "📩 Анонимное сообщение"),
                reply_markup=markup,
            )
        elif msg.video:
            await bot.send_video(
                recipient_id, msg.video.file_id,
                caption=(msg.caption or "📩 Анонимное сообщение"),
                reply_markup=markup,
            )
        elif msg.voice:
            await bot.send_voice(
                recipient_id, msg.voice.file_id,
                caption=(msg.caption or "📩 Анонимное сообщение"),
                reply_markup=markup,
            )
        elif msg.video_note:
            await bot.send_video_note(recipient_id, msg.video_note.file_id, reply_markup=markup)
        elif msg.document:
            await bot.send_document(
                recipient_id, msg.document.file_id,
                caption=(msg.caption or "📩 Анонимное сообщение"),
                reply_markup=markup,
            )
        else:
            await bot.send_message(recipient_id, "📩 Анонимное сообщение", reply_markup=markup)

        repo.clear_pending(sender_id)

    @dp.message(Command("id"))
    async def cmd_id(m: Message):
        if m.from_user:
            await m.answer(f"Ваш ID: {m.from_user.id}")

    @dp.message(CommandStart())
    async def on_start(m: Message):
        if not m.from_user:
            return
        user_id = m.from_user.id

        if repo.is_banned(user_id):
            await m.answer("Доступ ограничен.")
            return

        repo.ensure_user(user_id, cfg.default_block_links)

        parts = (m.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("u_"):
            code = parts[1][2:]
            recipient_id = repo.user_by_code(code)
            if not recipient_id:
                await m.answer("Ссылка недействительна.")
                return

            if recipient_id == user_id:
                link, home_kb = await render_home(user_id)
                await m.answer(f"Ваша ссылка:\n{link}", reply_markup=home_kb.as_markup())
                return

            rs = repo.settings(recipient_id, cfg.default_block_links)
            if rs["anon_enabled"] == 0:
                await m.answer("У пользователя отключены анонимные сообщения.")
                return

            if repo.is_blocked(recipient_id, user_id):
                await m.answer("Вы не можете отправлять сообщения этому пользователю.")
                return

            repo.set_pending(user_id, recipient_id)
            await m.answer("Напишите сообщение — я доставлю его анонимно.")
            return

        link, home_kb = await render_home(user_id)
        s = repo.settings(user_id, cfg.default_block_links)
        status = "включены" if s["anon_enabled"] == 1 else "отключены"
        await m.answer(f"Анонимные сообщения: {status}\n\nВаша ссылка:\n{link}", reply_markup=home_kb.as_markup())

    @dp.message(Command("my"))
    async def cmd_my(m: Message):
        if not m.from_user:
            return
        user_id = m.from_user.id
        if repo.is_banned(user_id):
            await m.answer("Доступ ограничен.")
            return
        link, home_kb = await render_home(user_id)
        await m.answer(f"Ваша ссылка:\n{link}", reply_markup=home_kb.as_markup())

    @dp.message(Command("settings"))
    async def cmd_settings(m: Message):
        if not m.from_user:
            return
        user_id = m.from_user.id
        if repo.is_banned(user_id):
            await m.answer("Доступ ограничен.")
            return
        link, home_kb = await render_home(user_id)
        await m.answer(f"Настройки:\n{link}", reply_markup=home_kb.as_markup())

    @dp.callback_query(F.data == "ui:link")
    async def ui_link(c: CallbackQuery):
        if not c.from_user:
            return
        link, home_kb = await render_home(c.from_user.id)
        await c.answer()
        await c.message.edit_text(f"Ваша ссылка:\n{link}", reply_markup=home_kb.as_markup())

    @dp.callback_query(F.data == "ui:toggle_anon")
    async def ui_toggle_anon(c: CallbackQuery):
        if not c.from_user:
            return
        uid = c.from_user.id
        s = repo.settings(uid, cfg.default_block_links)
        repo.set_anon(uid, s["anon_enabled"] == 0)
        link, home_kb = await render_home(uid)
        await c.answer("Готово.")
        await c.message.edit_text(f"Ваша ссылка:\n{link}", reply_markup=home_kb.as_markup())

    @dp.callback_query(F.data == "ui:toggle_links")
    async def ui_toggle_links(c: CallbackQuery):
        if not c.from_user:
            return
        uid = c.from_user.id
        s = repo.settings(uid, cfg.default_block_links)
        repo.set_block_links(uid, s["block_links"] == 0)
        link, home_kb = await render_home(uid)
        await c.answer("Готово.")
        await c.message.edit_text(f"Ваша ссылка:\n{link}", reply_markup=home_kb.as_markup())

    @dp.callback_query(F.data == "ui:inbox")
    async def ui_inbox(c: CallbackQuery):
        if not c.from_user:
            return
        uid = c.from_user.id
        if repo.is_banned(uid):
            await c.answer("Доступ ограничен.", show_alert=True)
            return
        threads = repo.inbox_threads(uid, cfg.inbox_limit)
        if not threads:
            await c.answer()
            await c.message.edit_text("Инбокс пуст.", reply_markup=None)
            return
        await c.answer()
        await c.message.edit_text("Последние диалоги:", reply_markup=kb_inbox_list(threads).as_markup())

    @dp.callback_query(F.data.startswith("inbox:"))
    async def inbox_open(c: CallbackQuery):
        if not c.from_user:
            return
        uid = c.from_user.id
        tid = int(c.data.split(":", 1)[1])
        parties = repo.thread_parties(tid)
        if not parties:
            await c.answer("Диалог не найден.", show_alert=True)
            return
        recipient_id, sender_id = parties
        if uid != recipient_id:
            await c.answer("Нет доступа.", show_alert=True)
            return
        await c.answer()
        await c.message.edit_text(
            f"Диалог #{tid}\n\nНажмите «Ответить», чтобы написать.",
            reply_markup=kb_inbound(tid, sender_id).as_markup(),
        )

    @dp.message(F.content_type.in_({"text", "photo", "video", "voice", "video_note", "document"}))
    async def on_content(m: Message):
        if not m.from_user:
            return
        sender_id = m.from_user.id
        target = repo.pending_target(sender_id)
        if not target:
            await m.answer("Чтобы отправить анонимное сообщение, перейдите по персональной ссылке получателя.")
            return
        await deliver(sender_id, target, m)

    @dp.callback_query(F.data.startswith("block:"))
    async def on_block(c: CallbackQuery):
        if not c.from_user:
            return
        recipient_id = c.from_user.id
        sender_id = int(c.data.split(":", 1)[1])
        repo.block(recipient_id, sender_id)
        await c.answer("Заблокировано.")
        try:
            await c.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("reply:"))
    async def on_reply(c: CallbackQuery):
        if not c.from_user:
            return
        uid = c.from_user.id
        tid = int(c.data.split(":", 1)[1])
        parties = repo.thread_parties(tid)
        if not parties:
            await c.answer("Тред не найден.", show_alert=True)
            return
        recipient_id, sender_id = parties
        if uid != recipient_id:
            await c.answer("Нельзя ответить.", show_alert=True)
            return
        if repo.is_banned(sender_id) or repo.is_blocked(sender_id, recipient_id):
            await c.answer("Нельзя ответить.", show_alert=True)
            return
        repo.set_pending(recipient_id, sender_id)
        await c.answer()
        await c.message.reply("Напишите ответ — я доставлю его анонимно отправителю.")

    @dp.callback_query(F.data.startswith("report:"))
    async def on_report(c: CallbackQuery):
        if not c.from_user:
            return
        reporter_id = c.from_user.id
        tid = int(c.data.split(":", 1)[1])
        parties = repo.thread_parties(tid)
        if not parties:
            await c.answer("Тред не найден.", show_alert=True)
            return
        recipient_id, sender_id = parties
        if reporter_id != recipient_id:
            await c.answer("Нельзя.", show_alert=True)
            return

        await c.answer("Жалоба отправлена.")
        if cfg.admin_id > 0:
            try:
                await bot.send_message(
                    cfg.admin_id,
                    f"⚠️ Жалоба\nthread_id={tid}\nrecipient_id={recipient_id}\nsender_id={sender_id}\n"
                    f"/ban {sender_id}\n/unban {sender_id}",
                )
            except Exception:
                pass

    @dp.message(Command("ban"))
    async def cmd_ban(m: Message):
        if not m.from_user or m.from_user.id != cfg.admin_id:
            return
        parts = (m.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await m.answer("Использование: /ban <user_id>")
            return
        uid = int(parts[1])
        repo.ban(uid)
        await m.answer(f"Забанен: {uid}")

    @dp.message(Command("unban"))
    async def cmd_unban(m: Message):
        if not m.from_user or m.from_user.id != cfg.admin_id:
            return
        parts = (m.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await m.answer("Использование: /unban <user_id>")
            return
        uid = int(parts[1])
        repo.unban(uid)
        await m.answer(f"Разбанен: {uid}")

    @dp.message(Command("stats"))
    async def cmd_stats(m: Message):
        if not m.from_user or m.from_user.id != cfg.admin_id:
            return
        users, threads, bans = repo.stats()
        await m.answer(f"users={users}\nthreads={threads}\nbans={bans}")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    cfg = Config(
        token="8573434477:AAHLmz9v_oayas5aCIh1vI3WxoG17LpY76A",
        admin_id=6034590034,
        db_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "anon.db"),
    )
    asyncio.run(run_bot(cfg))
