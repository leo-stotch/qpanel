from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
import uuid
import time
import json
import os
from urllib.parse import urlparse
from qbt_client import get_client
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
    'telegram_notification_enabled': False
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
    look_for_deleted_torrents = db.Column(db.Boolean, default=False)
    tag_nohardlinks = db.Column(db.Boolean, default=False)
    pause_cross_seeded_torrents = db.Column(db.Boolean, default=False)
    tag_unregistered_torrents = db.Column(db.Boolean, default=False)
    monitor_paused_up = db.Column(db.Boolean, default=False)
    last_processed_log_id = db.Column(db.Integer, default=0)

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
            'telegram_notification_enabled': request.form.get('telegram_notification_enabled') == 'on'
        }
        save_settings(new_settings)
        flash('Settings saved successfully! Please restart the application for the new interval to take effect.', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html', settings=load_settings())

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
            look_for_deleted_torrents=request.form.get('look_for_deleted_torrents') == 'true',
            tag_nohardlinks=request.form.get('tag_nohardlinks') == 'true',
            pause_cross_seeded_torrents=request.form.get('pause_cross_seeded_torrents') == 'true',
            tag_unregistered_torrents=request.form.get('tag_unregistered_torrents') == 'true',
            monitor_paused_up=request.form.get('monitor_paused_up') == 'on'
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
        instance.look_for_deleted_torrents = request.form.get('look_for_deleted_torrents') == 'true'
        instance.tag_nohardlinks = request.form.get('tag_nohardlinks') == 'true'
        instance.pause_cross_seeded_torrents = request.form.get('pause_cross_seeded_torrents') == 'true'
        instance.tag_unregistered_torrents = request.form.get('tag_unregistered_torrents') == 'true'
        instance.monitor_paused_up = request.form.get('monitor_paused_up') == 'on'
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

                    torrents = client.torrents_info()
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
    from scheduler import apply_rules_job, tag_unregistered_torrents_job, tag_torrents_with_no_hard_links_job, monitor_paused_up_torrents_job
    from log_parser import log_parsing_job
    from cross_seed_checker import pause_cross_seeded_torrents_job
    scheduler = BackgroundScheduler()

    interval_minutes = settings.get('scheduler_interval_minutes', 10)
    
    # Stagger the jobs
    scheduler.add_job(func=tag_unregistered_torrents_job, trigger="interval", minutes=interval_minutes, next_run_time=datetime.now())
    scheduler.add_job(func=tag_torrents_with_no_hard_links_job, trigger="interval", minutes=interval_minutes, next_run_time=datetime.now() + timedelta(minutes=1))
    scheduler.add_job(func=apply_rules_job, trigger="interval", minutes=interval_minutes, next_run_time=datetime.now() + timedelta(minutes=2))
    scheduler.add_job(func=pause_cross_seeded_torrents_job, trigger="interval", minutes=interval_minutes, next_run_time=datetime.now() + timedelta(minutes=3))
    scheduler.add_job(func=log_parsing_job, trigger="interval", minutes=interval_minutes, next_run_time=datetime.now() + timedelta(minutes=4))
    scheduler.add_job(func=monitor_paused_up_torrents_job, trigger="interval", minutes=interval_minutes, next_run_time=datetime.now() + timedelta(minutes=5))
    
    scheduler.start()

    port = int(os.environ.get("FLASK_PORT", 5001))
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=port)
