import re
import httpx
import logging
from datetime import datetime
from app import db, Instance, TelegramMessage, ActionLog, load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def send_telegram_message(bot_token, chat_id, message, parse_mode='HTML'):
    """Sends a message to a Telegram bot."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    params = {"chat_id": chat_id, "text": message, "parse_mode": parse_mode}
    try:
        response = httpx.post(url, params=params)
        response.raise_for_status()
        logging.info("Message sent successfully!")
        return True
    except httpx.HTTPError as e:
        logging.error(f"Failed to send message: {e}")
        return False

def epoch_to_human_readable(epoch_timestamp):
    """Converts an epoch timestamp to a human-readable string."""
    try:
        datetime_obj = datetime.fromtimestamp(epoch_timestamp)
        return datetime_obj.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logging.error(f"Error converting epoch to human-readable timestamp: {e}")
        return "N/A"

def process_logs_for_instance(instance, client):
    """Processes logs for a single instance."""
    settings = load_settings()

    try:
        logs = client.log_main(last_known_id=instance.last_processed_log_id)
        last_id = instance.last_processed_log_id

        for log in logs:
            if "Removed torrent" in log['message']:
                readable_timestamp = epoch_to_human_readable(log['timestamp'])
                torrent_info = "n/a"
                pattern = r'Torrent:\s*"(.*)"'
                matches = re.search(pattern, log['message'])
                if matches:
                    torrent_info = matches.group(1)
                
                action = ActionLog(
                    instance_id=instance.id,
                    action="Removed torrent (detected in log)",
                    details=torrent_info
                )
                db.session.add(action)

                if settings.get('telegram_notification_enabled'):
                    message_text = f"➖ {torrent_info} ({readable_timestamp}) on {instance.name}"
                    if send_telegram_message(settings['telegram_bot_token'], settings['telegram_chat_id'], message_text):
                        new_message = TelegramMessage(message=message_text)
                        db.session.add(new_message)
            
            last_id = log['id']

        if last_id != instance.last_processed_log_id:
            instance.last_processed_log_id = last_id
            db.session.commit()

    except Exception as e:
        logging.error(f"Error processing logs for instance {instance.name}: {e}")

def log_parsing_job():
    """Job to be scheduled for parsing logs from all enabled instances."""
    from app import app
    with app.app_context():
        instances = Instance.query.filter_by(look_for_deleted_torrents=True).all()
        for instance in instances:
            from qbt_client import get_client
            client = get_client(instance)
            if client:
                try:
                    client.auth_log_in()
                    process_logs_for_instance(instance, client)
                except Exception as e:
                    logging.error(f"Failed to process logs for {instance.name}: {e}")
            else:
                logging.warning(f"Could not connect to instance {instance.name} to parse logs.") 