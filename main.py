import logging
import os
import json
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from dateutil import parser as date_parser
import tweepy

# =================== CONFIG ===================

API_TOKEN = "8428126884:AAFeYk650yE4oUXNIDSi_Mjv9Rl9WIPZ8SQ"  # <-- Place your bot token here
ADMIN_ID = 6535216093  # <-- Place your Telegram user ID here

DATA_DIR = "data"
TWEETS_FILE = os.path.join(DATA_DIR, "tweets.txt")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
USED_TWEETS_FILE = os.path.join(DATA_DIR, "used_tweets.json")

os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(USED_TWEETS_FILE):
    with open(USED_TWEETS_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)

# ========== ACCESS CONTROL ==========
APPROVED_TOKENS = {"STA44215"}  # add more tokens if needed
approved_users = set([ADMIN_ID])  # admin always approved

def is_authorized(user_id):
    return user_id == ADMIN_ID or user_id in approved_users

# =================== BOT, DISPATCHER, SCHEDULER ===================
bot = Bot(token=API_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# =================== HELPERS ===================

async def save_uploaded_file(file_id: str, destination: str):
    await bot.download(file_id, destination)

def load_tweets():
    if not os.path.exists(TWEETS_FILE):
        return []
    with open(TWEETS_FILE, encoding="utf-8") as f:
        raw = f.read()
        all_tweets = [x.strip() for x in raw.strip().split("\n\n") if x.strip()]
    if not os.path.exists(USED_TWEETS_FILE):
        used_tweets = []
    else:
        with open(USED_TWEETS_FILE, encoding="utf-8") as f:
            used_tweets = json.load(f)
    return [tweet for tweet in all_tweets if tweet not in used_tweets]

def save_used_tweets(tweets):
    if not os.path.exists(USED_TWEETS_FILE):
        used = []
    else:
        with open(USED_TWEETS_FILE, "r", encoding="utf-8") as f:
            used = json.load(f)
    used.extend(tweets)
    with open(USED_TWEETS_FILE, "w", encoding="utf-8") as f:
        json.dump(used, f, indent=2)

def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        return json.load(f)

def post_tweet(api_keys, content):
    auth = tweepy.OAuth1UserHandler(
        api_keys["api_key"],
        api_keys["api_secret"],
        api_keys["access_token"],
        api_keys["access_token_secret"]
    )
    api = tweepy.API(auth)
    tweet = api.update_status(content)
    username = api.verify_credentials().screen_name
    return f"https://x.com/{username}/status/{tweet.id}"

async def require_auth(msg):
    if not is_authorized(msg.from_user.id):
        await msg.answer("🔒 You are not authorized to use this bot.")
        return False
    return True

# =================== HANDLERS ===================

@dp.message(Command("start"))
async def start_handler(msg: Message):
    if not is_authorized(msg.from_user.id):
        await msg.answer("🔒 This bot is private. Please send your access token to use it.")
    else:
        await msg.answer(
            "👋 Welcome to the Twitter Scheduler Bot!\n\n"
            "Commands:\n"
            "/uploadApiKeys – Upload Twitter accounts JSON\n"
            "/uploadTweets – Upload tweets.txt file\n"
            "/schedule – Set tweet schedule time (e.g. 3 August 2025 @12:31AM)"
        )

@dp.message(Command("uploadApiKeys"))
async def upload_keys_handler(msg: Message):
    if not await require_auth(msg): return
    await msg.answer("📥 Please send the <b>accounts.json</b> file")

@dp.message(Command("uploadTweets"))
async def upload_tweets_handler(msg: Message):
    if not await require_auth(msg): return
    await msg.answer("📥 Please send the <b>tweets.txt</b> file (each tweet separated by 2 line breaks)")

@dp.message(F.document)
async def handle_files(msg: Message):
    if not await require_auth(msg): return
    doc = msg.document
    if doc.file_name == "accounts.json":
        await save_uploaded_file(doc.file_id, ACCOUNTS_FILE)
        await msg.answer("✅ Accounts saved successfully!")
    elif doc.file_name == "tweets.txt":
        await save_uploaded_file(doc.file_id, TWEETS_FILE)
        await msg.answer("✅ Tweets saved successfully!")
    else:
        await msg.answer("❌ Unsupported file. Please send accounts.json or tweets.txt")

@dp.message(Command("schedule"))
async def schedule_handler(msg: Message):
    if not await require_auth(msg): return
    await msg.answer("🕰 Enter Tweet post time with date\n\nExample: <code>3 August 2025 @12:31AM</code>")

@dp.message(F.text)
async def token_or_schedule_handler(msg: Message):
    user_id = msg.from_user.id
    text = msg.text.strip()
    # Token check
    if not is_authorized(user_id):
        if text in APPROVED_TOKENS:
            approved_users.add(user_id)
            await msg.answer("✅ Access granted! You can now use the bot commands.")
        else:
            await msg.answer("❌ Invalid token. Please contact admin for access.")
        return
    # If authorized, handle scheduling as before
    if text and ("@" in text or ":" in text):
        try:
            user_time = date_parser.parse(text, fuzzy=True)
            await msg.answer(f"✅ Tweets scheduled for: <b>{user_time}</b>")
            scheduler.add_job(post_all_tweets, DateTrigger(run_date=user_time), args=[msg.chat.id])
        except Exception as e:
            logging.error(f"Schedule parse error: {e}")
            await msg.answer("❌ Invalid time format. Try again.")

async def post_all_tweets(chat_id):
    tweets = load_tweets()
    accounts = load_accounts()

    if not tweets:
        await bot.send_message(chat_id, "⚠ No tweets found! Please upload tweets.txt.")
        return
    if not accounts:
        await bot.send_message(chat_id, "⚠ No Twitter accounts found! Please upload accounts.json.")
        return

    if len(tweets) > len(accounts):
        await bot.send_message(chat_id, "⚠ Not enough Twitter accounts for all tweets!")
        return

    links = []
    for i in range(len(tweets)):
        try:
            url = post_tweet(accounts[i], tweets[i])
            links.append(url)
        except Exception as e:
            logging.error(f"Tweet failed for account {i}: {e}")
            links.append(f"❌ Failed: {e}")

    save_used_tweets(tweets[:len(links)])

    for link in links:
        await bot.send_message(chat_id, link)

# =================== MAIN ===================

async def main():
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
