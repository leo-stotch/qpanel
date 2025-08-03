import requests
import logging
from app import db, Instance, TelegramMessage, ActionLog, load_settings
from log_parser import send_telegram_message
from qbt_client import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

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
        response.raise_for_status()  # Raise an exception for bad status codes
        logger.info("Successfully sent Telegram message.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False

def pause_cross_seeded_torrents_for_instance(instance, client):
    """
    Checks for and pauses cross-seeded torrents on a single qBittorrent instance.
    A torrent is considered a duplicate if it has the same name as another torrent
    that is in a paused state.
    """
    settings = load_settings()
    try:
        torrents = client.torrents_info()
        # Get names of all torrents that are in any paused state.
        paused_torrent_names = {t.name for t in torrents if 'paused' in t.state.lower()}

        for torrent in torrents:
            # If a torrent has the same name as a paused one, and is not itself paused, pause it.
            if torrent.name in paused_torrent_names and 'paused' not in torrent.state.lower():
                client.torrents_pause(torrent_hashes=torrent.hash)

                # Get tracker and format message first
                http_tracker = next((t.url for t in torrent.trackers if t.url.startswith('http')), 'N/A')
                message_text = f"Paused cross-seeded torrent on {instance.name}: {torrent.name} ({http_tracker})"
                
                # Now log, create db entries, and notify
                logging.info(message_text)
                
                action = ActionLog(
                    instance_id=instance.id,
                    action="Paused cross-seeded torrent",
                    details=f"{torrent.name} ({http_tracker})"
                )
                db.session.add(action)
                
                if settings.get('telegram_notification_enabled'):
                    if send_telegram_message(settings['telegram_bot_token'], settings['telegram_chat_id'], message_text, parse_mode='HTML'):
                        new_message = TelegramMessage(message=message_text)
                        db.session.add(new_message)

        db.session.commit()
    except Exception as e:
        logging.error(f"Error checking for cross-seeded torrents on {instance.name}: {e}")

def pause_cross_seeded_torrents_job():
    """
    Scheduled job to check for and pause cross-seeded torrents across all instances.
    """
    from app import app
    with app.app_context():
        instances = Instance.query.filter_by(pause_cross_seeded_torrents=True).all()
        for instance in instances:
            client = get_client(instance)
            if client:
                try:
                    client.auth_log_in()
                    pause_cross_seeded_torrents_for_instance(instance, client)
                except Exception as e:
                    logging.error(f"Failed to process instance {instance.name} for cross-seeded torrents: {e}")
            else:
                logging.warning(f"Could not connect to instance {instance.name} to check for cross-seeded torrents.")
