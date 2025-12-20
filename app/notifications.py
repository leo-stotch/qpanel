"""
Notification utilities for qPanel.
Handles sending notifications to Telegram and Discord.
"""

import requests
import logging

logger = logging.getLogger(__name__)


def send_telegram_message(bot_token, chat_id, message, parse_mode=None):
    """
    Sends a message to a specified Telegram chat.
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram bot token or chat ID is not configured.")
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'disable_web_page_preview': True
    }
    if parse_mode:
        payload['parse_mode'] = parse_mode
        
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Successfully sent Telegram message.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def send_discord_message(webhook_url, message):
    """
    Sends a message to a Discord channel via webhook.
    """
    if not webhook_url:
        logger.warning("Discord webhook URL is not configured.")
        return False
    
    payload = {
        'content': message
    }
        
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Successfully sent Discord message.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Discord message: {e}")
        return False


def send_notification(message, settings=None, parse_mode=None):
    """
    Sends notification to all enabled channels (Telegram and/or Discord).
    Returns True if at least one notification was sent successfully.
    """
    if settings is None:
        from app import load_settings
        settings = load_settings()
    
    success = False
    
    # Send Telegram notification if enabled
    if settings.get('telegram_notification_enabled'):
        if send_telegram_message(
            settings.get('telegram_bot_token'),
            settings.get('telegram_chat_id'),
            message,
            parse_mode=parse_mode
        ):
            success = True
    
    # Send Discord notification if enabled
    if settings.get('discord_notification_enabled'):
        if send_discord_message(settings.get('discord_webhook_url'), message):
            success = True
    
    return success


