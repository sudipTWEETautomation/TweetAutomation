import logging
import os
import json
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from datetime import datetime
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from dateutil import parser as date_parser
import tweepy

# =================== CONFIG ===================

API_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
ADMIN_ID = 123456789  # change this to your Telegram ID

DATA_DIR = "data"
TWEETS_FILE = os.path.join(DATA_DIR, "tweets.txt")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
USED_TWEETS_FILE = os.path.join(DATA_DIR, "used_tweets.json")

os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(USED_TWEETS_FILE):
    with open(USED_TWEETS_FILE, "w") as f:
        json.dump([], f)

bot = Bot(token=API_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# =================== HELPERS ===================

async def save_uploaded_file(file: types.File, destination: str):
    file_path = await bot.download_file(file.file_path)
    with open(destination, "wb") as f:
        f.write(file_path.read())

def load_tweets():
    with open(TWEETS_FILE, encoding="utf-8") as f:
        raw = f.read()
        all_tweets = [x.strip() for x in raw.strip().split("\n\n") if x.strip()]
    with open(USED_TWEETS_FILE, encoding="utf-8") as f:
        used_tweets = json.load(f)
    return [tweet for tweet in all_tweets if tweet not in used_tweets]

def save_used_tweets(tweets):
    with open(USED_TWEETS_FILE, "r", encoding="utf-8") as f:
        used = json.load(f)
    used.extend(tweets)
    with open(USED_TWEETS_FILE, "w", encoding="utf-8") as f:
        json.dump(used, f, indent=2)

def load_accounts():
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

# =================== HANDLERS ===================

@dp.message(Command("start"))
async def start_handler(msg: Message):
    await msg.answer(
        "👋 Welcome to the Twitter Scheduler Bot!\n\n"
        "Commands:\n"
        "/uploadApiKeys – Upload Twitter accounts JSON\n"
        "/uploadTweets – Upload tweets.txt file\n"
        "/schedule – Set tweet schedule time (e.g. 3 August 2025 @12:31AM)"
    )

@dp.message(Command("uploadApiKeys"))
async def upload_keys_handler(msg: Message):
    await msg.answer("📥 Please send the <b>accounts.json</b> file")

@dp.message(Command("uploadTweets"))
async def upload_tweets_handler(msg: Message):
    await msg.answer("📥 Please send the <b>tweets.txt</b> file (each tweet separated by 2 line breaks)")

@dp.message(F.document)
async def handle_files(msg: Message):
    doc = msg.document
    if doc.file_name == "accounts.json":
        await save_uploaded_file(await bot.get_file(doc.file_id), ACCOUNTS_FILE)
        await msg.answer("✅ Accounts saved successfully!")
    elif doc.file_name == "tweets.txt":
        await save_uploaded_file(await bot.get_file(doc.file_id), TWEETS_FILE)
        await msg.answer("✅ Tweets saved successfully!")
    else:
        await msg.answer("❌ Unsupported file. Please send accounts.json or tweets.txt")

@dp.message(Command("schedule"))
async def schedule_handler(msg: Message):
    await msg.answer("🕰 Enter Tweet post time with date\n\nExample: <code>3 August 2025 @12:31AM</code>")
    dp.message.register(set_schedule_time, F.text)

async def set_schedule_time(msg: Message):
    try:
        user_time = date_parser.parse(msg.text, fuzzy=True)
        await msg.answer(f"✅ Tweets scheduled for: <b>{user_time}</b>")
        scheduler.add_job(post_all_tweets, DateTrigger(run_date=user_time), args=[msg.chat.id])
    except Exception as e:
        await msg.answer("❌ Invalid time format. Try again.")

async def post_all_tweets(chat_id):
    tweets = load_tweets()
    accounts = load_accounts()

    if len(tweets) > len(accounts):
        await bot.send_message(chat_id, "⚠ Not enough Twitter accounts for all tweets!")
        return

    links = []
    for i in range(len(tweets)):
        try:
            url = post_tweet(accounts[i], tweets[i])
            links.append(url)
        except Exception as e:
            links.append(f"❌ Failed: {e}")

    save_used_tweets(tweets[:len(links)])

    for link in links:
        await bot.send_message(chat_id, link)

# =================== MAIN ===================

async def main():
    scheduler.start()
    await dp.start_polling(bot)

if _name_ == "_main_":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
