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
            "CHAT_ID": None,
            "TOKEN_ISSUER": os.getenv('TOKEN_ISSUER', 'r93hE5FNShDdUqazHzNvwsCxL9mSqwyiru'),
            "TOKEN_CURRENCY": os.getenv('TOKEN_CURRENCY', '52504C5300000000000000000000000000000000'),
            "THRESHOLD": os.getenv('THRESHOLD', '0'),
            "EMOJI_ICON": os.getenv('EMOJI', '🚀'),
            "MEDIA": os.getenv('MEDIA_URL', 'https://example.com/buy.gif'),
            "TYPE": True  # True for GIF, False for image
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
        """Save current configuration to file."""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as file:
                json.dump(self.config, file, indent=2, ensure_ascii=False)
            logger.info("Configuration saved successfully")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

    def update_config(self, key, value):
        """Update a specific configuration value."""
        try:
            if key in self.config:
                self.config[key] = value
                self.save_config()
                logger.info(f"Updated {key} configuration")
                return True
            else:
                logger.warning(f"Attempted to update invalid config key: {key}")
                return False
        except Exception as e:
            logger.error(f"Error updating configuration: {e}")
            return False

    def get_config(self):
        """Get current configuration."""
        return self.config

    def reset_config(self):
        """Reset configuration to default values."""
        try:
            self.config = {
                "CHAT_ID": None,
                "TOKEN_ISSUER": os.getenv('TOKEN_ISSUER', 'r93hE5FNShDdUqazHzNvwsCxL9mSqwyiru'),
                "TOKEN_CURRENCY": os.getenv('TOKEN_CURRENCY', '52504C5300000000000000000000000000000000'),
                "THRESHOLD": os.getenv('THRESHOLD', '0'),
                "EMOJI_ICON": os.getenv('EMOJI', '🚀'),
                "MEDIA": os.getenv('MEDIA_URL', 'https://example.com/buy.gif'),
                "TYPE": True
            }
            self.save_config()
            logger.info("Configuration reset to defaults")
            return True
        except Exception as e:
            logger.error(f"Error resetting configuration: {e}")
            return False

    def update_multiple(self, updates):
        """Update multiple configuration values at once."""
        try:
            for key, value in updates.items():
                if key in self.config:
                    self.config[key] = value
                else:
                    logger.warning(f"Skipped invalid config key: {key}")
            self.save_config()
            logger.info("Updated multiple configuration values")
            return True
        except Exception as e:
            logger.error(f"Error updating multiple configurations: {e}")
            return False

    def validate_config(self):
        """Validate the current configuration."""
        required_keys = ["TOKEN_ISSUER", "TOKEN_CURRENCY", "THRESHOLD"]
        missing_keys = [key for key in required_keys if not self.config.get(key)]
        
        if missing_keys:
            logger.error(f"Missing required configuration keys: {missing_keys}")
            return False
        
        # Validate TOKEN_ISSUER format (should start with 'r')
        if not self.config["TOKEN_ISSUER"].startswith('r'):
            logger.error("Invalid TOKEN_ISSUER format")
            return False
        
        # Validate THRESHOLD is a positive number
        try:
            threshold = float(self.config["THRESHOLD"])
            if threshold <= 0:
                logger.error("THRESHOLD must be positive")
                return False
        except ValueError:
            logger.error("THRESHOLD must be a valid number")
            return False
        
        return True

    def get_formatted_config(self):
        """Get a formatted string representation of the configuration."""
        config = self.get_config()
        return (
            "Current Configuration:\n"
            f"- Chat ID: {config['CHAT_ID'] or 'Not set'}\n"
            f"- Token Issuer: {config['TOKEN_ISSUER']}\n"
            f"- Currency Code: {config['TOKEN_CURRENCY']}\n"
            f"- Threshold: {config['THRESHOLD']} XRP\n"
            f"- Emoji: {config['EMOJI_ICON']}\n"
            f"- Media Type: {'GIF' if config['TYPE'] else 'Photo'}\n"
            f"- Media URL: {config['MEDIA']}"
        )