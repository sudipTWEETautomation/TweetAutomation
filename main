import asyncio
import json
import os
import re
import sys
import traceback
import uuid
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ContentType,
    FSInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# Playwright
from playwright.async_api import async_playwright, Error as PlaywrightError

# =========================
# Hard-coded constants
# =========================
BOT_TOKEN = "8428126884:AAFeYk650yE4oUXNIDSi_Mjv9Rl9WIPZ8SQ"  # <--- Put your Telegram Bot Token here
ADMIN_CODE = "STA54123"
IST = ZoneInfo("Asia/Kolkata")
PLAYWRIGHT_HEADLESS = True
MAX_TWEET_IMAGES = 4
TWEET_POST_RETRIES = 3
POSTING_TIMEOUT_SECONDS = 180
BROWSER_INSTALL_TIMEOUT = 600

# Paths
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
GLOBAL_LOGS = DATA_DIR / "logs.json"

# =========================
# Utilities: JSON and FS
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

def sanitize_filename(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", text)[:100]

# =========================
# Admin and Users registry
# =========================
USERS_FILE = DATA_DIR / "users.json"
ADMINS_FILE = DATA_DIR / "admins.json"

def register_user(user_id: int, first_name: str, username: Optional[str]):
    users = load_json(USERS_FILE, [])
    if not any(u.get("user_id") == user_id for u in users):
        users.append({
            "user_id": user_id,
            "first_name": first_name,
            "username": username,
            "joined_at": iso_ist()
        })
        save_json(USERS_FILE, users)

def list_users() -> List[Dict[str, Any]]:
    return load_json(USERS_FILE, [])

def is_admin(user_id: int) -> bool:
    admins = load_json(ADMINS_FILE, [])
    return user_id in admins

def add_admin(user_id: int):
    admins = load_json(ADMINS_FILE, [])
    if user_id not in admins:
        admins.append(user_id)
        save_json(ADMINS_FILE, admins)

# =========================
# Per-user storage helpers
# =========================
def load_accounts(user_id: int) -> List[Dict[str, Any]]:
    return load_json(user_file(user_id, "accounts.json"), [])

def save_accounts(user_id: int, accounts: List[Dict[str, Any]]):
    # reindex ids starting from 1
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
    }
    accounts.append(entry)
    save_accounts(user_id, accounts)
    return entry

def delete_account(user_id: int, account_id: int) -> bool:
    accounts = load_accounts(user_id)
    new_accounts = [a for a in accounts if int(a.get("id")) != int(account_id)]
    if len(new_accounts) == len(accounts):
        return False
    save_accounts(user_id, new_accounts)
    return True

def load_tweets(user_id: int) -> List[Dict[str, Any]]:
    return load_json(user_file(user_id, "tweets.json"), [])

def save_tweets(user_id: int, tweets: List[Dict[str, Any]]):
    # reindex ids starting from 1
    for idx, t in enumerate(tweets, start=1):
        t["id"] = idx
    save_json(user_file(user_id, "tweets.json"), tweets)

def add_tweet(user_id: int, text: str, images: List[str]) -> Dict[str, Any]:
    tweets = load_tweets(user_id)
    entry = {
        "id": len(tweets) + 1,
        "text": text,
        "images": images,
        "added_at": iso_ist(),
    }
    tweets.append(entry)
    save_tweets(user_id, tweets)
    return entry

def load_used_tweets(user_id: int) -> List[int]:
    return load_json(user_file(user_id, "used_tweets.json"), [])

def save_used_tweets(user_id: int, used: List[int]):
    save_json(user_file(user_id, "used_tweets.json"), used)

def mark_tweet_used(user_id: int, tweet_id: int):
    used = load_used_tweets(user_id)
    if tweet_id not in used:
        used.append(tweet_id)
        save_used_tweets(user_id, used)

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
    changed = False
    for s in schedules:
        if s.get("schedule_id") == schedule_id:
            s["status"] = status
            changed = True
            break
    if changed:
        save_schedules(user_id, schedules)

# =========================
# Concurrency: per-user locks
# =========================
_USER_LOCKS: Dict[int, asyncio.Lock] = {}
def get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _USER_LOCKS:
        _USER_LOCKS[user_id] = asyncio.Lock()
    return _USER_LOCKS[user_id]

# =========================
# Time parsing (IST)
# Format example: "3 August 2025 @12:31AM"
# =========================
MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

DT_REGEX = re.compile(
    r"^\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*@\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*$"
)

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
        dt = datetime(year, month, day, hour, minute, tzinfo=IST)
        return dt
    except ValueError:
        return None

# =========================
# Playwright helpers
# =========================
async def ensure_playwright_installed():
    # Try to quickly launch; if fails due to missing browser, attempt to install.
    try:
        async with async_playwright() as p:
            # If this succeeds, we have binaries
            _ = p.chromium
            return
    except Exception:
        pass
    # Try installing chromium with deps
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

async def post_tweet_via_playwright(
    account_username: str,
    account_password: str,
    tweet_text: str,
    image_paths: List[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Login to X (Twitter) and post a tweet. Returns (success, tweet_url, error_message).
    Headless by default. No API, no cookies.
    """
    try:
        await ensure_playwright_installed()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=PLAYWRIGHT_HEADLESS)
            context = await browser.new_context(
                locale="en-US",
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            page.set_default_timeout(30000)

            # Login flow
            await page.goto("https://x.com/login", wait_until="domcontentloaded")
            # Username field
            try:
                await page.wait_for_selector('input[name="text"]', timeout=15000)
                await page.fill('input[name="text"]', account_username)
                # Next
                next_btn = page.locator('div[role="button"]:has-text("Next")')
                if await next_btn.count() > 0:
                    await next_btn.first.click()
                else:
                    await page.keyboard.press("Enter")
            except PlaywrightError:
                await context.close()
                await browser.close()
                return False, None, "Login page not reachable"

            # Possible additional username confirmation (Twitter sometimes asks again)
            try:
                # If it still shows username field, just press next again
                if await page.locator('input[name="text"]').count() > 0:
                    await page.fill('input[name="text"]', account_username)
                    next_btn2 = page.locator('div[role="button"]:has-text("Next")')
                    if await next_btn2.count() > 0:
                        await next_btn2.first.click()
                    else:
                        await page.keyboard.press("Enter")
            except PlaywrightError:
                pass

            # Password field
            try:
                await page.wait_for_selector('input[name="password"]', timeout=20000)
                await page.fill('input[name="password"]', account_password)
                login_btn = page.locator('div[role="button"]:has-text("Log in")')
                if await login_btn.count() > 0:
                    await login_btn.first.click()
                else:
                    await page.keyboard.press("Enter")
            except PlaywrightError:
                await context.close()
                await browser.close()
                return False, None, "Password step not available (challenge/blocked?)"

            # Check login success or challenge
            try:
                # Wait for home indicator or compose box
                await page.wait_for_selector('[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="tweetTextarea_0"], [aria-label="Post text"]', timeout=30000)
            except PlaywrightError:
                # Look for challenge errors
                if await page.locator("text=Enter your phone number").count() > 0 or await page.locator("text=Verify").count() > 0:
                    await context.close()
                    await browser.close()
                    return False, None, "Login requires additional verification (2FA/phone)."
                # Incorrect password?
                if await page.locator("text=Wrong password").count() > 0:
                    await context.close()
                    await browser.close()
                    return False, None, "Wrong password."
                await context.close()
                await browser.close()
                return False, None, "Login failed (unknown reason)."

            # Compose tweet (click into the composer)
            composer = page.locator('[data-testid="tweetTextarea_0"], div[aria-label="Post text"]')
            if await composer.count() == 0:
                # Open composer via "Post" button on home
                try:
                    compose_btn = page.locator('[data-testid="SideNav_NewTweet_Button"]')
                    if await compose_btn.count() > 0:
                        await compose_btn.first.click()
                        composer = page.locator('[data-testid="tweetTextarea_0"], div[aria-label="Post text"]')
                except:
                    pass

            if await composer.count() == 0:
                await context.close()
                await browser.close()
                return False, None, "Cannot find tweet composer."

            await composer.first.click()
            await composer.first.type(tweet_text)

            # Upload images if any
            if image_paths:
                # There is usually an input[type=file] beneath the composer
                file_input = page.locator('input[type="file"]')
                if await file_input.count() == 0:
                    # Try clicking the media button first
                    media_btn = page.locator('[data-testid="fileInput"]')
                    # some UIs may not have visible input; attempt different approach
                # Upload files (one shot or sequentially)
                uploaded = False
                try:
                    # Try a common locator for file input under composer
                    upload_target = page.locator('input[type="file"][accept*="image"]')
                    if await upload_target.count() == 0:
                        upload_target = page.locator('input[type="file"]')
                    paths = [str(Path(p)) for p in image_paths[:MAX_TWEET_IMAGES]]
                    await upload_target.first.set_input_files(paths)
                    uploaded = True
                except Exception:
                    # Try clicking an image button then set input
                    try:
                        img_btn = page.locator('[data-testid="toolBar"] [data-testid="fileInput"]')
                        if await img_btn.count() > 0:
                            await img_btn.first.set_input_files([str(Path(p)) for p in image_paths[:MAX_TWEET_IMAGES]])
                            uploaded = True
                    except Exception:
                        pass
                if not uploaded:
                    await context.close()
                    await browser.close()
                    return False, None, "Image upload failed."

            # Post tweet
            post_btn = page.locator('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]')
            if await post_btn.count() == 0:
                await context.close()
                await browser.close()
                return False, None, "Cannot find Post button."
            await post_btn.first.click()

            # Wait a moment for the tweet to be created
            await page.wait_for_timeout(4000)

            # Try to capture tweet URL
            tweet_url = None
            try:
                # Navigate to profile and grab latest tweet link
                profile_url = f"https://x.com/{account_username}"
                await page.goto(profile_url, wait_until="domcontentloaded")
                # Find the first status link under an article
                link = page.locator('article a[href*="/status/"]')
                await link.first.wait_for(timeout=20000)
                href = await link.first.get_attribute("href")
                if href and href.startswith("/"):
                    tweet_url = "https://x.com" + href
                elif href and href.startswith("http"):
                    tweet_url = href
            except Exception:
                # Fallback: search on home timeline
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
            return True, None, "Tweet posted but URL not located (UI changed)."
    except PlaywrightError as e:
        return False, None, f"Playwright error: {e}"
    except Exception as e:
        return False, None, f"Unexpected error: {e}"

# =========================
# Scheduler: manage scheduled tasks
# =========================
SCHEDULE_TASKS: Dict[str, asyncio.Task] = {}  # schedule_id -> task

async def schedule_execution(bot: Bot, user_id: int, schedule_id: str, run_at: datetime):
    """
    Create a task that sleeps until run_at (IST) then posts the next unused tweet.
    """
    async def runner():
        try:
            # Sleep until time
            delay = (run_at - now_ist()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)

            # Execute posting
            await post_next_tweet_for_user(bot, user_id, schedule_id)
        finally:
            # Mark completed if not already done
            update_schedule_status(user_id, schedule_id, "completed")
            SCHEDULE_TASKS.pop(schedule_id, None)

    # Cancel existing for same id, if any
    old = SCHEDULE_TASKS.get(schedule_id)
    if old and not old.done():
        old.cancel()
    task = asyncio.create_task(runner())
    SCHEDULE_TASKS[schedule_id] = task

async def post_next_tweet_for_user(bot: Bot, user_id: int, schedule_id: Optional[str] = None):
    """
    Finds next unused tweet and posts it using the next available account (round-robin).
    """
    lock = get_user_lock(user_id)
    async with lock:
        tweets = load_tweets(user_id)
        used = set(load_used_tweets(user_id))
        # pick first tweet not in used
        next_tweet = None
        for t in tweets:
            if t.get("id") not in used:
                next_tweet = t
                break

        if not next_tweet:
            await bot.send_message(user_id, "No pending tweets left to post.")
            append_global_log({
                "ts": iso_ist(),
                "user_id": user_id,
                "action": "post",
                "result": "no_tweets"
            })
            if schedule_id:
                update_schedule_status(user_id, schedule_id, "no_tweets")
            return

        account = select_next_account(user_id)
        if not account:
            await bot.send_message(user_id, "No Twitter accounts available. Add accounts with /addaccount.")
            append_global_log({
                "ts": iso_ist(),
                "user_id": user_id,
                "action": "post",
                "tweet_id": next_tweet.get("id"),
                "result": "no_accounts"
            })
            if schedule_id:
                update_schedule_status(user_id, schedule_id, "no_accounts")
            return

        # Retry logic
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
                        image_paths=[str(Path(p)) for p in next_tweet.get("images", [])][:MAX_TWEET_IMAGES],
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
            await asyncio.sleep(3)  # small backoff

        # Report to user + logs
        if success:
            mark_tweet_used(user_id, int(next_tweet["id"]))
            details = {
                "tweet_id": int(next_tweet["id"]),
                "account": account["username"],
                "url": tweet_url,
            }
            append_global_log({
                "ts": iso_ist(),
                "user_id": user_id,
                "action": "post",
                "details": details,
                "result": "success",
            })
            if tweet_url:
                await bot.send_message(user_id, f"Tweet posted from @{account['username']}: {tweet_url}")
            else:
                await bot.send_message(user_id, f"Tweet posted from @{account['username']}, but link could not be detected.")
        else:
            append_global_log({
                "ts": iso_ist(),
                "user_id": user_id,
                "action": "post",
                "tweet_id": int(next_tweet["id"]),
                "account": account["username"],
                "result": "failed",
                "error": last_error,
            })
            await bot.send_message(user_id, f"Failed to post tweet from @{account['username']}. Error: {last_error or 'Unknown error'}")

        if schedule_id:
            update_schedule_status(user_id, schedule_id, "completed" if success else "failed")

# =========================
# FSM States
# =========================
class AdminAuth(StatesGroup):
    waiting_code = State()

class AddAccountFlow(StatesGroup):
    waiting_username = State()
    waiting_password = State()

class DeleteAccountFlow(StatesGroup):
    waiting_id = State()

class AddTweetFlow(StatesGroup):
    waiting_text = State()
    waiting_images = State()

class ScheduleFlow(StatesGroup):
    waiting_datetime = State()

class BroadcastFlow(StatesGroup):
    waiting_message = State()

# =========================
# Bot setup
# =========================
bot = Bot(BOT_TOKEN, parse_mode=None)
dp = Dispatcher()

# =========================
# Handlers: User commands
# =========================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    register_user(message.from_user.id, message.from_user.first_name, message.from_user.username)
    ensure_dir(DATA_DIR)
    ensure_dir(user_dir(message.from_user.id))
    await state.clear()
    await message.answer(
        "Welcome! This bot can add multiple X (Twitter) accounts, save tweets with optional images, and schedule posting in IST.\n"
        "Commands:\n"
        "- /addaccount — add a Twitter account (supports single or bulk via text/file)\n"
        "- /deleteaccount — delete an account by its ID\n"
        "- /addtweet — add a tweet (text + up to 4 images)\n"
        "- /schedule — schedule posting time (e.g., 3 August 2025 @12:31AM)\n"
        "Admin? Use /admin to unlock admin tools."
    )

# ---- Add Account (supports single flow and bulk text/file) ----
@dp.message(Command("addaccount"))
async def cmd_addaccount(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddAccountFlow.waiting_username)
    await message.answer(
        "Add account:\n"
        "1) Send username now to add a single account.\n"
        "OR\n"
        "2) Send text with multiple lines in the format username,password for bulk.\n"
        "OR\n"
        "3) Upload a .txt or .csv file containing lines like username,password for bulk."
    )

@dp.message(AddAccountFlow.waiting_username, F.document)
async def addaccount_document_bulk(message: Message, state: FSMContext):
    # Bulk via file
    doc = message.document
    if not doc.file_name.lower().endswith((".txt", ".csv")):
        await message.answer("Please upload a .txt or .csv file with lines: username,password")
        return

    temp_path = user_file(message.from_user.id, f"tmp_{sanitize_filename(doc.file_name)}")
    ensure_dir(temp_path.parent)

    # Download
    try:
        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, destination=str(temp_path))
    except Exception:
        # fallback new API
        try:
            await bot.download(doc, destination=str(temp_path))
        except Exception as e:
            await message.answer(f"Failed to download file: {e}")
            return

    # Parse lines
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
                add_account(message.from_user.id, u, p)
                added += 1

    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass

    await state.clear()
    await message.answer(f"Bulk add complete. Added {added} accounts.")
    accounts = load_accounts(message.from_user.id)
    if accounts:
        lines = [f"{a['id']}. {a['username']}" for a in accounts]
        await message.answer("Your accounts:\n" + "\n".join(lines))

@dp.message(AddAccountFlow.waiting_username, F.text)
async def addaccount_text_or_bulk(message: Message, state: FSMContext):
    content = message.text.strip()
    # Bulk by multi-line "username,password"
    if "\n" in content or ("," in content and len(content.splitlines()) == 1 and content.count(",") > 1):
        added = 0
        lines = content.splitlines()
        for line in lines:
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
        await state.clear()
        await message.answer(f"Bulk add complete. Added {added} accounts.")
        accounts = load_accounts(message.from_user.id)
        if accounts:
            lines = [f"{a['id']}. {a['username']}" for a in accounts]
            await message.answer("Your accounts:\n" + "\n".join(lines))
        return

    # Single-flow: ask for password next
    await state.update_data(tmp_username=content)
    await state.set_state(AddAccountFlow.waiting_password)
    await message.answer("Username received. Now send the password.")

@dp.message(AddAccountFlow.waiting_password, F.text)
async def addaccount_password(message: Message, state: FSMContext):
    data = await state.get_data()
    username = data.get("tmp_username")
    password = message.text.strip()
    if not username or not password:
        await message.answer("Please start again: /addaccount")
        await state.clear()
        return
    entry = add_account(message.from_user.id, username, password)
    await state.clear()
    await message.answer(f"Account added with ID {entry['id']}: @{entry['username']}")

# ---- Delete Account ----
@dp.message(Command("deleteaccount"))
async def cmd_deleteaccount(message: Message, state: FSMContext):
    await state.clear()
    accounts = load_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts to delete.")
        return
    lines = [f"{a['id']}. {a['username']}" for a in accounts]
    await state.set_state(DeleteAccountFlow.waiting_id)
    await message.answer("Send the serial ID of the account to delete:\n" + "\n".join(lines))

@dp.message(DeleteAccountFlow.waiting_id, F.text)
async def handle_deleteaccount(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Please send a valid numeric account ID.")
        return
    account_id = int(text)
    ok = delete_account(message.from_user.id, account_id)
    await state.clear()
    if ok:
        await message.answer(f"Account {account_id} deleted and IDs re-indexed.")
    else:
        await message.answer("Account ID not found.")

# ---- Add Tweet (text + up to 4 images) ----
@dp.message(Command("addtweet"))
async def cmd_addtweet(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddTweetFlow.waiting_text)
    await message.answer("Send the tweet text.")

@dp.message(AddTweetFlow.waiting_text, F.text)
async def addtweet_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        await message.answer("Please send non-empty text.")
        return
    await state.update_data(tweet_text=text, images=[])
    await state.set_state(AddTweetFlow.waiting_images)
    await message.answer(
        "Text saved. Now send up to 4 images (each as a photo). When done, send the word: done\n"
        "(Images are optional. You can also send 'done' immediately.)"
    )

@dp.message(AddTweetFlow.waiting_images, F.text.casefold() == "done")
async def addtweet_done(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("tweet_text", "")
    images = data.get("images", [])
    entry = add_tweet(message.from_user.id, text, images)
    await state.clear()
    await message.answer(
        f"Tweet saved (ID {entry['id']}). Text length: {len(text)}. Images: {len(images)}."
    )

@dp.message(AddTweetFlow.waiting_images, F.photo)
async def addtweet_image(message: Message, state: FSMContext):
    data = await state.get_data()
    images = data.get("images", [])
    if len(images) >= MAX_TWEET_IMAGES:
        await message.answer(f"Already have {MAX_TWEET_IMAGES} images. Send 'done' to finish.")
        return
    photo = message.photo[-1]
    dest_dir = user_file(message.from_user.id, "images")
    ensure_dir(dest_dir)
    filename = f"img_{uuid.uuid4().hex}.jpg"
    dest = dest_dir / filename

    try:
        file = await bot.get_file(photo.file_id)
        await bot.download_file(file.file_path, destination=str(dest))
    except Exception:
        try:
            await bot.download(photo, destination=str(dest))
        except Exception as e:
            await message.answer(f"Failed to download image: {e}")
            return

    images.append(str(dest))
    await state.update_data(images=images)
    await message.answer(f"Image added ({len(images)}/{MAX_TWEET_IMAGES}). Send more or 'done' to save.")

# ---- Schedule ----
@dp.message(Command("schedule"))
async def cmd_schedule(message: Message, state: FSMContext):
    args = message.text.strip().split(maxsplit=1)
    if len(args) > 1:
        # User provided the datetime inline
        dt = parse_ist_datetime(args[1])
        if not dt:
            await message.answer("Invalid format. Use like: 3 August 2025 @12:31AM")
            return
        entry = add_schedule(message.from_user.id, dt)
        await schedule_execution(bot, message.from_user.id, entry["schedule_id"], dt)
        await message.answer(f"Scheduled at {human_ist(dt)}. Will post the next unused tweet using one account.")
        return

    await state.clear()
    await state.set_state(ScheduleFlow.waiting_datetime)
    await message.answer('Send date and time in this format: 3 August 2025 @12:31AM (IST)')

@dp.message(ScheduleFlow.waiting_datetime, F.text)
async def schedule_datetime(message: Message, state: FSMContext):
    dt = parse_ist_datetime(message.text)
    if not dt:
        await message.answer("Invalid format. Example: 3 August 2025 @12:31AM")
        return
    entry = add_schedule(message.from_user.id, dt)
    await schedule_execution(bot, message.from_user.id, entry["schedule_id"], dt)
    await state.clear()
    await message.answer(f"Scheduled at {human_ist(dt)}. At that time, I will post the next unused tweet using one account.")

# =========================
# Admin: unlock + tools
# =========================
@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer("Admin already unlocked. Available commands:\n/viewusers\n/viewaccounts {user_id}\n/broadcast\n/logs")
        return
    await state.set_state(AdminAuth.waiting_code)
    await message.answer("Enter admin code:")

@dp.message(AdminAuth.waiting_code, F.text)
async def admin_code(message: Message, state: FSMContext):
    code = message.text.strip()
    await state.clear()
    if code == ADMIN_CODE:
        add_admin(message.from_user.id)
        await message.answer("Admin access granted. Commands:\n/viewusers\n/viewaccounts {user_id}\n/broadcast\n/logs")
    else:
        await message.answer("Invalid code.")

def admin_only(func):
    async def wrapper(message: Message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            await message.answer("Admin access required. Use /admin.")
            return
        return await func(message, *args, **kwargs)
    return wrapper

@dp.message(Command("viewusers"))
@admin_only
async def cmd_viewusers(message: Message):
    users = list_users()
    if not users:
        await message.answer("No users yet.")
        return
    lines = []
    for u in users:
        lines.append(f"{u['user_id']}: {u['first_name']} (@{u.get('username')})")
    await message.answer("Registered users:\n" + "\n".join(lines))

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
    lines = [f"{a['id']}. {a['username']} | {a['password']}" for a in accounts]
    await message.answer(f"User {uid} accounts:\n" + "\n".join(lines))

@dp.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(message: Message, state: FSMContext):
    await state.set_state(BroadcastFlow.waiting_message)
    await message.answer("Send the broadcast message (text).")

@dp.message(BroadcastFlow.waiting_message, F.text)
@admin_only
async def do_broadcast(message: Message, state: FSMContext):
    text = message.text
    await state.clear()
    users = list_users()
    sent = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], f"[Broadcast]\n{text}")
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
    # Show last 30 entries
    tail = logs[-30:]
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
    text = "Recent logs:\n" + "\n".join(lines)
    # Telegram messages have limits; chunk if needed
    MAX_LEN = 3800
    for i in range(0, len(text), MAX_LEN):
        await message.answer(text[i:i+MAX_LEN])

# =========================
# Startup: reload pending schedules
# =========================
async def reload_and_schedule_all(bot: Bot):
    ensure_dir(DATA_DIR)
    # For each user dir under data/
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
                # If in the past (but still pending), schedule immediate
                await schedule_execution(bot, uid, s["schedule_id"], run_at)
            except Exception:
                continue

# =========================
# Error handler (optional)
# =========================
@dp.error()
async def on_error(event, exception):
    try:
        ctx_msg = getattr(event, "update", None)
        append_global_log({
            "ts": iso_ist(),
            "user_id": getattr(getattr(ctx_msg, "message", None), "from_user", {"id": "?"}).get("id", "?") if ctx_msg else "?",
            "action": "error",
            "error": str(exception),
        })
    except Exception:
        pass
    print("Error:", exception, file=sys.stderr)
    traceback.print_exc()

# =========================
# Main entry
# =========================
async def main():
    print("Starting bot (IST timezone, headless Playwright)...", flush=True)
    # Ensure base folders and files exist
    ensure_dir(DATA_DIR)
    if not GLOBAL_LOGS.exists():
        save_json(GLOBAL_LOGS, [])
    if not ADMINS_FILE.exists():
        save_json(ADMINS_FILE, [])
    if not USERS_FILE.exists():
        save_json(USERS_FILE, [])

    # Pre-flight: try to ensure Playwright browser is available (non-fatal if fails)
    try:
        await ensure_playwright_installed()
    except Exception as e:
        print(f"Playwright ensure failed: {e}", file=sys.stderr)

    # Reload pending schedules
    await reload_and_schedule_all(bot)

    # Start polling
    await dp.start_polling(bot, allowed_updates=["message"])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")
