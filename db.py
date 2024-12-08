import xrpl
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.models.requests import Tx
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os
import json
load_dotenv()

XRPL_URL = os.getenv('XRPL_URL')
USER_CONFIG_FILE = os.getenv('USER_CONFIG_FILE')
HASH = os.getenv('HASH')

MIN_MONTHLY_FEE = float(os.getenv("MIN_MONTHLY_FEE"))  # Minimum monthly fee in XRP

class GetTimestamp:
  def __init__(self) -> None:
    self.client = AsyncJsonRpcClient(XRPL_URL)
    self.json_file_path = USER_CONFIG_FILE
    self.hash_file_path = HASH
    
    # Load user data from the JSON file
    try:
      with open(self.json_file_path, 'r', encoding='utf-8') as file:
        self.data = json.load(file)
      with open(self.hash_file_path, 'r', encoding='utf-8') as hash_file:
        self.hash_data = json.load(hash_file)
      print(f"Loaded data from {self.json_file_path} and {self.hash_file_path}")
    except (FileNotFoundError, json.JSONDecodeError):
      self.data = []  # Start with empty data if file is not found or corrupted
      self.hash_data = []
      # Create a new file if it doesn't exist
      self.save_hash_data()
      print(f"No data file found. Created new file {self.json_file_path} and {self.hash_file_path}")

  def _save_to_file(self):
    """Save the current data back to the JSON file."""
    with open(self.json_file_path, 'w', encoding="utf-8") as file:
        # ensure_ascii=False ensures that emojis and other non-ASCII characters are saved properly
        json.dump(self.data, file, indent=4, ensure_ascii=False)

  def save_hash_data(self):
    """Save the hash data to the file."""
    with open(self.hash_file_path, 'w', encoding='utf-8') as hash_file:
      json.dump(self.hash_data, hash_file)
    print(f"Saved data to {self.hash_file_path}")
  
  def _find_user(self, user_id):
    """Find user data by user_id."""
    with open(self.json_file_path, 'r', encoding='utf-8') as file:
      self.data = json.load(file)
    # return next((user for user in self.data if user['user_id'] == user_id), None)
    return [user for user in self.data if user['user_id'] == user_id]

  def _find_group(self, CHAT_ID):
    with open(self.json_file_path, 'r', encoding='utf-8') as file:
      self.data = json.load(file)
    return next((user for user in self.data if user['CHAT_ID'] == CHAT_ID), None)
  
  def add_or_update_user(self, user_id, total_xrp, timeLimit, CHAT_ID = 0):
    user_data = self._find_user(user_id)
    chat_group_data = self._find_group(CHAT_ID=CHAT_ID)
    # one_day_later_timestamp = (datetime.fromtimestamp(timeLimit, timezone.utc) + timedelta(minutes=2)).timestamp()
    one_day_later_timestamp = (datetime.fromtimestamp(timeLimit, timezone.utc) + timedelta(days=1)).timestamp()

    if chat_group_data:
      if (chat_group_data['total_xrp'] + total_xrp) < MIN_MONTHLY_FEE:
        chat_group_data['total_xrp'] += total_xrp
        self._save_to_file()
        return f"Not enough monthly fee {(MIN_MONTHLY_FEE - user_data[0]['total_xrp'])}"
      else:
        total_xrp_collected = chat_group_data['total_xrp'] + total_xrp
        monthly = total_xrp_collected // MIN_MONTHLY_FEE
        total_amount = total_xrp_collected % MIN_MONTHLY_FEE
        expire_time = (datetime.fromtimestamp(timeLimit, timezone.utc) + timedelta(days=monthly * 30))
        chat_group_data['total_xrp'] = total_amount
        chat_group_data['expire_time'] = expire_time.timestamp()
        self._save_to_file()
        return f"You can use for {int(monthly)} months. Last day is {expire_time.strftime('%Y-%m-%d')}. Have a good day."
    else:
      issuer = "rneirorRCs765VoFgPkocb7rr4BzBoHABs"
      currency = "4E4549524F000000000000000000000000000000"
      threshold="50"
      emoji="🐶"
      new_user = {
          "user_id": user_id,
          "NEIRO_ISSUER": issuer,
          "NEIRO_CURRENCY": currency,
          "THRESHOLD": threshold,
          "EMOJI_ICON": emoji,
          "MEDIA": "https://neirox.fun/buy.gif",
          "TYPE": True,
          "CHAT_ID": CHAT_ID,
          "total_xrp": total_xrp,
          "expire_time": one_day_later_timestamp,
      }
      self.data.append(new_user)
      self._save_to_file()
      return "You can use for one day."

  async def get_time(self, user_id, chat_id, tx_hash: str):
    # Fetch transaction details
    tx_request = Tx(transaction=tx_hash)
    user_data = self._find_user(user_id)
    with open(self.hash_file_path, 'r', encoding='utf-8') as hash_file:
      self.hash_data = json.load(hash_file)
    print(tx_hash)
    if tx_hash in self.hash_data:
      return "Hash already registed!", False
    else:
      tx_response = await self.client.request(tx_request)
      tx_data = tx_response.result
      # Extract the close_time safely
      # try:
      BOT_OWNER = os.getenv('BOT_OWNER')
      if tx_data['tx_json']['Destination'] == BOT_OWNER:
        DeliverMax = float(tx_data['tx_json']['DeliverMax'])/1000000
        transaction_time = int(tx_data['tx_json']['date'])
        ripple_epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
        given_time = ripple_epoch + timedelta(seconds=transaction_time)
        current_time = datetime.now(timezone.utc)
        time_difference = current_time - given_time
        # Convert the difference to days
        days_ago = time_difference.days
        if days_ago > 100:
          return "Period Passed", False
        else:
          self.hash_data.append(tx_hash)
          self.save_hash_data()
          return self.add_or_update_user(user_id, DeliverMax, given_time.timestamp(), chat_id), True
      else:
        return "The destination address is incorrect. Please check again.", False
      # except:
      #   return "Hash Error!!!"
      
  def update_env_variable(self, chat_id, key, new_value):
    try:
      user_data = self._find_group(int(chat_id))
      if user_data:
        user_data[key] = new_value
        self._save_to_file()
    except Exception as e:
      print(f"An error occurred: {e}")
      
  def get_expire_time(self, chat_id):
    user_data = self._find_group(chat_id)
    if user_data:
      return user_data
