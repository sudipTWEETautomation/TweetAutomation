#!/usr/bin/env python3
"""
Enhanced Telegram Bot for Twitter/X Automation
Fixed version with IST timezone, tweet link extraction, and individual account management
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
    """Enhanced Twitter automation bot with individual account management"""
    
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
        logger.info("Bot initialized successfully with individual account management")
    
    def _setup_handlers(self):
        """Setup all message handlers"""
        self.router.message(CommandStart())(self.cmd_start)
        self.router.message(AuthState.waiting_for_code)(self.auth_code_check)
        
        # Original commands
        self.router.message(Command("uploadkeys"))(self.upload_keys)
        self.router.message(Command("uploadtweets"))(self.upload_tweets)
        self.router.message(Command("schedule"))(self.schedule_prompt)
        self.router.message(Command("status"))(self.status_command)
        self.router.message(Command("cancel"))(self.cancel_command)
        self.router.message(Command("help"))(self.help_command)
        self.router.message(Command("time"))(self.time_command)
        
        # New account management commands
        self.router.message(Command("addaccount"))(self.add_single_account)
        self.router.message(Command("listaccounts"))(self.list_accounts)
        
        # States
        self.router.message(ScheduleState.waiting_for_time)(self.handle_schedule)
    
    async def cmd_start(self, message: Message, state: FSMContext):
        """Start command handler"""
        try:
            current_time = ist_now().strftime('%d %B %Y, %I:%M %p IST')
            await message.answer(
                "🔐 <b>Twitter/X Automation Bot</b>\n\n"
                f"🇮🇳 Current IST Time: {current_time}\n\n"
                "✨ <b>New Features:</b>\n"
                "• Individual account management (/addaccount)\n"
                "• Account status monitoring (/listaccounts)\n"
                "• Tweet link extraction after posting\n\n"
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
                    "📋 <b>Available commands:</b>\n\n"
                    "🆕 <b>Account Management:</b>\n"
                    "📁 /addaccount - Add single account (one by one)\n"
                    "📋 /listaccounts - Show all accounts with status\n\n"
                    "📋 <b>Main Commands:</b>\n"
                    "📁 /uploadkeys - Upload accounts.json (traditional bulk)\n"
                    "📝 /uploadtweets - Upload tweets.txt file\n"
                    "⏰ /schedule - Schedule posting time (IST)\n"
                    "🕐 /time - Show current IST time\n"
                    "📊 /status - Check active tasks\n"
                    "❌ /cancel - Cancel operations\n"
                    "❓ /help - Show detailed help\n\n"
                    "🔗 <b>Auto Features:</b> Tweet links sent after each post!"
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

    async def add_single_account(self, message: Message):
        """Add single account - NEW FEATURE"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        if not message.document:
            await message.answer(
                "📎 <b>Upload Single Account JSON File</b>\n\n"
                "💡 <b>Format:</b> Single account object\n"
                "<code>{\n"
                '  "cookies": [\n'
                '    {"name": "auth_token", "value": "your_token", "domain": ".x.com"},\n'
                '    {"name": "ct0", "value": "your_ct0", "domain": ".x.com"},\n'
                '    {"name": "twid", "value": "u%3Dyour_userid", "domain": ".x.com"}\n'
                '  ],\n'
                '  "origins": [{"origin": "https://x.com", "localStorage": []}]\n'
                "}</code>\n\n"
                "📏 <b>Max size:</b> 1MB per account"
            )
            return

        try:
            if message.document.file_size > 1024 * 1024:  # 1MB limit per account
                await message.answer("❌ File too large. Maximum size is 1MB per account.")
                return

            user_dir = DATA_DIR / str(message.from_user.id)
            user_dir.mkdir(parents=True, exist_ok=True)
            
            # Load existing accounts
            accounts_file = user_dir / "accounts.json"
            if accounts_file.exists():
                async with aiofiles.open(accounts_file, 'r') as f:
                    accounts = json.loads(await f.read())
            else:
                accounts = []

            # Download new account file
            temp_file = user_dir / "temp_account.json"
            await self.bot.download(message.document, destination=temp_file)
            
            # Load and validate new account
            async with aiofiles.open(temp_file, 'r') as f:
                new_account = json.loads(await f.read())
            
            # Validate single account structure
            if not isinstance(new_account, dict) or 'cookies' not in new_account:
                await message.answer("❌ Invalid account format. Must contain 'cookies' field.")
                temp_file.unlink()
                return

            # Check for required cookies
            cookie_names = [cookie.get('name') for cookie in new_account.get('cookies', [])]
            required_cookies = ['auth_token', 'ct0', 'twid']
            missing_cookies = [cookie for cookie in required_cookies if cookie not in cookie_names]
            
            if missing_cookies:
                await message.answer(f"⚠️ Missing cookies: {', '.join(missing_cookies)}\nAccount added but may not work properly.")

            # Check if account already exists (by twid or auth_token)
            existing_ids = []
            for account in accounts:
                for cookie in account.get('cookies', []):
                    if cookie.get('name') in ['twid', 'auth_token']:
                        existing_ids.append(cookie.get('value', ''))

            # Check new account for duplicates
            new_account_id = None
            for cookie in new_account.get('cookies', []):
                if cookie.get('name') in ['twid', 'auth_token']:
                    if cookie.get('value') in existing_ids:
                        await message.answer("⚠️ This account already exists!")
                        temp_file.unlink()
                        return
                    if cookie.get('name') == 'twid':
                        new_account_id = cookie.get('value', '')

            # Add to accounts list
            accounts.append(new_account)
            
            # Save updated accounts
            async with aiofiles.open(accounts_file, 'w') as f:
                await f.write(json.dumps(accounts, indent=2))
            
            # Clean up temp file
            temp_file.unlink()
            
            # Extract user info for display
            display_info = "New Account"
            if new_account_id and 'u%3D' in new_account_id:
                userid = new_account_id.split('u%3D')[1][:10]
                display_info = f"ID: {userid}..."
            
            await message.answer(
                f"✅ <b>Account Added Successfully!</b>\n\n"
                f"🆔 <b>Account:</b> {display_info}\n"
                f"📊 <b>Total Accounts:</b> {len(accounts)}\n"
                f"📍 <b>Position:</b> Account #{len(accounts)}\n"
                f"💾 <b>Status:</b> Saved to accounts.json\n\n"
                f"💡 Use /listaccounts to see all accounts\n"
                f"🚀 Use /addaccount to add more accounts"
            )
            
            logger.info(f"Single account added by user {message.from_user.id}. Total: {len(accounts)}")

        except json.JSONDecodeError:
            await message.answer("❌ Invalid JSON format. Please check your file.")
            if 'temp_file' in locals() and temp_file.exists():
                temp_file.unlink()
        except Exception as e:
            logger.error(f"Error adding single account: {e}")
            await message.answer("❌ Error adding account. Please try again.")
            if 'temp_file' in locals() and temp_file.exists():
                temp_file.unlink()

    async def list_accounts(self, message: Message):
        """List all accounts with details - NEW FEATURE"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        try:
            user_dir = DATA_DIR / str(message.from_user.id)
            accounts_file = user_dir / "accounts.json"
            
            if not accounts_file.exists():
                await message.answer(
                    "📋 <b>No Accounts Found</b>\n\n"
                    "💡 Use /addaccount to add your first account\n"
                    "📁 Or use /uploadkeys for bulk upload"
                )
                return

            async with aiofiles.open(accounts_file, 'r') as f:
                accounts = json.loads(await f.read())

            if not accounts:
                await message.answer(
                    "📋 <b>No Accounts Found</b>\n\n"
                    "💡 Use /addaccount to add your first account\n"
                    "📁 Or use /uploadkeys for bulk upload"
                )
                return

            # Build detailed account list
            account_list = []
            for i, account in enumerate(accounts, 1):
                # Extract account info
                username = "Unknown"
                auth_status = "❓"
                cookie_count = len(account.get('cookies', []))
                
                cookies = account.get('cookies', [])
                has_auth = False
                has_ct0 = False
                has_twid = False
                
                for cookie in cookies:
                    if cookie.get('name') == 'twid':
                        has_twid = True
                        twid_value = cookie.get('value', '')
                        if 'u%3D' in twid_value:
                            userid = twid_value.split('u%3D')[1]
                            username = f"ID: {userid[:12]}..."
                    elif cookie.get('name') == 'auth_token':
                        has_auth = True
                        if cookie.get('value') and len(cookie.get('value', '')) > 10:
                            pass
                    elif cookie.get('name') == 'ct0':
                        has_ct0 = True

                # Determine status
                if has_auth and has_ct0 and has_twid:
                    auth_status = "✅"
                elif has_auth and has_ct0:
                    auth_status = "⚠️"
                else:
                    auth_status = "❌"

                account_list.append(f"{i}. {username} {auth_status} ({cookie_count} cookies)")

            # Create response (split if too long)
            accounts_text = "\n".join(account_list)
            current_time = ist_now().strftime('%d %B %Y, %I:%M %p IST')
            
            # Create summary
            ready_count = sum(1 for line in account_list if "✅" in line)
            partial_count = sum(1 for line in account_list if "⚠️" in line)
            
            response = (
                f"📋 <b>Your Twitter Accounts:</b>\n\n"
                f"{accounts_text}\n\n"
                f"📊 <b>Summary:</b>\n"
                f"• Total Accounts: {len(accounts)}\n"
                f"• Ready to Use: {ready_count}\n"
                f"• Partial Setup: {partial_count}\n"
                f"• Need Attention: {len(accounts) - ready_count - partial_count}\n\n"
                f"🕐 <b>Listed at:</b> {current_time}\n\n"
                f"💡 <b>Legend:</b>\n"
                f"✅ = Ready (auth_token + ct0 + twid)\n"
                f"⚠️ = Partial (missing twid)\n"
                f"❌ = Incomplete (missing required cookies)\n\n"
                f"🚀 Use /addaccount to add more accounts"
            )

            # Split long messages
            if len(response) > 4000:
                # Send in parts
                parts = [
                    f"📋 <b>Your Twitter Accounts:</b>\n\n{accounts_text}",
                    f"📊 <b>Summary:</b>\n• Total: {len(accounts)}\n• Ready: {ready_count}\n• Partial: {partial_count}\n\n💡 Legend: ✅=Ready ⚠️=Partial ❌=Incomplete"
                ]
                for part in parts:
                    await message.answer(part)
            else:
                await message.answer(response)

        except json.JSONDecodeError:
            await message.answer("❌ Corrupted accounts file. Please re-add accounts using /addaccount.")
        except Exception as e:
            logger.error(f"Error listing accounts: {e}")
            await message.answer("❌ Error retrieving accounts list.")

    async def upload_keys(self, message: Message):
        """Enhanced accounts file upload with validation"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        if not message.document:
            await message.answer(
                "📎 Please upload your accounts.json file.\n\n"
                "💡 <b>Format:</b> Playwright storage state JSON file\n"
                "📏 <b>Max size:</b> 10MB\n\n"
                "🆕 <b>Alternative:</b> Use /addaccount to add accounts one by one"
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
                f"🔗 Bot will extract tweet links after posting\n\n"
                f"💡 Use /listaccounts to see account details"
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
        """Help command with enhanced account management"""
        if not self._check_auth(message.from_user.id):
            await message.answer("🔒 Unauthorized. Use /start to login.")
            return

        help_text = """
🤖 <b>Twitter/X Automation Bot - Enhanced Help</b>

<b>🆕 Account Management:</b>
• /addaccount - Add single account (one by one)
• /listaccounts - Show all accounts with status

<b>📋 Main Commands:</b>
• /uploadkeys - Upload accounts.json (traditional bulk)
• /uploadtweets - Upload tweets.txt
• /schedule - Schedule posting time (IST)
• /time - Show current IST time
• /status - Check current tasks
• /cancel - Cancel active operations
• /help - Show this help

<b>🔄 Recommended Workflow:</b>
1. Use /addaccount to add accounts one by one
2. Use /listaccounts to verify all accounts
3. Use /uploadtweets to upload your tweets
4. Use /schedule to start automated posting

<b>📄 Single Account Format:</b>
<code>{
  "cookies": [
    {"name": "auth_token", "value": "your_token", "domain": ".x.com"},
    {"name": "ct0", "value": "your_ct0", "domain": ".x.com"},
    {"name": "twid", "value": "u%3Dyour_userid", "domain": ".x.com"}
  ],
  "origins": [{"origin": "https://x.com", "localStorage": []}]
}</code>

<b>📅 IST Time Formats:</b>
• 3 August 2025 @12:31PM
• 03/08/2025 12:31
• 2025-08-03 12:31
• 3 August 2025 12:31

<b>✨ Enhanced Features:</b>
• Individual account management
• Duplicate account detection
• Account status monitoring
• Automatic tweet link extraction
• Real-time posting progress

<b>🇮🇳 Timezone:</b>
All times are in IST (Indian Standard Time, UTC+5:30)
Current IST time: Use /time command

<b>⚠️ Important:</b>
• Respect Twitter/X terms of service
• Use reasonable posting intervals (5-15 seconds)
• Monitor for rate limits
• Keep credentials secure
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
                await message.answer("❌ Please upload accounts first using /uploadkeys or /addaccount")
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
                    f"⚠️ <b>Notice:</b> {len(tweets)} tweets but only {len(accounts)} accounts.\n"
                    f"Tweets will cycle through accounts. Each account may post multiple tweets."
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
            total_tweets = len(tweets)
            
            for i, tweet in enumerate(tweets, 1):
                try:
                    # Add random delay between posts
                    if i > 1:
                        delay = random.uniform(POST_DELAY_MIN, POST_DELAY_MAX)
                        await asyncio.sleep(delay)
                    
                    # Select account (cycle through accounts)
                    account_index = (i - 1) % len(accounts)
                    account = accounts[account_index]
                    
                    # Post tweet and get URL
                    result, tweet_url = await self.post_tweet(tweet[:MAX_TWEET_LENGTH], account)
                    results.append(f"Tweet {i}: {result}")
                    
                    # Send individual tweet result with link
                    if tweet_url:
                        tweet_links.append(f"Tweet {i}: {tweet_url}")
                        await message.answer(
                            f"✅ <b>Tweet {i} Posted!</b>\n"
                            f"🔗 Link: {tweet_url}\n"
                            f"👤 Account: #{account_index + 1}\n"
                            f"📝 Text: {tweet[:50]}{'...' if len(tweet) > 50 else ''}"
                        )
                    else:
                        await message.answer(
                            f"✅ <b>Tweet {i} Posted!</b>\n"
                            f"⚠️ Could not extract tweet link\n"
                            f"👤 Account: #{account_index + 1}\n"
                            f"📝 Text: {tweet[:50]}{'...' if len(tweet) > 50 else ''}"
                        )
                    
                    # Progress updates
                    if i % 5 == 0 or i == total_tweets:
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
                # Split links if too many
                links_text = "\n".join(tweet_links)
                if len(links_text) > 3000:
                    # Send in batches
                    batch_size = 10
                    for i in range(0, len(tweet_links), batch_size):
                        batch = tweet_links[i:i+batch_size]
                        batch_text = "\n".join(batch)
                        await message.answer(f"🔗 <b>Tweet Links (Batch {i//batch_size + 1}):</b>\n{batch_text}")
                else:
                    await message.answer(f"{summary}🔗 <b>All Tweet Links:</b>\n{links_text}")
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
            logger.info("Starting Twitter/X Automation Bot with enhanced account management...")
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
        print(f"🆕 Individual account management: ENABLED")
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
