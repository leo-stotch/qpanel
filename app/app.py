from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import uuid
import time
import json
import os
from urllib.parse import urlparse
from qbt_client import get_client, get_all_torrents
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta

# --- PATHS ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')

# --- SETTINGS ---
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
DEFAULT_SETTINGS = {
    'scheduler_interval_minutes': 10,
    'cache_duration_minutes': 10,
    'telegram_bot_token': '',
    'telegram_chat_id': '',
    'telegram_notification_enabled': False,
    'discord_webhook_url': '',
    'discord_notification_enabled': False
}

def load_settings():
    try:
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_SETTINGS

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

# Ensure the data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(DATA_DIR, 'qpanel.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Association table for the many-to-many relationship between Instance and Rule
instance_rules = db.Table('instance_rules',
    db.Column('instance_id', db.Integer, db.ForeignKey('instance.id'), primary_key=True),
    db.Column('rule_id', db.Integer, db.ForeignKey('rule.id'), primary_key=True)
)

class Instance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    host = db.Column(db.String(200), nullable=False)
    username = db.Column(db.String(100))
    password = db.Column(db.String(100))
    rules = db.relationship('Rule', secondary=instance_rules, lazy='subquery',
        backref=db.backref('instances', lazy=True))
    logs = db.relationship('ActionLog', backref='instance', lazy=True, cascade="all, delete-orphan")
    qbt_download_dir = db.Column(db.String(500))
    mapped_download_dir = db.Column(db.String(500))
    tag_nohardlinks = db.Column(db.Boolean, default=False)
    pause_cross_seeded_torrents = db.Column(db.Boolean, default=False)
    tag_unregistered_torrents = db.Column(db.Boolean, default=False)
    orphaned_scan_enabled = db.Column(db.Boolean, default=False)
    orphaned_min_age_days = db.Column(db.Integer, default=7)
    orphaned_ignore_patterns = db.Column(db.Text, default='')
    orphaned_files = db.relationship('OrphanedFile', backref='instance', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Instance {self.name}>'

class Rule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    condition_type = db.Column(db.String(50), nullable=False)  # 'tracker' or 'tag'
    condition_value = db.Column(db.String(255), nullable=False)
    share_limit_ratio = db.Column(db.Float)
    share_limit_time = db.Column(db.Integer)  # in minutes
    max_upload_speed = db.Column(db.Integer)  # in bytes/s
    max_download_speed = db.Column(db.Integer)  # in bytes/s

class TelegramMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    message = db.Column(db.Text, nullable=False)

class ActionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    instance_id = db.Column(db.Integer, db.ForeignKey('instance.id'), nullable=False)
    action = db.Column(db.String(255), nullable=False)
    details = db.Column(db.Text, nullable=True)

class OrphanedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    instance_id = db.Column(db.Integer, db.ForeignKey('instance.id'), nullable=False)
    file_path = db.Column(db.Text, nullable=False)
    file_size = db.Column(db.BigInteger, nullable=True)
    file_mtime = db.Column(db.DateTime, nullable=True)

@app.route('/')
def index():
    instances = Instance.query.all()
    rules = Rule.query.all()
    instance_statuses = {}
    for instance in instances:
        client = get_client(instance)
        if client:
            try:
                version = client.app_version()
                instance_statuses[instance.id] = {'status': 'Online', 'version': version}
            except Exception as e:
                instance_statuses[instance.id] = {'status': 'Offline', 'error': f'An unexpected error occurred: {e}'}

        else:
            instance_statuses[instance.id] = {'status': 'Offline', 'error': 'Could not connect. Check logs for details.'}

    logs = ActionLog.query.order_by(ActionLog.timestamp.desc()).limit(20).all()

    return render_template('index.html', 
                           instances=instances, 
                           instance_statuses=instance_statuses, 
                           logs=logs,
                           rules=rules)

@app.route('/instances/<instance_id>/assign-rule', methods=['POST'])
def assign_rule(instance_id):
    instance = Instance.query.get_or_404(instance_id)
    rule_id = request.form.get('rule_id')

    if not rule_id:
        flash('Please select a rule to assign.', 'warning')
        return redirect(url_for('index'))

    rule = Rule.query.get_or_404(rule_id)
    
    if rule not in instance.rules:
        instance.rules.append(rule)
        db.session.commit()
        flash(f"Rule '{rule.name}' assigned to instance '{instance.name}' successfully!", 'success')
    else:
        flash(f"Rule '{rule.name}' is already assigned to instance '{instance.name}'.", 'info')
        
    return redirect(url_for('index'))

@app.route('/instances/<instance_id>/remove-rule/<rule_id>', methods=['POST'])
def remove_rule_from_instance(instance_id, rule_id):
    instance = Instance.query.get_or_404(instance_id)
    rule = Rule.query.get_or_404(rule_id)
    
    if rule in instance.rules:
        instance.rules.remove(rule)
        db.session.commit()
        flash(f"Rule '{rule.name}' removed from instance '{instance.name}' successfully!", 'success')
    else:
        flash(f"Rule '{rule.name}' was not assigned to instance '{instance.name}'.", 'info')

    return redirect(url_for('index'))

@app.route('/logs/clear', methods=['POST'])
def clear_logs():
    try:
        num_rows_deleted = db.session.query(ActionLog).delete()
        db.session.commit()
        flash(f'Successfully cleared {num_rows_deleted} log entries.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error clearing logs: {e}', 'danger')
    return redirect(url_for('index'))

@app.route('/telegram/clear', methods=['POST'])
def clear_telegram_messages():
    try:
        num_rows_deleted = db.session.query(TelegramMessage).delete()
        db.session.commit()
        flash(f'Successfully cleared {num_rows_deleted} Telegram messages.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error clearing Telegram messages: {e}', 'danger')
    return redirect(url_for('index'))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        current_settings = load_settings()
        new_settings = {
            'scheduler_interval_minutes': int(request.form['scheduler_interval_minutes']),
            'cache_duration_minutes': int(request.form['cache_duration_minutes']),
            'telegram_bot_token': request.form['telegram_bot_token'] if request.form['telegram_bot_token'] else current_settings.get('telegram_bot_token', ''),
            'telegram_chat_id': request.form['telegram_chat_id'],
            'telegram_notification_enabled': request.form.get('telegram_notification_enabled') == 'on',
            'discord_webhook_url': request.form['discord_webhook_url'] if request.form.get('discord_webhook_url') else current_settings.get('discord_webhook_url', ''),
            'discord_notification_enabled': request.form.get('discord_notification_enabled') == 'on'
        }
        save_settings(new_settings)
        flash('Settings saved successfully! Please restart the application for the new interval to take effect.', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html', settings=load_settings())

def group_orphaned_files_by_directory(orphaned_files):
    """
    Group orphaned files by their common parent directories.
    Returns a structure like:
    {
        'instance_id': {
            'instance': Instance,
            'groups': [
                {
                    'directory': '/downloads/Movie.2022...',
                    'files': [OrphanedFile, ...],
                    'total_size': 12345678
                },
                ...
            ],
            'ungrouped': [OrphanedFile, ...]  # Files that don't share a parent with others
        }
    }
    """
    from collections import defaultdict
    
    # First, organize files by instance
    files_by_instance = defaultdict(list)
    instances_map = {}
    for f in orphaned_files:
        files_by_instance[f.instance_id].append(f)
        instances_map[f.instance_id] = f.instance
    
    result = {}
    
    for instance_id, files in files_by_instance.items():
        # Group files by their parent directories
        files_by_parent = defaultdict(list)
        for f in files:
            # Get the parent directory (one level up from the file)
            parent = os.path.dirname(f.file_path)
            files_by_parent[parent].append(f)
        
        # Find common ancestors for directories that share a parent
        # We want to find the "release folder" level grouping
        def find_common_grouping_directory(file_path):
            """
            Find the appropriate grouping directory for a file.
            This looks for common media release folder patterns.
            """
            parts = file_path.split('/')
            # Skip empty parts
            parts = [p for p in parts if p]
            
            # Return the first two levels after root (e.g., /downloads/ReleaseName)
            # This typically captures the release folder
            if len(parts) >= 2:
                return '/' + '/'.join(parts[:2])
            elif len(parts) == 1:
                return '/' + parts[0]
            return '/'
        
        # Group by the common release directory
        files_by_release = defaultdict(list)
        for f in files:
            release_dir = find_common_grouping_directory(f.file_path)
            files_by_release[release_dir].append(f)
        
        groups = []
        ungrouped = []
        
        for directory, dir_files in sorted(files_by_release.items()):
            if len(dir_files) > 1:
                # Multiple files in this directory - create a group
                total_size = sum(f.file_size or 0 for f in dir_files)
                # Sort files within group by path
                dir_files.sort(key=lambda x: x.file_path)
                groups.append({
                    'directory': directory,
                    'files': dir_files,
                    'total_size': total_size
                })
            else:
                # Single file - add to ungrouped
                ungrouped.extend(dir_files)
        
        # Sort groups by directory name
        groups.sort(key=lambda x: x['directory'])
        
        result[instance_id] = {
            'instance': instances_map[instance_id],
            'groups': groups,
            'ungrouped': ungrouped
        }
    
    return result

@app.route('/orphaned-files')
def orphaned_files():
    instances = Instance.query.all()
    orphaned = OrphanedFile.query.order_by(OrphanedFile.timestamp.desc()).all()
    grouped_files = group_orphaned_files_by_directory(orphaned)
    return render_template('orphaned_files.html', instances=instances, orphaned_files=orphaned, grouped_files=grouped_files)

@app.route('/api/orphaned-files/check-permissions')
def check_orphaned_permissions():
    """Check write permissions for all configured instance mapped directories."""
    instances = Instance.query.filter(Instance.mapped_download_dir.isnot(None)).all()
    results = {}
    
    for instance in instances:
        mapped_dir = instance.mapped_download_dir
        if not mapped_dir:
            continue
            
        test_file = os.path.join(mapped_dir, '.qpanel_permission_test')
        try:
            # Try to create and delete a test file
            with open(test_file, 'w') as f:
                f.write('')
            os.remove(test_file)
            results[instance.id] = {'status': 'ok', 'name': instance.name, 'path': mapped_dir}
        except PermissionError:
            results[instance.id] = {'status': 'error', 'name': instance.name, 'path': mapped_dir, 'error': 'Permission denied'}
        except Exception as e:
            results[instance.id] = {'status': 'error', 'name': instance.name, 'path': mapped_dir, 'error': str(e)}
    
    return jsonify(results)

@app.route('/api/orphaned-files/delete-file/<int:file_id>', methods=['POST'])
def delete_orphaned_file_from_disk(file_id):
    """Delete an orphaned file from disk and remove from database."""
    orphaned_file = OrphanedFile.query.get_or_404(file_id)
    instance = orphaned_file.instance
    
    # Convert qBt path to local mapped path
    file_path = orphaned_file.file_path
    if instance.qbt_download_dir and instance.mapped_download_dir:
        if file_path.startswith(instance.qbt_download_dir):
            file_path = file_path.replace(instance.qbt_download_dir, instance.mapped_download_dir, 1)
    
    errors = []
    deleted_path = orphaned_file.file_path
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        # Remove from database
        db.session.delete(orphaned_file)
        db.session.commit()
        return jsonify({'status': 'success', 'message': f'Deleted: {deleted_path}'})
    except PermissionError:
        return jsonify({'status': 'error', 'message': f'Permission denied: {deleted_path}'}), 403
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Error deleting {deleted_path}: {str(e)}'}), 500

@app.route('/api/orphaned-files/delete-folder', methods=['POST'])
def delete_orphaned_folder():
    """Delete all orphaned files in a folder from disk and remove from database."""
    data = request.get_json()
    file_ids = data.get('file_ids', [])
    
    if not file_ids:
        return jsonify({'status': 'error', 'message': 'No files specified'}), 400
    
    results = {'deleted': [], 'errors': []}
    
    for file_id in file_ids:
        orphaned_file = OrphanedFile.query.get(file_id)
        if not orphaned_file:
            results['errors'].append({'id': file_id, 'error': 'File not found in database'})
            continue
            
        instance = orphaned_file.instance
        file_path = orphaned_file.file_path
        
        # Convert qBt path to local mapped path
        if instance.qbt_download_dir and instance.mapped_download_dir:
            if file_path.startswith(instance.qbt_download_dir):
                file_path = file_path.replace(instance.qbt_download_dir, instance.mapped_download_dir, 1)
        
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            db.session.delete(orphaned_file)
            results['deleted'].append(orphaned_file.file_path)
        except PermissionError:
            results['errors'].append({'path': orphaned_file.file_path, 'error': 'Permission denied'})
        except Exception as e:
            results['errors'].append({'path': orphaned_file.file_path, 'error': str(e)})
    
    # Try to remove the parent directory if it's empty
    if results['deleted'] and not results['errors']:
        try:
            # Get the common directory from the first deleted file
            first_deleted = results['deleted'][0]
            instance = Instance.query.get(data.get('instance_id'))
            if instance and instance.qbt_download_dir and instance.mapped_download_dir:
                dir_path = data.get('directory', '')
                if dir_path.startswith(instance.qbt_download_dir):
                    local_dir = dir_path.replace(instance.qbt_download_dir, instance.mapped_download_dir, 1)
                    # Try to remove empty directories up to the mapped root
                    while local_dir != instance.mapped_download_dir and local_dir != '/':
                        if os.path.isdir(local_dir) and not os.listdir(local_dir):
                            os.rmdir(local_dir)
                            local_dir = os.path.dirname(local_dir)
                        else:
                            break
        except Exception:
            pass  # Ignore errors when cleaning up empty directories
    
    db.session.commit()
    
    if results['errors']:
        return jsonify({
            'status': 'partial',
            'message': f"Deleted {len(results['deleted'])} files, {len(results['errors'])} errors",
            'deleted': results['deleted'],
            'errors': results['errors']
        }), 207
    
    return jsonify({
        'status': 'success',
        'message': f"Deleted {len(results['deleted'])} files",
        'deleted': results['deleted']
    })

@app.route('/orphaned-files/settings/<instance_id>', methods=['POST'])
def update_orphaned_settings(instance_id):
    instance = Instance.query.get_or_404(instance_id)
    instance.orphaned_scan_enabled = request.form.get('orphaned_scan_enabled') == 'on'
    instance.orphaned_min_age_days = int(request.form.get('orphaned_min_age_days') or 7)
    raw_patterns = request.form.get('orphaned_ignore_patterns', '')
    instance.orphaned_ignore_patterns = raw_patterns
    db.session.commit()
    flash(f"Orphaned files settings for '{instance.name}' updated successfully!", 'success')
    return redirect(url_for('orphaned_files'))

@app.route('/orphaned-files/clear', methods=['POST'])
def clear_orphaned_files():
    instance_id = request.form.get('instance_id')
    try:
        if instance_id:
            num_rows_deleted = db.session.query(OrphanedFile).filter_by(instance_id=instance_id).delete()
            instance = Instance.query.get(instance_id)
            flash(f'Successfully cleared {num_rows_deleted} orphaned files for {instance.name}.', 'success')
        else:
            num_rows_deleted = db.session.query(OrphanedFile).delete()
            flash(f'Successfully cleared {num_rows_deleted} orphaned files.', 'success')
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Error clearing orphaned files: {e}', 'danger')
    return redirect(url_for('orphaned_files'))

@app.route('/orphaned-files/delete/<int:file_id>', methods=['POST'])
def delete_orphaned_file(file_id):
    orphaned_file = OrphanedFile.query.get_or_404(file_id)
    try:
        db.session.delete(orphaned_file)
        db.session.commit()
        flash('Orphaned file entry removed.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error removing file entry: {e}', 'danger')
    return redirect(url_for('orphaned_files'))

@app.route('/admin/remove-db', methods=['POST'])
def remove_db():
    try:
        db.session.close()
        db_path = os.path.join(DATA_DIR, 'qpanel.db')
        cache_path = os.path.join(DATA_DIR, 'cache.json')
        if os.path.exists(db_path):
            os.remove(db_path)
        
        # Also clear the cache
        if os.path.exists(cache_path):
            os.remove(cache_path)
        
        flash('Database and cache have been successfully removed. Please restart the application.', 'success')
    except Exception as e:
        flash(f'Error removing database: {e}', 'danger')
        
    return redirect(url_for('settings'))

@app.route('/instances', methods=['GET', 'POST'])
def instances():
    if request.method == 'POST':
        tag_nohardlinks = request.form.get('tag_nohardlinks') == 'true'
        if tag_nohardlinks:
            qbt_download_dir = request.form.get('qbt_download_dir')
            mapped_download_dir = request.form.get('mapped_download_dir')
            if not qbt_download_dir or not mapped_download_dir:
                flash('Path mappings are required when hard link tagging is enabled.', 'danger')
                return redirect(url_for('instances'))

        new_instance = Instance(
            name=request.form['name'],
            host=request.form['host'],
            username=request.form['username'],
            password=request.form['password'],
            qbt_download_dir=request.form.get('qbt_download_dir'),
            mapped_download_dir=request.form.get('mapped_download_dir'),
            tag_nohardlinks=request.form.get('tag_nohardlinks') == 'true',
            pause_cross_seeded_torrents=request.form.get('pause_cross_seeded_torrents') == 'true',
            tag_unregistered_torrents=request.form.get('tag_unregistered_torrents') == 'true'
        )
        db.session.add(new_instance)
        db.session.commit()
        flash(f"Instance '{new_instance.name}' saved successfully!", 'success')
        return redirect(url_for('instances'))
    
    instances = Instance.query.all()
    instance_statuses = {}
    for instance in instances:
        client = get_client(instance)
        if client:
            try:
                version = client.app_version()
                instance_statuses[instance.id] = {'status': 'Online', 'version': version}
            except Exception as e:
                instance_statuses[instance.id] = {'status': 'Offline', 'error': f'An unexpected error occurred: {e}'}
        else:
            instance_statuses[instance.id] = {'status': 'Offline', 'error': 'Could not connect. Check logs for details.'}
            
    return render_template('instances.html', configs=instances, instance_statuses=instance_statuses)

@app.route('/instances/edit/<instance_id>', methods=['GET', 'POST'])
def edit_instance(instance_id):
    instance = Instance.query.get_or_404(instance_id)

    if request.method == 'POST':
        tag_nohardlinks = request.form.get('tag_nohardlinks') == 'true'
        if tag_nohardlinks:
            qbt_download_dir = request.form.get('qbt_download_dir')
            mapped_download_dir = request.form.get('mapped_download_dir')
            if not qbt_download_dir or not mapped_download_dir:
                flash('Path mappings are required when hard link tagging is enabled.', 'danger')
                return redirect(url_for('edit_instance', instance_id=instance_id))
            
        instance.name = request.form['name']
        instance.host = request.form['host']
        instance.username = request.form['username']
        if request.form.get('password'):
            instance.password = request.form['password']
        instance.qbt_download_dir = request.form.get('qbt_download_dir')
        instance.mapped_download_dir = request.form.get('mapped_download_dir')
        instance.tag_nohardlinks = request.form.get('tag_nohardlinks') == 'true'
        instance.pause_cross_seeded_torrents = request.form.get('pause_cross_seeded_torrents') == 'true'
        instance.tag_unregistered_torrents = request.form.get('tag_unregistered_torrents') == 'true'
        db.session.commit()
        flash(f"Instance '{instance.name}' updated successfully!", 'success')
        return redirect(url_for('instances'))

    return render_template('edit_instance.html', instance=instance)

@app.route('/instances/delete/<instance_id>', methods=['POST'])
def delete_instance(instance_id):
    instance = Instance.query.get(instance_id)
    if instance:
        db.session.delete(instance)
        db.session.commit()
        flash(f"Instance '{instance.name}' deleted successfully!", 'success')
    else:
        flash('Instance not found.', 'danger')
    return redirect(url_for('instances'))

# File-based cache for rule options
CACHE_FILE = os.path.join(DATA_DIR, 'cache.json')
CACHE_DURATION = 1200  # 20 minutes

def read_cache():
    settings = load_settings()
    cache_duration_seconds = settings.get('cache_duration_minutes', 10) * 60
    try:
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
            now = time.time()
            if now - cache.get('timestamp', 0) < cache_duration_seconds:
                return cache.get('data')
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None

def write_cache(data):
    with open(CACHE_FILE, 'w') as f:
        json.dump({'data': data, 'timestamp': time.time()}, f)

def clear_cache():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

@app.route('/api/rule-options')
def get_rule_options():
    cached_data = read_cache()
    if cached_data:
        return jsonify(cached_data)

    instances = Instance.query.all()
    all_trackers = set()
    all_tags = set()

    if not instances:
        all_trackers.add('N/A')
        all_tags.add('N/A')
    else:
        for instance in instances:
            client = get_client(instance)
            if client:
                try:
                    tags = client.torrents_tags()
                    if tags:
                        for tag in tags:
                            if tag:
                                all_tags.add(tag)

                    torrents = get_all_torrents(client)
                    for torrent in torrents:
                        for tracker in torrent.trackers:
                            parsed_url = urlparse(tracker.url)
                            if parsed_url.netloc:
                                all_trackers.add(parsed_url.netloc)
                except Exception as e:
                    # Log error instead of flashing in an API context
                    print(f"An error occurred while fetching data from '{instance.name}': {e}")

    if not all_trackers:
        all_trackers.add('N/A')
    if not all_tags:
        all_tags.add('N/A')

    fetched_data = {
        'trackers': sorted(list(all_trackers)),
        'tags': sorted(list(all_tags))
    }
    
    write_cache(fetched_data)

    return jsonify(fetched_data)

@app.route('/api/refresh-rule-options', methods=['POST'])
def refresh_rule_options():
    clear_cache()
    return jsonify({'status': 'success', 'message': 'Cache cleared.'})

@app.route('/rules', methods=['GET', 'POST'])
def rules():
    if request.method == 'POST':
        new_rule = Rule(
            name=request.form['name'],
            condition_type=request.form['condition_type'],
            condition_value=request.form['condition_value'],
            share_limit_ratio=float(request.form['share_limit_ratio']) if request.form.get('share_limit_ratio') else None,
            share_limit_time=int(request.form['share_limit_time']) if request.form.get('share_limit_time') else None,
            max_upload_speed=int(request.form['max_upload_speed']) * 1024 if request.form.get('max_upload_speed') else None,
            max_download_speed=int(request.form['max_download_speed']) * 1024 if request.form.get('max_download_speed') else None
        )
        db.session.add(new_rule)
        db.session.commit()
        flash(f"Rule '{new_rule.name}' saved successfully!", 'success')
        return redirect(url_for('rules'))
    
    rules = Rule.query.all()
    instances = Instance.query.all()
    return render_template('rules.html', rules=rules, instances=instances)

@app.route('/rules/edit/<rule_id>', methods=['GET', 'POST'])
def edit_rule(rule_id):
    rule = Rule.query.get_or_404(rule_id)

    if request.method == 'POST':
        rule.name = request.form['name']
        rule.condition_type = request.form['condition_type']
        rule.condition_value = request.form['condition_value']
        rule.share_limit_ratio = float(request.form['share_limit_ratio']) if request.form.get('share_limit_ratio') else None
        rule.share_limit_time = int(request.form['share_limit_time']) if request.form.get('share_limit_time') else None
        rule.max_upload_speed = int(request.form['max_upload_speed']) * 1024 if request.form.get('max_upload_speed') else None
        rule.max_download_speed = int(request.form['max_download_speed']) * 1024 if request.form.get('max_download_speed') else None
        db.session.commit()
        flash(f"Rule '{rule.name}' updated successfully!", 'success')
        return redirect(url_for('rules'))
        
    return render_template('edit_rule.html', rule=rule)

@app.route('/rules/delete/<rule_id>', methods=['POST'])
def delete_rule(rule_id):
    rule = Rule.query.get(rule_id)
    if rule:
        db.session.delete(rule)
        db.session.commit()
        flash(f"Rule '{rule.name}' deleted successfully!", 'success')
    else:
        flash('Rule not found.', 'danger')
    return redirect(url_for('rules'))

@app.route('/api/test-telegram', methods=['POST'])
def test_telegram():
    """Send a test Telegram notification."""
    from notifications import send_telegram_message
    settings = load_settings()
    
    bot_token = settings.get('telegram_bot_token')
    chat_id = settings.get('telegram_chat_id')
    
    if not bot_token or not chat_id:
        return jsonify({'status': 'error', 'message': 'Telegram bot token or chat ID is not configured.'})
    
    message = "🔔 Test notification from qPanel!\n\nIf you see this, Telegram notifications are working correctly."
    
    if send_telegram_message(bot_token, chat_id, message):
        return jsonify({'status': 'success', 'message': 'Test notification sent successfully!'})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to send notification. Check your bot token and chat ID.'})

@app.route('/api/test-discord', methods=['POST'])
def test_discord():
    """Send a test Discord notification."""
    from notifications import send_discord_message
    settings = load_settings()
    
    webhook_url = settings.get('discord_webhook_url')
    
    if not webhook_url:
        return jsonify({'status': 'error', 'message': 'Discord webhook URL is not configured.'})
    
    message = "🔔 **Test notification from qPanel!**\n\nIf you see this, Discord notifications are working correctly."
    
    if send_discord_message(webhook_url, message):
        return jsonify({'status': 'success', 'message': 'Test notification sent successfully!'})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to send notification. Check your webhook URL.'})

@app.route('/admin/restart', methods=['POST'])
def restart():
    """Restarts the application by touching the main app file to trigger the reloader."""
    try:
        file_path = __file__
        with open(file_path, 'a'):
            os.utime(file_path, None)
        flash('Application is restarting...', 'success')
    except Exception as e:
        flash(f'Error restarting application: {e}', 'danger')
    return redirect(url_for('settings'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    settings = load_settings()
    from scheduler import run_all_jobs
    scheduler = BackgroundScheduler()

    interval_minutes = settings.get('scheduler_interval_minutes', 10)
    
    # Single unified job that fetches torrents once and runs all tasks
    scheduler.add_job(func=run_all_jobs, trigger="interval", minutes=interval_minutes, next_run_time=datetime.now())
    
    scheduler.start()

    port = int(os.environ.get("FLASK_PORT", 5001))
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=port)
