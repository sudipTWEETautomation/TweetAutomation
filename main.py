#!/usr/bin/env python3
"""
Enhanced Telegram Bot for Twitter/X Automation
Fixed version with IST timezone, tweet link extraction, and hardcoded config
File: main.py
"""

import os
import json
import asyncio
import logging
import hashlib
import random
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path
from contextlib import asynccontextmanager

# Third-party imports
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramAPIError

try:
    from playwright.async_api import async_playwright
    import aiofiles
    import jsonschema
except ImportError as e:
    print(f"Missing required package: {e}")
    print("Install with: pip install playwright aiofiles jsonschema")
    print("Then run: playwright install chromium")
    exit(1)

# =====================================================
# CONFIGURATION - EDIT THESE VALUES
# =====================================================
BOT_TOKEN = "8428126884:AAFeYk650yE4oUXNIDSi_Mjv9Rl9WIPZ8SQ"  # Get from @BotFather
ADMIN_CODE = "STA42931"  # Change this to your secure code

# Other settings
DATA_DIR = Path("data")
LOG_LEVEL = "INFO"
MAX_TWEET_LENGTH = 280
BROWSER_HEADLESS = True  # Set to False to see browser
POST_DELAY_MIN = 5  # seconds between posts
POST_DELAY_MAX = 15
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
TWEET_LINK_WAIT_TIME = 10  # seconds to wait for tweet URL after posting

# IST Timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Setup logging
DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(DATA_DIR / 'bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# JSON Schema for accounts validation
ACCOUNTS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "cookies": {"type": "array"},
            "origins": {"type": "array"},
            "localStorage": {"type": "array"}
        },
        "required": ["cookies"]
    }
}

class AuthState(StatesGroup):
    waiting_for_code = State()

class ScheduleState(StatesGroup):
    waiting_for_time = State()

class BotError(Exception):
    """Custom exception for bot errors"""
    pass

def ist_now():
    """Get current time in IST"""
    return datetime.now(IST)

def ist_from_string(time_str: str) -> datetime:
    """Parse time string as IST"""
    formats = [
        "%d %B %Y @%I:%M%p",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M",
        "%d %B %Y %H:%M"
    ]
    
    for fmt in formats:
        try:
            # Parse as naive datetime, then localize to IST
            naive_dt = datetime.strptime(time_str, fmt)
            return naive_dt.replace(tzinfo=IST)
        except ValueError:
            continue
    return None

def extract_tweet_url(page_url: str) -> Optional[str]:
    """Extract tweet URL from page URL"""
    try:
        # Match Twitter/X URL patterns
        patterns = [
            r'https://(?:twitter\.com|x\.com)/[^/]+/status/(\d+)',
            r'https://(?:twitter\.com|x\.com)/.*?/status/(\d+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, page_url)
            if match:
                tweet_id = match.group(1)
                return f"https://x.com/i/status/{tweet_id}"
        
        return None
    except Exception as e:
        logger.error(f"Error extracting tweet URL: {e}")
        return None

class TwitterAutomationBot:
    """Enhanced Twitter automation bot with tweet link extraction and IST timezone support"""
    
    def __init__(self):
        if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            raise BotError("Please set BOT_TOKEN in the configuration section of main.py")
            
        self.bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.router = Router()
        self.dp.include_router(self.router)
        self.user_auth = set()
        self.active_tasks = {}
        
        self._setup_handlers()
        logger.info("Bot initialized successfully with IST timezone and tweet link extraction")
    
    def _setup_handlers(self):
        """Setup all message handlers"""
        self.router.message(CommandStart())(self.cmd_start)
        self.router.message(AuthState.waiting_for_code)(self.auth_code_check)
        self.router.message(Command("uploadkeys"))(self.upload_keys)
        self.router.message(Command("uploadtweets"))(self.upload_tweets)
        self.router.message(Command("schedule"))(self.schedule_prompt)
        self.router.message(Command("status"))(self.status_command)
        self.router.message(Command("cancel"))(self.cancel_command)
        self.router.message(Command("help"))(self.help_command)
        self.router.message(Command("time"))(self.time_command)
        self.router.message(ScheduleState.waiting_for_time)(self.handle_schedule)
    
    async def cmd_start(self, message: Message, state: FSMContext):
        """Start command handler"""
        try:
            current_time = ist_now().strftime('%d %B %Y, %I:%M %p IST')
            await message.answer(
                "🔐 <b>Twitter/X Automation Bot</b>\n\n"
                f"🇮🇳 Current IST Time: {current_time}\n\n"
                "✨ <b>New Feature:</b> Bot now sends posted tweet links!\n\n"
                "Welcome! Please enter your authorization code to continue:"
            )
            await state.set_state(AuthState.waiting_for_code)
            logger.info(f"Start command from user {message.from_user.id}")
        except Exception as e:
            logger.error(f"Error in cmd_start: {e}")
            await message.answer("❌ An error occurred. Please try again.")

    async def auth_code_check(self, message: Message, state: FSMContext):
        """Enhanced authentication with input validation"""
        try:
            code = message.text.strip() if message.text else ""
            
            # Delete the message containing the code for security
            try:
                await message.delete()
            except:
                pass
            
            # Hash comparison for security
            input_hash = hashlib.sha256(code.encode()).hexdigest()
            admin_hash = hashlib.sha256(ADMIN_CODE.encode()).hexdigest()
            
            if input_hash == admin_hash:
                self.user_auth.add(message.from_user.id)
                await message.answer(
                    "✅ <b>Authorization successful!</b>\n\n"
                    "📋 <b>Available commands:</b>\n"
                    "📁 /uploadkeys - Upload accounts.json file\n"
                    "📝 /uploadtweets - Upload tweets.txt file\n"
                    "⏰ /schedule - Schedule posting time (IST)\n"
                    "🕐 /time - Show current IST time\n"
                    "📊 /status - Check active tasks\n"
                    "❌ /cancel - Cancel operations\n"
                    "❓ /help - Show help information\n\n"
                    "🔗 <b>New:</b> Bot will send posted tweet links after each post!"
                )
                await state.clear()
                logger.info(f"Successful authentication for user {message.from_user.id}")
            else:
                await message.answer("❌ Incorrect code. Please try again.")
                logger.warning(f"Failed authentication from user {message.from_user.id}")
                
        except Exception as e:
            logger.error(f"Error in auth_code_check: {e}")
            await message.answer("❌ Authentication error. Please try again.")

    def _check_auth(self, user_id: int) -> bool:
        """Check if user is authorized"""
        return user_id in self.user_auth

    async def time_command(self, message: Message):
        """Show current IST time"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return
        
        current_time = ist_now()
        await message.answer(
            f"🕐 <b>Current Time (IST):</b>\n"
            f"📅 {current_time.strftime('%d %B %Y')}\n"
            f"⏰ {current_time.strftime('%I:%M %p')}\n"
            f"🌍 Timezone: Asia/Kolkata (UTC+5:30)"
        )

    async def upload_keys(self, message: Message):
        """Enhanced accounts file upload with validation"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        if not message.document:
            await message.answer(
                "📎 Please upload your accounts.json file.\n\n"
                "💡 <b>Format:</b> Playwright storage state JSON file\n"
                "📏 <b>Max size:</b> 10MB"
            )
            return

        try:
            # Validate file
            if not message.document.file_name.endswith('.json'):
                await message.answer("❌ Please upload a JSON file (.json extension required)")
                return

            if message.document.file_size > MAX_FILE_SIZE:
                await message.answer(f"❌ File too large. Maximum size is {MAX_FILE_SIZE//1024//1024}MB")
                return

            # Create user directory
            user_dir = DATA_DIR / str(message.from_user.id)
            user_dir.mkdir(parents=True, exist_ok=True)
            file_path = user_dir / "accounts.json"

            # Download file
            await self.bot.download(message.document, destination=file_path)
            
            # Validate JSON structure
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                data = json.loads(content)
                
            # Validate schema
            jsonschema.validate(data, ACCOUNTS_SCHEMA)

            await message.answer(
                f"✅ <b>Accounts uploaded successfully!</b>\n"
                f"📊 Found {len(data)} account(s)\n"
                f"💾 Saved to: accounts.json\n"
                f"🔗 Bot will extract tweet links after posting"
            )
            logger.info(f"Accounts file uploaded by user {message.from_user.id} ({len(data)} accounts)")

        except json.JSONDecodeError:
            await message.answer("❌ Invalid JSON file format. Please check your file.")
        except jsonschema.ValidationError as e:
            await message.answer(f"❌ Invalid file structure: {e.message}")
        except Exception as e:
            logger.error(f"Error uploading accounts: {e}")
            await message.answer("❌ Error uploading file. Please try again.")

    async def upload_tweets(self, message: Message):
        """Enhanced tweets file upload with validation"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        if not message.document:
            await message.answer(
                "📎 Please upload your tweets.txt file.\n\n"
                "💡 <b>Format:</b> Plain text, separate tweets with double newline\n"
                "📏 <b>Max size:</b> 5MB\n"
                "📝 <b>Example:</b>\n"
                "<code>First tweet here\n\n"
                "Second tweet here\n\n"
                "Third tweet here</code>"
            )
            return

        try:
            if not message.document.file_name.endswith('.txt'):
                await message.answer("❌ Please upload a TXT file (.txt extension required)")
                return

            if message.document.file_size > MAX_FILE_SIZE // 2:  # 5MB for tweets
                await message.answer("❌ File too large. Maximum size is 5MB")
                return

            user_dir = DATA_DIR / str(message.from_user.id)
            user_dir.mkdir(parents=True, exist_ok=True)
            file_path = user_dir / "tweets.txt"

            await self.bot.download(message.document, destination=file_path)

            # Process and validate tweets
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                tweets = [t.strip() for t in content.split("\n\n") if t.strip()]

            if not tweets:
                await message.answer("❌ No tweets found in file. Make sure tweets are separated by double newlines.")
                return

            # Check tweet lengths
            long_tweets = [(i+1, len(tweet)) for i, tweet in enumerate(tweets) 
                          if len(tweet) > MAX_TWEET_LENGTH]
            
            warning_msg = ""
            if long_tweets:
                warning_msg = f"\n⚠️ <b>Warning:</b> {len(long_tweets)} tweets exceed {MAX_TWEET_LENGTH} characters"

            await message.answer(
                f"✅ <b>Tweets uploaded successfully!</b>\n"
                f"📊 Found {len(tweets)} tweet(s)\n"
                f"💾 Saved to: tweets.txt{warning_msg}\n"
                f"🔗 Tweet links will be sent after each post"
            )
            logger.info(f"Tweets file uploaded by user {message.from_user.id} ({len(tweets)} tweets)")

        except Exception as e:
            logger.error(f"Error uploading tweets: {e}")
            await message.answer("❌ Error uploading file. Please try again.")

    async def help_command(self, message: Message):
        """Help command with detailed instructions"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        help_text = """
🤖 <b>Twitter/X Automation Bot - Help</b>

<b>📋 Commands:</b>
• /uploadkeys - Upload accounts.json (Playwright storage state)
• /uploadtweets - Upload tweets.txt (one tweet per paragraph)
• /schedule - Schedule posting time (IST timezone)
• /time - Show current IST time
• /status - Check current tasks
• /cancel - Cancel active operations
• /help - Show this help

<b>📅 IST Time Formats:</b>
• 3 August 2025 @12:31PM
• 03/08/2025 12:31
• 2025-08-03 12:31
• 3 August 2025 12:31

<b>📄 File Formats:</b>
<b>accounts.json:</b> Playwright browser storage state
<b>tweets.txt:</b> Plain text, separate tweets with double newlines

<b>🇮🇳 Timezone:</b>
All times are in IST (Indian Standard Time, UTC+5:30)
Current IST time: Use /time command

<b>🔗 Tweet Links Feature:</b>
• Bot automatically extracts tweet URLs after posting
• Sends clickable links for each posted tweet
• Works with both successful and retry attempts

<b>⚠️ Important:</b>
• Respect Twitter/X terms of service
• Use reasonable posting intervals (5-15 seconds)
• Monitor for rate limits
• Keep credentials secure

<b>🛡️ Security:</b>
• Files are stored securely per user
• Comprehensive logging enabled
• Anti-detection measures included
        """
        await message.answer(help_text)

    async def schedule_prompt(self, message: Message, state: FSMContext):
        """Enhanced scheduling prompt with IST timezone"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        current_time = ist_now().strftime('%d %B %Y, %I:%M %p IST')
        await message.answer(
            "📅 <b>Schedule Posting Time (IST)</b>\n\n"
            f"🇮🇳 Current IST Time: {current_time}\n\n"
            "Enter the time in one of these formats:\n\n"
            "🔸 <code>3 August 2025 @12:31PM</code>\n"
            "🔸 <code>03/08/2025 12:31</code>\n"
            "🔸 <code>2025-08-03 12:31</code>\n"
            "🔸 <code>3 August 2025 12:31</code>\n\n"
            "⏰ <b>Note:</b> All times are in IST (Indian Standard Time)\n"
            "🔗 <b>Feature:</b> Tweet links will be sent after each post"
        )
        await state.set_state(ScheduleState.waiting_for_time)

    async def handle_schedule(self, message: Message, state: FSMContext):
        """Enhanced scheduling with IST timezone support"""
        try:
            time_input = message.text.strip()
            
            # Parse time as IST
            dt = ist_from_string(time_input)
            
            if not dt:
                await message.answer(
                    "❌ Invalid time format. Please use one of these formats:\n"
                    "• 3 August 2025 @12:31PM\n"
                    "• 03/08/2025 12:31\n"
                    "• 2025-08-03 12:31\n"
                    "• 3 August 2025 12:31"
                )
                return

            # Validate future time
            now = ist_now()
            if dt <= now:
                await message.answer(f"❌ Scheduled time must be in the future.\nCurrent IST time: {now.strftime('%d %B %Y, %I:%M %p')}")
                return

            # Check files exist
            user_dir = DATA_DIR / str(message.from_user.id)
            tweets_file = user_dir / "tweets.txt"
            accounts_file = user_dir / "accounts.json"
            
            if not tweets_file.exists():
                await message.answer("❌ Please upload tweets.txt file first using /uploadtweets")
                return
                
            if not accounts_file.exists():
                await message.answer("❌ Please upload accounts.json file first using /uploadkeys")
                return

            # Load files
            async with aiofiles.open(tweets_file, "r", encoding="utf-8") as f:
                content = await f.read()
                tweets = [t.strip() for t in content.split("\n\n") if t.strip()]

            async with aiofiles.open(accounts_file, "r", encoding="utf-8") as f:
                accounts = json.loads(await f.read())

            if not tweets:
                await message.answer("❌ No tweets found in uploaded file.")
                return

            if not accounts:
                await message.answer("❌ No accounts found in uploaded file.")
                return

            # Validate tweet/account ratio
            if len(tweets) > len(accounts):
                await message.answer(
                    f"⚠️ <b>Warning:</b> {len(tweets)} tweets but only {len(accounts)} accounts.\n"
                    f"Only first {len(accounts)} tweets will be posted."
                )

            # Cancel existing task
            user_id = message.from_user.id
            if user_id in self.active_tasks:
                self.active_tasks[user_id].cancel()
                await message.answer("🔄 Cancelled previous task.")

            # Create scheduling task
            task = asyncio.create_task(
                self.run_scheduler(dt, tweets, accounts, message)
            )
            self.active_tasks[user_id] = task

            time_str = dt.strftime('%d %B %Y at %I:%M %p IST')
            delay = (dt - now).total_seconds()
            
            await message.answer(
                f"⏰ <b>Scheduling Confirmed!</b>\n\n"
                f"📅 <b>IST Time:</b> {time_str}\n"
                f"📊 <b>Tweets:</b> {len(tweets)}\n"
                f"👥 <b>Accounts:</b> {len(accounts)}\n"
                f"⏱️ <b>Starts in:</b> {int(delay//60)} minutes\n"
                f"🆔 <b>Task ID:</b> {id(task)}\n"
                f"🇮🇳 <b>Timezone:</b> Indian Standard Time\n"
                f"🔗 <b>Feature:</b> Tweet links will be sent automatically"
            )
            await state.clear()
            logger.info(f"Scheduling created by user {user_id} for {dt} IST")

        except Exception as e:
            logger.error(f"Error in handle_schedule: {e}")
            await message.answer("❌ Error creating schedule. Please try again.")

    async def status_command(self, message: Message):
        """Check status of active tasks"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        user_id = message.from_user.id
        current_time = ist_now().strftime('%d %B %Y, %I:%M %p IST')
        
        if user_id in self.active_tasks and not self.active_tasks[user_id].done():
            task = self.active_tasks[user_id]
            await message.answer(
                f"📊 <b>Task Status: ACTIVE</b>\n"
                f"🆔 Task ID: {id(task)}\n"
                f"⏳ Status: Running\n"
                f"🇮🇳 Current IST: {current_time}\n"
                f"🔗 Tweet links: Auto-extract enabled\n"
                f"📱 Use /cancel to stop"
            )
        else:
            await message.answer(
                f"📊 <b>Task Status: IDLE</b>\n"
                f"🇮🇳 Current IST: {current_time}\n"
                f"No active tasks running."
            )

    async def cancel_command(self, message: Message):
        """Cancel active scheduling task"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        user_id = message.from_user.id
        if user_id in self.active_tasks and not self.active_tasks[user_id].done():
            self.active_tasks[user_id].cancel()
            del self.active_tasks[user_id]
            current_time = ist_now().strftime('%d %B %Y, %I:%M %p IST')
            await message.answer(
                f"❌ <b>Task Cancelled</b>\n"
                f"Active scheduling task stopped.\n"
                f"🇮🇳 Cancelled at: {current_time}"
            )
            logger.info(f"Task cancelled by user {user_id}")
        else:
            await message.answer("📊 No active tasks to cancel.")

    @asynccontextmanager
    async def get_browser(self):
        """Context manager for browser lifecycle with stealth features"""
        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                headless=BROWSER_HEADLESS,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-web-security',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding'
                ]
            )
            yield browser
        except Exception as e:
            logger.error(f"Browser error: {e}")
            raise
        finally:
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()

    async def post_tweet(self, tweet: str, account: Dict, retry_count: int = 3) -> Tuple[str, Optional[str]]:
        """Enhanced tweet posting with retry, stealth features, and URL extraction"""
        for attempt in range(retry_count):
            try:
                async with self.get_browser() as browser:
                    # Create context with storage state
                    context = await browser.new_context(
                        storage_state=account,
                        viewport={'width': 1280, 'height': 720},
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    )
                    
                    page = await context.new_page()
                    
                    # Stealth mode
                    await page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined,
                        });
                        Object.defineProperty(navigator, 'plugins', {
                            get: () => [1, 2, 3, 4, 5],
                        });
                        Object.defineProperty(navigator, 'languages', {
                            get: () => ['en-US', 'en'],
                        });
                        window.chrome = {
                            runtime: {},
                        };
                    """)
                    
                    # Navigate to Twitter compose
                    await page.goto("https://x.com/compose/tweet", wait_until="networkidle", timeout=30000)
                    
                    # Random delay to seem human
                    await asyncio.sleep(random.uniform(2, 4))
                    
                    # Find and fill tweet textarea
                    selectors = [
                        "[aria-label='Post text']",
                        "[data-testid='tweetTextarea_0']",
                        "[contenteditable='true'][aria-label='Post text']"
                    ]
                    
                    textarea_found = False
                    for selector in selectors:
                        try:
                            await page.wait_for_selector(selector, timeout=10000)
                            await page.fill(selector, tweet)
                            textarea_found = True
                            break
                        except:
                            continue
                    
                    if not textarea_found:
                        raise Exception("Could not find tweet textarea")
                    
                    # Human-like typing delay
                    await asyncio.sleep(random.uniform(1, 3))
                    
                    # Find and click post button
                    post_selectors = [
                        "[data-testid='tweetButtonInline']",
                        "[data-testid='tweetButton']",
                        "[role='button'][aria-label*='Post']"
                    ]
                    
                    button_found = False
                    for selector in post_selectors:
                        try:
                            await page.click(selector, timeout=5000)
                            button_found = True
                            break
                        except:
                            continue
                    
                    if not button_found:
                        raise Exception("Could not find post button")
                    
                    # Wait for post to complete and try to get the URL
                    await asyncio.sleep(random.uniform(3, 5))
                    
                    # Try to extract tweet URL
                    tweet_url = None
                    try:
                        # Wait for URL change or specific elements that indicate successful posting
                        await page.wait_for_function(
                            "window.location.href.includes('/status/') || document.querySelector('[data-testid=\"toast\"]')",
                            timeout=TWEET_LINK_WAIT_TIME * 1000
                        )
                        
                        current_url = page.url
                        tweet_url = extract_tweet_url(current_url)
                        
                        # If URL extraction from current page fails, try alternative methods
                        if not tweet_url:
                            # Look for any links containing status
                            try:
                                status_links = await page.query_selector_all('a[href*="/status/"]')
                                if status_links:
                                    href = await status_links[0].get_attribute('href')
                                    if href:
                                        tweet_url = f"https://x.com{href}" if not href.startswith('http') else href
                            except:
                                pass
                        
                        # Final attempt: check if we're on a status page
                        if not tweet_url and '/status/' in current_url:
                            tweet_url = current_url
                            
                    except Exception as url_error:
                        logger.warning(f"Could not extract tweet URL: {url_error}")
                        tweet_url = None
                    
                    await context.close()
                    return ("✅ Posted successfully", tweet_url)
                    
            except Exception as e:
                logger.error(f"Tweet posting attempt {attempt + 1} failed: {e}")
                if attempt == retry_count - 1:
                    return (f"❌ Failed after {retry_count} attempts: {str(e)[:100]}", None)
                await asyncio.sleep(random.uniform(5, 10))
        
        return ("❌ All attempts failed", None)

    async def run_scheduler(self, dt: datetime, tweets: List[str], accounts: List[Dict], message: Message):
        """Enhanced scheduler with IST timezone, progress tracking, and tweet link extraction"""
        user_id = message.from_user.id
        try:
            # Calculate delay
            now = ist_now()
            delay = (dt - now).total_seconds()
            
            if delay > 0:
                await message.answer(f"⏳ Waiting {int(delay//60)} minutes until scheduled time (IST)...")
                await asyncio.sleep(delay)
            
            start_time = ist_now().strftime('%I:%M %p IST')
            await message.answer(f"🚀 <b>Starting automated posting...</b>\n🇮🇳 Started at: {start_time}\n🔗 Tweet links will be sent after each post")
            
            results = []
            tweet_links = []
            total_tweets = min(len(tweets), len(accounts))
            
            for i, (tweet, account) in enumerate(zip(tweets, accounts), 1):
                try:
                    # Add random delay between posts
                    if i > 1:
                        delay = random.uniform(POST_DELAY_MIN, POST_DELAY_MAX)
                        await asyncio.sleep(delay)
                    
                    # Post tweet and get URL
                    result, tweet_url = await self.post_tweet(tweet[:MAX_TWEET_LENGTH], account)
                    results.append(f"Tweet {i}: {result}")
                    
                    # Send individual tweet result with link
                    if tweet_url:
                        tweet_links.append(f"Tweet {i}: {tweet_url}")
                        await message.answer(
                            f"✅ <b>Tweet {i} Posted!</b>\n"
                            f"🔗 Link: {tweet_url}\n"
                            f"📝 Text: {tweet[:50]}{'...' if len(tweet) > 50 else ''}"
                        )
                    else:
                        await message.answer(
                            f"✅ <b>Tweet {i} Posted!</b>\n"
                            f"⚠️ Could not extract tweet link\n"
                            f"📝 Text: {tweet[:50]}{'...' if len(tweet) > 50 else ''}"
                        )
                    
                    # Progress updates
                    if i % 3 == 0 or i == total_tweets:
                        progress = int((i / total_tweets) * 100)
                        current_time = ist_now().strftime('%I:%M %p IST')
                        await message.answer(f"📊 Progress: {i}/{total_tweets} ({progress}%)\n🇮🇳 Time: {current_time}")
                        
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error posting tweet {i}: {e}")
                    results.append(f"Tweet {i}: ❌ Error - {str(e)[:50]}")
                    await message.answer(f"❌ <b>Tweet {i} Failed!</b>\nError: {str(e)[:100]}")

            # Send final summary
            success_count = sum(1 for r in results if "✅" in r)
            failure_count = len(results) - success_count
            end_time = ist_now().strftime('%I:%M %p IST')
            
            summary = (f"📤 <b>Posting Complete!</b>\n"
                      f"✅ Success: {success_count}\n"
                      f"❌ Failed: {failure_count}\n"
                      f"🔗 Links extracted: {len(tweet_links)}\n"
                      f"🇮🇳 Completed at: {end_time}\n\n")
            
            # Send summary of all tweet links
            if tweet_links:
                links_text = "\n".join(tweet_links)
                await message.answer(
                    f"{summary}🔗 <b>All Tweet Links:</b>\n{links_text}"
                )
            else:
                await message.answer(summary + "⚠️ No tweet links could be extracted.")
                
            logger.info(f"Scheduling completed for user {user_id}: {success_count}/{len(results)} successful, {len(tweet_links)} links extracted")
            
        except asyncio.CancelledError:
            cancel_time = ist_now().strftime('%I:%M %p IST')
            await message.answer(f"❌ <b>Task Cancelled</b>\n🇮🇳 Cancelled at: {cancel_time}")
            logger.info(f"Scheduling cancelled for user {user_id}")
        except Exception as e:
            logger.error(f"Error in run_scheduler: {e}")
            error_time = ist_now().strftime('%I:%M %p IST')
            await message.answer(f"❌ <b>Scheduling Error:</b>\n{str(e)[:200]}\n🇮🇳 Error at: {error_time}")
        finally:
            # Clean up task reference
            if user_id in self.active_tasks:
                del self.active_tasks[user_id]

    async def start_bot(self):
        """Start the bot with proper error handling"""
        try:
            logger.info("Starting Twitter/X Automation Bot with IST timezone and tweet link extraction...")
            logger.info(f"Data directory: {DATA_DIR.absolute()}")
            logger.info(f"Browser headless mode: {BROWSER_HEADLESS}")
            logger.info(f"Tweet link wait time: {TWEET_LINK_WAIT_TIME} seconds")
            logger.info(f"Current IST time: {ist_now().strftime('%d %B %Y, %I:%M %p IST')}")
            
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Bot error: {e}")
            raise
        finally:
            await self.bot.session.close()

def main():
    """Main function with error handling"""
    try:
        # Validate configuration
        if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            print("❌ Error: Please set your BOT_TOKEN in the configuration section!")
            print("Edit main.py and replace 'YOUR_BOT_TOKEN_HERE' with your actual bot token")
            print("Get your token from @BotFather on Telegram")
            return
        
        print(f"🇮🇳 Starting bot with IST timezone...")
        print(f"🔗 Tweet link extraction: ENABLED")
        print(f"🕐 Current IST time: {ist_now().strftime('%d %B %Y, %I:%M %p IST')}")
        
        # Create and start bot
        bot = TwitterAutomationBot()
        asyncio.run(bot.start_bot())
        
    except KeyboardInterrupt:
        print(f"\n🛑 Bot stopped by user at {ist_now().strftime('%I:%M %p IST')}")
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"❌ Fatal error: {e}")

if __name__ == "__main__":
    main()
