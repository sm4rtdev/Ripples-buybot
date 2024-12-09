import os
import ssl
import json
import logging
import asyncio
import certifi
import websockets
import requests
import time
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from db import TokenConfig
from datetime import datetime
from telegram.constants import ChatMemberStatus
from telegram.error import Conflict
from xrpl.clients import JsonRpcClient
from xrpl.models.requests import AccountLines

client = JsonRpcClient("https://s.altnet.rippletest.net:51234")  # Use the appropriate URL for your network
load_dotenv()

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("BuyBot")

# Bot Configuration
TOKEN = os.getenv("TOKEN")
XRPL_WS_URL = os.getenv('XRPL_WS_URL')
OWNER_ID = int(os.getenv('OWNER_ID'))

config = TokenConfig()
ws_task = None

# async def get_rpls_price():
#     """Get current $RPLS price in USD."""
#     try:
#         async with aiohttp.ClientSession() as session:
#             async with session.get('https://api.coingecko.com/api/v3/simple/price?ids=$rpls&vs_currencies=usd') as response:
#                 data = await response.json()
#                 return data['ripple']['usd']
#     except Exception as e:
#         logger.error(f"Error fetching XRP price: {e}")
#         return 0

async def error_handler(update, context):
    if isinstance(context.error, Conflict):
        logger.error("Conflict error: Make sure only one bot instance is running.")
    else:
        logger.error(f"Unhandled error: {context.error}")

async def xrpl_stream(websocket):
    """Stream transactions for the configured token."""
    token_config = config.get_config()
    
    await websocket.send(json.dumps({
        "command": "subscribe",
        "accounts": [token_config['TOKEN_ISSUER']]
    }))
    logger.info(f"Subscribed to transactions for issuer: {token_config['TOKEN_ISSUER']}")

    while True:
        response = await websocket.recv()
        await handle_transaction(response)

async def maintain_websocket_connection():
    """Maintain WebSocket connection with reconnection logic."""
    while True:
        try:
            await start_ws_connection()
        except Exception as e:
            logger.error(f"Connection lost, reconnecting... Error: {e}")
            await asyncio.sleep(5)  # Wait before reconnecting


async def handle_transaction(response):
    """Process incoming transactions."""
    token_config = config.get_config()
    transaction = json.loads(response)
    
    if "transaction" not in transaction:
        return

    tx = transaction["transaction"]
    meta = transaction["meta"]
    
    if tx.get("TransactionType") not in ["Payment"]:
        return

    try:
        if tx.get("TransactionType") == "Payment":
            await handle_payment(tx, meta, token_config)
        elif tx.get("TransactionType") == "OfferCreate":
            await handle_offer_create(tx, meta, token_config)
    except Exception as e:
        logger.error(f"Error processing transaction: {e}")

async def handle_payment(tx, meta, token_config):
    """Handle Payment type transactions."""
    if tx['Account'] != tx['Destination']:
        return

    amount = tx.get("Amount", {})
    if isinstance(amount, dict) and \
       amount.get("currency") == token_config['TOKEN_CURRENCY'] and \
       amount.get("issuer") == token_config['TOKEN_ISSUER']:
        
        try:
            delivered_amount = float(meta.get('delivered_amount', {}).get('value', 0))
            xrp_spent = float(tx.get("SendMax", "0")) / 1000000

            # Send notifications to all configured groups
            for chat_id in token_config["CHAT_IDS"]:
                group_settings = config.get_group_settings(chat_id)
                if xrp_spent > float(group_settings['THRESHOLD']):
                    await send_notification(delivered_amount, xrp_spent, group_settings, tx, chat_id)
        except (ValueError, TypeError) as e:
            logger.error(f"Error processing payment values: {e}")

async def handle_offer_create(tx, meta, token_config):
    """Handle OfferCreate type transactions."""
    xrp_spent = 0.0
    value = 0.0
    
    taker_pays = tx.get("TakerPays", {})
    taker_gets = tx.get("TakerGets", {})

    try:
        if isinstance(taker_pays, str) and \
           isinstance(taker_gets, dict) and \
           taker_gets["currency"] == token_config['TOKEN_CURRENCY'] and \
           taker_gets["issuer"] == token_config['TOKEN_ISSUER']:
            
            value = float(taker_gets["value"])
            xrp_spent = float(taker_pays) / 1000000

            # Check affected nodes for actual XRP movement
            for node in meta.get("AffectedNodes", []):
                modified_node = node.get("ModifiedNode", {})
                if modified_node.get("LedgerEntryType") == "AccountRoot":
                    final_balance = int(modified_node["FinalFields"].get("Balance", 0))
                    previous_balance = int(modified_node["PreviousFields"].get("Balance", 0))
                    xrp_diff = (final_balance - previous_balance) / 1000000
                    if xrp_diff > 0:
                        xrp_spent = xrp_diff
                        break

            # Send notifications to all configured groups
            for chat_id in token_config["CHAT_IDS"]:
                group_settings = config.get_group_settings(chat_id)
                if xrp_spent > float(group_settings['THRESHOLD']):
                    await send_notification(value, xrp_spent, group_settings, tx, chat_id)
                    
    except (ValueError, TypeError) as e:
        logger.error(f"Error processing offer create values: {e}")
        
async def send_notification(value, xrp_spent, group_settings, tx, chat_id):
    """Send buy notification to a specific group."""
    price = xrp_spent / value if value else 0
    emoji_count = min(int(xrp_spent / 10), 50)
    emojis = group_settings['EMOJI_ICON'] * emoji_count
    market_cap = calculate_market_cap()

    # Convert currency code from hex to string if needed
    currency_code = config.get_config()['TOKEN_CURRENCY']
    try:
        if len(currency_code) == 40:  # Hex format
            currency_code = bytes.fromhex(currency_code).decode('utf-8').strip('\x00')
    except:
        pass  # Keep original if conversion fails

    # New message format
    message = (
        f"🚀 <b>New ${currency_code} Buy!</b>\n\n"
        f"{emojis}\n\n"
        f"💸 <b>Spent:</b> {xrp_spent:.2f} XRP\n"
        f"💳 <b>Bought:</b> {value:,.3f} (${currency_code})\n"
        f"🧢 <b>MC:</b> ${market_cap:,.3f} USD\n"  # You'll need to implement market cap calculation
        f"💰 <b>CA:</b> {config.get_config()['TOKEN_ISSUER']}\n"
        f"👛 <b>Wallet:</b> {tx['Account']}\n\n"
        # f"📢 Paid Ad:\n"
        # f"👁 $3RDEYE Sees Beyond All Chains\n"
        # f"🔮 Awaken your 3rd Eye, unlock the truth\n"
        # f"👁 $3RDEYE aligns your mind, body, and soul\n"
        # f"🔴 X Marks the Vision\n"
        # f"TG | X | FL\n\n"
        f"🤖 <b>in:</b> {len(config.get_config()['CHAT_IDS'])} TG group(s)"
    )

    keyboard = [
        [
            InlineKeyboardButton("View Transaction", url=f"https://xrpscan.com/account/{tx['Account']}"),
            InlineKeyboardButton("Chart", url=f"https://firstledger.net/token/{config.get_config()['TOKEN_ISSUER']}/{config.get_config()['TOKEN_CURRENCY']}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    bot = Bot(token=TOKEN)
    try:
        if group_settings['TYPE']:  # GIF
            await bot.send_animation(
                chat_id=chat_id,
                animation=group_settings['MEDIA'],
                caption=message,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:  # Photo
            await bot.send_photo(
                chat_id=chat_id,
                photo=group_settings['MEDIA'],
                caption=message,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending notification to group {chat_id}: {e}")

async def start_ws_connection():
    """Maintain WebSocket connection."""
    while True:
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with websockets.connect(XRPL_WS_URL, ssl=ssl_context) as websocket:
                await xrpl_stream(websocket)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            await asyncio.sleep(5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start monitoring in the current group."""
    chat_id = update.effective_chat.id
    
    # Check if this is a group chat
    if update.effective_chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ This bot can only be used in groups.")
        return

  # Check if the user has admin privileges
    user_id = update.effective_user.id
    if not await is_group_admin(chat_id, user_id, context):
        await update.message.reply_text("❌ Only group administrators can start the monitoring.")
        return

    # Check if the group is already being monitored
    if chat_id in config.get_config()["CHAT_IDS"]:
        await update.message.reply_text("ℹ️ This group is already being monitored.")
        return

    # Add the group to monitoring list
    config.add_group(chat_id)
    
    # Start WebSocket connection if not already running
    global ws_task
    if not ws_task or ws_task.done():
        ws_task = asyncio.create_task(maintain_websocket_connection())
        
    await update.message.reply_text(
        "✅ Bot started successfully!\n\n"
        "Use /help to see available commands.\n"
        "Use /threshold to set minimum XRP amount for notifications.\n"
        "Use /status to see current settings."
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop monitoring in the current group."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Check admin status
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        is_admin = chat_member.status in ['creator', 'administrator']
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await update.message.reply_text("❌ Unable to verify admin status. Please try again.")
        return

    if not is_admin:
        await update.message.reply_text("❌ Only group administrators can stop the monitoring.")
        return

    try:
        # Remove the group from monitoring
        if chat_id in config.get_config()["CHAT_IDS"]:
            if config.remove_group(chat_id):
                logger.info(f"Group {chat_id} removed from monitoring.")
                await update.message.reply_text("✅ Group removed from monitoring list.")
                
                # If no groups left, stop the WebSocket connection
                if not config.get_config()["CHAT_IDS"]:
                    global ws_task
                    if ws_task:
                        try:
                            ws_task.cancel()
                            ws_task = None
                            await update.message.reply_text("🛑 Bot stopped as no groups are being monitored.")
                            logger.info("WebSocket task stopped because no groups are being monitored.")
                        except Exception as e:
                            logger.error(f"Error stopping WebSocket task: {e}")
                            await update.message.reply_text("⚠️ Error stopping the bot. Please check the logs.")
            else:
                await update.message.reply_text("⚠️ Error removing the group. Please try again.")
        else:
            logger.info(f"Stop command received for non-monitored group {chat_id}.")
            await update.message.reply_text("ℹ️ This group is not being monitored.")
    except Exception as e:
        logger.error(f"Unexpected error in stop function: {e}")
        await update.message.reply_text("⚠️ An unexpected error occurred. Please try again.")

# Helper function to check admin status (can be used by other commands)
async def is_group_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if a user is an admin in the group."""
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)

        if chat_member.status == 'creator':
            return True
            
        # Check admin permissions
        if hasattr(chat_member, 'can_change_info') or \
           hasattr(chat_member, 'can_delete_messages') or \
           hasattr(chat_member, 'can_restrict_members') or \
           hasattr(chat_member, 'can_invite_users') or \
           hasattr(chat_member, 'can_pin_messages') or \
           hasattr(chat_member, 'can_promote_members'):
            return True
            
        return False
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

async def set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the minimum XRP threshold for notifications in the current group."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_group_admin(chat_id, user_id, context):
        await update.message.reply_text("❌ Only group administrators can change settings.")
        return

    if not context.args:
        await update.message.reply_text("❌ Please provide a threshold value in XRP.")
        return

    try:
        threshold = float(context.args[0])
        if threshold <= 0:
            await update.message.reply_text("❌ Threshold must be positive.")
            return

        if chat_id not in config.get_config()["CHAT_IDS"]:
            await update.message.reply_text("❌ This group is not being monitored. Use /start first.")
            return

        group_settings = config.get_group_settings(chat_id)
        group_settings['THRESHOLD'] = str(threshold)
        config.update_group_settings(chat_id, group_settings)
        
        await update.message.reply_text(f"✅ Buy notification threshold set to {threshold} XRP for this group.")
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number.")

async def set_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set media URL for buy notifications in the current group."""
    if not context.args:
        await update.message.reply_text("❌ Please provide a media URL and type (gif/photo).")
        return

    if len(context.args) < 2:
        await update.message.reply_text("❌ Please specify both URL and type (gif/photo).")
        return

    url = context.args[0]
    media_type = context.args[1].lower()

    if media_type not in ['gif', 'photo']:
        await update.message.reply_text("❌ Media type must be either 'gif' or 'photo'.")
        return

    chat_id = update.effective_chat.id
    if chat_id not in config.get_config()["CHAT_IDS"]:
        await update.message.reply_text("❌ This group is not being monitored. Use /start first.")
        return

    group_settings = config.get_group_settings(chat_id)
    group_settings['MEDIA'] = url
    group_settings['TYPE'] = (media_type == 'gif')
    config.update_group_settings(chat_id, group_settings)

    await update.message.reply_text(f"✅ Buy notification media updated for this group.")

async def set_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set emoji for buy notifications in the current group."""
    if not context.args:
        await update.message.reply_text("❌ Please provide an emoji.")
        return

    emoji = context.args[0]
    chat_id = update.effective_chat.id
    
    if chat_id not in config.get_config()["CHAT_IDS"]:
        await update.message.reply_text("❌ This group is not being monitored. Use /start first.")
        return

    group_settings = config.get_group_settings(chat_id)
    group_settings['EMOJI_ICON'] = emoji
    config.update_group_settings(chat_id, group_settings)

    await update.message.reply_text(f"✅ Buy notification emoji updated to {emoji} for this group.")

def get_circulating_supply(coin_id):
    """
    Fetch the circulating supply of a cryptocurrency from CoinGecko API.
    :param coin_id: The CoinGecko ID of the cryptocurrency (e.g., "ripple" for XRP).
    :return: Circulating supply as a float, or None if not found.
    """
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
    max_retries = 5  # Maximum number of retries
    retry_delay = 10  # Delay in seconds between retries

    for attempt in range(max_retries):
        try:
            response = requests.get(url)
            
            # Handle rate limit (429 Too Many Requests)
            if response.status_code == 429:
                print(f"Rate limit exceeded. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue  # Retry the request
            
            # Raise an error for other bad status codes
            response.raise_for_status()
            
            # Parse the response
            data = response.json()
            circulating_supply = data["market_data"]["circulating_supply"]
            return circulating_supply
        
        except requests.exceptions.RequestException as e:
            print(f"Error fetching circulating supply (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:  # If retries are left
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print("Max retries reached. Exiting.")
                return None
    
def get_token_price(token_symbol):
    """
    Fetch the current token price in USD from CoinGecko.
    """
    try:
        response = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={token_symbol}&vs_currencies=usd"
        )
        response.raise_for_status()
        data = response.json()

        # Check if the token exists in the response
        if token_symbol not in data:
            raise ValueError(f"Token '{token_symbol}' not found in CoinGecko response.")
        return data[token_symbol]["usd"]
    except Exception as e:
        logger.error(f"Error fetching token price: {e}")
        return 0.0

def calculate_market_cap():
    """
    Calculate the market cap of the token.
    """
    try:
        # Ensure the coroutine is awaited
        token_symbol = "ripples"
        market_cap = get_circulating_supply(token_symbol) * get_token_price(token_symbol)
        return market_cap
    except Exception as e:
        logger.error(f"Error calculating market cap: {e}", exc_info=True)
        return 0


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current status and settings for the group."""
    chat_id = update.effective_chat.id
    
    if chat_id not in config.get_config()["CHAT_IDS"]:
        await update.message.reply_text("❌ This group is not being monitored. Use /start first.")
        return

    group_settings = config.get_group_settings(chat_id)
    token_config = config.get_config()

    # Convert currency code from hex to string if needed
    currency_code = token_config['TOKEN_CURRENCY']
    market_cap = calculate_market_cap()
    try:
        if len(currency_code) == 40:  # Hex format
            currency_code = bytes.fromhex(currency_code).decode('utf-8').strip('\x00')
    except:
        pass  # Keep original if conversion fails

    status_message = (
        "<b>🤖 Bot Status</b>\n\n"
        f"🎯 <b>Token:</b> {currency_code}\n"
        f"📝 <b>Issuer:</b> {token_config['TOKEN_ISSUER']}\n"
        f"💰 <b>Threshold:</b> {group_settings['THRESHOLD']} XRP\n"
        f"🧢 <b>MC:</b> ${market_cap:,.3f} USD\n"  # You'll need to implement market cap calculation
        f" <b>Emoji:</b> {group_settings['EMOJI_ICON']}\n"
        f"🖼️ <b>Media Type:</b> {'GIF' if group_settings['TYPE'] else 'Photo'}\n"
        f"🔗 <b>Media URL:</b> {group_settings['MEDIA']}\n"
        f"📡 <b>WebSocket:</b> {'Connected' if ws_task and not ws_task.done() else 'Disconnected'}\n"
    )

    await update.message.reply_text(status_message, parse_mode="HTML")

async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show complete bot status (admin only)."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can use this command.")
        return

    token_config = config.get_config()
    groups_info = ""
    
    for chat_id in token_config["CHAT_IDS"]:
        group_settings = config.get_group_settings(chat_id)
        try:
            chat = await context.bot.get_chat(chat_id)
            group_name = chat.title
        except:
            group_name = f"Group {chat_id}"
            
        groups_info += (
            f"\n<b>{group_name}</b>\n"
            f"- Threshold: {group_settings['THRESHOLD']} XRP\n"
            f"- Emoji: {group_settings['EMOJI_ICON']}\n"
            f"- Media Type: {'GIF' if group_settings['TYPE'] else 'Photo'}\n"
        )

    status_message = (
        "<b>🤖 Bot Admin Status</b>\n\n"
        f"🎯 <b>Token:</b> {token_config['TOKEN_CURRENCY']}\n"
        f"📝 <b>Issuer:</b> {token_config['TOKEN_ISSUER']}\n"
        f"👥 <b>Monitored Groups:</b> {len(token_config['CHAT_IDS'])}\n"
        f"📡 <b>WebSocket:</b> {'Connected' if ws_task and not ws_task.done() else 'Disconnected'}\n\n"
        "<b>Group Settings:</b>"
        f"{groups_info}"
    )

    await update.message.reply_text(status_message, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message."""
    help_text = """
<b>🤖 Buy Bot Commands:</b>

<b>Group Management (Admins only):</b>
/start - Start monitoring in this group
/stop - Stop monitoring in this group
/threshold [amount] - Set minimum XRP amount for notifications
/setmedia [url] [gif/photo] - Set notification media
/setemoji [emoji] - Set notification emoji

<b>General Commands:</b>
/status - Show current settings
/help - Show this help message

<b>Admin Commands:</b>
/adminstatus - Show complete bot status (bot owner only)

<b>Note:</b> 
- Group admin permissions are required for management commands
- All settings are group-specific
- Each group can have different thresholds, media, and emojis
"""
    await update.message.reply_text(help_text, parse_mode="HTML")

def main():
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    application.add_error_handler(error_handler)

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("threshold", set_threshold))
    application.add_handler(CommandHandler("setmedia", set_media))
    application.add_handler(CommandHandler("setemoji", set_emoji))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("adminstatus", admin_status))
    application.add_handler(CommandHandler("help", help_command))

    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()