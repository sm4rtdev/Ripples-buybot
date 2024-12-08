import os
import ssl
import json
import logging
import asyncio
import certifi
import threading
import websockets
from asyncio import Lock
from db import GetTimestamp
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from datetime import datetime, timezone

load_dotenv()
# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("Neiro_BOT.log")
MEDIA_DIR = 'img'
user_role = {}
user_state = {}
user_group = {}
active_sessions = {}
session_lock = Lock()
getTimestamp = GetTimestamp()

# Ensure the directory exists
os.makedirs(MEDIA_DIR, exist_ok=True)
# XRPL Settings
TOKEN = os.getenv("TOKEN")
XRPL_WS_URL = os.getenv('XRPL_WS_URL',)
MAX_FILE_SIZE = 50 * 1024 * 1024

ALLOWED_MIME_TYPES = ['image/jpeg', 'image/png', 'image/gif']

async def xrpl_stream(chat_id, stop_event):
  """Stream transactions for a specific user based on their chat_id."""
  while not stop_event.is_set():  # Loop until the stop event is triggered
    try:
      logger.info("Connecting to WebSocket...")
      ssl_context = ssl.create_default_context(cafile=certifi.where())
      async with websockets.connect(XRPL_WS_URL, ssl=ssl_context) as websocket:
        user_data = getTimestamp._find_group(CHAT_ID=chat_id)
        NEIRO_ISSUER = user_data['NEIRO_ISSUER']

        await websocket.send(json.dumps({
            "command": "subscribe",
            "accounts": [NEIRO_ISSUER]
        }))
        logger.info(f"Subscribed to transactions related to the address: {NEIRO_ISSUER}")

        while not stop_event.is_set():  # Check if stop_event is set
          response = await websocket.recv()  # Wait for incoming messages
          await handle_transaction(response, chat_id)

    except Exception as e:
      logger.error(f"WebSocket connection error: {e}. Reconnecting in 5 seconds...")
      await asyncio.sleep(2)  # Wait before trying to reconnect

async def handle_transaction(response, chat_id):
  user_data = getTimestamp._find_group(CHAT_ID=chat_id)
  NEIRO_ISSUER = user_data['NEIRO_ISSUER']
  NEIRO_CURRENCY = user_data['NEIRO_CURRENCY']
  THRESHOLD = float(user_data['THRESHOLD'])
  CHAT_ID = user_data['CHAT_ID']
  EMOJI_ICON = user_data['EMOJI_ICON']
  MEDIA = user_data['MEDIA']
  TYPE = user_data['TYPE']
  transaction = json.loads(response)
  logger.info(f"Received transaction data: {response}")
  if "transaction" in transaction:
    tx = transaction["transaction"]
    meta = transaction["meta"]
    affected_nodes = meta.get("AffectedNodes", [])
    tx_type = tx.get("TransactionType")
    logger.info(f"Transaction type: {tx_type}")
    with open('supply.json', 'r', encoding='utf-8') as file:
      SUPPLY = json.load(file)
    if tx_type == "Payment" and tx['Account'] == tx['Destination']:
      amount = tx.get("Amount", {})
      if isinstance(amount, dict) and amount.get("currency") == NEIRO_CURRENCY and amount.get("issuer") == NEIRO_ISSUER:
        try:
          delivered_amount = meta.get('delivered_amount', {}).get('value', 0)
          try:
            value = float(delivered_amount)
          except ValueError:
            print("Error: Unable to convert delivered_amount to float.")
          xrp_spent = float(tx.get("SendMax", "0")) / 1000000
          if xrp_spent > THRESHOLD:
            await process_transaction(chat_id, value, xrp_spent, NEIRO_CURRENCY, SUPPLY[NEIRO_ISSUER], EMOJI_ICON, MEDIA, TYPE, tx)
        except (ValueError, TypeError) as e:
          logger.error(f"Error converting NEIRO amount to float in Payment transaction: {e}, Transaction ID: {tx.get('hash', 'N/A')}")
    elif tx_type == "OfferCreate":
      xrp_spent = 0.0
      value = 0.0
      is_accountroot = False
      is_accountroot_xrp = 0.0
      taker_pays = tx.get("TakerPays", {})
      taker_gets = tx.get("TakerGets", {})
      offer_sequence = tx.get("OfferSequence")
      try:
        # if isinstance(taker_gets, str) and offer_sequence and isinstance(taker_pays, dict) and taker_pays["currency"] == NEIRO_CURRENCY and taker_pays["issuer"] == NEIRO_ISSUER:
        #   value = float(taker_pays["value"])
        #   xrp_spent = float(taker_gets) / 1_000_000  # Convert drops to XRP
        if isinstance(taker_pays, str) and isinstance(taker_gets, dict) and taker_gets["currency"] == NEIRO_CURRENCY and taker_gets["issuer"] == NEIRO_ISSUER:
          value = float(taker_gets["value"])
          xrp_spent = float(taker_pays) / 1_000_000  # Convert drops to XRP
        for node in affected_nodes:
          modified_node = node.get("ModifiedNode", {})
          ledger_entry_type = modified_node.get("LedgerEntryType")
          if ledger_entry_type == "AccountRoot":
            final_balance = int(modified_node["FinalFields"]["Balance"])
            previous_balance = int(modified_node["PreviousFields"]["Balance"])
            xrp_diff = (final_balance - previous_balance) / 1_000_000  # Convert drops to XRP

            if xrp_diff > 0:  # XRP was credited to the account
              is_accountroot = True
              is_accountroot_xrp += xrp_diff
        if is_accountroot: 
          xrp_spent = is_accountroot_xrp
        if xrp_spent > THRESHOLD:
          await process_transaction(chat_id, value, xrp_spent, NEIRO_CURRENCY, SUPPLY[NEIRO_ISSUER], EMOJI_ICON, MEDIA, TYPE, tx)
      except (ValueError, TypeError) as e:
        logger.error(f"Error processing OfferCreate transaction: {e}, Transaction ID: {tx.get('hash', 'N/A')}")

async def process_transaction(chat_id, value, xrp_spent, NEIRO_CURRENCY, SUPPLY, EMOJI_ICON, MEDIA, TYPE, tx):
  price_neiro = xrp_spent / value
  market_cap = price_neiro * SUPPLY if price_neiro != 0 else 0.0

  price_neiro = round(price_neiro, 20)
  market_cap = round(market_cap, 10)

  emoji_count = min(int(xrp_spent / 50), 50)
  emojis = EMOJI_ICON * emoji_count
  bytes_data = bytes.fromhex(NEIRO_CURRENCY)
  decoded_str = bytes_data.decode('utf-8', errors='replace').replace('\x00', '')
  message = (
    f"<b>TRANSACTION</b>\n\n"
    f"{emojis}\n\n"
    f"💰 <b>Spent:</b> {xrp_spent} XRP\n"
    f"🎯 <b>Bought:</b> {value} {decoded_str}\n"
    f"{EMOJI_ICON} <b>Emoji Price:</b> 50 XRP\n"
    f"💹 <b>Current Price:</b> {price_neiro:.12f} XRP\n"
    f"💼 <b>Market Cap:</b> {market_cap} XRP"
  )
  keyboard = [
    [InlineKeyboardButton("EXPLORER", url="https://xmagnetic.org/dex/")],
    [InlineKeyboardButton("HASH", url=f"https://bithomp.com/explorer/{tx['hash']}")]
  ]
  reply_markup = InlineKeyboardMarkup(keyboard)
  bot = Bot(token=TOKEN)
  if TYPE:
    await bot.send_video(chat_id=chat_id, video=MEDIA, caption=message, parse_mode="HTML", reply_markup=reply_markup)
  else:
    await bot.send_photo(chat_id=chat_id, photo=MEDIA, caption=message, parse_mode="HTML", reply_markup=reply_markup)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  user_id = update.message.from_user.id
  chat_id = update.message.chat.id
  user_data = getTimestamp._find_user(user_id=user_id)
  chat_group_data = getTimestamp._find_group(CHAT_ID=chat_id)
  current_timestamp = datetime.now(timezone.utc).timestamp()
  chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
  if chat_group_data:
    if chat_group_data['expire_time'] > current_timestamp:
      # Start a WebSocket if no active session for this chat_id
      if chat_id not in active_sessions:
        stop_event = threading.Event()
        thread = threading.Thread(target=start_xrpl_stream, args=(chat_id, stop_event))
        active_sessions[chat_id] = {'stop_event': stop_event}
        asyncio.create_task(stop_session_after_timeout(chat_id, chat_group_data['expire_time']))
        thread.start()
        await update.message.reply_text(
            f"👋 Hello! I will notify you of token purchases over {chat_group_data['THRESHOLD']} XRP."
        )
      else:
        await update.message.reply_text("Warning... Already Started.")
    else:
      await update.message.reply_text("Your subscription has expired. Please re-subscribe to continue receiving notifications. /hash 'hash'")
  else:
    await update.message.reply_text("You are not a registered user. Click /register to register.")

def start_xrpl_stream(chat_id, stop_event):
  """Starts the XRPL stream in a separate thread."""
  asyncio.run(xrpl_stream(chat_id, stop_event))

async def set_issuer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if chat_member.status == 'creator' or user_role[user_id] == True:
      if context.args:
          address = context.args[0]
          if address.startswith('r') and len(address) >= 25:
              NEIRO_ISSUER = address
              getTimestamp.update_env_variable(user_id,"NEIRO_ISSUER", NEIRO_ISSUER)
              user_role[user_id] = False
              await update.message.reply_text(f'😊 Issuer set to: {NEIRO_ISSUER}')
          else:
              await update.message.reply_text(f'Issuer address is incorrect, please check again!!!')
      else:
          await update.message.reply_text('⚠️ Please provide an issuer address.')
    else:
      await update.message.reply_text("📌 You are not the owner.")

async def set_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if chat_member.status == 'creator' or user_role[user_id] == True:
      if context.args:
          currency = context.args[0]
          result = await convert_to_hex_if_string(currency, 20)
          NEIRO_CURRENCY = result
          getTimestamp.update_env_variable(user_id,"NEIRO_CURRENCY", NEIRO_CURRENCY)
          user_role[user_id] = False
          await update.message.reply_text(f'😊 Currency set to: {NEIRO_CURRENCY}')
      else:
          await update.message.reply_text('⚠️ Please provide a currency type.')
    else:
      await update.message.reply_text("📌 You are not the owner.")

async def convert_to_hex_if_string(input_data, length):
  if is_hex(input_data):
    return input_data  # Return as is if it's already a hex string
  else:
    return await string_to_hex(input_data, length)

def is_hex(s):
  return len(s) % 2 == 0 and all(c in '0123456789abcdefABCDEF' for c in s)

async def string_to_hex(input_str, length):
  bytes_data = input_str.encode('utf-8')
  hex_str = bytes_data.hex()
  hex_str = hex_str.ljust(length * 2, '0')
  
  return hex_str.upper()

async def set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  user_id = update.message.from_user.id
  chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
  if chat_member.status == 'creator' or user_role[user_id] == True:
    if context.args and context.args[0]:
      THRESHOLD = context.args[0]
      getTimestamp.update_env_variable(user_id,"THRESHOLD", THRESHOLD)
      user_role[user_id] = False
      await update.message.reply_text(f'😊 Threshold set to {THRESHOLD} XRP.')
    else:
        await update.message.reply_text('⚠️ Please provide a valid numeric threshold.')
  else:
    await update.message.reply_text("📌 You are not the owner.")

async def set_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  user_id = update.message.from_user.id
  chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
  if chat_member.status == 'creator' or user_role[user_id] == True:
    if context.args and context.args[0]:
      EMOJI_ICON = context.args[0]
      getTimestamp.update_env_variable(user_id,"EMOJI_ICON", EMOJI_ICON)
      user_role[user_id] = False
      await update.message.reply_text(f'😊 EMOJI_ICON set to {context.args[0]}.')
    else:
        await update.message.reply_text('⚠️ Please provide a valid numeric EMOJI_ICON.')
  else:
    await update.message.reply_text("📌 You are not the owner.")

async def setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  user_id = update.message.from_user.id
  chat_id = update.message.chat.id
  chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
  user_data = getTimestamp._find_user(user_id=user_id)
  if chat_id in active_sessions and chat_member.status == "creator":
    # del active_sessions[chat_id]
    if chat_member.status == 'creator':
      message_GROUP = (
        f"<b>🔧 TOKEN BUY BOT SETTING 🔧 </b>\n"
      )
      url = f"https://t.me/NeiroBUYAlam_bot?setting={chat_id}"
      keyboard_GROUP = [
        [
          InlineKeyboardButton("➡️ SETTING", url=url),
        ]
      ]
      reply_markup_GROUP = InlineKeyboardMarkup(keyboard_GROUP)
      await update.message.reply_text(text=message_GROUP, reply_markup=reply_markup_GROUP, parse_mode='HTML')
    message_DM = (
      f"<b>🔧 TOKEN BUY BOT SETTING 🔧</b>\n\n"
    )
    keyboard_DM = [
      [
        InlineKeyboardButton("🧑 Issuer 🧑", callback_data=f"set_issuer:{chat_id}"),
        InlineKeyboardButton("📚 Currency 📚", callback_data=f"set_currency:{chat_id}"),
        InlineKeyboardButton("🏊‍♀️ Threshold 🏊‍♀️", callback_data=f"set_threshold:{chat_id}"),
      ],
      [
        InlineKeyboardButton("🥳🌟 Emoji 🌟🥳", callback_data=f"set_emoji:{chat_id}"),
        InlineKeyboardButton("🖼 Photo 🖼", callback_data=f"set_photo:{chat_id}"),
        # InlineKeyboardButton("Hash", callback_data="hash"),
      ]
    ]
    reply_markup_DM = InlineKeyboardMarkup(keyboard_DM)
    await context.bot.send_message(chat_id=user_id, text=message_DM, parse_mode="HTML", reply_markup=reply_markup_DM)
  else:
    await update.message.reply_text("📌 You are not the owner.")

async def route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  query = update.callback_query
  user_id = query.from_user.id
  action, group_id = query.data.split(':', 1)

  user_role[user_id] = True
  user_group[user_id] = group_id
  await query.answer()

  actions_map = {
    "set_issuer": "📣 Please enter the issuer address using [address]",
    "set_currency": "📣 Please enter the currency using [ticker/hex]",
    "set_threshold": "📣 Please enter the threshold using [number]",
    "set_emoji": "📣 Please enter the emoji using [emoji]",
    "set_photo": "📣 Please enter the PHOTO/GIF",
    "hash": "📣 Please enter the Hash"
  }

  if action in actions_map:
    user_state[user_id] = action
    await query.message.reply_text(text=actions_map[action])

async def message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  user_id = update.message.from_user.id
  chat_id = user_group.get(user_id)

  if user_id in user_state and user_role.get(user_id):
    state = user_state[user_id]
    user_input = update.message.text

    if state == 'set_issuer' and valid_issuer(user_input):
      await save_data(update, chat_id, "NEIRO_ISSUER", user_input, user_id, "Issuer")
    elif state == 'set_currency':
      currency = await convert_to_hex_if_string(user_input, 20)
      await save_data(update, chat_id, "NEIRO_CURRENCY", currency, user_id, "Currency")
    elif state == 'set_threshold':
      await save_data(update, chat_id, "THRESHOLD", user_input, user_id, "Threshold")
    elif state == 'set_emoji':
      await save_data(update, chat_id, "EMOJI_ICON", user_input, user_id, "Emoji")
    elif state == 'hash' and await handle_hash(update, context, user_id, chat_id, user_input):
      await update.message.reply_text(f"Session started successfully for hash {user_input}")
    else:
      await update.message.reply_text(f'Invalid input for {state}.')

async def save_data(update, chat_id, key, value, user_id, field_name):
  getTimestamp.update_env_variable(chat_id, key=key, new_value=value)
  user_role[user_id] = False
  await update.message.reply_text(f'😊 {field_name} set to: {value}\nPlease start the bot again with /restart at the Your Group.')

async def handle_hash(update, context, user_id, chat_id, hash_value):
  user_data = getTimestamp._find_user(user_id=user_id)
  if user_data:
    time_info = await getTimestamp.get_time(user_id, chat_id, hash_value)
    await update.message.reply_text(time_info)
    await start(update, context)
    return True
  await update.message.reply_text("You are not the owner!")
  return False

def valid_issuer(address):
  return address.startswith('r') and len(address) >= 25

async def validate_file(file, update: Update):
  if file.file_size > MAX_FILE_SIZE:
    await update.message.reply_text(f"File too large! Maximum allowed size is {MAX_FILE_SIZE / (1024 * 1024)} MB.")
    return False
  return True

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  user_id = update.message.from_user.id
  chat_id = user_group.get(user_id)
  
  media_file, is_gif = await get_media_file(update)
  if not media_file:
    return

  if not await validate_file(media_file, update):
    return

  new_file_path = await save_media_file(media_file)
  media_type = "GIF" if is_gif else "PHOTO"
  getTimestamp.update_env_variable(chat_id, key="MEDIA", new_value=new_file_path)
  getTimestamp.update_env_variable(chat_id, key="TYPE", new_value=is_gif)

  await update.message.reply_text(f"{media_type} uploaded and saved as {new_file_path}!")

async def get_media_file(update: Update):
  """Get the media file and determine if it's a GIF or photo."""
  if update.message.photo:
    return await update.message.photo[-1].get_file(), False
  elif update.message.animation:
    return await update.message.animation.get_file(), True
  elif update.message.document and update.message.document.mime_type in ALLOWED_MIME_TYPES:
    return await update.message.document.get_file(), update.message.document.mime_type == "image/gif"
  await update.message.reply_text("Please upload a valid photo, GIF, or file.")
  return None, None

async def save_media_file(media_file):
  """Save the media file to the designated directory."""
  timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
  file_extension = os.path.splitext(media_file.file_path)[1]
  new_file_name = f"{timestamp}{file_extension}"
  file_path = os.path.join(MEDIA_DIR, new_file_name)
  await media_file.download_to_drive(file_path)
  return file_path

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  user_id = update.message.from_user.id
  chat_id = update.message.chat.id
  current_timestamp = datetime.now(timezone.utc).timestamp()
  chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
  if chat_member.status == "creator":
    user_data = getTimestamp._find_group(CHAT_ID=chat_id)
    if user_data:
      await update.message.reply_text(text="Already registry!")
    else:
      result = getTimestamp.add_or_update_user(user_id=user_id, total_xrp=0, timeLimit=current_timestamp, CHAT_ID=chat_id)
      expire_time = getTimestamp.get_expire_time(chat_id=chat_id)
      await start(update, context)
  else:
    return await update.message.reply_text(text="Registry is only available to groups and Owner.")

async def hash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  user_id = update.message.from_user.id
  chat_id = update.message.chat.id
  user_data = getTimestamp._find_group(CHAT_ID=chat_id)
  chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
  if chat_member.status == "creator":
    if user_data:
      pass
    else:
      return await update.message.reply_text("You should registry")
    if context.args and context.args[0]:
      try:
        time, status = await getTimestamp.get_time(user_id, chat_id, context.args[0])
        await update.message.reply_text(time)
        await start(update, context)
      except Exception as e:
        print(f"An error occurred: {e}")
    else:
      await update.message.reply_text('Please provide a valid Hash.')
  else:
    await update.message.reply_text(text="You are not owner.")

async def restart(update: Update, context):
    chat_id = update.message.chat.id
    chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if chat_id in active_sessions and chat_member.status == "creator":
      if active_sessions.get(chat_id):
        active_sessions[chat_id]['stop_event'].set()
        del active_sessions[chat_id]
      await context.bot.send_message(chat_id=chat_id, text="Restarting WebSocket connection...")
      await start(update, context)

async def stop(update: Update, context):
  chat_id = update.message.chat.id
  chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
  if chat_id in active_sessions and chat_member.status == "creator":
    active_sessions[chat_id]['stop_event'].set()
    del active_sessions[chat_id]
    await context.bot.send_message(chat_id=chat_id, text="Your session has been successfully stopped.")
  else:
    await context.bot.send_message(chat_id=chat_id, text="You are not owner")

async def stop_session_after_timeout(chat_id, expire_time):
  await asyncio.sleep(expire_time - datetime.now(timezone.utc).timestamp())
  
  if chat_id in active_sessions:
    logger.info(f"Stopping session for chat_id: {chat_id} at {datetime.now(timezone.utc)}")
    active_sessions[chat_id]['stop_event'].set()
    del active_sessions[chat_id]    
    await Bot(token=TOKEN).send_message(
        chat_id=chat_id,
        text="Your subscription has expired. Please re-subscribe."
    )
    logger.info(f"Session for chat_id: {chat_id} has been stopped and notified.")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  text = (
    f"✅ <code>/start</code> is used to run the bot.\n"
    f"✅ If you have not registered the bot at this time, you can register the bot using <code>/register</code>.\n"
    f"✅ To register, just type <code>/register</code>.\n"
    f"✅ <code>Registration</code> and <code>setting</code> is only possible in GROUP.\n"
    f"✅ Upon new registration, you will receive a one-day free trial.\n"
    f"✅ Once the trial period is over, you can no longer use the bot and can extend it by paying a monthly fee.\n"
    f"✅ It can be extended by using <code>/hash</code> &lt;hash_value&gt;.\n\n"
    f"✅ You can use <code>/setting</code> to set the token owner, currency, threshold, EMOJI, PHOTO/GIF."
  )

  reply_keyboard = [
    [InlineKeyboardButton("OK", callback_data="delete_msg")]
  ]
  reply_markup = InlineKeyboardMarkup(reply_keyboard)

  await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

def main():
  application = Application.builder().token(TOKEN).build()
  application.add_handler(CommandHandler("start", start))
  application.add_handler(CommandHandler("restart", restart))
  application.add_handler(CommandHandler("stop", stop))
  application.add_handler(CommandHandler("setting", setting))
  application.add_handler(CommandHandler("issuer", set_issuer))
  application.add_handler(CommandHandler("currency", set_currency))
  application.add_handler(CommandHandler("threshold", set_threshold))
  application.add_handler(CommandHandler("emoji", set_emoji))
  application.add_handler(CommandHandler("register", register))
  application.add_handler(CommandHandler("hash", hash))
  application.add_handler(CommandHandler("help", help))
  application.add_handler(CallbackQueryHandler(route))
  application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))
  application.add_handler(MessageHandler(filters.PHOTO | filters.ANIMATION, handle_media))

  application.run_polling()

if __name__ == '__main__':
  main()