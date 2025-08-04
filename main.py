#!/usr/bin/env python3
"""
Telegram Bot for Twitter/X Automation
– IST timezone
– Individual account management
– Automatic tweet-link extraction
– Admin user-management suite
– Robust file-upload workflow (aiogram 3.x)
"""

# -----------------------------  STANDARD LIBS  ------------------------------
import asyncio
import hashlib
import json
import logging
import os
import random
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# -----------------------------  THIRD-PARTY  --------------------------------
from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

try:
    from playwright.async_api import async_playwright
    import aiofiles
    import jsonschema
except ImportError as e:
    print(f"Missing package: {e}.  Run:")
    print("   pip install aiogram playwright aiofiles jsonschema")
    print("   playwright install chromium")
    raise SystemExit(1)

# -----------------------------  CONFIGURATION  ------------------------------
BOT_TOKEN             = "YOUR_REAL_BOT_TOKEN"
DEFAULT_ADMIN_CODE    = "CHANGE_ME"
YOUR_TELEGRAM_USER_ID = 123456789            # ← replace with your own ID

DATA_DIR              = Path("data")
LOG_LEVEL             = "INFO"
MAX_FILE_SIZE         = 10 * 1024 * 1024      # 10 MB
MAX_TWEET_LENGTH      = 280
POST_DELAY_MIN        = 5
POST_DELAY_MAX        = 15
BROWSER_HEADLESS      = True
TWEET_LINK_WAIT_TIME  = 10                    # seconds
IST                   = timezone(timedelta(hours=5, minutes=30))

DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler(DATA_DIR / "bot.log"),
              logging.StreamHandler()]
)
logger = logging.getLogger("bot")

# -----------------------------  JSON SCHEMAS  --------------------------------
ACCOUNTS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "cookies":      {"type": "array"},
            "origins":      {"type": "array"},
            "localStorage": {"type": "array"}
        },
        "required": ["cookies"]
    }
}

# --------------------------  STATE MACHINES  ---------------------------------
class AuthState(StatesGroup):
    waiting_for_code = State()

class UploadKeysState(StatesGroup):
    waiting_for_file = State()

class UploadTweetsState(StatesGroup):
    waiting_for_file = State()

class ScheduleState(StatesGroup):
    waiting_for_time = State()

class BlockState(StatesGroup):
    waiting_for_uid = State()

class UnblockState(StatesGroup):
    waiting_for_uid = State()

class ChangeCodeState(StatesGroup):
    waiting_for_new_code = State()

class UserDataState(StatesGroup):
    waiting_for_uid = State()

# ----------------------------  HELPERS  --------------------------------------
def ist_now() -> datetime:
    return datetime.now(IST)

def ist_from_string(s: str) -> Optional[datetime]:
    fmts = ["%d %B %Y @%I:%M%p", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M",
            "%d-%m-%Y %H:%M", "%d %B %Y %H:%M"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).replace(tzinfo=IST)
        except ValueError:
            pass
    return None

def extract_tweet_url(page_url: str) -> Optional[str]:
    m = re.search(r"(?:twitter\.com|x\.com)/[^/]+/status/(\d+)", page_url)
    return f"https://x.com/i/status/{m.group(1)}" if m else None

# ------------------------------  BOT  ----------------------------------------
class BotError(Exception): ...

class TwitterBot:
    ADMIN_FILE   = DATA_DIR / "admin_code.json"
    USERS_FILE   = DATA_DIR / "authorized_users.json"
    BLOCK_FILE   = DATA_DIR / "blocked_users.json"

    def __init__(self) -> None:
        if BOT_TOKEN == "YOUR_REAL_BOT_TOKEN":
            raise BotError("Set BOT_TOKEN before running.")
        self.bot  = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
        self.dp   = Dispatcher(storage=MemoryStorage())
        self.r    = Router()
        self.dp.include_router(self.r)

        self.admin_code     = self._load_admin_code()
        self.authorized     = set(self._load_json(self.USERS_FILE)  or [])
        self.blocked        = set(self._load_json(self.BLOCK_FILE)  or [])
        self.active_tasks   = {}

        self._setup_handlers()

    # ----------  JSON helpers ----------
    @staticmethod
    def _load_json(path: Path):
        if path.exists():
            try:
                return json.loads(path.read_text("utf-8"))
            except Exception as e:
                logger.error(f"Load {path}: {e}")
        return None

    @staticmethod
    def _save_json(path: Path, data) -> None:
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Save {path}: {e}")

    # ----------  admin code -------------
    def _load_admin_code(self) -> str:
        data = self._load_json(self.ADMIN_FILE) or {}
        code = data.get("admin_code", DEFAULT_ADMIN_CODE)
        self._save_json(self.ADMIN_FILE, {"admin_code": code})
        return code

    # ----------  permissions -----------
    def is_admin(self, uid: int) -> bool:
        return uid == YOUR_TELEGRAM_USER_ID

    def is_blocked(self, uid: int) -> bool:
        return uid in self.blocked

    def is_auth(self, uid: int) -> bool:
        return uid in self.authorized and not self.is_blocked(uid)

    def authorize(self, uid: int) -> None:
        self.authorized.add(uid)
        self._save_json(self.USERS_FILE, list(self.authorized))

    def block(self, uid: int) -> None:
        self.blocked.add(uid)
        self.authorized.discard(uid)
        self._save_json(self.BLOCK_FILE, list(self.blocked))
        self._save_json(self.USERS_FILE,  list(self.authorized))

    def unblock(self, uid: int) -> None:
        self.blocked.discard(uid)
        self._save_json(self.BLOCK_FILE, list(self.blocked))

    # ----------  router setup ----------
    def _setup_handlers(self) -> None:
        # start / auth
        self.r.message(CommandStart())(self.cmd_start)
        self.r.message(AuthState.waiting_for_code)(self.auth_code)
        # commands
        self.r.message(Command("uploadkeys"))(self.cmd_upload_keys)
        self.r.message(Command("uploadtweets"))(self.cmd_upload_tweets)
        self.r.message(Command("listaccounts"))(self.list_accounts)
        self.r.message(Command("addaccount"))(self.add_single_account)
        self.r.message(Command("schedule"))(self.cmd_schedule)
        self.r.message(Command("status"))(self.status)
        self.r.message(Command("cancel"))(self.cancel)
        self.r.message(Command("help"))(self.help)
        self.r.message(Command("time"))(self.time)
        # admin commands
        self.r.message(Command("allusers"))(self.admin_all_users)
        self.r.message(Command("setcode"))(self.admin_set_code)
        self.r.message(Command("block"))(self.admin_block)
        self.r.message(Command("unblock"))(self.admin_unblock)
        self.r.message(Command("getuser"))(self.admin_get_user)
        # states
        self.r.message(UploadKeysState.waiting_for_file,   F.document)(self.handle_keys_file)
        self.r.message(UploadTweetsState.waiting_for_file, F.document)(self.handle_tweets_file)
        self.r.message(ScheduleState.waiting_for_time)(self.handle_schedule)
        self.r.message(BlockState.waiting_for_uid)(self.handle_block_uid)
        self.r.message(UnblockState.waiting_for_uid)(self.handle_unblock_uid)
        self.r.message(ChangeCodeState.waiting_for_new_code)(self.handle_new_code)
        self.r.message(UserDataState.waiting_for_uid)(self.handle_user_data_uid)
        # fallback: any document without state
        self.r.message(F.document)(self.unexpected_file)

    # ----------------  COMMANDS & HANDLERS  ----------------
    async def cmd_start(self, m: Message, st: FSMContext):
        if self.is_blocked(m.from_user.id):
            return await m.answer("🚫 You are blocked.")
        if not self.is_auth(m.from_user.id):
            await m.answer("🔐 <b>Enter approval code</b> :")
            return await st.set_state(AuthState.waiting_for_code)
        await m.answer("✅ Already authorized. Use /help.")

    async def auth_code(self, m: Message, st: FSMContext):
        code_ok = hashlib.sha256(m.text.encode()).hexdigest() == \
                  hashlib.sha256(self.admin_code.encode()).hexdigest()
        await m.delete()
        if code_ok:
            self.authorize(m.from_user.id)
            await m.answer("✅ Authorized!\nUse /help for commands.")
            await st.clear()
        else:
            await m.answer("❌ Wrong code. Try again.")

    # ---------- upload keys ----------
    async def cmd_upload_keys(self, m: Message, st: FSMContext):
        if not self.is_auth(m.from_user.id):
            return await m.answer("🔒 Unauthorized.")
        await m.answer("📎 Send <b>accounts.json</b>")
        await st.set_state(UploadKeysState.waiting_for_file)

    async def handle_keys_file(self, m: Message, st: FSMContext):
        try:
            if not m.document.file_name.endswith(".json"):
                return await m.answer("❌ JSON required.")
            if m.document.file_size > MAX_FILE_SIZE:
                return await m.answer("❌ File too big.")
            user_dir = DATA_DIR / str(m.from_user.id); user_dir.mkdir(exist_ok=True)
            dest = user_dir / "accounts.json"
            await self.bot.download(m.document, destination=dest)
            data = json.loads(dest.read_text("utf-8"))
            jsonschema.validate(data, ACCOUNTS_SCHEMA)
            await m.answer(f"✅ {len(data)} account(s) saved.")
        except Exception as e:
            logger.error(e)
            await m.answer("❌ Upload failed.")
        finally:
            await st.clear()

    # ---------- upload tweets ----------
    async def cmd_upload_tweets(self, m: Message, st: FSMContext):
        if not self.is_auth(m.from_user.id):
            return await m.answer("🔒 Unauthorized.")
        await m.answer("📎 Send <b>tweets.txt</b> (double-newline separated)")
        await st.set_state(UploadTweetsState.waiting_for_file)

    async def handle_tweets_file(self, m: Message, st: FSMContext):
        try:
            if not m.document.file_name.endswith(".txt"):
                return await m.answer("❌ TXT required.")
            if m.document.file_size > MAX_FILE_SIZE // 2:
                return await m.answer("❌ File too big.")
            user_dir = DATA_DIR / str(m.from_user.id); user_dir.mkdir(exist_ok=True)
            dest = user_dir / "tweets.txt"
            await self.bot.download(m.document, destination=dest)
            tweets = [t.strip() for t in dest.read_text("utf-8").split("\n\n") if t.strip()]
            if not tweets:
                return await m.answer("❌ No tweets found.")
            overs = sum(1 for t in tweets if len(t) > MAX_TWEET_LENGTH)
            warn = f"\n⚠️ {overs} too-long tweet(s)" if overs else ""
            await m.answer(f"✅ {len(tweets)} tweet(s) saved.{warn}")
        except Exception as e:
            logger.error(e)
            await m.answer("❌ Upload failed.")
        finally:
            await st.clear()

    async def unexpected_file(self, m: Message):
        await m.answer("📄 File ignored. Use /uploadkeys or /uploadtweets first.")

    # ---------- list accounts ----------
    async def list_accounts(self, m: Message):
        if not self.is_auth(m.from_user.id):
            return await m.answer("🔒 Unauthorized.")
        f = DATA_DIR / str(m.from_user.id) / "accounts.json"
        if not f.exists():
            return await m.answer("❌ No accounts.json uploaded.")
        data = json.loads(f.read_text("utf-8"))
        rows = []
        for idx, acc in enumerate(data, 1):
            cookies = {c["name"]: c["value"] for c in acc["cookies"]}
            status = "✅" if all(k in cookies for k in ("auth_token", "ct0", "twid")) else "⚠️"
            rows.append(f"{idx}. {cookies.get('twid','?')[:15]}… {status}")
        await m.answer("📋 <b>Accounts</b>\n" + "\n".join(rows))

    # ---------- add single account ----------
    async def add_single_account(self, m: Message):
        if not self.is_auth(m.from_user.id):
            return await m.answer("🔒 Unauthorized.")
        if not m.document:
            return await m.answer("📎 Send single account JSON file.")
        if m.document.file_size > 1_000_000:
            return await m.answer("❌ Max 1 MB")
        user_dir = DATA_DIR / str(m.from_user.id); user_dir.mkdir(exist_ok=True)
        tmp = user_dir / "tmp.json"
        await self.bot.download(m.document, destination=tmp)
        try:
            acc = json.loads(tmp.read_text("utf-8"))
            if "cookies" not in acc:
                raise ValueError("missing cookies")
            f = user_dir / "accounts.json"
            accs = json.loads(f.read_text("utf-8")) if f.exists() else []
            accs.append(acc)
            f.write_text(json.dumps(accs, indent=2))
            await m.answer("✅ Account added.")
        except Exception as e:
            logger.error(e)
            await m.answer("❌ Invalid account.")
        finally:
            tmp.unlink(missing_ok=True)

    # ---------- schedule ----------
    async def cmd_schedule(self, m: Message, st: FSMContext):
        if not self.is_auth(m.from_user.id):
            return await m.answer("🔒 Unauthorized.")
        await m.answer("🗓  Send time (e.g. 03/08/2025 12:31)")
        await st.set_state(ScheduleState.waiting_for_time)

    async def handle_schedule(self, m: Message, st: FSMContext):
        await st.clear()
        if not self.is_auth(m.from_user.id):
            return await m.answer("🔒 Unauthorized.")
        dt = ist_from_string(m.text.strip())
        if not dt or dt <= ist_now():
            return await m.answer("❌ Invalid / past time.")
        user_dir = DATA_DIR / str(m.from_user.id)
        tweets_f = user_dir / "tweets.txt"
        accounts_f = user_dir / "accounts.json"
        if not tweets_f.exists() or not accounts_f.exists():
            return await m.answer("❌ Upload tweets and accounts first.")
        tweets = [t.strip() for t in tweets_f.read_text("utf-8").split("\n\n") if t.strip()]
        accounts = json.loads(accounts_f.read_text("utf-8"))
        delay = (dt - ist_now()).total_seconds()
        if m.from_user.id in self.active_tasks:
            self.active_tasks[m.from_user.id].cancel()
        task = asyncio.create_task(self.run_scheduler(m, dt, tweets, accounts))
        self.active_tasks[m.from_user.id] = task
        await m.answer(f"⏰ Scheduled in {int(delay//60)} min.")

    # ---------- scheduler ----------
    async def run_scheduler(self, m: Message, dt, tweets, accounts):
        await asyncio.sleep(max(0, (dt - ist_now()).total_seconds()))
        for i, tweet in enumerate(tweets, 1):
            await asyncio.sleep(random.uniform(POST_DELAY_MIN, POST_DELAY_MAX))
            result, url = await self.post_tweet(tweet, accounts[(i-1)%len(accounts)])
            txt = f"{result}"
            if url:
                txt += f"\n🔗 {url}"
            await m.answer(f"Tweet {i}/{len(tweets)}: {txt}")

    # ---------- misc ----------
    async def status(self, m: Message):
        t = self.active_tasks.get(m.from_user.id)
        s = "ACTIVE" if t and not t.done() else "IDLE"
        await m.answer(f"Task status: {s}")

    async def cancel(self, m: Message):
        t = self.active_tasks.pop(m.from_user.id, None)
        if t:
            t.cancel()
            await m.answer("❌ Task cancelled.")
        else:
            await m.answer("No active task.")

    async def time(self, m: Message):
        now = ist_now().strftime("%d %B %Y  %I:%M %p")
        await m.answer(f"🇮🇳 IST :  <b>{now}</b>")

    async def help(self, m: Message):
        await m.answer("Use /uploadkeys, /uploadtweets, /schedule, /addaccount, /listaccounts…")

    # ---------- admin ----------
    async def admin_all_users(self, m: Message):
        if not self.is_admin(m.from_user.id):
            return await m.answer("🚫 Admin only.")
        txt = "\n".join(f"{uid} {'🚫' if uid in self.blocked else '✅'}"
                        for uid in sorted(self.authorized|self.blocked))
        await m.answer(f"👥 Users:\n{txt or 'None'}")

    async def admin_set_code(self, m: Message, st: FSMContext):
        if not self.is_admin(m.from_user.id):
            return await m.answer("🚫 Admin only.")
        await m.answer("📝 Send new admin code:")
        await st.set_state(ChangeCodeState.waiting_for_new_code)

    async def handle_new_code(self, m: Message, st: FSMContext):
        if not self.is_admin(m.from_user.id):
            return await st.clear()
        self.admin_code = m.text.strip()
        self._save_json(self.ADMIN_FILE, {"admin_code": self.admin_code})
        await m.delete(); await m.answer("✅ Code changed."); await st.clear()

    async def admin_block(self, m: Message, st: FSMContext):
        if not self.is_admin(m.from_user.id):
            return await m.answer("🚫 Admin only.")
        await m.answer("Send UID to block:"); await st.set_state(BlockState.waiting_for_uid)

    async def handle_block_uid(self, m: Message, st: FSMContext):
        if not self.is_admin(m.from_user.id):
            return await st.clear()
        try:
            self.block(int(m.text.strip())); await m.answer("🚫 Blocked.")
        except: await m.answer("❌"); 
        await st.clear()

    async def admin_unblock(self, m: Message, st: FSMContext):
        if not self.is_admin(m.from_user.id):
            return await m.answer("🚫 Admin only.")
        await m.answer("Send UID to unblock:"); await st.set_state(UnblockState.waiting_for_uid)

    async def handle_unblock_uid(self, m: Message, st: FSMContext):
        if not self.is_admin(m.from_user.id):
            return await st.clear()
        try:
            self.unblock(int(m.text.strip())); await m.answer("✅ Unblocked.")
        except: await m.answer("❌"); 
        await st.clear()

    async def admin_get_user(self, m: Message, st: FSMContext):
        if not self.is_admin(m.from_user.id):
            return await m.answer("🚫 Admin only.")
        await m.answer("Send UID:"); await st.set_state(UserDataState.waiting_for_uid)

    async def handle_user_data_uid(self, m: Message, st: FSMContext):
        if not self.is_admin(m.from_user.id):
            return await st.clear()
        uid = int(m.text.strip())
        p = DATA_DIR / str(uid)
        await m.answer(f"Data dir for {uid}: {'exists' if p.exists() else 'missing'}")
        await st.clear()

    # -----------------  Browser / Tweet post  ----------------
    @asynccontextmanager
    async def browser(self):
        pw = await async_playwright().start()
        br = await pw.chromium.launch(headless=BROWSER_HEADLESS)
        try:
            yield br
        finally:
            await br.close(); await pw.stop()

    async def post_tweet(self, tweet: str, acc: Dict, retries: int = 3) -> Tuple[str, Optional[str]]:
        for _ in range(retries):
            try:
                async with self.browser() as br:
                    ctx = await br.new_context(storage_state=acc)
                    pg  = await ctx.new_page()
                    await pg.goto("https://x.com/compose/tweet", timeout=30_000)
                    await pg.fill("[aria-label=\"Post text\"]", tweet)
                    await pg.click("[data-testid=\"tweetButtonInline\"]")
                    await asyncio.sleep(5)
                    url = extract_tweet_url(pg.url)
                    return "✅", url
            except Exception as e:
                logger.warning(f"Post failed: {e}")
        return "❌", None

    # -----------------  MAIN LOOP  ---------------------------
    async def start(self):
        logger.info("Bot starting…"); await self.dp.start_polling(self.bot)

# ------------------------------  ENTRY  --------------------------------------
def main():
    if BOT_TOKEN == "YOUR_REAL_BOT_TOKEN":
        print("Edit main.py and set BOT_TOKEN & YOUR_TELEGRAM_USER_ID")
        return
    bot = TwitterBot()
    asyncio.run(bot.start())

if __name__ == "__main__":
    main()
