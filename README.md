# Anon_Messages_Bot

**Anon_Messages_Bot is a Telegram bot that allows users to receive and reply to anonymous messages via a personal opt-in link.
A user explicitly enables anonymous messages and shares their link — the bot does not allow unsolicited or forced messaging.**

🔗 Repository:
https://github.com/NEO-KLIZZERX/Anon_Messages_Bot.git

## Features
### User Features

- Personal anonymous inbox link

- Receive anonymous messages (opt-in only)

- Anonymous replies to senders

- Block specific senders

- Report abusive messages

- Enable / disable anonymous messages

- Allow or block links in messages

- Inbox with recent conversations

## Supported Message Types

- Text

- Photos (with caption)

- Videos (with caption)

- Voice messages

- Video notes

- Documents

## Anti-Abuse Protection

- Cooldown between messages (default: 15 seconds)

- Daily message limit per sender → recipient pair

- Per-user blocking

- Global bans (admin)

## Admin Features

- Get your Telegram user ID

- Global ban / unban users

- View basic statistics

- Receive user reports directly in Telegram

## Architecture

- Python 3.10+

- aiogram 3

- SQLite (local database)

- Polling mode (no webhook, no open ports required)

The database file `anon.db` is created automatically and stored next to the bot script.

## Installation
- Clone the repository
```
git clone https://github.com/NEO-KLIZZERX/Anon_Messages_Bot.git
cd Anon_Messages_Bot
```

- Install dependencies
```
pip install aiogram
```
- Create a Telegram bot
```
Open @BotFather

Create a new bot

Copy the bot token
```
## Configuration

Open the main bot file and set the configuration:
```
cfg = Config(
    token="YOUR_BOT_TOKEN",
    admin_id=YOUR_TELEGRAM_ID,
    db_path="anon.db",
)
```
## How to get your admin_id

After starting the bot, send:
```
/id
```

**The bot will reply with your Telegram user ID.**

## Running the Bot
```
python main.py
```

**If everything is correct, you should see:**
```
INFO:aiogram.dispatcher:Start polling
```

## Commands
### User Commands

- /start — start the bot and get your personal link

- /my — show your personal anonymous link

- /settings — open settings

- /id — show your Telegram user ID

### Admin Commands

- /ban <user_id> — globally ban a user

- /unban <user_id> — remove a global ban

- /stats — show bot statistics

## Anonymous Messaging Logic

- The sender’s identity is never shown to the recipient

- Telegram user_id is stored only internally for:

- spam prevention

- blocking

- abuse reports

- Users **cannot** send anonymous messages without an explicit personal link

## Data Storage

The SQLite database stores:

- users

- conversation threads

- blocks

- rate limits

- global bans

**Database schema updates are handled automatically.**

## Deployment

The bot can be run:

- locally

- on any VPS

- on any Linux server

**Polling mode requires no open ports.**

## Limitations

- Not designed for mass broadcasting

- Polling only (no webhook by default)

- SQLite is not intended for very high traffic (tens of thousands of active users)

## License

```
MIT License
You are free to use, modify, and distribute this project.
```

# Disclaimer!!!
```
The developer is not responsible for user-generated content!
Use this bot in compliance with local laws and Telegram’s Terms of Service!
```
