import logging
from app import db, Instance, TelegramMessage, ActionLog, load_settings
from notifications import send_notification
from qbt_client import get_client, get_all_torrents

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

logger = logging.getLogger(__name__)


def pause_cross_seeded_torrents_for_instance(instance, client, torrents=None):
    """
    Checks for and pauses cross-seeded torrents on a single qBittorrent instance.
    A torrent is considered a duplicate if it has the same name as another torrent
    that is in a paused state.
    
    Args:
        instance: The Instance object
        client: The qBittorrent client
        torrents: Optional pre-fetched torrent list (for memory optimization)
    """
    settings = load_settings()
    try:
        # Use pre-fetched torrents if provided, otherwise fetch them
        if torrents is None:
            torrents = get_all_torrents(client)
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
                
                if send_notification(message_text, settings, parse_mode='HTML'):
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
