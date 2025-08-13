import asyncio
import json
import os
import re
import sys
import uuid
import zipfile
import csv
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from playwright.async_api import async_playwright, Error as PlaywrightError

# =========================
# Configuration (hard-coded)
# =========================
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"  # <-- replace with your bot token
ADMIN_IDS = [1234567890]  # <-- replace with your Telegram user ID(s)
USER_APPROVAL_CODE = "STA54123"
IST = ZoneInfo("Asia/Kolkata")

PLAYWRIGHT_HEADLESS = True
MAX_MEDIA = 4  # X allows up to 4 images or 1 video
TWEET_POST_RETRIES = 3
POSTING_TIMEOUT_SECONDS = 240
BROWSER_INSTALL_TIMEOUT = 600

# Paths
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
GLOBAL_LOGS = DATA_DIR / "logs.json"
USERS_FILE = DATA_DIR / "users.json"
ADMINS_FILE = DATA_DIR / "admins.json"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: Path, data):
    try:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving JSON {path}: {e}", file=sys.stderr)

def now_ist() -> datetime:
    return datetime.now(IST)

def iso_ist(dt: Optional[datetime] = None) -> str:
    return (dt or now_ist()).isoformat()

def human_ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")

def sanitize_filename(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", text)[:100]

def user_dir(user_id: int) -> Path:
    p = DATA_DIR / str(user_id)
    ensure_dir(p)
    return p

def user_file(user_id: int, name: str) -> Path:
    return user_dir(user_id) / name

def append_global_log(entry: Dict[str, Any]):
    logs = load_json(GLOBAL_LOGS, [])
    logs.append(entry)
    save_json(GLOBAL_LOGS, logs)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def _find_user(users: List[Dict[str, Any]], user_id: int) -> Optional[Dict[str, Any]]:
    for u in users:
        if u.get("user_id") == user_id:
            return u
    return None

def list_users() -> List[Dict[str, Any]]:
    return load_json(USERS_FILE, [])

def register_or_touch_user(user_id: int, first_name: str, username: Optional[str]) -> Dict[str, Any]:
    ensure_dir(DATA_DIR)
    users = load_json(USERS_FILE, [])
    existing = _find_user(users, user_id)
    if existing:
        existing["first_name"] = first_name
        existing["username"] = username
        existing["last_seen"] = iso_ist()
        save_json(USERS_FILE, users)
        return existing
    entry = {
        "user_id": user_id,
        "first_name": first_name,
        "username": username,
        "joined_at": iso_ist(),
        "last_seen": iso_ist(),
        "approved": True if is_admin(user_id) else False,
    }
    users.append(entry)
    save_json(USERS_FILE, users)
    return entry

def set_user_approved(user_id: int, approved: bool):
    users = load_json(USERS_FILE, [])
    u = _find_user(users, user_id)
    if not u:
        return
    u["approved"] = approved
    save_json(USERS_FILE, users)

def is_user_approved(user_id: int) -> bool:
    users = load_json(USERS_FILE, [])
    u = _find_user(users, user_id)
    return bool(u and u.get("approved"))

# Blocking users
def is_user_blocked(user_id: int) -> bool:
    users = load_json(USERS_FILE, [])
    u = _find_user(users, user_id)
    return bool(u and u.get("blocked"))

def set_user_blocked(user_id: int):
    users = load_json(USERS_FILE, [])
    u = _find_user(users, user_id)
    if not u:
        return False
    u["blocked"] = True
    u["approved"] = False
    save_json(USERS_FILE, users)
    return True

def set_user_unblocked(user_id: int):
    users = load_json(USERS_FILE, [])
    u = _find_user(users, user_id)
    if not u:
        return False
    u["blocked"] = False
    u["approved"] = True
    save_json(USERS_FILE, users)
    return True

# Per-user storage
def load_accounts(user_id: int) -> List[Dict[str, Any]]:
    return load_json(user_file(user_id, "accounts.json"), [])

def save_accounts(user_id: int, accounts: List[Dict[str, Any]]):
    for idx, acc in enumerate(accounts, start=1):
        acc["id"] = idx
    save_json(user_file(user_id, "accounts.json"), accounts)

def add_account(user_id: int, username: str, password: str) -> Dict[str, Any]:
    accounts = load_accounts(user_id)
    entry = {
        "id": len(accounts) + 1,
        "username": username.strip(),
        "password": password.strip(),
        "added_at": iso_ist(),
        "last_used_at": None,
        "last_status": "unknown",
        "last_error": None,
    }
    accounts.append(entry)
    save_accounts(user_id, accounts)
    return entry

def update_account_status(user_id: int, username: str, status: str, error: Optional[str]):
    accounts = load_accounts(user_id)
    changed = False
    for acc in accounts:
        if acc["username"] == username:
            acc["last_used_at"] = iso_ist()
            acc["last_status"] = status
            acc["last_error"] = error
            changed = True
            break
    if changed:
        save_accounts(user_id, accounts)

def load_tweets(user_id: int) -> List[Dict[str, Any]]:
    return load_json(user_file(user_id, "tweets.json"), [])

def save_tweets(user_id: int, tweets: List[Dict[str, Any]]):
    for idx, t in enumerate(tweets, start=1):
        t["id"] = idx
    save_json(user_file(user_id, "tweets.json"), tweets)

def add_tweet(user_id: int, text: str, media_paths: List[str]) -> Dict[str, Any]:
    tweets = load_tweets(user_id)
    entry = {
        "id": len(tweets) + 1,
        "text": text,
        "media": media_paths,
        "added_at": iso_ist(),
    }
    tweets.append(entry)
    save_tweets(user_id, tweets)
    return entry

def load_used_tweets(user_id: int) -> List[int]:
    return load_json(user_file(user_id, "used_tweets.json"), [])

def mark_tweet_used(user_id: int, tweet_id: int):
    used = load_used_tweets(user_id)
    if tweet_id not in used:
        used.append(tweet_id)
        save_json(user_file(user_id, "used_tweets.json"), used)

def load_account_rotation(user_id: int) -> Dict[str, Any]:
    return load_json(user_file(user_id, "accounts_state.json"), {"next_index": 0})

def save_account_rotation(user_id: int, state: Dict[str, Any]):
    save_json(user_file(user_id, "accounts_state.json"), state)

def select_next_account(user_id: int) -> Optional[Dict[str, Any]]:
    accounts = load_accounts(user_id)
    if not accounts:
        return None
    state = load_account_rotation(user_id)
    idx = state.get("next_index", 0) % len(accounts)
    selected = accounts[idx]
    state["next_index"] = (idx + 1) % len(accounts)
    save_account_rotation(user_id, state)
    return selected

def load_schedules(user_id: int) -> List[Dict[str, Any]]:
    return load_json(user_file(user_id, "schedules.json"), [])

def save_schedules(user_id: int, schedules: List[Dict[str, Any]]):
    save_json(user_file(user_id, "schedules.json"), schedules)

def add_schedule(user_id: int, run_at: datetime) -> Dict[str, Any]:
    schedules = load_schedules(user_id)
    entry = {
        "schedule_id": str(uuid.uuid4()),
        "run_at": run_at.astimezone(IST).isoformat(),
        "created_at": iso_ist(),
        "status": "pending",
    }
    schedules.append(entry)
    save_schedules(user_id, schedules)
    return entry

def update_schedule_status(user_id: int, schedule_id: str, status: str):
    schedules = load_schedules(user_id)
    for s in schedules:
        if s.get("schedule_id") == schedule_id:
            s["status"] = status
            break
    save_schedules(user_id, schedules)

# Parse IST datetime, format: "3 August 2025 @12:31AM"
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}
DT_REGEX = re.compile(r"^\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*@\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*$")

def parse_ist_datetime(text: str) -> Optional[datetime]:
    m = DT_REGEX.match(text.strip())
    if not m:
        return None
    day = int(m.group(1))
    month_name = m.group(2).lower()
    year = int(m.group(3))
    hour = int(m.group(4))
    minute = int(m.group(5))
    ampm = m.group(6).lower()
    month = MONTHS.get(month_name)
    if not month:
        return None
    if hour == 12:
        hour = 0
    if ampm == "pm":
        hour += 12
    try:
        return datetime(year, month, day, hour, minute, tzinfo=IST)
    except ValueError:
        return None

async def ensure_playwright_installed():
    try:
        async with async_playwright() as p:
            _ = p.chromium
            return
    except Exception:
        pass
    try:
        print("Installing Playwright Chromium...", flush=True)
        subprocess.run(
            ["playwright", "install", "chromium", "--with-deps"],
            check=False, timeout=BROWSER_INSTALL_TIMEOUT
        )
    except Exception as e:
        print(f"Error installing Playwright: {e}", file=sys.stderr)

def split_media_paths(paths: List[str]) -> List[str]:
    imgs, vids = [], []
    for p in paths:
        suffix = Path(p).suffix.lower()
        if suffix in IMAGE_EXTS:
            imgs.append(p)
        elif suffix in VIDEO_EXTS:
            vids.append(p)
    if vids:
        return [vids[0]]
    return imgs[:MAX_MEDIA]

async def post_tweet_via_playwright(
    account_username: str,
    account_password: str,
    tweet_text: str,
    media_paths: List[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Login to X and post a tweet. Returns (success, tweet_url, error_message).
    """
    try:
        await ensure_playwright_installed()
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                locale="en-US", viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            page.set_default_timeout(35000)
            # Login
            await page.goto("https://x.com/login", wait_until="domcontentloaded")
            await page.wait_for_selector('input[name="text"]', timeout=20000)
            await page.fill('input[name="text"]', account_username)
            next_btn = page.locator('div[role="button"]:has-text("Next")')
            if await next_btn.count():
                await next_btn.first.click()
            else:
                await page.keyboard.press("Enter")
            if await page.locator('input[name="text"]').count():
                await page.fill('input[name="text"]', account_username)
                next_btn2 = page.locator('div[role="button"]:has-text("Next")')
                if await next_btn2.count():
                    await next_btn2.first.click()
                else:
                    await page.keyboard.press("Enter")
            await page.wait_for_selector('input[name="password"]', timeout=25000)
            await page.fill('input[name="password"]', account_password)
            login_btn = page.locator('div[role="button"]:has-text("Log in")')
            if await login_btn.count():
                await login_btn.first.click()
            else:
                await page.keyboard.press("Enter")
            # Check login success
            try:
                await page.wait_for_selector(
                    '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="tweetTextarea_0"], [aria-label="Post text"]',
                    timeout=35000
                )
            except PlaywrightError:
                if await page.locator("text=Wrong password").count():
                    return False, None, "❌ Wrong password"
                if await page.locator("text=Enter your phone number").count() or await page.locator("text=Verify").count():
                    return False, None, "❌ Verification required (2FA)"
                return False, None, "❌ Login failed"
            # Compose tweet
            composer = page.locator('[data-testid="tweetTextarea_0"], [aria-label="Post text"]')
            if not await composer.count():
                compose_btn = page.locator('[data-testid="SideNav_NewTweet_Button"]')
                if await compose_btn.count():
                    await compose_btn.first.click()
                    composer = page.locator('[data-testid="tweetTextarea_0"], [aria-label="Post text"]')
            if not await composer.count():
                return False, None, "❌ Tweet composer not found"
            await composer.first.click()
            if tweet_text:
                await composer.first.type(tweet_text)
            # Upload media
            media_paths = split_media_paths([str(Path(p)) for p in media_paths])
            for mp in media_paths:
                input_file = page.locator('input[type="file"]')
                try:
                    await input_file.set_input_files(mp)
                except Exception:
                    pass
            # Submit
            post_btn = page.locator('div[role="button"]:has-text("Tweet")')
            if await post_btn.count():
                await post_btn.first.click()
            else:
                await page.keyboard.press("Meta+Enter")
            # Wait for tweet to post
            await asyncio.sleep(3)
            # Try to get tweet URL
            url = page.url
            tweet_url = None
            if "x.com/i/web/status" in url or "/status/" in url:
                tweet_url = url
            await context.close()
            await browser.close()
            return True, tweet_url, None
    except Exception as e:
        return False, None, f"❌ Error posting: {e}"

async def post_next_tweet_for_user(bot: Bot, user_id: int, schedule_id: Optional[str] = None):
    tweets = load_tweets(user_id)
    used = set(load_used_tweets(user_id))
    next_tweet = None
    for t in tweets:
        if t['id'] not in used:
            next_tweet = t
            break
    if not next_tweet:
        await bot.send_message(user_id, "No pending tweets left. 🎉")
        if schedule_id:
            update_schedule_status(user_id, schedule_id, "no_tweets")
        append_global_log({"ts": iso_ist(), "user_id": user_id, "action": "post", "result": "no_tweets"})
        return
    account = select_next_account(user_id)
    if not account:
        await bot.send_message(user_id, "No Twitter accounts available. Please add one.")
        if schedule_id:
            update_schedule_status(user_id, schedule_id, "no_accounts")
        append_global_log({"ts": iso_ist(), "user_id": user_id, "action": "post", "result": "no_accounts", "tweet_id": next_tweet["id"]})
        return
    success = False
    tweet_url = None
    last_error = None
    for attempt in range(1, TWEET_POST_RETRIES + 1):
        try:
            success, tweet_url, err = await asyncio.wait_for(
                post_tweet_via_playwright(
                    account_username=account["username"],
                    account_password=account["password"],
                    tweet_text=next_tweet["text"],
                    media_paths=next_tweet.get("media", []),
                ),
                timeout=POSTING_TIMEOUT_SECONDS
            )
            last_error = err
            if success:
                break
        except asyncio.TimeoutError:
            last_error = "Timeout posting"
        except Exception as e:
            last_error = str(e)
        await asyncio.sleep(2)
    if success:
        mark_tweet_used(user_id, next_tweet["id"])
        update_account_status(user_id, account["username"], "ok", None)
        append_global_log({
            "ts": iso_ist(), "user_id": user_id, "action": "post",
            "result": "success", "details": {"tweet_id": next_tweet["id"], "account": account["username"], "url": tweet_url}
        })
        if tweet_url:
            await bot.send_message(user_id, f"✅ Tweet posted from @{account['username']}: {tweet_url}")
        else:
            await bot.send_message(user_id, f"✅ Tweet posted from @{account['username']}!")
        if schedule_id:
            update_schedule_status(user_id, schedule_id, "completed")
    else:
        update_account_status(user_id, account["username"], "failed", last_error)
        append_global_log({
            "ts": iso_ist(), "user_id": user_id, "action": "post",
            "result": "failed", "tweet_id": next_tweet["id"], "account": account["username"], "error": last_error
        })
        await bot.send_message(user_id, f"❌ Failed to post tweet from @{account['username']}: {last_error}")
        if schedule_id:
            update_schedule_status(user_id, schedule_id, "failed")

async def schedule_execution(bot: Bot, user_id: int, schedule_id: str, run_at: datetime):
    async def runner():
        try:
            delay = (run_at - now_ist()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            await post_next_tweet_for_user(bot, user_id, schedule_id)
        finally:
            SCHEDULE_TASKS.pop(schedule_id, None)
    if schedule_id in SCHEDULE_TASKS and not SCHEDULE_TASKS[schedule_id].done():
        SCHEDULE_TASKS[schedule_id].cancel()
    SCHEDULE_TASKS[schedule_id] = asyncio.create_task(runner())

SCHEDULE_TASKS: Dict[str, asyncio.Task] = {}

# FSM States
class ApprovalFlow(StatesGroup):
    waiting_code = State()

class AddAccountFlow(StatesGroup):
    waiting_username = State()
    waiting_password = State()

class UploadTweetsSingleFlow(StatesGroup):
    waiting_text = State()
    waiting_media = State()

class AddAccounts2FAFlow(StatesGroup):
    waiting_username = State()
    waiting_password = State()
    waiting_otp = State()

class BulkTweetsFlow(StatesGroup):
    waiting_input = State()

bot = Bot(token=BOT_TOKEN, parse_mode=None)
dp = Dispatcher()

# 2FA login sessions
LOGIN_SESSIONS: Dict[int, Dict[str, Any]] = {}

async def close_login_session(user_id: int):
    sess = LOGIN_SESSIONS.pop(user_id, None)
    if not sess:
        return
    for key in ["context", "browser", "pw"]:
        try:
            if sess.get(key):
                await (sess[key].stop() if key == "pw" else sess[key].close())
        except Exception:
            pass

async def start_interactive_login(user_id: int, username: str, password: str) -> Tuple[str, str]:
    try:
        await ensure_playwright_installed()
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=PLAYWRIGHT_HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            locale="en-US", viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(35000)
        await page.goto("https://x.com/login", wait_until="domcontentloaded")
        await page.wait_for_selector('input[name="text"]', timeout=20000)
        await page.fill('input[name="text"]', username)
        next_btn = page.locator('div[role="button"]:has-text("Next")')
        if await next_btn.count():
            await next_btn.first.click()
        else:
            await page.keyboard.press("Enter")
        if await page.locator('input[name="text"]').count() and not await page.locator('input[name="password"]').count():
            await page.fill('input[name="text"]', username)
            next_btn2 = page.locator('div[role="button"]:has-text("Next")')
            if await next_btn2.count():
                await next_btn2.first.click()
            else:
                await page.keyboard.press("Enter")
        await page.wait_for_selector('input[name="password"]', timeout=25000)
        await page.fill('input[name="password"]', password)
        login_btn = page.locator('div[role="button"]:has-text("Log in")')
        if await login_btn.count():
            await login_btn.first.click()
        else:
            await page.keyboard.press("Enter")
        try:
            await page.wait_for_selector(
                '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="tweetTextarea_0"], [aria-label="Post text"]',
                timeout=15000
            )
            await context.close()
            await browser.close()
            await pw.stop()
            return "success", "Logged in successfully."
        except PlaywrightError:
            if await page.locator("text=Wrong password").count():
                await context.close(); await browser.close(); await pw.stop()
                return "error", "Wrong password."
            if await page.locator("text=Enter your phone number").count() or await page.locator("text=Verify your identity").count():
                await context.close(); await browser.close(); await pw.stop()
                return "error", "2FA required (phone/email)."
            content = await page.content()
            otp_input = page.locator('input[autocomplete="one-time-code"], input[name="text"], input[name="verification_code"], input[name="challenge_response"]')
            if await otp_input.count() or any(kw in content.lower() for kw in ["two-factor", "2fa", "verification code", "enter code", "login code"]):
                LOGIN_SESSIONS[user_id] = {"pw": pw, "browser": browser, "context": context, "page": page, "username": username, "created_at": iso_ist()}
                return "otp", "2FA required, please send the code."
            await context.close(); await browser.close(); await pw.stop()
            return "error", "Login failed (unknown reason)."
    except Exception as e:
        return "error", f"Login error: {e}"

async def submit_otp_code(user_id: int, code: str) -> Tuple[str, str]:
    sess = LOGIN_SESSIONS.get(user_id)
    if not sess:
        return "error", "No active login session."
    page = sess["page"]
    context = sess["context"]
    browser = sess["browser"]
    pw = sess["pw"]
    try:
        otp_input = page.locator('input[autocomplete="one-time-code"], input[name="verification_code"], input[name="challenge_response"]')
        if not await otp_input.count():
            return "error", "OTP input not found."
        await otp_input.first.fill(code)
        submit_btn = page.locator('div[role="button"]:has-text("Verify"), div[role="button"]:has-text("Next"), div[role="button"]:has-text("Log in")')
        if await submit_btn.count():
            await submit_btn.first.click()
        else:
            await page.keyboard.press("Enter")
        try:
            await page.wait_for_selector(
                '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="tweetTextarea_0"], [aria-label="Post text"]',
                timeout=20000
            )
            await context.close(); await browser.close(); await pw.stop()
            LOGIN_SESSIONS.pop(user_id, None)
            return "success", "2FA verification successful."
        except PlaywrightError:
            if await page.locator("text=incorrect code").count() or await page.locator("text=Try again").count():
                return "retry", "Incorrect code, try again."
            return "error", "2FA verification failed."
    except Exception as e:
        return "error", f"Error submitting OTP: {e}"

async def ensure_allowed(message: Message, state: FSMContext) -> bool:
    uid = message.from_user.id
    register_or_touch_user(uid, message.from_user.first_name, message.from_user.username)
    if is_admin(uid):
        return True
    if is_user_blocked(uid):
        await message.answer("🚫 You are blocked from using this bot.")
        return False
    if is_user_approved(uid):
        return True
    await state.set_state(ApprovalFlow.waiting_code)
    await message.answer("🔑 Please enter the approval code to use this bot.")
    return False

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    u = register_or_touch_user(uid, message.from_user.first_name, message.from_user.username)
    if is_admin(uid):
        save_json(ADMINS_FILE, ADMIN_IDS)
    welcome = f"👋 Hello, {message.from_user.first_name}!\nThis bot posts tweets to X using browser automation."
    if is_admin(uid) or u.get("approved"):
        await message.answer(welcome + "\nType /help to see available commands.")
    else:
        await state.set_state(ApprovalFlow.waiting_code)
        await message.answer(welcome + "\nThis bot requires an approval code. Please enter it now.")

@dp.message(ApprovalFlow.waiting_code, F.text)
async def approval_code(message: Message, state: FSMContext):
    code = message.text.strip()
    if code == USER_APPROVAL_CODE:
        set_user_approved(message.from_user.id, True)
        await state.clear()
        await message.answer("✅ Approval successful! You can now use the bot. Type /help to see commands.")
    else:
        await message.answer("❌ Invalid code. Please try again.")

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    if not (is_admin(message.from_user.id) or is_user_approved(message.from_user.id)):
        await approval_code(message, state)
        return
    help_text = (
        "🛠 *Commands:* \n"
        "/start - Start bot and register\n"
        "/help - Show this help message\n"
        "/addaccount - Add a Twitter account (username & password)\n"
        "/addaccounts - Add account with login check (supports 2FA)\n"
        "/accountlist - List your saved accounts\n"
        "/uploadtweetssingle - Add a single tweet (text + media)\n"
        "/uploadtweetbulk - Add tweets in bulk (.txt or text)\n"
        "/schedule - Schedule a tweet (e.g. `/schedule 3 August 2025 @12:31AM`)\n"
        "/time - Show current IST time\n"
        "/status - Show current status of tasks\n"
        "/cancel - Cancel the current operation\n"
        "/admin - Admin commands menu\n"
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("time"))
async def cmd_time(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await message.answer(f"🕒 Current IST time: {human_ist(now_ist())}")

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await close_login_session(message.from_user.id)
    await message.answer("✖️ Operation cancelled.")

@dp.message(Command("addaccount"))
async def cmd_addaccount(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.clear()
    await state.set_state(AddAccountFlow.waiting_username)
    await message.answer("📑 Send the Twitter username to add:")

@dp.message(AddAccountFlow.waiting_username, F.text)
async def addaccount_username(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.update_data(tmp_username=message.text.strip())
    await state.set_state(AddAccountFlow.waiting_password)
    await message.answer("🔒 Now send the password for that account:")

@dp.message(AddAccountFlow.waiting_password, F.text)
async def addaccount_password(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    data = await state.get_data()
    username = data.get("tmp_username")
    password = message.text.strip()
    if not username or not password:
        await state.clear()
        await message.answer("❌ Invalid input. Please use /addaccount again.")
        return
    entry = add_account(message.from_user.id, username, password)
    await state.clear()
    await message.answer(f"✅ Added account ID {entry['id']}: @{entry['username']}")

@dp.message(Command("addaccounts"))
async def cmd_addaccounts(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.clear()
    await state.set_state(AddAccounts2FAFlow.waiting_username)
    await message.answer("📑 Send your X (Twitter) username for login:")

@dp.message(AddAccounts2FAFlow.waiting_username, F.text)
async def addaccounts_username(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.update_data(tmp_username=message.text.strip())
    await state.set_state(AddAccounts2FAFlow.waiting_password)
    await message.answer("🔒 Now send your password:")

@dp.message(AddAccounts2FAFlow.waiting_password, F.text)
async def addaccounts_password(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    data = await state.get_data()
    username = data.get("tmp_username")
    password = message.text.strip()
    if not username or not password:
        await state.clear()
        await message.answer("❌ Invalid input. Please use /addaccounts again.")
        return
    await message.answer("⏳ Trying to log in... please wait.")
    status, msg = await start_interactive_login(message.from_user.id, username, password)
    if status == "success":
        entry = add_account(message.from_user.id, username, password)
        update_account_status(message.from_user.id, username, "ok", None)
        await state.clear()
        await message.answer(f"✅ Login successful. Account saved as ID {entry['id']} (@{username}).")
    elif status == "otp":
        await state.update_data(tmp_password=password)
        await state.set_state(AddAccounts2FAFlow.waiting_otp)
        await message.answer("🔑 2FA detected. Please send the 6-digit code:")
    else:
        await state.clear()
        await message.answer(f"❌ Login failed: {msg}")

@dp.message(AddAccounts2FAFlow.waiting_otp, F.text)
async def addaccounts_otp(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    code = re.sub(r"\D", "", message.text.strip())
    if len(code) < 4:
        await message.answer("❌ Please send a valid code.")
        return
    status, msg = await submit_otp_code(message.from_user.id, code)
    if status == "success":
        data = await state.get_data()
        username = data.get("tmp_username")
        password = data.get("tmp_password")
        entry = add_account(message.from_user.id, username, password)
        update_account_status(message.from_user.id, username, "ok", None)
        await state.clear()
        await message.answer(f"✅ 2FA succeeded. Account saved as ID {entry['id']} (@{username}).")
    elif status == "retry":
        await message.answer(f"{msg} Send the code again or /cancel to stop.")
    else:
        await state.clear()
        await close_login_session(message.from_user.id)
        await message.answer(f"❌ Verification failed: {msg}")

@dp.message(Command("uploadtweetssingle"))
async def cmd_uploadtweetssingle(message: Message, state: FSMContext):
    # Reuse the uploadtweets flow
    await cmd_uploadtweets(message, state)

@dp.message(Command("uploadtweetbulk"))
async def cmd_uploadtweetbulk(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.clear()
    await state.set_state(BulkTweetsFlow.waiting_input)
    await message.answer("📂 Send a .txt or .zip file, or paste your tweets (separate tweets by new lines).")

@dp.message(Command("accountlist"))
async def cmd_accountlist(message: Message, state: FSMContext):
    await cmd_listaccounts(message, state)

@dp.message(Command("listaccounts"))
async def cmd_listaccounts(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    accounts = load_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts added yet.")
        return
    lines = []
    for a in accounts:
        status = a.get("last_status", "unknown")
        last_used = a.get("last_used_at") or "-"
        err = a.get("last_error") or ""
        line = f"{a['id']}. @{a['username']} | status={status} | last_used={last_used}"
        if err:
            line += f" | error={err}"
        lines.append(line)
    await message.answer("💼 Your accounts:\n" + "\n".join(lines))

@dp.message(Command("uploadtweets"))
async def cmd_uploadtweets(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.clear()
    await message.answer(
        "📨 Upload tweets:\n"
        "• *Single mode:* send tweet text, then up to 4 media, then 'done'.\n"
        "• *Bulk (.txt):* one tweet per line.\n"
        "• *Bulk (.zip):* include tweets.csv (text,media1..4) and media files.",
        parse_mode="Markdown"
    )
    await state.set_state(UploadTweetsSingleFlow.waiting_text)

@dp.message(UploadTweetsSingleFlow.waiting_text, F.text)
async def uploadtweets_single_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == "cancel":
        await state.clear()
        await message.answer("✖️ Cancelled.")
        return
    await state.update_data(tweet_text=text, media=[])
    await state.set_state(UploadTweetsSingleFlow.waiting_media)
    await message.answer(f"📝 Text saved. Now send up to {MAX_MEDIA} images/videos. Send 'done' when finished.")

@dp.message(UploadTweetsSingleFlow.waiting_media, F.text.casefold() == "done")
async def uploadtweets_single_done(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("tweet_text", "")
    media = split_media_paths(data.get("media", []))
    entry = add_tweet(message.from_user.id, text, media)
    await state.clear()
    await message.answer(f"✅ Tweet saved (ID {entry['id']}). Media files: {len(media)}")

@dp.message(UploadTweetsSingleFlow.waiting_media, F.photo | F.video)
async def uploadtweets_single_media(message: Message, state: FSMContext):
    data = await state.get_data()
    media = data.get("media", [])
    if len(media) >= MAX_MEDIA:
        await message.answer(f"🚫 Already have {MAX_MEDIA} media files. Send 'done' to finish.")
        return
    dest_dir = user_file(message.from_user.id, "media")
    ensure_dir(dest_dir)
    if message.photo:
        filename = f"img_{uuid.uuid4().hex}.jpg"
        dest = dest_dir / filename
        try:
            await bot.download(message.photo[-1], destination=str(dest))
        except Exception as e:
            await message.answer(f"❌ Failed to download image: {e}")
            return
    else:
        ext = Path(message.video.file_name or "").suffix or ".mp4"
        filename = f"vid_{uuid.uuid4().hex}{ext}"
        dest = dest_dir / filename
        try:
            await bot.download(message.video, destination=str(dest))
        except Exception as e:
            await message.answer(f"❌ Failed to download video: {e}")
            return
    media.append(str(dest))
    await state.update_data(media=media)
    await message.answer(f"✅ Media added ({len(media)}/{MAX_MEDIA}). Send more or 'done'.")

@dp.message(BulkTweetsFlow.waiting_input, F.document)
async def uploadtweets_bulk_document(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.clear()
    # Reuse process_tweets_package logic
    name = message.document.file_name or ""
    if name.lower().endswith((".txt", ".zip", ".csv")):
        await process_tweets_package(message)
    else:
        await message.answer("❌ Unsupported file. Please send .txt, .csv, or .zip.")

@dp.message(BulkTweetsFlow.waiting_input, F.text)
async def uploadtweets_bulk_text(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    text = message.text.strip()
    if not text:
        await state.clear()
        await message.answer("❌ No text provided.")
        return
    blocks = [blk.strip() for blk in re.split(r"\n{2,}", text) if blk.strip()]
    added = 0
    for blk in blocks:
        add_tweet(message.from_user.id, blk, [])
        added += 1
    await state.clear()
    await message.answer(f"✅ Added {added} tweets from text.")

@dp.message(F.document)
async def handle_document_upload(message: Message, state: FSMContext):
    name = (message.document.file_name or "").lower()
    if name.endswith(".txt") and "account" in name:
        if not await ensure_allowed(message, state):
            return
        await process_accounts_file(message)
    elif name.endswith((".txt", ".csv", ".zip")):
        if not await ensure_allowed(message, state):
            return
        await process_tweets_package(message)

async def process_accounts_file(message: Message):
    user_id = message.from_user.id
    temp_path = user_file(user_id, f"tmp_{sanitize_filename(message.document.file_name)}")
    ensure_dir(temp_path.parent)
    try:
        await bot.download(message.document, destination=str(temp_path))
    except Exception as e:
        await message.answer(f"❌ Failed to download file: {e}")
        return
    added = 0
    try:
        with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                if "," in line:
                    u, p = line.split(",", 1)
                elif ";" in line:
                    u, p = line.split(";", 1)
                else:
                    continue
                u, p = u.strip(), p.strip()
                if u and p:
                    add_account(user_id, u, p)
                    added += 1
    except Exception as e:
        await message.answer(f"❌ Error reading file: {e}")
    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass
    await message.answer(f"✅ Bulk add complete. Added {added} accounts.")

async def process_tweets_package(message: Message):
    user_id = message.from_user.id
    name = message.document.file_name.lower()
    temp_path = user_file(user_id, f"tmp_{sanitize_filename(name)}")
    ensure_dir(temp_path.parent)
    try:
        await bot.download(message.document, destination=str(temp_path))
    except Exception as e:
        await message.answer(f"❌ Failed to download file: {e}")
        return
    added = 0
    try:
        if name.endswith(".txt"):
            with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    text = line.strip()
                    if text:
                        add_tweet(user_id, text, [])
                        added += 1
        elif name.endswith(".csv"):
            with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    text = (row.get("text") or "").strip()
                    media_cols = [row.get(f"media{i}", "").strip() for i in range(1,5)]
                    media_cols = [m for m in media_cols if m]
                    resolved = []
                    for m in media_cols:
                        p = Path(m)
                        if not p.is_absolute():
                            p = user_dir(user_id) / m
                        if p.exists():
                            resolved.append(str(p))
                    add_tweet(user_id, text, split_media_paths(resolved))
                    added += 1
        elif name.endswith(".zip"):
            extract_root = user_file(user_id, f"tweets_{uuid.uuid4().hex}")
            ensure_dir(extract_root)
            with zipfile.ZipFile(temp_path, "r") as z:
                z.extractall(extract_root)
            csv_path = None
            txt_path = None
            for root, dirs, files in os.walk(extract_root):
                for fn in files:
                    fl = fn.lower()
                    if fl == "tweets.csv" and csv_path is None:
                        csv_path = Path(root) / fn
                    elif fl == "tweets.txt" and txt_path is None:
                        txt_path = Path(root) / fn
            if csv_path and csv_path.exists():
                with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        text = (row.get("text") or "").strip()
                        media_cols = [row.get(f"media{i}", "").strip() for i in range(1,5)]
                        media_cols = [m for m in media_cols if m]
                        resolved = []
                        for m in media_cols:
                            p = Path(m)
                            if not p.is_absolute():
                                p = csv_path.parent / m
                            if p.exists():
                                resolved.append(str(p))
                        add_tweet(user_id, text, split_media_paths(resolved))
                        added += 1
            elif txt_path and txt_path.exists():
                with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        text = line.strip()
                        if text:
                            add_tweet(user_id, text, [])
                            added += 1
            else:
                await message.answer("❌ ZIP missing tweets.csv or tweets.txt.")
                return
        else:
            await message.answer("❌ Unsupported file type. Use .txt, .csv or .zip for tweets.")
            return
    except Exception as e:
        await message.answer(f"❌ Error processing file: {e}")
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
    await message.answer(f"✅ Tweets added: {added}")

@dp.message(Command("schedule"))
async def cmd_schedule(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) > 1:
        dt = parse_ist_datetime(parts[1])
        if not dt:
            await message.answer("❌ Invalid format. Example: `/schedule 3 August 2025 @12:31AM`", parse_mode="Markdown")
            return
        entry = add_schedule(message.from_user.id, dt)
        await schedule_execution(bot, message.from_user.id, entry["schedule_id"], dt)
        await message.answer(f"⏰ Scheduled at {human_ist(dt)}. It will post the next unused tweet.", parse_mode="Markdown")
    else:
        await message.answer("❌ Please provide date/time. Example: `/schedule 3 August 2025 @12:31AM`", parse_mode="Markdown")

@dp.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    schedules = load_schedules(message.from_user.id)
    pending = [s for s in schedules if s.get("status") == "pending"]
    running_cnt = sum(1 for _ in SCHEDULE_TASKS)
    used = set(load_used_tweets(message.from_user.id))
    tweets = load_tweets(message.from_user.id)
    remain = len([t for t in tweets if t['id'] not in used])
    txt = []
    txt.append(f"📅 Pending schedules: {len(pending)}")
    for s in pending[:5]:
        txt.append(f"- {s['schedule_id'][:8]} at {human_ist(datetime.fromisoformat(s['run_at']))}")
    txt.append(f"📝 Tweets remaining: {remain}")
    txt.append(f"⚙️ Active tasks: {running_cnt}")
    await message.answer("\n".join(txt))

# =========================
# Admin commands
# =========================
def admin_only(handler):
    async def wrapper(message: Message, state: FSMContext = None):
        if not is_admin(message.from_user.id):
            await message.answer("🚫 Admin only.")
            return
        return await handler(message, state)
    return wrapper

@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 Admin only.")
        return
    menu = (
        "🛠 *Admin Menu:*\n"
        "/listusers - List all users\n"
        "/viewaccounts {user_id} - Show user's accounts\n"
        "/block {user_id} - Block a user\n"
        "/unblock {user_id} - Unblock a user\n"
        "/broadcast {text} - Broadcast message to all\n"
    )
    await message.answer(menu, parse_mode="Markdown")

@dp.message(Command("listusers"))
@admin_only
async def cmd_listusers(message: Message, state: FSMContext):
    users = list_users()
    if not users:
        await message.answer("No users yet.")
        return
    lines = [f"{u['user_id']} | {u['first_name']} (@{u.get('username')}) | approved={u.get('approved')} | blocked={u.get('blocked', False)}" for u in users]
    await message.answer("👥 Users:\n" + "\n".join(lines[:100]))

@dp.message(Command("viewaccounts"))
@admin_only
async def cmd_viewaccounts(message: Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /viewaccounts {user_id}")
        return
    uid = int(parts[1])
    accounts = load_accounts(uid)
    if not accounts:
        await message.answer("No accounts for this user.")
        return
    lines = [f"{a['id']}. @{a['username']} | {a['password']} | status={a.get('last_status')}" for a in accounts]
    await message.answer(f"💼 Accounts for user {uid}:\n" + "\n".join(lines))

@dp.message(Command("block"))
@admin_only
async def cmd_block(message: Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /block {user_id}")
        return
    uid = int(parts[1])
    if set_user_blocked(uid):
        await message.answer(f"🔒 User {uid} has been blocked.")
    else:
        await message.answer("User not found.")

@dp.message(Command("unblock"))
@admin_only
async def cmd_unblock(message: Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /unblock {user_id}")
        return
    uid = int(parts[1])
    if set_user_unblocked(uid):
        await message.answer(f"🔓 User {uid} has been unblocked.")
    else:
        await message.answer("User not found.")

@dp.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(message: Message, state: FSMContext):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /broadcast Your message here")
        return
    payload = parts[1]
    users = list_users()
    sent = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], f"📢 [Broadcast]\n{payload}")
            sent += 1
        except Exception:
            pass
    await message.answer(f"Broadcast sent to {sent} users.")

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
