#!/usr/bin/env python3
"""
Web Assistant — Flask Portal
AI-чат с LDAP-аутентификацией и управлением сессиями
"""

import os
import uuid
import json
import time
import threading
import requests
import ldap
import logging
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, abort, g
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_session import Session
from flask_wtf.csrf import CSRFProtect
import redis
from werkzeug.middleware.proxy_fix import ProxyFix

# ==================== CONFIG & LOGGING ====================
# Structured logging setup
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "module": "%(module)s", "message": "%(message)s"}'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:////app/data/app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Trust proxy headers for rate limiting behind reverse proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Redis Session Backend
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_LIFETIME'] = timedelta(hours=24)
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'webassist:'
app.config['SESSION_REDIS'] = redis.from_url(os.environ.get('REDIS_URL', 'redis://redis:6379/0'))

db = SQLAlchemy(app)
Session(app)  # Инициализация Flask-Session

# CSRF Protection
csrf = CSRFProtect(app)

# Rate Limiting (simple in-memory implementation)
class RateLimiter:
    def __init__(self):
        self.requests = {}  # {ip: [(timestamp, endpoint), ...]}
        self.lock = threading.Lock()

    def is_allowed(self, ip, endpoint, max_requests=5, window_seconds=60):
        with self.lock:
            now = time.time()
            if ip not in self.requests:
                self.requests[ip] = []

            # Clean old requests
            self.requests[ip] = [(t, e) for t, e in self.requests[ip] if now - t < window_seconds]

            # Check limit for login endpoint
            if endpoint == 'login':
                login_attempts = sum(1 for t, e in self.requests[ip] if e == 'login')
                if login_attempts >= max_requests:
                    return False

            self.requests[ip].append((now, endpoint))
            return True

rate_limiter = RateLimiter()

def rate_limit(max_requests=5, window_seconds=60):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            if not rate_limiter.is_allowed(ip, f.__name__, max_requests, window_seconds):
                logger.warning(f"Rate limit exceeded for IP {ip} on {f.__name__}")
                return jsonify({'error': 'Too many requests, please try again later'}), 429
            return f(*args, **kwargs)
        return wrapped
    return decorator

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Ensure DB directory exists
db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '', 1)
db_dir = os.path.dirname(db_path)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

# External Services Config
LDAP_URI = os.environ.get('LDAP_URI', 'ldap://lldap:3890')
LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN', 'dc=dash-panel,dc=tech')
LDAP_ADMIN_PASSWORD = os.environ.get('LDAP_ADMIN_PASSWORD', '')
LDAP_ADMIN_USER = os.environ.get('LDAP_ADMIN_USER', 'admin')  # Documented env var
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://ollama:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen:7b')

# ==================== MODELS ====================
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Переименовано в UserSession, чтобы не конфликтовать с flask.session
class UserSession(db.Model):
    __tablename__ = 'sessions'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    session_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    # Changed backref to avoid confusion with flask.session and table name
    user = db.relationship('User', backref='user_sessions')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'session_id': self.session_id[:8] + '...',
            'created_at': self.created_at.isoformat(),
            'last_activity': self.last_activity.isoformat(),
            'ip_address': self.ip_address,
            'user_agent': self.user_agent[:50] + ('...' if len(self.user_agent) > 50 else '')
        }

with app.app_context():
    db.create_all()

# ==================== AUTH & SESSION TRACKING ====================
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def authenticate_ldap(username, password):
    try:
        conn = ldap.initialize(LDAP_URI)
        conn.set_option(ldap.OPT_REFERRALS, 0)
        user_dn = f"uid={username},ou=people,{LDAP_BASE_DN}"
        conn.simple_bind_s(user_dn, password)
        conn.unbind()
        return True
    except Exception:
        return False

def track_session_activity():
    if not current_user.is_authenticated:
        return

    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        session['session_created'] = datetime.utcnow().isoformat()

    sid = session['session_id']
    now = datetime.utcnow()

    # Обновляем метаданные в SQLite для UI
    db_sess = UserSession.query.filter_by(session_id=sid).first()
    if not db_sess:
        db_sess = UserSession(
            session_id=sid,
            user_id=current_user.id,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')[:255]
        )
        db.session.add(db_sess)
    db_sess.last_activity = now
    db_sess.ip_address = request.remote_addr
    db_sess.user_agent = request.headers.get('User-Agent', '')[:255]
    db.session.commit()

@app.before_request
def update_activity():
    if current_user.is_authenticated and not request.path.startswith('/static'):
        track_session_activity()

# ==================== CONTEXT PROCESSORS ====================
@app.context_processor
def inject_now():
    """Inject current datetime into all templates for footer"""
    return {'now': datetime.utcnow()}

# ==================== BACKGROUND CLEANUP ====================
def cleanup_stale_sessions():
    """Удаляет сессии, неактивные более 24 часов"""
    try:
        while True:
            time.sleep(3600)  # Каждый час
            try:
                cutoff = datetime.utcnow() - timedelta(hours=24)
                stale = UserSession.query.filter(UserSession.last_activity < cutoff).all()
                if stale:
                    for s in stale:
                        db.session.delete(s)
                        # Чистим Redis-сессию, если ключ известен
                        # Flask-Session автоматически чистит по TTL, но можно форсировать:
                        # r = app.config['SESSION_REDIS']
                        # r.delete(f"webassist:{s.session_id}")
                    db.session.commit()
                    logger.info(f"🧹 Cleaned {len(stale)} stale sessions")
            except Exception as e:
                logger.error(f"⚠️ Cleanup error: {e}")
                db.session.rollback()
    except KeyboardInterrupt:
        logger.info("Cleanup thread interrupted")

cleanup_thread = threading.Thread(target=cleanup_stale_sessions, daemon=True)
cleanup_thread.start()

# ==================== ROUTES ====================
@app.route('/health')
def health():
    """Health check endpoint that works without Redis (for Docker healthcheck)"""
    r = app.config['SESSION_REDIS']
    redis_status = 'unknown'
    try:
        r.ping()
        redis_status = 'connected'
    except Exception as e:
        redis_status = f'error: {str(e)}'
        # Don't fail healthcheck if Redis is temporarily unavailable

    return jsonify({
        'status': 'healthy',
        'service': 'portal',
        'timestamp': datetime.utcnow().isoformat(),
        'redis': redis_status
    }), 200

@app.route('/login', methods=['GET', 'POST'])
@rate_limit(max_requests=5, window_seconds=60)
def login():
    if current_user.is_authenticated:
        return redirect(url_for('chat'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash('Введите логин и пароль', 'warning')
            return render_template('login.html')

        if authenticate_ldap(username, password):
            user = User.query.filter_by(username=username).first()
            if not user:
                # Use documented LDAP_ADMIN_USER env var instead of hardcoded 'tr0jan'
                is_admin = username == LDAP_ADMIN_USER
                logger.info(f"Creating new user: {username}, is_admin={is_admin}")
                user = User(username=username, is_admin=is_admin)
                db.session.add(user)
                db.session.commit()

            login_user(user)
            track_session_activity()
            logger.info(f"User logged in: {username}")
            flash('Успешный вход!', 'success')
            return redirect(url_for('chat'))
        else:
            logger.warning(f"Failed login attempt for user: {username}")
            flash('Неверный логин или пароль', 'danger')

    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    sid = session.get('session_id')
    username = current_user.username

    if sid:
        # Удаляем из Redis (Flask-Session)
        session.clear()
        # Удаляем метаданные из SQLite
        UserSession.query.filter_by(session_id=sid).delete()
        db.session.commit()

    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))

@app.route('/chat')
@login_required
def chat():
    return render_template('chat.html')

@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    data = request.json
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'error': 'Empty message'}), 400

    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": message,
            "stream": False,
            "options": {"temperature": 0.7, "num_ctx": 4096}
        }
        resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        return jsonify({
            'response': result.get('response', ''),
            'model': result.get('model', OLLAMA_MODEL),
            'done': result.get('done', True)
        })
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Ollama error: {str(e)}'}), 503

@app.route('/api/sessions')
@login_required
def get_sessions():
    if current_user.is_admin:
        sessions = UserSession.query.order_by(UserSession.last_activity.desc()).all()
    else:
        sessions = UserSession.query.filter_by(user_id=current_user.id).order_by(UserSession.last_activity.desc()).all()

    return jsonify({'success': True, 'sessions': [s.to_dict() for s in sessions]})

@app.route('/api/sessions/<session_id>', methods=['DELETE'])
@login_required
def terminate_session(session_id):
    target = UserSession.query.filter_by(session_id=session_id).first_or_404()

    if not current_user.is_admin and target.user_id != current_user.id:
        abort(403)

    current_sid = session.get('session_id')
    if current_sid == session_id:
        session.clear()

    try:
        db.session.delete(target)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Session terminated'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/terminate-all', methods=['POST'])
@login_required
def terminate_all_sessions():
    current_sid = session.get('session_id')
    query = UserSession.query if current_user.is_admin else UserSession.query.filter_by(user_id=current_user.id)

    if not current_user.is_admin and current_sid:
        sessions_to_del = query.filter(UserSession.session_id != current_sid).all()
    else:
        sessions_to_del = [s for s in query.all() if s.session_id != current_sid]

    count = 0
    for s in sessions_to_del:
        db.session.delete(s)
        count += 1

    try:
        db.session.commit()
        return jsonify({'success': True, 'terminated': count})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error terminating sessions: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== ADMIN PANEL ROUTES ====================
@app.route('/admin')
@login_required
def admin_panel():
    """Admin panel for audit and session management"""
    if not current_user.is_admin:
        logger.warning(f"Non-admin user {current_user.username} tried to access admin panel")
        abort(403)
    return render_template('admin.html')

@app.route('/api/admin/sessions')
@login_required
def api_admin_sessions():
    """Get all sessions (admin only)"""
    if not current_user.is_admin:
        abort(403)

    sessions = UserSession.query.order_by(UserSession.last_activity.desc()).all()
    # Enrich with username
    result = []
    for s in sessions:
        data = s.to_dict()
        data['username'] = s.user.username if s.user else 'Unknown'
        # Count messages (placeholder - would need message history table)
        data['message_count'] = 0
        result.append(data)

    return jsonify(result)

@app.route('/api/admin/session/<session_id>')
@login_required
def api_admin_session_detail(session_id):
    """Get detailed session info including messages (admin only)"""
    if not current_user.is_admin:
        abort(403)

    target = UserSession.query.filter_by(session_id=session_id).first_or_404()

    # Note: Message history is not currently stored in DB
    # This would require a separate Message model
    session_data = target.to_dict()
    session_data['username'] = target.user.username if target.user else 'Unknown'
    session_data['is_active'] = (datetime.utcnow() - target.last_activity).total_seconds() < 3600

    return jsonify({
        'session': session_data,
        'messages': []  # Placeholder - implement message storage for full functionality
    })

@app.route('/api/admin/audit')
@login_required
def api_admin_audit():
    """Audit log endpoint (admin only) - placeholder for future implementation"""
    if not current_user.is_admin:
        abort(403)

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    # Placeholder: Return empty audit log
    # To implement fully, create an AuditLog model and populate it on key events
    return jsonify({
        'logs': [],
        'current_page': page,
        'pages': 0,
        'total': 0
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5443, debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true')
