# === TELEGRAM USERNAME CLAIMER BOT ===
Developed by @dreadGPT

--- SETUP ---
1. Install dependencies:
   pip install -r requirements.txt

2. Set your bot token:
   Linux/Mac : export BOT_TOKEN=your_token_here
   Windows   : set BOT_TOKEN=your_token_here
   Or replace "YOUR_BOT_TOKEN_HERE" directly in bot.py line 47

3. Run:
   python bot.py

--- HOW TO GET SESSION STRING ---
Use this snippet once to generate session strings for accounts:

   from telethon.sync import TelegramClient
   from telethon.sessions import StringSession

   api_id = int(input("API ID: "))
   api_hash = input("API Hash: ")

   with TelegramClient(StringSession(), api_id, api_hash) as client:
       print("Session string:", client.session.save())

--- COMMANDS ---
/start          - Main menu
/monitor        - Live status of claimer
/stop           - Stop the claimer
/clearhistory   - Wipe claim history and attempt counts

--- ACCOUNT FORMAT ---
API_ID|API_HASH|SESSION_STRING
Example: 12345678|abc123def456|1BVtsOHABu...

--- PROXY FORMAT ---
TYPE|HOST|PORT|USERNAME|PASSWORD
Example: socks5|127.0.0.1|1080||
Example with auth: socks5|proxy.example.com|1080|user|pass

--- NOTES ---
- Minimum delay is 10s (below that risks flood bans)
- Recommended delay: 60-120s
- Claimer checks each username every cycle
- When a username is claimed, it is removed from the pending list
- Bot logs are saved to claimer.log