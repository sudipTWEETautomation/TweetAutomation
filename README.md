# TweetAutomation Telegram Bot by FLASH BHAI

A Telegram bot for scheduling and posting tweets across multiple Twitter accounts.  
Built with [aiogram](https://docs.aiogram.dev/en/latest/) v3, [tweepy](https://www.tweepy.org/), [python-dateutil](https://dateutil.readthedocs.io/en/stable/), and [APScheduler](https://apscheduler.readthedocs.io/en/latest/).

---

## Features

- **Private Access:** Only admin and users with approved tokens can use the bot.
- **Upload Twitter accounts** (`accounts.json`)
- **Upload tweets** (`tweets.txt`)
- **Schedule tweets for future posting**
- **Automatically post tweets via multiple Twitter accounts**
- **Receive posted tweet links in Telegram**
- **Prevents reposting of tweets**

---

## Getting Started

### 1. Clone the Repo

```bash
git clone https://github.com/sudipTWEETautomation/TweetAutomation.git
cd TweetAutomation
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configuration

Open `main.py` and set your Telegram bot token and admin ID at the top:

```python
API_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
ADMIN_ID = 123456789  # your Telegram user ID
```

### 4. Prepare Twitter Account Credentials

Create a file named `accounts.json` in the `data` folder.  
Format example (list of Twitter account credentials):

```json
[
  {
    "api_key": "API_KEY_1",
    "api_secret": "API_SECRET_1",
    "access_token": "ACCESS_TOKEN_1",
    "access_token_secret": "ACCESS_TOKEN_SECRET_1"
  },
  {
    "api_key": "API_KEY_2",
    "api_secret": "API_SECRET_2",
    "access_token": "ACCESS_TOKEN_2",
    "access_token_secret": "ACCESS_TOKEN_SECRET_2"
  }
]
```

### 5. Prepare Tweets

Create a `tweets.txt` file in the `data` folder.  
Each tweet should be separated by **two line breaks**.

Example:
```
First tweet text.

Second tweet text.

Third tweet text.
```

---

## Usage

1. Start your bot:

   ```bash
   python main.py
   ```

2. In Telegram, open a chat with your bot.

3. Use these commands:

   - `/start` — Show help and commands
   - `/uploadApiKeys` — Upload your `accounts.json`
   - `/uploadTweets` — Upload your `tweets.txt`
   - `/schedule` — Enter a date/time (e.g. `3 August 2025 @12:31AM`)

4. When tweets are posted, you’ll receive the tweet links in your Telegram chat.

---

## File Structure

```
TweetAutomation/
├── data/
│   ├── accounts.json
│   ├── tweets.txt
│   └── used_tweets.json
├── main.py
├── requirements.txt
└── README.md
```

---

## Requirements

See `requirements.txt`.  
Minimum versions:

- aiogram==3.3.0
- tweepy==4.14.0
- python-dateutil==2.9.0
- apscheduler==3.10.4

---

## License

MIT

---

## Disclaimer

- Your Twitter account credentials are sensitive!  
  Do **not** share them or commit them to public repositories.
- This bot posts tweets on your behalf.  
  Use with caution.
