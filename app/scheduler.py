from app import app, db, Instance, ActionLog
from qbt_client import get_client
from flask import flash
import logging
import traceback
from app import load_settings
import os
from cross_seed_checker import send_telegram_message
from app import TelegramMessage
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def apply_rules_for_instance(instance, client, torrents):
    """
    Applies rules to a single instance.
    """
    logging.info(f"Checking rules for instance: {instance.name}")
    logger.info(f"Found {len(torrents)} torrents in '{instance.name}'.")

    for torrent in torrents:
        for rule in instance.rules:
            matched = False
            # Robustly check for tag match
            if rule.condition_type == 'tag':
                current_tags = [t.strip() for t in torrent.tags.split(',') if t.strip()]
                if rule.condition_value in current_tags:
                    matched = True
            
            # Check for tracker match
            elif rule.condition_type == 'tracker':
                for tracker in torrent.trackers:
                    if rule.condition_value in tracker.url:
                        matched = True
                        break
            
            if matched:
                # Check if the rule is already applied by comparing limits.
                is_already_applied = True
                if rule.share_limit_ratio is not None and torrent.ratio_limit != rule.share_limit_ratio:
                    is_already_applied = False
                if rule.share_limit_time is not None and torrent.seeding_time_limit != rule.share_limit_time:
                    is_already_applied = False
                if rule.max_upload_speed is not None and torrent.up_limit != rule.max_upload_speed:
                    is_already_applied = False
                if rule.max_download_speed is not None and torrent.dl_limit != rule.max_download_speed:
                    is_already_applied = False

                if is_already_applied:
                    logger.info(f"Torrent '{torrent.name}' already conforms to rule '{rule.name}'. Skipping.")
                else:
                    logger.info(f"Torrent '{torrent.name}' matched rule '{rule.name}'. Applying limits.")

                    # Set share limits only if they are defined in the rule
                    if rule.share_limit_ratio is not None or rule.share_limit_time is not None:
                        client.torrents_set_share_limits(
                            torrent_hashes=torrent.hash,
                            ratio_limit=rule.share_limit_ratio if rule.share_limit_ratio is not None else -2,
                            seeding_time_limit=rule.share_limit_time if rule.share_limit_time is not None else -2,
                            inactive_seeding_time_limit=-2
                        )
                    
                    # Set speed limits
                    if rule.max_upload_speed is not None:
                        client.torrents_set_upload_limit(limit=rule.max_upload_speed, torrent_hashes=torrent.hash)
                    if rule.max_download_speed is not None:
                        client.torrents_set_download_limit(limit=rule.max_download_speed, torrent_hashes=torrent.hash)

                    # Log the action
                    details_parts = []
                    if rule.share_limit_ratio is not None:
                        details_parts.append(f"Share Ratio: {rule.share_limit_ratio}")
                    if rule.share_limit_time is not None:
                        details_parts.append(f"Seeding Time: {rule.share_limit_time}m")
                    if rule.max_upload_speed is not None:
                        details_parts.append(f"Up: {rule.max_upload_speed // 1024}KiB/s")
                    if rule.max_download_speed is not None:
                        details_parts.append(f"Down: {rule.max_download_speed // 1024}KiB/s")
                    
                    details = ", ".join(details_parts)
                    log_entry = ActionLog(instance_id=instance.id, action=f"Applied rule '{rule.name}' to '{torrent.name}'", details=details)
                    db.session.add(log_entry)
                
                # Once a rule is matched and applied, we can stop checking other rules for this torrent.
                break

def tag_torrents_with_no_hard_links(instance, client, torrents):
    """Scheduled job to tag torrents with no hard links."""
    if not instance.qbt_download_dir or not instance.mapped_download_dir:
        logger.warning(f"Skipping no hard link check for instance '{instance.name}' because path mapping is not configured.")
        return

    try:
        logger.info(f"Checking for torrents with no hard links in '{instance.name}'.")
        
        for torrent in torrents:
            has_hard_link = False
            # Use torrent's save_path, which is what qBittorrent reports
            torrent_save_path = torrent.save_path
            
            # Get the list of files for the torrent
            files = client.torrents_files(torrent_hash=torrent.hash)

            for file_info in files:
                # Construct the full path as qBittorrent sees it
                qbt_full_path = os.path.join(torrent_save_path, file_info.name)
                
                # Translate to the path accessible by qPanel
                if qbt_full_path.startswith(instance.qbt_download_dir):
                    mapped_path = os.path.join(instance.mapped_download_dir, os.path.relpath(qbt_full_path, instance.qbt_download_dir))
                    
                    try:
                        if os.path.exists(mapped_path) and os.stat(mapped_path).st_nlink > 1:
                            has_hard_link = True
                            break  # A single hard-linked file is enough
                    except FileNotFoundError:
                        logger.warning(f"File not found: {mapped_path}. Skipping hard link check for this file.")

            # Robustly check for and manage the 'noHL' tag
            current_tags = [t.strip() for t in torrent.tags.split(',') if t.strip()]
            has_noHL_tag = 'noHL' in current_tags

            if has_hard_link:
                if has_noHL_tag:
                    client.torrents_remove_tags(tags='noHL', torrent_hashes=torrent.hash)
                    logger.info(f"Removed 'noHL' tag from '{torrent.name}' as it now has hard links.")
            else:
                if not has_noHL_tag:
                    if torrent.completion_on > 0:
                        completion_time = datetime.fromtimestamp(torrent.completion_on)
                        if datetime.now() - completion_time > timedelta(hours=1):
                            client.torrents_add_tags(tags='noHL', torrent_hashes=torrent.hash)
                            logger.info(f"Added 'noHL' tag to '{torrent.name}' as it has no hard links and was completed over an hour ago.")
                            
                            # Log action
                            log_entry = ActionLog(
                                instance_id=instance.id,
                                action="Tagged with noHL",
                                details=f"Torrent '{torrent.name}' has no hard links and was completed over an hour ago."
                            )
                            db.session.add(log_entry)

                            # Send Telegram notification
                            settings = load_settings()
                            if settings.get('telegram_notification_enabled'):
                                message = f"Torrent '{torrent.name}' on '{instance.name}' was tagged with 'noHL' because it has no hard links and was completed over an hour ago."
                                send_telegram_message(settings.get('telegram_bot_token'), settings.get('telegram_chat_id'), message, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Failed to check for no hard links for {instance.name}: {e}")

def tag_unregistered_torrents_for_instance(instance, client, torrents):
    """Tags torrents with 'unregistered' if their tracker status indicates they are no longer registered."""
    UNREGISTERED_STATUS_SUBSTRINGS = [
        "unregistered",
        "Torrent has been deleted",
        "Torrent not registered with this tracker",
        "Torrent is not authorized for use on this tracker",
        "This torrent does not exist"
    ]
    
    for torrent in torrents:
        is_unregistered = False
        offending_msg = ""
        for tracker in torrent.trackers:
            for status_substring in UNREGISTERED_STATUS_SUBSTRINGS:
                if status_substring.lower() in tracker.msg.lower():
                    is_unregistered = True
                    offending_msg = tracker.msg
                    break
            if is_unregistered:
                break

        # Robustly check for and manage the 'unregistered' tag
        current_tags = [t.strip() for t in torrent.tags.split(',') if t.strip()]
        has_unregistered_tag = 'unregistered' in current_tags

        if is_unregistered:
            if not has_unregistered_tag:
                client.torrents_add_tags(tags='unregistered', torrent_hashes=torrent.hash)
                logger.info(f"Tagged '{torrent.name}' as unregistered on {instance.name}.")
                log_entry = ActionLog(instance_id=instance.id, action=f"Tagged '{torrent.name}' as unregistered", details=f"Tracker status: {offending_msg}")
                db.session.add(log_entry)

                # Send Telegram notification
                settings = load_settings()
                if settings.get('telegram_notification_enabled'):
                    message = f"Tagged '{torrent.name}' as unregistered on '{instance.name}'.\nTracker status: {offending_msg}"
                    if send_telegram_message(settings.get('telegram_bot_token'), settings.get('telegram_chat_id'), message, parse_mode='HTML'):
                        new_message = TelegramMessage(message=message)
                        db.session.add(new_message)
        else:
            if has_unregistered_tag:
                client.torrents_remove_tags(tags='unregistered', torrent_hashes=torrent.hash)
                logger.info(f"Removed 'unregistered' tag from '{torrent.name}' on {instance.name}.")
                log_entry = ActionLog(instance_id=instance.id, action=f"Removed 'unregistered' tag from '{torrent.name}'", details="Tracker status is now normal.")
                db.session.add(log_entry)

def apply_rules_job():
    """Scheduled job to apply all defined rules to all instances."""
    from app import app
    with app.app_context():
        instances = Instance.query.all()
        for instance in instances:
            try:
                client = get_client(instance)
                torrents = client.torrents_info()
                apply_rules_for_instance(instance, client, torrents)
                db.session.commit()
            except Exception as e:
                logging.error(f"An unexpected error occurred in apply_rules_job for instance '{instance.name}': {e}")
                db.session.rollback()

def tag_unregistered_torrents_job():
    """Scheduled job to tag unregistered torrents."""
    from app import app
    with app.app_context():
        instances = Instance.query.filter_by(tag_unregistered_torrents=True).all()
        for instance in instances:
            try:
                client = get_client(instance)
                torrents = client.torrents_info()
                tag_unregistered_torrents_for_instance(instance, client, torrents)
                db.session.commit()
            except Exception as e:
                logging.error(f"An unexpected error occurred in tag_unregistered_torrents_job for instance '{instance.name}': {e}")
                db.session.rollback()

def tag_torrents_with_no_hard_links_job():
    """Scheduled job to tag torrents with no hard links."""
    from app import app
    with app.app_context():
        instances = Instance.query.filter_by(tag_nohardlinks=True).all()
        for instance in instances:
            try:
                client = get_client(instance)
                torrents = client.torrents_info()
                tag_torrents_with_no_hard_links(instance, client, torrents)
                db.session.commit()
            except Exception as e:
                logging.error(f"An unexpected error occurred in tag_torrents_with_no_hard_links_job for instance '{instance.name}': {e}")
                db.session.rollback()

def monitor_paused_up_torrents_job():
    """Scheduled job to monitor for torrents in pausedUP state."""
    from app import app
    with app.app_context():
        instances = Instance.query.filter_by(monitor_paused_up=True).all()
        for instance in instances:
            try:
                client = get_client(instance)
                all_torrents = client.torrents_info()
                paused_up_torrents = [t for t in all_torrents if t.state == 'pausedUP']

                if paused_up_torrents:
                    # Check against previously logged torrents to avoid repeat notifications
                    already_logged_hashes = {
                        log.details.split("'")[1] for log in ActionLog.query.filter(
                            ActionLog.instance_id == instance.id,
                            ActionLog.action == "pausedUP torrents detected"
                        ).all() if log.details
                    }

                    newly_paused_up_torrents = [t for t in paused_up_torrents if t.hash not in already_logged_hashes]

                    if newly_paused_up_torrents:
                        torrent_links = [f"<a href='{instance.host}/#/torrent/{t.hash}'>{t.name}</a>" for t in newly_paused_up_torrents]
                        logger.info(f"Found {len(torrent_links)} new pausedUP torrents on {instance.name}: {', '.join(torrent_links)}")
                        
                        # Log action
                        for torrent in newly_paused_up_torrents:
                            log_entry = ActionLog(
                                instance_id=instance.id,
                                action="pausedUP torrents detected",
                                details=f"Torrent: '{torrent.hash}'" # Log hash to prevent duplicates
                            )
                            db.session.add(log_entry)
                        db.session.commit()

                        # Send Telegram notification
                        settings = load_settings()
                        if settings.get('telegram_notification_enabled'):
                            message = f"PausedUP torrents detected on '{instance.name}':\n" + "\n".join(torrent_links)
                            send_telegram_message(settings.get('telegram_bot_token'), settings.get('telegram_chat_id'), message, parse_mode='HTML')

            except Exception as e:
                logging.error(f"An unexpected error occurred in monitor_paused_up_torrents_job for instance '{instance.name}': {e}")
                db.session.rollback()
