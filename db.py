import os
import json
import logging
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

logger = logging.getLogger("BuyBot.DB")

class TokenConfig:
    def __init__(self) -> None:
        self.config_file = 'config.json'
        self.config = {
            "CHAT_IDS": [],  # Changed from single CHAT_ID to list of CHAT_IDS
            "TOKEN_ISSUER": os.getenv('TOKEN_ISSUER', 'r93hE5FNShDdUqazHzNvwsCxL9mSqwyiru'),
            "TOKEN_CURRENCY": os.getenv('TOKEN_CURRENCY', '52504C5300000000000000000000000000000000'),
            "GROUP_SETTINGS": {},  # New field to store per-group settings
            "DEFAULT_SETTINGS": {
                "THRESHOLD": os.getenv('THRESHOLD', '0'),
                "EMOJI_ICON": os.getenv('EMOJI', '💥'),
                "MEDIA": os.getenv('MEDIA_URL', 'https://example.com/buy.gif'),
                "TYPE": True  # True for GIF, False for image
            }
        }
        self.load_config()

    def load_config(self):
        """Load configuration from file or create default."""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as file:
                    saved_config = json.load(file)
                    self.config.update(saved_config)
                    logger.info("Configuration loaded successfully")
            else:
                self.save_config()
                logger.info("Created new configuration file with defaults")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            self.save_config()

    def save_config(self):
        """Save current configuration to file with a backup."""
        try:
            if os.path.exists(self.config_file):
                os.rename(self.config_file, f"{self.config_file}.backup")
            with open(self.config_file, 'w', encoding='utf-8') as file:
                json.dump(self.config, file, indent=2, ensure_ascii=False)
            logger.info("Configuration saved successfully")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

    def add_group(self, chat_id):
        """Add a new group to the configuration."""
        try:
            if chat_id not in self.config["CHAT_IDS"]:
                self.config["CHAT_IDS"].append(chat_id)
                self.config["GROUP_SETTINGS"][str(chat_id)] = self.config["DEFAULT_SETTINGS"].copy()
                self.save_config()
                logger.info(f"Added new group: {chat_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error adding group: {e}")
            return False

    def remove_group(self, chat_id):
        """Remove a group from the configuration."""
        try:
            if chat_id in self.config["CHAT_IDS"]:
                self.config["CHAT_IDS"].remove(chat_id)
                self.config["GROUP_SETTINGS"].pop(str(chat_id), None)
                self.save_config()
                logger.info(f"Removed group: {chat_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error removing group: {e}")
            return False

    def get_group_settings(self, chat_id):
        """Get settings for a specific group."""
        return self.config["GROUP_SETTINGS"].get(str(chat_id), self.config["DEFAULT_SETTINGS"].copy())

    def update_group_settings(self, chat_id, settings):
        """Update settings for a specific group."""
        try:
            chat_id_str = str(chat_id)
            if chat_id_str not in self.config["GROUP_SETTINGS"]:
                self.config["GROUP_SETTINGS"][chat_id_str] = self.config["DEFAULT_SETTINGS"].copy()
            self.config["GROUP_SETTINGS"][chat_id_str].update(settings)
            self.save_config()
            logger.info(f"Updated settings for group: {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating group settings: {e}")
            return False

    def get_config(self):
        """Get current configuration."""
        return self.config

    def update_config(self, key, value):
        """Update a specific configuration value."""
        try:
            if key in self.config:
                self.config[key] = value
                self.save_config()
                logger.info(f"Updated {key} configuration")
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating configuration: {e}")
            return False

    def validate_config(self):
        """Validate the current configuration."""
        required_keys = ["TOKEN_ISSUER", "TOKEN_CURRENCY"]
        missing_keys = [key for key in required_keys if not self.config.get(key)]
        
        if missing_keys:
            logger.error(f"Missing required configuration keys: {missing_keys}")
            return False
        
        if not self.config["TOKEN_ISSUER"].startswith('r'):
            logger.error("Invalid TOKEN_ISSUER format")
            return False
        
        return True

    def get_formatted_config(self):
        """Get a formatted string representation of the configuration."""
        config = self.get_config()
        groups_info = "\n".join([f"- Group {cid}: {self.get_group_settings(cid)}" 
                               for cid in config['CHAT_IDS']])
        return (
            "Current Configuration:\n"
            f"- Token Issuer: {config['TOKEN_ISSUER']}\n"
            f"- Token Currency: {config['TOKEN_CURRENCY']}\n"
            f"- Monitored Groups:\n{groups_info}"
        )