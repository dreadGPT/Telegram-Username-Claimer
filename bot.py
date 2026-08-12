import asyncio
import json
import logging
import os
import time
import re
from datetime import datetime
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    UsernameOccupiedError,
    UsernameInvalidError,
    UsernameNotModifiedError,
    SessionPasswordNeededError,
    AuthKeyError,
    RPCError,
)
from telethon.tl.functions.account import UpdateUsernameRequest
from telethon.tl.functions.channels import UpdateUsernameRequest as ChannelUpdateUsernameRequest
from telethon.tl.types import Channel, Chat

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("claimer.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("claimer")

# ─── Constants ───────────────────────────────────────────────────────────────

DATA_FILE = "data.json"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8986816534:AAE3MRlO5T1gj0Go_bK01NfGeukwMc_-hJE")

(
    AWAIT_ACCOUNT_INPUT,
    AWAIT_PROXY_INPUT,
    AWAIT_USERNAMES_INPUT,
    AWAIT_DELAY_INPUT,
    AWAIT_CHANNEL_SELECTION,
) = range(5)

# ─── Data Layer ──────────────────────────────────────────────────────────────

DEFAULT_DATA = {
    "accounts": [],
    "proxies": [],
    "usernames": [],
    "channels": {},
    "delay": 90,
}


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA.copy())
        return DEFAULT_DATA.copy()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
        for key, val in DEFAULT_DATA.items():
            if key not in stored:
                stored[key] = val
        return stored
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Data load error: {e}")
        return DEFAULT_DATA.copy()


def save_data(data: dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.error(f"Data save error: {e}")


# ─── Keyboard Builders ───────────────────────────────────────────────────────

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Add Accounts", callback_data="menu_accounts"),
            InlineKeyboardButton("Set Proxy", callback_data="menu_proxy"),
        ],
        [
            InlineKeyboardButton("Set Usernames", callback_data="menu_usernames"),
            InlineKeyboardButton("Start Claimer", callback_data="menu_claimer"),
        ],
    ])


def kb_back(target: str = "main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Back", callback_data=f"back_{target}")]
    ])


def kb_claimer_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Start", callback_data="claimer_start"),
            InlineKeyboardButton("Set Channels", callback_data="claimer_channels"),
            InlineKeyboardButton("Set Delay", callback_data="claimer_delay"),
        ],
        [InlineKeyboardButton("Back", callback_data="back_main")],
    ])


def kb_yes_no(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes", callback_data=yes_cb),
            InlineKeyboardButton("No", callback_data=no_cb),
        ]
    ])


# ─── Telethon Helpers ─────────────────────────────────────────────────────────

def build_proxy(proxies: list) -> Optional[tuple]:
    if not proxies:
        return None
    try:
        import socks
        p = proxies[0]
        ptype = socks.SOCKS5 if p["type"] in ("socks5", "socks4") else socks.HTTP
        return (
            ptype,
            p["host"],
            int(p["port"]),
            True,
            p.get("username") or None,
            p.get("password") or None,
        )
    except ImportError:
        logger.warning("PySocks not installed. Proxy ignored.")
        return None
    except Exception as e:
        logger.error(f"Proxy build error: {e}")
        return None


async def get_telethon_client(acc: dict, proxy=None) -> TelegramClient:
    client = TelegramClient(
        StringSession(acc["session"]),
        int(acc["api_id"]),
        acc["api_hash"],
        proxy=proxy,
        connection_retries=3,
        retry_delay=5,
        auto_reconnect=True,
    )
    return client


async def validate_account(acc: dict, proxy=None) -> tuple[bool, str]:
    client = await get_telethon_client(acc, proxy)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return False, "Not authorized"
        me = await client.get_me()
        label = f"@{me.username}" if me.username else f"{me.first_name} (ID:{me.id})"
        return True, label
    except AuthKeyError:
        return False, "Invalid session string"
    except Exception as e:
        return False, str(e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def fetch_user_channels(acc: dict, proxy=None) -> list:
    client = await get_telethon_client(acc, proxy)
    channels = []
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return []
        dialogs = await client.get_dialogs(limit=500)
        for d in dialogs:
            entity = d.entity
            if isinstance(entity, Channel) and (entity.creator or entity.admin_rights):
                channels.append({
                    "id": entity.id,
                    "access_hash": entity.access_hash,
                    "title": entity.title,
                    "username": entity.username or "",
                })
    except Exception as e:
        logger.error(f"Fetch channels error: {e}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return channels


async def try_claim_username_as_user(client: TelegramClient, username: str) -> bool:
    try:
        await client(UpdateUsernameRequest(username))
        return True
    except UsernameOccupiedError:
        return False
    except UsernameNotModifiedError:
        return True
    except UsernameInvalidError:
        raise
    except FloodWaitError:
        raise
    except RPCError as e:
        logger.warning(f"RPC error claiming @{username} as user: {e}")
        return False


async def try_claim_username_for_channel(client: TelegramClient, channel_id: int, access_hash: int, username: str) -> bool:
    try:
        from telethon.tl.types import InputChannel
        entity = InputChannel(channel_id, access_hash)
        await client(ChannelUpdateUsernameRequest(entity, username))
        return True
    except UsernameOccupiedError:
        return False
    except UsernameNotModifiedError:
        return True
    except UsernameInvalidError:
        raise
    except FloodWaitError:
        raise
    except RPCError as e:
        logger.warning(f"RPC error claiming @{username} for channel {channel_id}: {e}")
        return False


# ─── Claimer Loop ─────────────────────────────────────────────────────────────

async def claimer_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int, target: str):
    logger.info(f"Claimer started. Target={target}, chat_id={chat_id}")

    if "attempts" not in context.bot_data:
        context.bot_data["attempts"] = {}
    if "claim_history" not in context.bot_data:
        context.bot_data["claim_history"] = []
    if "claimer_log" not in context.bot_data:
        context.bot_data["claimer_log"] = []

    while context.bot_data.get("claimer_running"):
        data = load_data()
        delay = data.get("delay", 90)
        usernames = data.get("usernames", [])
        accounts = data.get("accounts", [])
        channels_map = data.get("channels", {})
        proxies = data.get("proxies", [])
        proxy = build_proxy(proxies)

        remaining = [u for u in usernames if u not in context.bot_data["claim_history"]]

        if not remaining:
            context.bot_data["claimer_running"] = False
            await context.bot.send_message(chat_id, "All usernames processed. Claimer stopped.")
            logger.info("Claimer stopped: all usernames processed.")
            return

        for username in remaining:
            if not context.bot_data.get("claimer_running"):
                break

            context.bot_data["attempts"][username] = context.bot_data["attempts"].get(username, 0) + 1
            attempts = context.bot_data["attempts"][username]
            claimed = False
            claimed_by = ""

            for acc in accounts:
                if claimed:
                    break

                client = None
                try:
                    client = await get_telethon_client(acc, proxy)
                    await client.connect()

                    if not await client.is_user_authorized():
                        logger.warning(f"Account {acc.get('label')} not authorized, skipping.")
                        continue

                    acc_label = acc.get("label", "Unknown")

                    if target in ("both", "user"):
                        try:
                            result = await try_claim_username_as_user(client, username)
                            if result:
                                claimed = True
                                claimed_by = f"User account: {acc_label}"
                        except UsernameInvalidError:
                            logger.warning(f"@{username} is invalid. Skipping.")
                            context.bot_data["claim_history"].append(username)
                            break
                        except FloodWaitError as e:
                            logger.warning(f"Flood wait {e.seconds}s on @{username}")
                            await asyncio.sleep(e.seconds)
                            continue

                    if not claimed and target in ("both", "channel"):
                        acc_channels = channels_map.get(acc_label, [])
                        for ch in acc_channels:
                            try:
                                result = await try_claim_username_for_channel(
                                    client, ch["id"], ch.get("access_hash", 0), username
                                )
                                if result:
                                    claimed = True
                                    claimed_by = f"Channel: {ch['title']} | Account: {acc_label}"
                                    break
                            except UsernameInvalidError:
                                logger.warning(f"@{username} invalid for channel. Skipping.")
                                break
                            except FloodWaitError as e:
                                logger.warning(f"Flood wait {e.seconds}s on channel claim @{username}")
                                await asyncio.sleep(e.seconds)
                            except Exception as e:
                                logger.error(f"Channel claim error @{username}: {e}")

                except AuthKeyError:
                    logger.error(f"Auth key error for account {acc.get('label')}. Session may be expired.")
                except Exception as e:
                    logger.error(f"Error processing account {acc.get('label')}: {e}")
                finally:
                    if client:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass

                if claimed:
                    break

            if claimed:
                context.bot_data["claim_history"].append(username)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_entry = {
                    "username": username,
                    "claimed_by": claimed_by,
                    "attempts": attempts,
                    "timestamp": ts,
                }
                context.bot_data["claimer_log"].append(log_entry)

                msg = (
                    f"Username : @{username}\n"
                    f"Claimed By Valtryek ! Bot Developed By : @fusid\n"
                    f"Attempts : {attempts}"
                )
                try:
                    await context.bot.send_message(chat_id, msg)
                except Exception as e:
                    logger.error(f"Failed to send claim notification: {e}")

                logger.info(f"Claimed @{username} via {claimed_by} after {attempts} attempts.")

        if context.bot_data.get("claimer_running"):
            logger.info(f"Cycle complete. Sleeping {delay}s.")
            await asyncio.sleep(delay)

    logger.info("Claimer loop exited.")


# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Welcome to Username Claimer Bot\n"
        "Developed by @fusid\n\n"
        "Configure your accounts, proxies, and usernames below.\n"
        "Then hit Start Claimer to begin."
    )
    await update.message.reply_text(text, reply_markup=kb_main_menu())


# ─── /monitor ─────────────────────────────────────────────────────────────────

async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    usernames = data.get("usernames", [])
    claimed = context.bot_data.get("claim_history", [])
    attempts = context.bot_data.get("attempts", {})
    running = context.bot_data.get("claimer_running", False)
    target = context.bot_data.get("claimer_target", "N/A")
    delay = data.get("delay", 90)

    lines = [
        "=== CLAIMER MONITOR ===",
        f"Status       : {'RUNNING' if running else 'STOPPED'}",
        f"Target       : {target}",
        f"Delay        : {delay}s",
        f"Accounts     : {len(data.get('accounts', []))}",
        f"Total Names  : {len(usernames)}",
        f"Claimed      : {len(claimed)}",
        f"Remaining    : {len([u for u in usernames if u not in claimed])}",
        "",
        "--- Username Status ---",
    ]

    for u in usernames:
        status = "CLAIMED" if u in claimed else "PENDING"
        att = attempts.get(u, 0)
        lines.append(f"@{u:<20} {status:<8} Attempts: {att}")

    await update.message.reply_text("\n".join(lines))


# ─── /clearhistory ────────────────────────────────────────────────────────────

async def cmd_clearhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Are you sure? All claim history and attempt counts will be wiped.",
        reply_markup=kb_yes_no("clearhistory_yes", "clearhistory_no"),
    )


# ─── /stop ────────────────────────────────────────────────────────────────────

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.bot_data.get("claimer_running"):
        context.bot_data["claimer_running"] = False
        await update.message.reply_text("Claimer stop signal sent. Will stop after current cycle.")
    else:
        await update.message.reply_text("Claimer is not running.")


# ─── Main Callback Handler ────────────────────────────────────────────────────

async def main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    cb = query.data

    # ── Back buttons ──────────────────────────────────────────────────────────
    if cb == "back_main":
        text = (
            "Welcome to Username Claimer Bot\n"
            "Developed by @fusid\n\n"
            "Configure your accounts, proxies, and usernames below.\n"
            "Then hit Start Claimer to begin."
        )
        await query.edit_message_text(text, reply_markup=kb_main_menu())
        return ConversationHandler.END

    if cb == "back_claimer":
        await query.edit_message_text("Claimer Settings", reply_markup=kb_claimer_menu())
        return ConversationHandler.END

    # ── Menu: Add Accounts ────────────────────────────────────────────────────
    if cb == "menu_accounts":
        accounts = data.get("accounts", [])
        acc_list = ""
        if accounts:
            acc_list = "\n".join([f"{i+1}. {a.get('label', 'Unknown')}" for i, a in enumerate(accounts)])
            acc_list = f"\nCurrent accounts:\n{acc_list}\n"

        text = (
            f"Accounts loaded: {len(accounts)}{acc_list}\n"
            "Send account details (one per line):\n\n"
            "Format: API_ID|API_HASH|SESSION_STRING\n\n"
            "Send 'clear' to remove all accounts.\n"
            "Send 'list' to see current accounts."
        )
        await query.edit_message_text(text, reply_markup=kb_back("main"))
        return AWAIT_ACCOUNT_INPUT

    # ── Menu: Set Proxy ───────────────────────────────────────────────────────
    if cb == "menu_proxy":
        proxies = data.get("proxies", [])
        proxy_list = "\n".join([
            f"{i+1}. {p['type'].upper()} {p['host']}:{p['port']}"
            for i, p in enumerate(proxies)
        ]) or "None"

        text = (
            f"Current proxies:\n{proxy_list}\n\n"
            "Proxy is optional. Claimer works without it.\n\n"
            "Format: TYPE|HOST|PORT|USERNAME|PASSWORD\n"
            "TYPE: socks5, socks4, or https\n"
            "Leave USERNAME|PASSWORD blank if not needed.\n\n"
            "Example: socks5|127.0.0.1|1080||\n\n"
            "Send 'clear' to remove all proxies."
        )
        await query.edit_message_text(text, reply_markup=kb_back("main"))
        return AWAIT_PROXY_INPUT

    # ── Menu: Set Usernames ───────────────────────────────────────────────────
    if cb == "menu_usernames":
        usernames = data.get("usernames", [])
        un_list = "\n".join([f"{i+1}. @{u}" for i, u in enumerate(usernames)]) or "None"
        text = (
            f"Usernames to claim ({len(usernames)} total):\n{un_list}\n\n"
            "Send usernames to add (one per line, with or without @).\n"
            "Send 'clear' to reset the list."
        )
        await query.edit_message_text(text, reply_markup=kb_back("main"))
        return AWAIT_USERNAMES_INPUT

    # ── Menu: Claimer ─────────────────────────────────────────────────────────
    if cb == "menu_claimer":
        running = context.bot_data.get("claimer_running", False)
        status = "RUNNING" if running else "STOPPED"
        delay = data.get("delay", 90)
        text = f"Claimer Settings\n\nStatus: {status}\nDelay: {delay}s"
        await query.edit_message_text(text, reply_markup=kb_claimer_menu())
        return ConversationHandler.END

    # ── Claimer: Set Delay ────────────────────────────────────────────────────
    if cb == "claimer_delay":
        current = data.get("delay", 90)
        await query.edit_message_text(
            f"Current delay: {current}s\n\nSend new delay in seconds (e.g. 90):",
            reply_markup=kb_back("claimer"),
        )
        return AWAIT_DELAY_INPUT

    # ── Claimer: Set Channels ─────────────────────────────────────────────────
    if cb == "claimer_channels":
        accounts = data.get("accounts", [])
        if not accounts:
            await query.edit_message_text(
                "No accounts added. Add accounts first.",
                reply_markup=kb_back("claimer"),
            )
            return ConversationHandler.END

        buttons = []
        for i, acc in enumerate(accounts):
            label = acc.get("label", f"Account {i+1}")
            buttons.append([InlineKeyboardButton(label, callback_data=f"fetch_channels_{i}")])
        buttons.append([InlineKeyboardButton("Back", callback_data="back_claimer")])

        await query.edit_message_text(
            "Select an account to fetch its channels:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return ConversationHandler.END

    # ── Fetch Channels for Account ────────────────────────────────────────────
    if cb.startswith("fetch_channels_"):
        idx = int(cb.split("_")[-1])
        accounts = data.get("accounts", [])
        if idx >= len(accounts):
            await query.edit_message_text("Account not found.", reply_markup=kb_back("claimer"))
            return ConversationHandler.END

        acc = accounts[idx]
        proxies = data.get("proxies", [])
        proxy = build_proxy(proxies)

        await query.edit_message_text(f"Fetching channels for {acc.get('label', 'account')}...")

        try:
            channels = await fetch_user_channels(acc, proxy)
        except Exception as e:
            await query.edit_message_text(
                f"Error fetching channels: {e}",
                reply_markup=kb_back("claimer"),
            )
            return ConversationHandler.END

        if not channels:
            await query.edit_message_text(
                "No channels found for this account. Account must be creator or admin.",
                reply_markup=kb_back("claimer"),
            )
            return ConversationHandler.END

        context.user_data["fetched_channels"] = channels
        context.user_data["channel_account_idx"] = idx

        ch_list = "\n".join([
            f"{i+1}. {c['title']} (@{c['username'] or 'no username'})"
            for i, c in enumerate(channels)
        ])
        text = (
            f"Channels found ({len(channels)}):\n{ch_list}\n\n"
            "Send numbers to select channels (e.g. 1 2 3 or just 1):"
        )
        await query.edit_message_text(text, reply_markup=kb_back("claimer"))
        return AWAIT_CHANNEL_SELECTION

    # ── Claimer: Start ────────────────────────────────────────────────────────
    if cb == "claimer_start":
        accounts = data.get("accounts", [])
        usernames = data.get("usernames", [])

        if not accounts:
            await query.edit_message_text(
                "No accounts configured. Add accounts first.",
                reply_markup=kb_back("claimer"),
            )
            return ConversationHandler.END

        if not usernames:
            await query.edit_message_text(
                "No usernames to claim. Add usernames first.",
                reply_markup=kb_back("claimer"),
            )
            return ConversationHandler.END

        if context.bot_data.get("claimer_running"):
            await query.edit_message_text(
                "Claimer is already running. Use /monitor to check status.",
                reply_markup=kb_back("claimer"),
            )
            return ConversationHandler.END

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Both (User + Channel)", callback_data="target_both")],
            [
                InlineKeyboardButton("Only User", callback_data="target_user"),
                InlineKeyboardButton("Only Channel", callback_data="target_channel"),
            ],
            [InlineKeyboardButton("Back", callback_data="back_claimer")],
        ])
        await query.edit_message_text(
            "You want claimer to start on which target?",
            reply_markup=kb,
        )
        return ConversationHandler.END

    # ── Target Selection ──────────────────────────────────────────────────────
    if cb in ("target_both", "target_user", "target_channel"):
        target_map = {
            "target_both": "both",
            "target_user": "user",
            "target_channel": "channel",
        }
        target = target_map[cb]
        channels_map = data.get("channels", {})

        if target in ("both", "channel"):
            total_channels = sum(len(v) for v in channels_map.values())
            if total_channels == 0:
                await query.edit_message_text(
                    "No channels configured. Set channels first via 'Set Channels'.",
                    reply_markup=kb_back("claimer"),
                )
                return ConversationHandler.END

        context.bot_data["claimer_running"] = True
        context.bot_data["claimer_target"] = target
        context.bot_data["claimer_chat_id"] = query.message.chat_id

        delay = data.get("delay", 90)
        asyncio.create_task(
            claimer_loop(context, query.message.chat_id, target)
        )

        await query.edit_message_text(
            f"Claimer started.\n"
            f"Target : {target}\n"
            f"Delay  : {delay}s\n"
            f"Names  : {len(data.get('usernames', []))}\n\n"
            "Use /monitor to track status.\n"
            "Use /stop to stop the claimer.",
            reply_markup=kb_back("main"),
        )
        return ConversationHandler.END

    # ── Clear History Confirmation ────────────────────────────────────────────
    if cb == "clearhistory_yes":
        context.bot_data.pop("claim_history", None)
        context.bot_data.pop("attempts", None)
        context.bot_data.pop("claimer_log", None)
        await query.edit_message_text("History cleared.", reply_markup=kb_main_menu())

    if cb == "clearhistory_no":
        await query.edit_message_text("Cancelled.", reply_markup=kb_main_menu())

    return ConversationHandler.END


# ─── Conversation Input Handlers ──────────────────────────────────────────────

async def handle_account_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = load_data()

    if text.lower() == "clear":
        data["accounts"] = []
        save_data(data)
        await update.message.reply_text("All accounts removed.", reply_markup=kb_main_menu())
        return ConversationHandler.END

    if text.lower() == "list":
        accounts = data.get("accounts", [])
        if not accounts:
            await update.message.reply_text("No accounts saved.", reply_markup=kb_main_menu())
        else:
            lines = [f"{i+1}. {a.get('label', 'Unknown')}" for i, a in enumerate(accounts)]
            await update.message.reply_text("Saved accounts:\n" + "\n".join(lines), reply_markup=kb_main_menu())
        return ConversationHandler.END

    proxies = data.get("proxies", [])
    proxy = build_proxy(proxies)
    added = 0
    errors = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            errors.append(f"Invalid format: {line[:40]}")
            continue

        api_id_raw = parts[0].strip()
        api_hash = parts[1].strip()
        session = parts[2].strip()

        try:
            api_id = int(api_id_raw)
        except ValueError:
            errors.append(f"Invalid API ID: {api_id_raw}")
            continue

        # Check duplicate
        existing = [a for a in data["accounts"] if a["api_id"] == api_id and a["api_hash"] == api_hash]
        if existing:
            errors.append(f"Account with API ID {api_id} already exists.")
            continue

        acc_obj = {"api_id": api_id, "api_hash": api_hash, "session": session, "label": f"Account {len(data['accounts'])+1}"}

        valid, label = await validate_account(acc_obj, proxy)
        if valid:
            acc_obj["label"] = label
            data["accounts"].append(acc_obj)
            added += 1
        else:
            errors.append(f"Account validation failed (API ID {api_id}): {label}")

    save_data(data)

    lines = [f"{added} account(s) added. Total: {len(data['accounts'])}"]
    if errors:
        lines.append("\nErrors:")
        lines.extend(errors)

    await update.message.reply_text("\n".join(lines), reply_markup=kb_main_menu())
    return ConversationHandler.END


async def handle_proxy_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = load_data()

    if text.lower() == "clear":
        data["proxies"] = []
        save_data(data)
        await update.message.reply_text("All proxies removed.", reply_markup=kb_main_menu())
        return ConversationHandler.END

    added = 0
    errors = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            errors.append(f"Invalid format: {line[:40]}")
            continue

        proxy_type = parts[0].strip().lower()
        if proxy_type not in ("socks5", "socks4", "https", "http"):
            errors.append(f"Unknown proxy type: {proxy_type}")
            continue

        host = parts[1].strip()
        try:
            port = int(parts[2].strip())
        except ValueError:
            errors.append(f"Invalid port: {parts[2]}")
            continue

        username = parts[3].strip() if len(parts) > 3 else ""
        password = parts[4].strip() if len(parts) > 4 else ""

        data["proxies"].append({
            "type": proxy_type,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        })
        added += 1

    save_data(data)
    lines = [f"{added} proxy(s) added. Total: {len(data['proxies'])}"]
    if errors:
        lines.append("\nErrors:")
        lines.extend(errors)

    await update.message.reply_text("\n".join(lines), reply_markup=kb_main_menu())
    return ConversationHandler.END


async def handle_usernames_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = load_data()

    if text.lower() == "clear":
        data["usernames"] = []
        save_data(data)
        await update.message.reply_text("Username list cleared.", reply_markup=kb_main_menu())
        return ConversationHandler.END

    added = 0
    skipped = 0

    for line in text.splitlines():
        un = line.strip().lstrip("@").lower()
        if not un:
            continue
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', un):
            skipped += 1
            continue
        if un not in data["usernames"]:
            data["usernames"].append(un)
            added += 1
        else:
            skipped += 1

    save_data(data)
    msg = f"{added} username(s) added. Total: {len(data['usernames'])}"
    if skipped:
        msg += f"\n{skipped} skipped (duplicate or invalid format)."

    await update.message.reply_text(msg, reply_markup=kb_main_menu())
    return ConversationHandler.END


async def handle_delay_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower().rstrip("s").strip()
    data = load_data()
    try:
        delay = int(text)
        if delay < 10:
            await update.message.reply_text(
                "Minimum delay is 10 seconds to avoid flood bans.",
                reply_markup=kb_main_menu(),
            )
            return ConversationHandler.END
        data["delay"] = delay
        save_data(data)
        await update.message.reply_text(f"Delay set to {delay}s.", reply_markup=kb_main_menu())
    except ValueError:
        await update.message.reply_text(
            "Invalid input. Send a number like 90",
            reply_markup=kb_main_menu(),
        )
    return ConversationHandler.END


async def handle_channel_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    fetched = context.user_data.get("fetched_channels", [])
    acc_idx = context.user_data.get("channel_account_idx", 0)
    data = load_data()

    if acc_idx >= len(data["accounts"]):
        await update.message.reply_text("Account index out of range.", reply_markup=kb_main_menu())
        return ConversationHandler.END

    selected_indices = []
    for part in text.split():
        try:
            idx = int(part) - 1
            if 0 <= idx < len(fetched):
                selected_indices.append(idx)
        except ValueError:
            pass

    if not selected_indices:
        await update.message.reply_text(
            "No valid numbers found. Send numbers like: 1 2 3",
            reply_markup=kb_back("claimer"),
        )
        return AWAIT_CHANNEL_SELECTION

    acc = data["accounts"][acc_idx]
    acc_label = acc.get("label", f"Account {acc_idx+1}")

    if "channels" not in data:
        data["channels"] = {}
    if acc_label not in data["channels"]:
        data["channels"][acc_label] = []

    added = 0
    for i in selected_indices:
        ch = fetched[i]
        existing_ids = [c["id"] for c in data["channels"][acc_label]]
        if ch["id"] not in existing_ids:
            data["channels"][acc_label].append({
                "id": ch["id"],
                "access_hash": ch.get("access_hash", 0),
                "title": ch["title"],
                "username": ch["username"],
            })
            added += 1

    save_data(data)
    await update.message.reply_text(
        f"{added} channel(s) saved for {acc_label}.\nTotal channels for this account: {len(data['channels'][acc_label])}",
        reply_markup=kb_main_menu(),
    )
    return ConversationHandler.END


# ─── Fallback for unexpected text during conversation ─────────────────────────

async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Use the buttons or send the correct format.",
        reply_markup=kb_main_menu(),
    )
    return ConversationHandler.END


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN not set. Export BOT_TOKEN env variable or set it in the script.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(main_callback),
        ],
        states={
            AWAIT_ACCOUNT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_account_input),
                CallbackQueryHandler(main_callback),
            ],
            AWAIT_PROXY_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_proxy_input),
                CallbackQueryHandler(main_callback),
            ],
            AWAIT_USERNAMES_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_usernames_input),
                CallbackQueryHandler(main_callback),
            ],
            AWAIT_DELAY_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delay_input),
                CallbackQueryHandler(main_callback),
            ],
            AWAIT_CHANNEL_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channel_selection),
                CallbackQueryHandler(main_callback),
            ],
        },
        fallbacks=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler),
            CallbackQueryHandler(main_callback),
        ],
        per_message=False,
        allow_reentry=True,
        name="main_conv",
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("clearhistory", cmd_clearhistory))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(conv_handler)

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
