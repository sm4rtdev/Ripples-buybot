import os
import ssl
import json
import logging
import asyncio
import certifi
import websockets
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from datetime import datetime
from db import TokenConfig

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

async def handle_transaction(response):
    """Process incoming transactions."""
    token_config = config.get_config()
    transaction = json.loads(response)
    
    if "transaction" not in transaction:
        return

    tx = transaction["transaction"]
    meta = transaction["meta"]
    
    if tx.get("TransactionType") not in ["Payment", "OfferCreate"]:
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

            if xrp_spent > float(token_config['THRESHOLD']):
                await send_notification(delivered_amount, xrp_spent, token_config, tx)
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

            if xrp_spent > float(token_config['THRESHOLD']):
                await send_notification(value, xrp_spent, token_config, tx)
    except (ValueError, TypeError) as e:
        logger.error(f"Error processing offer create values: {e}")

async def send_notification(value, xrp_spent, token_config, tx):
    """Send buy notification to the configured group."""
    if not token_config['CHAT_ID']:
        logger.error("No target group configured")
        return

    price = xrp_spent / value if value else 0
    emoji_count = min(int(xrp_spent / 50), 50)
    emojis = token_config['EMOJI_ICON'] * emoji_count

    # Convert currency code from hex to string if needed
    currency_code = token_config['TOKEN_CURRENCY']
    try:
        if len(currency_code) == 40:  # Hex format
            currency_code = bytes.fromhex(currency_code).decode('utf-8').strip('\x00')
    except:
        pass  # Keep original if conversion fails

    message = (
        f"<b>🔥 NEW BUY</b>\n\n"
        f"{emojis}\n\n"
        f"💰 <b>Spent:</b> {xrp_spent:.2f} XRP\n"
        f"🎯 <b>Bought:</b> {value:.2f} {currency_code}\n"
        f"💎 <b>Price:</b> {price:.6f} XRP\n"
    )

    keyboard = [
        [InlineKeyboardButton("View Transaction", url=f"https://xrpscan.com/account/{token_config['TOKEN_ISSUER']}")],
        [InlineKeyboardButton("Chart", url=f"https://firstledger.net/token/{token_config['TOKEN_ISSUER']}/{token_config['TOKEN_CURRENCY']}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    bot = Bot(token=TOKEN)
    try:
        if token_config['TYPE']:  # GIF
            await bot.send_animation(
                chat_id=token_config['CHAT_ID'],
                animation=token_config['MEDIA'],
                caption=message,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:  # Photo
            await bot.send_photo(
                chat_id=token_config['CHAT_ID'],
                photo=token_config['MEDIA'],
                caption=message,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending notification: {e}")

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
    """Start the bot and WebSocket connection."""
    if not config.get_config()['CHAT_ID']:
        await update.message.reply_text(
            "No target group set. Admin must use /setgroup in the target group first."
        )
        return
    
    if update.effective_chat.id != config.get_config()['CHAT_ID']:
        await update.message.reply_text("This bot is configured for a specific group only.")
        return

    global ws_task
    if ws_task is None:
        ws_task = asyncio.create_task(start_ws_connection())
        await update.message.reply_text("✅ Bot started and monitoring transactions.")
    else:
        await update.message.reply_text("⚠️ Bot is already running.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the WebSocket connection."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can stop the bot.")
        return

    global ws_task
    if ws_task:
        ws_task.cancel()
        ws_task = None
        await update.message.reply_text("🛑 Bot stopped.")
    else:
        await update.message.reply_text("ℹ️ Bot is not running.")

async def set_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the target group for the bot."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can set the target group.")
        return

    if update.effective_chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command must be used in a group.")
        return

    config.update_config('CHAT_ID', update.effective_chat.id)
    await update.message.reply_text("✅ This group has been set as the target group for buy notifications.")

async def set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the minimum XRP threshold for notifications."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can change the threshold.")
        return

    if not context.args:
        await update.message.reply_text("❌ Please provide a threshold value in XRP.")
        return

    try:
        threshold = float(context.args[0])
        if threshold <= 0:
            raise ValueError("Threshold must be positive")
        
        config.update_config('THRESHOLD', str(threshold))
        await update.message.reply_text(f"✅ Notification threshold set to {threshold} XRP")
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid positive number.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current bot status and configuration."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the bot owner can view status.")
        return

    cfg = config.get_config()
    status_message = (
        f"🤖 <b>Bot Status</b>\n\n"
        f"Running: {'✅' if ws_task else '❌'}\n"
        f"Target Group: {cfg['CHAT_ID'] or 'Not set'}\n"
        f"Threshold: {cfg['THRESHOLD']} XRP\n"
        f"Token Issuer: {cfg['TOKEN_ISSUER']}\n"
        f"Currency Code: {cfg['TOKEN_CURRENCY']}\n"
        f"Media Type: {'GIF' if cfg['TYPE'] else 'Photo'}\n"
    )
    await update.message.reply_text(status_message, parse_mode="HTML")

def main():
    """Start the bot."""
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("setgroup", set_group))
    application.add_handler(CommandHandler("threshold", set_threshold))
    application.add_handler(CommandHandler("status", status))

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()