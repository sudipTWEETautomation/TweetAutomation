import asyncio
import json
import os
import re
import sys
import traceback
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
from aiogram.types import Message, Update
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# Playwright
from playwright.async_api import async_playwright, Error as PlaywrightError

# =========================
# Hard-coded constants
# =========================
# Bot token (read from env; fail fast if missing)
BOT_TOKEN = "8428126884:AAFeYk650yE4oUXNIDSi_Mjv9Rl9WIPZ8SQ"
# Admins by Telegram user ID (no code needed for admin commands)
ADMIN_IDS = [6535216093]  # <-- replace with your Telegram user ID(s)

# New user approval code
USER_APPROVAL_CODE = "STA54123"

# Timezone
IST = ZoneInfo("Asia/Kolkata")

# Playwright and posting config
PLAYWRIGHT_HEADLESS = True
MAX_MEDIA = 4  # X allows up to 4 images or 1 video (rule enforced below)
TWEET_POST_RETRIES = 3
POSTING_TIMEOUT_SECONDS = 240
BROWSER_INSTALL_TIMEOUT = 600

# Paths
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
GLOBAL_LOGS = DATA_DIR / "logs.json"
USERS_FILE = DATA_DIR / "users.json"
ADMINS_FILE = DATA_DIR / "admins.json"  # kept for visibility

# Allowed media extensions
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}

# =========================
# Utilities
# =========================
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
    return user_id in set(ADMIN_IDS)

# =========================
# Users registry and approval
# =========================
def _find_user(users: List[Dict[str, Any]], user_id: int) -> Optional[Dict[str, Any]]:
    for u in users:
        if u.get("user_id") == user_id:
            return u
    return None

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

def list_users() -> List[Dict[str, Any]]:
    return load_json(USERS_FILE, [])

# =========================
# Per-user storage helpers
# =========================
def load_accounts(user_id: int) -> List[Dict[str, Any]]:
    return load_json(user_file(user_id, "accounts.json"), [])

def save_accounts(user_id: int, accounts: List[Dict[str, Any]]):
    # Reindex and persist
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
        "last_status": "unknown",  # unknown|ok|failed
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
        "media": media_paths,  # images/videos, up to 4 or 1 video
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

# =========================
# Time parsing (IST)
# Example: "3 August 2025 @12:31AM"
# =========================
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
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

# =========================
# Playwright helpers
# =========================
async def ensure_playwright_installed():
    try:
        async with async_playwright() as p:
            _ = p.chromium
            return
    except Exception:
        pass
    try:
        print("Attempting to install Playwright Chromium...", flush=True)
        subprocess.run(
            ["playwright", "install", "chromium", "--with-deps"],
            check=False,
            timeout=BROWSER_INSTALL_TIMEOUT,
        )
        print("Playwright Chromium install attempted.", flush=True)
    except Exception as e:
        print(f"Playwright install error (continuing anyway): {e}", file=sys.stderr)

def split_media_paths(paths: List[str]) -> List[str]:
    """
    Enforce X rules: either up to 4 images, OR 1 video (safest).
    """
    imgs, vids = [], []
    for p in paths:
        suffix = Path(p).suffix.lower()
        if suffix in IMAGE_EXTS:
            imgs.append(p)
        elif suffix in VIDEO_EXTS:
            vids.append(p)
    if vids:
        return [vids[0]]  # prefer 1st video only
    return imgs[:MAX_MEDIA]

async def post_tweet_via_playwright(
    account_username: str,
    account_password: str,
    tweet_text: str,
    media_paths: List[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Login to X (Twitter) and post a tweet. Returns (success, tweet_url, error_message).
    Headless by default. No API, no cookies.
    """
    try:
        await ensure_playwright_installed()
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                locale="en-US",
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            page.set_default_timeout(35000)

            # Login flow
            await page.goto("https://x.com/login", wait_until="domcontentloaded")
            await page.wait_for_selector('input[name="text"]', timeout=20000)
            await page.fill('input[name="text"]', account_username)
            next_btn = page.locator('div[role="button"]:has-text("Next")')
            if await next_btn.count() > 0:
                await next_btn.first.click()
            else:
                await page.keyboard.press("Enter")

            # Sometimes they request username again
            if await page.locator('input[name="text"]').count() > 0:
                await page.fill('input[name="text"]', account_username)
                next_btn2 = page.locator('div[role="button"]:has-text("Next")')
                if await next_btn2.count() > 0:
                    await next_btn2.first.click()
                else:
                    await page.keyboard.press("Enter")

            await page.wait_for_selector('input[name="password"]', timeout=25000)
            await page.fill('input[name="password"]', account_password)
            login_btn = page.locator('div[role="button"]:has-text("Log in")')
            if await login_btn.count() > 0:
                await login_btn.first.click()
            else:
                await page.keyboard.press("Enter")

            # Check if logged in
            try:
                await page.wait_for_selector(
                    '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="tweetTextarea_0"], [aria-label="Post text"]',
                    timeout=35000
                )
            except PlaywrightError:
                if await page.locator("text=Enter your phone number").count() > 0 or await page.locator("text=Verify").count() > 0:
                    await context.close()
                    await browser.close()
                    return False, None, "Login requires verification (2FA/phone)."
                if await page.locator("text=Wrong password").count() > 0:
                    await context.close()
                    await browser.close()
                    return False, None, "Wrong password."
                await context.close()
                await browser.close()
                return False, None, "Login failed."

            # Compose tweet
            composer = page.locator('[data-testid="tweetTextarea_0"], div[aria-label="Post text"]')
            if await composer.count() == 0:
                compose_btn = page.locator('[data-testid="SideNav_NewTweet_Button"]')
                if await compose_btn.count() > 0:
                    await compose_btn.first.click()
                    composer = page.locator('[data-testid="tweetTextarea_0"], div[aria-label="Post text"]')

            if await composer.count() == 0:
                await context.close()
                await browser.close()
                return False, None, "Composer not found."

            await composer.first.click()
            if tweet_text:
                await composer.first.type(tweet_text)

            # Upload media if any
            media_paths = split_media_paths([str(Path(p)) for p in media_paths])
            if media_paths:
                file_input = page.locator('input[type="file"]')
                target = file_input
                if await target.count() == 0:
                    target = page.locator('input[type="file"][accept]')
                try:
                    await target.first.set_input_files(media_paths)
                except Exception:
                    try:
                        alt = page.locator('[data-testid="toolBar"] input[type="file"]')
                        await alt.first.set_input_files(media_paths)
                    except Exception as e:
                        await context.close()
                        await browser.close()
                        return False, None, f"Media upload failed: {e}"

            # Click Post button
            post_btn = page.locator('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]')
            if await post_btn.count() == 0:
                await context.close()
                await browser.close()
                return False, None, "Post button not found."
            await post_btn.first.click()
            await page.wait_for_timeout(4500)

            # Try to capture tweet URL by going to profile
            tweet_url = None
            try:
                profile_url = f"https://x.com/{account_username}"
                await page.goto(profile_url, wait_until="domcontentloaded")
                link = page.locator('article a[href*="/status/"]')
                await link.first.wait_for(timeout=20000)
                href = await link.first.get_attribute("href")
                if href:
                    tweet_url = href if href.startswith("http") else ("https://x.com" + href)
            except Exception:
                try:
                    await page.goto("https://x.com/home", wait_until="domcontentloaded")
                    link = page.locator('article a[href*="/status/"]')
                    await link.first.wait_for(timeout=20000)
                    href = await link.first.get_attribute("href")
                    if href:
                        tweet_url = href if href.startswith("http") else ("https://x.com" + href)
                except Exception:
                    pass

            await context.close()
            await browser.close()
            if tweet_url:
                return True, tweet_url, None
            return True, None, "Tweet posted but URL not found."
    except PlaywrightError as e:
        return False, None, f"Playwright error: {e}"
    except Exception as e:
        return False, None, f"Unexpected error: {e}"

# =========================
# Scheduler
# =========================
SCHEDULE_TASKS: Dict[str, asyncio.Task] = {}  # schedule_id -> task

async def post_next_tweet_for_user(bot: Bot, user_id: int, schedule_id: Optional[str] = None):
    tweets = load_tweets(user_id)
    used = set(load_used_tweets(user_id))
    next_tweet = None
    for t in tweets:
        if t.get("id") not in used:
            next_tweet = t
            break
    if not next_tweet:
        await bot.send_message(user_id, "No pending tweets left to post.")
        if schedule_id:
            update_schedule_status(user_id, schedule_id, "no_tweets")
        append_global_log({"ts": iso_ist(), "user_id": user_id, "action": "post", "result": "no_tweets"})
        return

    account = select_next_account(user_id)
    if not account:
        await bot.send_message(user_id, "No Twitter accounts available. Use addaccount/addaccounts or uploadkeys.")
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
                    media_paths=[str(Path(p)) for p in next_tweet.get("media", [])],
                ),
                timeout=POSTING_TIMEOUT_SECONDS
            )
            last_error = err
            if success:
                break
        except asyncio.TimeoutError:
            last_error = "Posting timed out."
        except Exception as e:
            last_error = f"Error: {e}"
        await asyncio.sleep(3)

    if success:
        mark_tweet_used(user_id, int(next_tweet["id"]))
        update_account_status(user_id, account["username"], "ok", None)
        append_global_log({
            "ts": iso_ist(), "user_id": user_id, "action": "post",
            "result": "success", "details": {"tweet_id": next_tweet["id"], "account": account["username"], "url": tweet_url}
        })
        if tweet_url:
            await bot.send_message(user_id, f"Tweet posted from @{account['username']}: {tweet_url}")
        else:
            await bot.send_message(user_id, f"Tweet posted from @{account['username']}, but link could not be detected.")
        if schedule_id:
            update_schedule_status(user_id, schedule_id, "completed")
    else:
        update_account_status(user_id, account["username"], "failed", last_error)
        append_global_log({
            "ts": iso_ist(), "user_id": user_id, "action": "post",
            "result": "failed", "tweet_id": next_tweet["id"], "account": account["username"], "error": last_error
        })
        await bot.send_message(user_id, f"Failed to post tweet from @{account['username']}. Error: {last_error or 'Unknown error'}")
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
            if schedule_id in SCHEDULE_TASKS:
                SCHEDULE_TASKS.pop(schedule_id, None)
    if schedule_id in SCHEDULE_TASKS and not SCHEDULE_TASKS[schedule_id].done():
        SCHEDULE_TASKS[schedule_id].cancel()
    SCHEDULE_TASKS[schedule_id] = asyncio.create_task(runner())

# =========================
# FSM States
# =========================
class ApprovalFlow(StatesGroup):
    waiting_code = State()

class AddAccountFlow(StatesGroup):
    waiting_username = State()
    waiting_password = State()

class UploadTweetsSingleFlow(StatesGroup):
    waiting_text = State()
    waiting_media = State()

# New flow for immediate login + 2FA
class AddAccounts2FAFlow(StatesGroup):
    waiting_username = State()
    waiting_password = State()
    waiting_otp = State()

# =========================
# Bot setup
# =========================
bot = Bot(BOT_TOKEN, parse_mode=None)
dp = Dispatcher()

# =========================
# 2FA interactive login sessions
# =========================
LOGIN_SESSIONS: Dict[int, Dict[str, Any]] = {}

async def close_login_session(user_id: int):
    sess = LOGIN_SESSIONS.pop(user_id, None)
    if not sess:
        return
    try:
        if sess.get("context"):
            await sess["context"].close()
    except Exception:
        pass
    try:
        if sess.get("browser"):
            await sess["browser"].close()
    except Exception:
        pass
    try:
        if sess.get("pw"):
            await sess["pw"].stop()
    except Exception:
        pass

async def start_interactive_login(user_id: int, username: str, password: str) -> Tuple[str, str]:
    """
    Try to login. Returns (status, message)
    status in {"success","otp","error"}
    If status == "otp": a session is stored in LOGIN_SESSIONS[user_id] waiting for submit_otp_code().
    """
    try:
        await ensure_playwright_installed()
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=PLAYWRIGHT_HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(35000)

        await page.goto("https://x.com/login", wait_until="domcontentloaded")
        await page.wait_for_selector('input[name="text"]', timeout=20000)
        await page.fill('input[name="text"]', username)
        next_btn = page.locator('div[role="button"]:has-text("Next")')
        if await next_btn.count() > 0:
            await next_btn.first.click()
        else:
            await page.keyboard.press("Enter")

        if await page.locator('input[name="text"]').count() > 0 and await page.locator('input[name="password"]').count() == 0:
            await page.fill('input[name="text"]', username)
            next_btn2 = page.locator('div[role="button"]:has-text("Next")')
            if await next_btn2.count() > 0:
                await next_btn2.first.click()
            else:
                await page.keyboard.press("Enter")

        await page.wait_for_selector('input[name="password"]', timeout=25000)
        await page.fill('input[name="password"]', password)
        login_btn = page.locator('div[role="button"]:has-text("Log in")')
        if await login_btn.count() > 0:
            await login_btn.first.click()
        else:
            await page.keyboard.press("Enter")

        try:
            await page.wait_for_selector(
                '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="tweetTextarea_0"], [aria-label="Post text"]',
                timeout=15000
            )
            await context.close(); await browser.close(); await pw.stop()
            return "success", "Logged in successfully."
        except PlaywrightError:
            if await page.locator("text=Wrong password").count() > 0:
                await context.close(); await browser.close(); await pw.stop()
                return "error", "Wrong password."
            if await page.locator("text=Enter your phone number").count() > 0 or await page.locator("text=Verify your identity").count() > 0:
                await context.close(); await browser.close(); await pw.stop()
                return "error", "Login challenge requires phone/email verification."

            body_text = ""
            try:
                body_text = (await page.content()).lower()
            except Exception:
                pass
            otp_input = page.locator('input[autocomplete="one-time-code"], input[name="text"], input[name="verification_code"], input[name="challenge_response"]')
            keywords = any(k in body_text for k in ["two-factor", "2fa", "verification code", "enter code", "login code"])
            if await otp_input.count() > 0 or keywords:
                await close_login_session(user_id)
                LOGIN_SESSIONS[user_id] = {
                    "pw": pw,
                    "browser": browser,
                    "context": context,
                    "page": page,
                    "username": username,
                    "created_at": iso_ist(),
                }
                return "otp", "2FA required. Please send the 6-digit code."
            await context.close(); await browser.close(); await pw.stop()
            return "error", "Login failed (unknown reason)."
    except Exception as e:
        return "error", f"Login error: {e}"

async def submit_otp_code(user_id: int, code: str) -> Tuple[str, str]:
    """
    Submit OTP to the stored session. Returns (status, message)
    status in {"success","retry","error"}
    """
    sess = LOGIN_SESSIONS.get(user_id)
    if not sess:
        return "error", "No active login session. Start again with addaccounts."
    page = sess["page"]
    context = sess["context"]
    browser = sess["browser"]
    pw = sess["pw"]
    try:
        otp_input = page.locator('input[autocomplete="one-time-code"], input[name="text"], input[name="verification_code"], input[name="challenge_response"]')
        if await otp_input.count() == 0:
            return "error", "OTP input not found. Session may have expired."
        await otp_input.first.fill(code)
        submit_btn = page.locator('div[role="button"]:has-text("Verify"), div[role="button"]:has-text("Next"), div[role="button"]:has-text("Log in")')
        if await submit_btn.count() > 0:
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
            if await page.locator("text=incorrect code").count() > 0 or await page.locator("text=Try again").count() > 0:
                return "retry", "Incorrect code. Please try again."
            return "error", "Verification failed."
    except Exception as e:
        return "error", f"OTP submit error: {e}"

# =========================
# Helper: approval gate
# =========================
async def ensure_allowed(message: Message, state: FSMContext) -> bool:
    register_or_touch_user(message.from_user.id, message.from_user.first_name, message.from_user.username)
    if is_admin(message.from_user.id):
        return True
    if is_user_approved(message.from_user.id):
        return True
    await state.set_state(ApprovalFlow.waiting_code)
    await message.answer("Please enter the approval code to use this bot.")
    return False

# =========================
# Handlers
# =========================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    u = register_or_touch_user(message.from_user.id, message.from_user.first_name, message.from_user.username)
    if is_admin(message.from_user.id):
        save_json(ADMINS_FILE, ADMIN_IDS)
    welcome = f"Welcome, {message.from_user.first_name}!\n"
    welcome += "This bot posts tweets to X (Twitter) using browser automation.\n"
    if is_admin(message.from_user.id) or u.get("approved"):
        await message.answer(welcome + "Type help to see available commands.")
    else:
        await state.set_state(ApprovalFlow.waiting_code)
        await message.answer(welcome + "This bot requires an approval code. Please enter it now.")

@dp.message(ApprovalFlow.waiting_code, F.text)
async def approval_code(message: Message, state: FSMContext):
    code = message.text.strip()
    if code == USER_APPROVAL_CODE:
        set_user_approved(message.from_user.id, True)
        await state.clear()
        await message.answer("Approval successful. You can now use the bot. Type help to see commands.")
    else:
        await message.answer("Invalid code. Please try again.")

# ---- Help ----
@dp.message(Command("help"))
@dp.message(F.text.casefold() == "help")
async def cmd_help(message: Message, state: FSMContext):
    if not (is_admin(message.from_user.id) or is_user_approved(message.from_user.id)):
        await approval_code(message, state)
        return
    text = (
        "Commands:\n"
        "start        - Start the bot (show welcome message <user name>)\n"
        "addaccount   - Add single account (one by one)\n"
        "addaccounts  - Add account with immediate login check (2FA supported)\n"
        "listaccounts - Show all accounts with status\n"
        "uploadkeys   - Upload accounts.txt (traditional bulk)\n"
        "uploadtweets - Upload tweets text/images/video (single or bulk)\n"
        "schedule     - Schedule posting time (IST). Example: schedule 3 August 2025 @12:31AM\n"
        "time         - Show current IST time\n"
        "status       - Check active tasks\n"
        "cancel       - Cancel current operation\n"
        "help         - Show this help\n"
        "\nAdmin-only: /viewusers, /viewaccounts {user_id}, /broadcast, /logs"
    )
    await message.answer(text)

# ---- Time ----
@dp.message(Command("time"))
@dp.message(F.text.casefold() == "time")
async def cmd_time(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await message.answer(f"Current IST time: {human_ist(now_ist())}")

# ---- Cancel ----
@dp.message(Command("cancel"))
@dp.message(F.text.casefold() == "cancel")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await close_login_session(message.from_user.id)
    await message.answer("Okay, cancelled the current operation.")

# ---- Add account (classic) ----
@dp.message(Command("addaccount"))
@dp.message(F.text.casefold() == "addaccount")
async def cmd_addaccount(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.clear()
    await state.set_state(AddAccountFlow.waiting_username)
    await message.answer("Send the Twitter username to add.")

@dp.message(AddAccountFlow.waiting_username, F.text)
async def addaccount_username(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.update_data(tmp_username=message.text.strip())
    await state.set_state(AddAccountFlow.waiting_password)
    await message.answer("Now send the password for that account.")

@dp.message(AddAccountFlow.waiting_password, F.text)
async def addaccount_password(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    data = await state.get_data()
    username = data.get("tmp_username")
    password = message.text.strip()
    if not username or not password:
        await state.clear()
        await message.answer("Invalid input. Please use addaccount again.")
        return
    entry = add_account(message.from_user.id, username, password)
    await state.clear()
    await message.answer(f"Added account ID {entry['id']}: @{entry['username']}")

# ---- Add accounts (immediate login + 2FA) ----
@dp.message(Command("addaccounts"))
@dp.message(F.text.casefold() == "addaccounts")
async def cmd_addaccounts(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.clear()
    await state.set_state(AddAccounts2FAFlow.waiting_username)
    await message.answer("Send your X username for login verification (2FA supported).")

@dp.message(AddAccounts2FAFlow.waiting_username, F.text)
async def addaccounts_username(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.update_data(tmp_username=message.text.strip())
    await state.set_state(AddAccounts2FAFlow.waiting_password)
    await message.answer("Now send your X password.")

@dp.message(AddAccounts2FAFlow.waiting_password, F.text)
async def addaccounts_password(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    data = await state.get_data()
    username = data.get("tmp_username")
    password = message.text.strip()
    if not username or not password:
        await state.clear()
        await message.answer("Invalid input. Please use addaccounts again.")
        return

    await message.answer("Trying to log in... Please wait.")
    status, msg = await start_interactive_login(message.from_user.id, username, password)
    if status == "success":
        entry = add_account(message.from_user.id, username, password)
        update_account_status(message.from_user.id, username, "ok", None)
        await state.clear()
        await message.answer(f"Login successful. Account saved as ID {entry['id']} (@{username}).")
    elif status == "otp":
        await state.update_data(tmp_password=password)
        await state.set_state(AddAccounts2FAFlow.waiting_otp)
        await message.answer("2FA enabled. Send your 6-digit verification code.")
    else:
        await state.clear()
        await message.answer(f"Login failed: {msg}")

@dp.message(AddAccounts2FAFlow.waiting_otp, F.text)
async def addaccounts_otp(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    code = re.sub(r"\D", "", message.text.strip())
    if len(code) < 4:
        await message.answer("Please send a valid code.")
        return
    status, msg = await submit_otp_code(message.from_user.id, code)
    if status == "success":
        data = await state.get_data()
        username = data.get("tmp_username")
        password = data.get("tmp_password")
        entry = add_account(message.from_user.id, username, password)
        update_account_status(message.from_user.id, username, "ok", None)
        await state.clear()
        await message.answer(f"Login successful via 2FA. Account saved as ID {entry['id']} (@{username}).")
    elif status == "retry":
        await message.answer(f"{msg} Send the code again, or type cancel to stop.")
    else:
        await state.clear()
        await close_login_session(message.from_user.id)
        await message.answer(f"Verification failed: {msg}")

# ---- Upload keys (bulk accounts) ----
@dp.message(Command("uploadkeys"))
@dp.message(F.text.casefold() == "uploadkeys")
async def cmd_uploadkeys(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await message.answer(
        "Upload a .txt file named accounts.txt where each line is: username,password\n"
        "Alternatively, you can paste lines directly here."
    )

@dp.message(F.document)
async def handle_document_upload(message: Message, state: FSMContext):
    name = (message.document.file_name or "").lower()
    if name.endswith(".txt") and ("account" in name or "accounts" in name):
        if not await ensure_allowed(message, state):
            return
        await process_accounts_file(message)
    elif name.endswith(".txt") or name.endswith(".csv") or name.endswith(".zip"):
        if not await ensure_allowed(message, state):
            return
        await process_tweets_package(message)
    else:
        await message.answer("Unsupported file. For accounts use accounts.txt; for tweets use .txt/.csv or a .zip package.")

@dp.message(F.text & ~F.via_bot)
async def maybe_bulk_accounts_or_general_text(message: Message, state: FSMContext):
    text = message.text.strip()
    cur = await state.get_state()
    if cur is not None:
        return

    if not (is_admin(message.from_user.id) or is_user_approved(message.from_user.id)):
        await approval_code(message, state)
        return

    if "\n" in text and any("," in ln for ln in text.splitlines()):
        added = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "," in line:
                u, p = line.split(",", 1)
            elif ";" in line:
                u, p = line.split(";", 1)
            else:
                continue
            u, p = u.strip(), p.strip()
            if u and p:
                add_account(message.from_user.id, u, p)
                added += 1
        if added > 0:
            await message.answer(f"Bulk accounts added: {added}")
            return

    low = text.lower()
    if low == "start":
        await cmd_start(message, state)
    elif low == "help":
        await cmd_help(message, state)
    elif low == "addaccount":
        await cmd_addaccount(message, state)
    elif low == "addaccounts":
        await cmd_addaccounts(message, state)
    elif low == "listaccounts":
        await cmd_listaccounts(message, state)
    elif low == "uploadkeys":
        await cmd_uploadkeys(message, state)
    elif low == "uploadtweets":
        await cmd_uploadtweets(message, state)
    elif low.startswith("schedule "):
        await cmd_schedule(message, state)
    elif low == "schedule":
        await cmd_schedule(message, state)
    elif low == "time":
        await cmd_time(message, state)
    elif low == "status":
        await cmd_status(message, state)
    elif low == "cancel":
        await cmd_cancel(message, state)
    else:
        await message.answer("I didn't catch that. Type help to see commands.")

# ---- List accounts ----
@dp.message(Command("listaccounts"))
@dp.message(F.text.casefold() == "listaccounts")
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
        err = a.get("last_error")
        line = f"{a['id']}. @{a['username']} | status={status} | last_used={last_used}"
        if err:
            line += f" | error={err}"
        lines.append(line)
    await message.answer("Your accounts:\n" + "\n".join(lines))

# ---- Upload tweets (single or bulk) ----
@dp.message(Command("uploadtweets"))
@dp.message(F.text.casefold() == "uploadtweets")
async def cmd_uploadtweets(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    await state.clear()
    await message.answer(
        "Upload tweets in one of the following ways:\n"
        "1) Single tweet mode: send the tweet text now, then send up to 4 media files (images/videos), then send 'done'.\n"
        "2) Bulk via .txt: one tweet text per line.\n"
        "3) Bulk via .zip: include tweets.csv (columns: text,media1,media2,media3,media4) and referenced media files."
    )
    await state.set_state(UploadTweetsSingleFlow.waiting_text)

@dp.message(UploadTweetsSingleFlow.waiting_text, F.text)
async def uploadtweets_single_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == "cancel":
        await state.clear()
        await message.answer("Cancelled.")
        return
    await state.update_data(tweet_text=text, media=[])
    await state.set_state(UploadTweetsSingleFlow.waiting_media)
    await message.answer("Text saved. Now send up to 4 images/videos. Send 'done' when finished.")

@dp.message(UploadTweetsSingleFlow.waiting_media, F.text.casefold() == "done")
async def uploadtweets_single_done(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("tweet_text", "")
    media = split_media_paths(data.get("media", []))
    entry = add_tweet(message.from_user.id, text, media)
    await state.clear()
    await message.answer(f"Tweet saved (ID {entry['id']}). Media files: {len(media)}")

@dp.message(UploadTweetsSingleFlow.waiting_media, F.photo | F.video)
async def uploadtweets_single_media(message: Message, state: FSMContext):
    data = await state.get_data()
    media = data.get("media", [])
    if len(media) >= MAX_MEDIA:
        await message.answer(f"Already have {MAX_MEDIA} media files. Send 'done' to finish.")
        return
    dest_dir = user_file(message.from_user.id, "media")
    ensure_dir(dest_dir)
    if message.photo:
        filename = f"img_{uuid.uuid4().hex}.jpg"
        dest = dest_dir / filename
        try:
            await bot.download(message.photo[-1], destination=str(dest))
        except Exception as e:
            await message.answer(f"Failed to download image: {e}")
            return
    else:
        ext = ".mp4"
        if message.video.file_name:
            ext = Path(message.video.file_name).suffix.lower() or ".mp4"
        filename = f"vid_{uuid.uuid4().hex}{ext}"
        dest = dest_dir / filename
        try:
            await bot.download(message.video, destination=str(dest))
        except Exception as e:
            await message.answer(f"Failed to download video: {e}")
            return
    media.append(str(dest))
    await state.update_data(media=media)
    await message.answer(f"Media added ({len(media)}/{MAX_MEDIA}). Send more or 'done'.")

# ---- Bulk processors ----
async def process_accounts_file(message: Message):
    user_id = message.from_user.id
    temp_path = user_file(user_id, f"tmp_{sanitize_filename(message.document.file_name)}")
    ensure_dir(temp_path.parent)
    try:
        await bot.download(message.document, destination=str(temp_path))
    except Exception as e:
        await message.answer(f"Failed to download file: {e}")
        return
    added = 0
    with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
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
    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass
    await message.answer(f"Bulk add complete. Added {added} accounts.")

async def process_tweets_package(message: Message):
    user_id = message.from_user.id
    name = message.document.file_name.lower()
    temp_path = user_file(user_id, f"tmp_{sanitize_filename(name)}")
    ensure_dir(temp_path.parent)
    try:
        await bot.download(message.document, destination=str(temp_path))
    except Exception as e:
        await message.answer(f"Failed to download file: {e}")
        return

    added = 0
    try:
        if name.endswith(".txt"):
            with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    text = line.strip()
                    if not text:
                        continue
                    add_tweet(user_id, text, [])
                    added += 1
        elif name.endswith(".csv"):
            with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    text = (row.get("text") or "").strip()
                    media_cols = [row.get(f"media{i}", "").strip() for i in range(1, 5)]
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
                        media_cols = [row.get(f"media{i}", "").strip() for i in range(1, 5)]
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
                        if not text:
                            continue
                        add_tweet(user_id, text, [])
                        added += 1
            else:
                await message.answer("ZIP missing tweets.csv or tweets.txt. Expected tweets.csv with columns text,media1..media4 and media files.")
                return
        else:
            await message.answer("Unsupported file type for tweets. Use .txt, .csv or .zip.")
            return
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    await message.answer(f"Tweets added: {added}")

# ---- Schedule ----
@dp.message(Command("schedule"))
@dp.message(F.text.lower().startswith("schedule"))
async def cmd_schedule(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) > 1:
        dt = parse_ist_datetime(parts[1])
        if not dt:
            await message.answer("Invalid format. Example: schedule 3 August 2025 @12:31AM")
            return
        entry = add_schedule(message.from_user.id, dt)
        await schedule_execution(bot, message.from_user.id, entry["schedule_id"], dt)
        await message.answer(f"Scheduled at {human_ist(dt)}. Will post the next unused tweet.")
        return
    await message.answer("Send in this format: schedule 3 August 2025 @12:31AM")

# ---- Status ----
@dp.message(Command("status"))
@dp.message(F.text.casefold() == "status")
async def cmd_status(message: Message, state: FSMContext):
    if not await ensure_allowed(message, state):
        return
    schedules = load_schedules(message.from_user.id)
    pending = [s for s in schedules if s.get("status") == "pending"]
    running_cnt = sum(1 for _ in SCHEDULE_TASKS.items())
    txt = []
    txt.append(f"Pending schedules: {len(pending)}")
    for s in pending[:10]:
        txt.append(f"- {s['schedule_id'][:8]} at {human_ist(datetime.fromisoformat(s['run_at']))}")
    used = set(load_used_tweets(message.from_user.id))
    tweets = load_tweets(message.from_user.id)
    remain = len([t for t in tweets if t['id'] not in used])
    txt.append(f"Tweets remaining: {remain}")
    txt.append(f"Active tasks (global): {running_cnt}")
    await message.answer("\n".join(txt))

# =========================
# Admin-only tools (no code needed for admins)
# =========================
def admin_only(handler):
    async def wrapper(message: Message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            await message.answer("Admin only.")
            return
        return await handler(message, *args, **kwargs)
    return wrapper

@dp.message(Command("viewusers"))
@admin_only
async def cmd_viewusers(message: Message):
    users = list_users()
    if not users:
        await message.answer("No users yet.")
        return
    lines = [f"{u['user_id']} | {u['first_name']} (@{u.get('username')}) | approved={u.get('approved')}" for u in users]
    await message.answer("Users:\n" + "\n".join(lines[:80]))

@dp.message(Command("viewaccounts"))
@admin_only
async def cmd_viewaccounts(message: Message):
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /viewaccounts {user_id}")
        return
    uid = int(parts[1])
    accounts = load_accounts(uid)
    if not accounts:
        await message.answer("No accounts for this user.")
        return
    lines = [f"{a['id']}. {a['username']} | {a['password']} | status={a.get('last_status')}" for a in accounts]
    await message.answer(f"User {uid} accounts:\n" + "\n".join(lines))

@dp.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(message: Message):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /broadcast Your message here")
        return
    payload = parts[1]
    users = list_users()
    sent = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], f"[Broadcast]\n{payload}")
            sent += 1
        except Exception:
            pass
    await message.answer(f"Broadcast sent to {sent} users.")

@dp.message(Command("logs"))
@admin_only
async def cmd_logs(message: Message):
    logs = load_json(GLOBAL_LOGS, [])
    if not logs:
        await message.answer("No logs yet.")
        return
    tail = logs[-40:]
    lines = []
    for e in tail:
        ts = e.get("ts", "")
        uid = e.get("user_id", "")
        action = e.get("action", "")
        result = e.get("result", "")
        details = e.get("details", {})
        error = e.get("error", "")
        line = f"{ts} | user={uid} | action={action} | result={result}"
        if details:
            line += f" | details={details}"
        if error:
            line += f" | error={error}"
        lines.append(line)
    MAX_LEN = 3800
    text = "Recent logs:\n" + "\n".join(lines)
    for i in range(0, len(text), MAX_LEN):
        await message.answer(text[i:i+MAX_LEN])

# =========================
# Error handler
# =========================
@dp.errors()
async def on_error(update: Update, exception: Exception):
    try:
        user_id = None
        if update and update.message and update.message.from_user:
            user_id = update.message.from_user.id
        append_global_log({
            "ts": iso_ist(),
            "user_id": user_id,
            "action": "error",
            "error": f"{type(exception).__name__}: {exception}",
        })
    except Exception:
        pass
    print("Error:", exception, file=sys.stderr)
    traceback.print_exc()

# =========================
# Startup: reload pending schedules
# =========================
async def reload_and_schedule_all():
    ensure_dir(DATA_DIR)
    save_json(ADMINS_FILE, ADMIN_IDS)
    for item in DATA_DIR.iterdir():
        if not item.is_dir():
            continue
        try:
            uid = int(item.name)
        except ValueError:
            continue
        schedules = load_schedules(uid)
        for s in schedules:
            if s.get("status") != "pending":
                continue
            try:
                run_at = datetime.fromisoformat(s["run_at"])
                await schedule_execution(bot, uid, s["schedule_id"], run_at)
            except Exception:
                continue

# =========================
# Main
# =========================
async def main():
    print("Starting bot (IST timezone, headless Playwright)...", flush=True)
    ensure_dir(DATA_DIR)
    if not GLOBAL_LOGS.exists():
        save_json(GLOBAL_LOGS, [])
    if not USERS_FILE.exists():
        save_json(USERS_FILE, [])
    save_json(ADMINS_FILE, ADMIN_IDS)
    try:
        await ensure_playwright_installed()
    except Exception as e:
        print(f"Playwright ensure failed: {e}", file=sys.stderr)

    await reload_and_schedule_all()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")
