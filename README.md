Telegram Username Claimer Bot

A Telegram bot for monitoring usernames and attempting to claim them when they become available.

Made by @dreadGPT

Setup

1. Install the dependencies

pip install -r requirements.txt

2. Add your bot token

Linux / macOS:

export BOT_TOKEN=your_token_here

Windows:

set BOT_TOKEN=your_token_here

You can also put the token directly in bot.py if you prefer.

3. Start the bot

python bot.py

Getting a Session String

You need a Telegram session string for the accounts you’re using.

Run this once:

from telethon.sync import TelegramClient
from telethon.sessions import StringSession
api_id = int(input("API ID: "))
api_hash = input("API Hash: "))
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("Session string:", client.session.save())

You’ll be asked for your API ID, API hash and Telegram login details. The generated session string can then be added to the account list.

Don’t share your session strings. They can give access to the associated Telegram account.

Commands

/start

Opens the main menu.

/monitor

Shows the current claimer status.

/stop

Stops the claimer.

/clearhistory

Clears the claim history and attempt counters.

Account Format

Accounts should be added in this format:

API_ID|API_HASH|SESSION_STRING

Example:

12345678|abc123def456|1BVtsOHABu...

Proxy Format

Proxies use:

TYPE|HOST|PORT|USERNAME|PASSWORD

For example:

socks5|127.0.0.1|1080||

With authentication:

socks5|proxy.example.com|1080|user|pass

Delay

The default setup uses a minimum delay of 10 seconds.

I recommend keeping the delay reasonably high rather than constantly hitting Telegram. Something around 60–120 seconds is a safer starting point for long-running monitoring.

The claimer checks the usernames once per cycle.

Once a username is successfully claimed, it is removed from the pending list so the bot doesn’t keep checking it.

Logs

Bot activity is saved in:

claimer.log

This can be useful when checking errors, attempts and other activity.

Important

Keep your bot token, API credentials and session strings private.

Telegram can also change its limits or behaviour, so avoid running aggressive request loops.

Use the bot responsibly and make sure your use follows Telegram’s rules.