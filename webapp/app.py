#!/usr/bin/env python3
"""
OpenCode Assistant — Flask Portal
AI-чат с LDAP-аутентификацией и управлением сессиями
"""

import os
import re
import json
import uuid
import logging
import requests
from datetime import datetime, timedelta
from functools import wraps

import ldap
import redis
from flask import (
    Flask, request, redirect, url_for, session, jsonify,
    render_template, flash, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

# =============================================================================
# 📋 Конфигурация приложения
# =============================================================================

app = Flask(__name__)

# Настройки из переменных окружения
app.config.update(
    # Flask core
    SECRET_KEY=os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod'),
    FLASK_ENV=os.environ.get('FLASK_ENV', 'production'),

    # Database (SQLite)
    SQLALCHEMY_DATABASE_URI=os.environ.get('DATABASE_URL', 'sqlite:////app/data/app.db'),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'timeout': 10}},

    # Sessions (cookie-based, безопасные настройки)
    SESSION_COOKIE_NAME='webassist_session',
    SESSION_COOKIE_SECURE=os.environ.get('FLASK_ENV') == 'production',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24),

    # LDAP
    LDAP_URI=os.environ.get('LDAP_URI', 'ldap://lldap:3890'),
    LDAP_BASE_DN=os.environ.get('LDAP_BASE_DN', 'dc=dash-panel,dc=tech'),
    LDAP_ADMIN_DN=os.environ.get('LDAP_ADMIN_DN', 'uid=tr0jan,ou=people,dc=dash-panel,dc=tech'),
    LDAP_ADMIN_PASSWORD=os.environ.get('LDAP_ADMIN_PASSWORD', ''),

    # Ollama
    OLLAMA_HOST=os.environ.get('OLLAMA_HOST', 'http://ollama:11434'),
    OLLAMA_MODEL=os.environ.get('OLLAMA_MODEL', 'qwen:7b'),
)

# Инициализация расширений
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Настройка логирования
logging.basicConfig(
    level=logging.INFO if app.config['FLASK_ENV'] == 'production' else logging.DEBUG,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# 🔌 Redis клиент (только для метаданных сессий)
# =============================================================================

redis_client = None
try:
    redis_client = redis.from_url(
        os.environ.get('REDIS_URL', 'redis://redis:6379/0'),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30
    )
    redis_client.ping()
    logger.info(f"✅ Redis connected: {os.environ.get('REDIS_URL')}")
except redis.ConnectionError as e:
    logger.warning(f"⚠️ Redis not available: {e}. Session management features will be limited.")

# =============================================================================
# 🗄️ Модели базы данных
# =============================================================================

class User(UserMixin, db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120))
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    # Связь с сообщениями чата
    messages = db.relationship('Message', backref='user', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.username}>'

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'is_admin': self.is_admin,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


class Message(db.Model):
    __tablename__ = 'message'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    session_id = db.Column(db.String(64), nullable=False, index=True)  # ID чат-сессии
    role = db.Column(db.String(10), nullable=False)  # 'user' или 'assistant'
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    tokens_used = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'role': self.role,
            'content': self.content,
            'created_at': self.created_at.isoformat(),
            'tokens_used': self.tokens_used
        }


# Создаём таблицы при старте (если не существуют)
with app.app_context():
    db.create_all()
    logger.info("✅ Database tables initialized")

# =============================================================================
# 🔐 Flask-Login конфигурация
# =============================================================================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'error': 'Authentication required'}), 401
    return redirect(url_for('login', next=request.url))

# =============================================================================
# 🔑 LDAP аутентификация
# =============================================================================

def authenticate_ldap(username: str, password: str) -> User | None:
    """Аутентификация пользователя через LLDAP"""
    if not username or not password:
        return None

    try:
        conn = ldap.initialize(app.config['LDAP_URI'])
        conn.set_option(ldap.OPT_REFERRALS, 0)
        conn.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)

        # LLDAP использует структуру: uid=<username>,ou=people,<BASE_DN>
        user_dn = f"uid={username},ou=people,{app.config['LDAP_BASE_DN']}"

        try:
            # Попытка бинда с учётными данными пользователя
            conn.simple_bind_s(user_dn, password)

            # Успешная аутентификация — ищем или создаём пользователя в локальной БД
            user = User.query.filter_by(username=username).first()
            if not user:
                # Проверка на админа по имени (можно расширить через группы LDAP)
                is_admin = username == os.environ.get('LLDAP_ADMIN_USER', 'tr0jan')
                user = User(username=username, is_admin=is_admin)
                db.session.add(user)
                db.session.commit()
                logger.info(f"✅ Created new user: {username}")

            # Обновляем время последнего входа
            user.last_login = datetime.utcnow()
            db.session.commit()

            conn.unbind()
            return user

        except ldap.INVALID_CREDENTIALS:
            logger.warning(f"❌ Invalid credentials for user: {username}")
            return None
        except ldap.NO_SUCH_OBJECT:
            logger.warning(f"❌ User DN not found: {user_dn}")
            return None
        finally:
            try:
                conn.unbind()
            except:
                pass

    except ldap.LDAPError as e:
        logger.error(f"❌ LDAP connection error: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected auth error: {e}")
        return None

# =============================================================================
# 🔐 Функции для управления метаданными сессий в Redis
# =============================================================================

def get_session_id() -> str:
    """Получает или создаёт идентификатор текущей сессии"""
    if '_session_id' not in session:
        session['_session_id'] = str(uuid.uuid4())
        session.permanent = True
    return session['_session_id']


def store_session_metadata(user: User, ip: str, ua: str) -> None:
    """Сохраняет метаданные сессии в Redis"""
    if not redis_client:
        return

    session_id = get_session_id()
    key = f"session:{session_id}"

    metadata = {
        'user_id': str(user.id),
        'username': user.username,
        'ip': ip or 'unknown',
        'ua': ua[:200] if ua else 'unknown',
        'created_at': datetime.utcnow().isoformat(),
        'last_activity': datetime.utcnow().isoformat()
    }

    try:
        # Сохраняем метаданные с TTL 24 часа
        redis_client.hset(key, mapping=metadata)
        redis_client.expire(key, 24 * 3600)

        # Индекс по user_id для быстрого поиска
        redis_client.sadd(f"user_sessions:{user.id}", session_id)
        redis_client.expire(f"user_sessions:{user.id}", 24 * 3600)
    except redis.RedisError as e:
        logger.warning(f"⚠️ Failed to store session metadata: {e}")


def get_user_sessions(user_id: int) -> list[dict]:
    """Возвращает список метаданных сессий пользователя"""
    if not redis_client:
        return []

    session_ids = redis_client.smembers(f"user_sessions:{user_id}")
    sessions = []

    for sid in session_ids:
        try:
            data = redis_client.hgetall(f"session:{sid}")
            if data and redis_client.ttl(f"session:{sid}") > 0:
                sessions.append({
                    'session_id': sid[:12] + '...',
                    'full_id': sid,
                    'username': data.get('username'),
                    'ip': data.get('ip'),
                    'ua': data.get('ua'),
                    'created_at': data.get('created_at'),
                    'last_activity': data.get('last_activity')
                })
        except redis.RedisError:
            continue

    return sorted(sessions, key=lambda x: x.get('last_activity', ''), reverse=True)


def terminate_session(session_id: str) -> bool:
    """Завершает сессию: удаляет метаданные из Redis"""
    if not redis_client:
        return False

    try:
        data = redis_client.hgetall(f"session:{session_id}")
        if data:
            user_id = data.get('user_id')
            redis_client.delete(f"session:{session_id}")
            if user_id:
                redis_client.srem(f"user_sessions:{user_id}", session_id)
            return True
    except redis.RedisError as e:
        logger.warning(f"⚠️ Failed to terminate session: {e}")
    return False


def terminate_all_user_sessions(user_id: int, exclude_session_id: str = None) -> int:
    """Завершает все сессии пользователя, кроме указанной"""
    if not redis_client:
        return 0

    session_ids = redis_client.smembers(f"user_sessions:{user_id}")
    terminated = 0

    for sid in session_ids:
        if exclude_session_id and sid == exclude_session_id:
            continue
        if terminate_session(sid):
            terminated += 1

    return terminated


def update_session_activity() -> None:
    """Обновляет last_activity для текущей сессии"""
    if not redis_client:
        return

    session_id = session.get('_session_id')
    if session_id and redis_client.exists(f"session:{session_id}"):
        try:
            redis_client.hset(f"session:{session_id}", 'last_activity', datetime.utcnow().isoformat())
            redis_client.expire(f"session:{session_id}", 24 * 3600)
        except redis.RedisError:
            pass

# =============================================================================
# 🔁 Middleware
# =============================================================================

@app.before_request
def track_session_activity():
    """Обновляет активность сессии при каждом запросе авторизованного пользователя"""
    if current_user.is_authenticated and redis_client:
        # Инициализируем метаданные при первом запросе после логина
        if '_session_tracked' not in session:
            store_session_metadata(
                user=current_user,
                ip=request.remote_addr,
                ua=request.headers.get('User-Agent', '')
            )
            session['_session_tracked'] = True
        else:
            # Обновляем last_activity каждые 5 минут
            last_update = session.get('_last_activity_update', 0)
            now = datetime.utcnow().timestamp()
            if now - last_update > 300:
                update_session_activity()
                session['_last_activity_update'] = now


@app.context_processor
def inject_globals():
    """Добавляет глобальные переменные в шаблоны"""
    return {
        'now': datetime.utcnow(),           # 🔥 Исправлено: теперь для {{ now.year }}
        'current_year': datetime.utcnow().year,
        'app_name': 'OpenCode Assistant'
    }

# =============================================================================
# 🌐 Маршруты: Аутентификация
# =============================================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница входа + обработка формы"""
    if current_user.is_authenticated:
        return redirect(url_for('chat'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        next_page = request.form.get('next') or request.args.get('next')

        if not username or not password:
            flash('Please enter username and password', 'warning')
            return render_template('login.html')

        user = authenticate_ldap(username, password)
        if user:
            login_user(user, remember=True)
            session.permanent = True
            logger.info(f"🔐 User '{username}' logged in from {request.remote_addr}")

            # Редирект на запрошенную страницу или в чат
            return redirect(next_page or url_for('chat'))
        else:
            flash('Invalid credentials', 'danger')
            logger.warning(f"❌ Failed login attempt for '{username}' from {request.remote_addr}")

    return render_template('login.html')


@app.route('/logout', methods=['GET', 'POST'])
@login_required
def logout():
    """Завершает текущую сессию"""
    username = current_user.username
    session_id = session.get('_session_id')

    # Удаляем метаданные из Redis
    if session_id:
        terminate_session(session_id)

    # Стандартный логаут Flask-Login
    logout_user()
    session.clear()

    logger.info(f"👋 User '{username}' logged out")

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': True, 'message': 'Logged out successfully'}), 200

    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

# =============================================================================
# 💬 Маршруты: Чат с AI
# =============================================================================

@app.route('/')
@app.route('/chat')
@login_required
def chat():
    """Основная страница чата"""
    return render_template('chat.html', model=app.config['OLLAMA_MODEL'])


@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    """API endpoint для отправки сообщения в Ollama"""
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({'error': 'Message is required'}), 400

        user_message = data['message'].strip()
        session_id = data.get('session_id', get_session_id())

        if not user_message:
            return jsonify({'error': 'Message cannot be empty'}), 400

        # Сохраняем сообщение пользователя в БД
        user_msg = Message(
            user_id=current_user.id,
            session_id=session_id,
            role='user',
            content=user_message
        )
        db.session.add(user_msg)
        db.session.flush()  # Получаем ID до коммита

        # Формируем контекст для Ollama (последние 10 сообщений сессии)
        history = Message.query.filter_by(
            user_id=current_user.id,
            session_id=session_id
        ).order_by(Message.created_at.desc()).limit(10).all()

        # Ollama ожидает список сообщений в формате: [{role, content}, ...]
        messages = []
        for msg in reversed(history):  # В хронологическом порядке
            messages.append({'role': msg.role, 'content': msg.content})
        messages.append({'role': 'user', 'content': user_message})

        # Запрос к Ollama API
        ollama_url = f"{app.config['OLLAMA_HOST'].rstrip('/')}/api/chat"
        response = requests.post(
            ollama_url,
            json={
                'model': app.config['OLLAMA_MODEL'],
                'messages': messages,
                'stream': False,
                'options': {
                    'temperature': 0.7,
                    'num_predict': 2048
                }
            },
            timeout=120  # 2 минуты на генерацию
        )
        response.raise_for_status()

        result = response.json()
        assistant_response = result.get('message', {}).get('content', '')

        if not assistant_response:
            raise ValueError("Empty response from Ollama")

        # Сохраняем ответ ассистента
        assistant_msg = Message(
            user_id=current_user.id,
            session_id=session_id,
            role='assistant',
            content=assistant_response,
            tokens_used=result.get('eval_count', 0)
        )
        db.session.add(assistant_msg)
        db.session.commit()

        return jsonify({
            'success': True,
            'response': assistant_response,
            'session_id': session_id,
            'tokens_used': result.get('eval_count', 0)
        }), 200

    except requests.Timeout:
        logger.error("Ollama request timeout")
        return jsonify({'error': 'Request timeout. The model is taking too long to respond.'}), 504
    except requests.ConnectionError:
        logger.error("Cannot connect to Ollama")
        return jsonify({'error': 'Cannot connect to AI server. Please try again later.'}), 503
    except Exception as e:
        logger.error(f"Chat API error: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({'error': str(e) or 'Internal server error'}), 500


@app.route('/api/sessions/<session_id>/messages', methods=['GET'])
@login_required
def api_get_messages(session_id: str):
    """Возвращает историю сообщений для указанной сессии чата"""
    messages = Message.query.filter_by(
        user_id=current_user.id,
        session_id=session_id
    ).order_by(Message.created_at.asc()).all()

    return jsonify({
        'success': True,
        'messages': [m.to_dict() for m in messages],
        'total': len(messages)
    }), 200


@app.route('/api/sessions', methods=['DELETE'])
@login_required
def api_delete_session():
    """Удаляет всю историю чата для текущей сессии"""
    session_id = request.args.get('session_id', get_session_id())

    count = Message.query.filter_by(
        user_id=current_user.id,
        session_id=session_id
    ).delete()
    db.session.commit()

    logger.info(f"🗑️ Deleted {count} messages for session {session_id[:8]}...")
    return jsonify({'success': True, 'deleted': count}), 200

# =============================================================================
# 🔐 Маршруты: Управление сессиями
# =============================================================================

@app.route('/api/sessions', methods=['GET'])
@login_required
def api_list_sessions():
    """Возвращает список активных сессий"""
    if current_user.is_admin:
        # Админ видит все сессии (упрощённая реализация)
        all_sessions = []
        if redis_client:
            try:
                keys = redis_client.keys("session:*")
                for key in keys:
                    data = redis_client.hgetall(key)
                    if data and redis_client.ttl(key) > 0:
                        sid = key.replace("session:", "")
                        all_sessions.append({
                            'session_id': sid[:12] + '...',
                            'full_id': sid,
                            'username': data.get('username'),
                            'ip': data.get('ip'),
                            'created_at': data.get('created_at'),
                            'last_activity': data.get('last_activity')
                        })
            except redis.RedisError:
                pass
        return jsonify({
            'success': True,
            'sessions': sorted(all_sessions, key=lambda x: x.get('last_activity', ''), reverse=True),
            'total': len(all_sessions)
        }), 200
    else:
        # Обычный пользователь видит только свои сессии
        sessions = get_user_sessions(current_user.id)
        return jsonify({
            'success': True,
            'sessions': sessions,
            'total': len(sessions)
        }), 200


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
@login_required
def api_terminate_session(session_id: str):
    """Завершает указанную сессию"""
    # Если передан короткий ID, ищем полный
    if len(session_id) < 20 and redis_client:
        try:
            keys = redis_client.keys(f"session:{session_id}*")
            if keys:
                session_id = keys[0].replace("session:", "")
        except redis.RedisError:
            pass

    # Проверка существования и прав доступа
    if redis_client:
        try:
            data = redis_client.hgetall(f"session:{session_id}")
            if not data:
                return jsonify({'success': False, 'error': 'Session not found'}), 404

            # Обычный пользователь может завершать только свои сессии
            if not current_user.is_admin and str(data.get('user_id')) != str(current_user.id):
                return jsonify({'success': False, 'error': 'Permission denied'}), 403
        except redis.RedisError:
            return jsonify({'success': False, 'error': 'Redis error'}), 500

    # Если пользователь завершает свою текущую сессию — делаем логаут
    if session.get('_session_id') == session_id:
        terminate_session(session_id)
        logout_user()
        session.clear()
        return jsonify({'success': True, 'message': 'Session terminated, logging out...'}), 200

    # Иначе просто удаляем метаданные
    terminate_session(session_id)
    return jsonify({'success': True, 'message': 'Session terminated successfully'}), 200


@app.route('/api/sessions/terminate-all', methods=['POST'])
@login_required
def api_terminate_all_sessions():
    """Завершает все сессии пользователя (кроме текущей)"""
    exclude = session.get('_session_id') if not current_user.is_admin else None
    user_id = current_user.id if not current_user.is_admin else None

    if current_user.is_admin and redis_client:
        # Админ: завершает ВСЕ сессии (осторожно!)
        terminated = 0
        try:
            keys = redis_client.keys("session:*")
            for key in keys:
                sid = key.replace("session:", "")
                if exclude and sid == exclude:
                    continue
                if terminate_session(sid):
                    terminated += 1
        except redis.RedisError as e:
            logger.error(f"Failed to terminate all sessions: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
        return jsonify({'success': True, 'terminated': terminated}), 200
    else:
        # Обычный пользователь: только свои
        count = terminate_all_user_sessions(user_id, exclude_session_id=exclude)
        return jsonify({'success': True, 'terminated': count}), 200

# =============================================================================
# ⚙️ Маршруты: Админ-панель
# =============================================================================

def admin_required(f):
    """Decorator для ограничения доступа к админ-функциям"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    """Панель администратора"""
    # Статистика
    user_count = User.query.count()
    message_count = Message.query.count()

    return render_template('admin.html', 
                          user_count=user_count, 
                          message_count=message_count)


@app.route('/api/admin/users', methods=['GET'])
@login_required
@admin_required
def api_list_users():
    """Список пользователей (только из локальной БД)"""
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify({
        'success': True,
        'users': [u.to_dict() for u in users],
        'total': len(users)
    }), 200


@app.route('/api/admin/audit', methods=['GET'])
@login_required
@admin_required
def api_audit_log():
    """Журнал аудита (упрощённый — последние логи)"""
    # В продакшене здесь должен быть запрос к proper audit log системе
    return jsonify({
        'success': True,
        'note': 'Audit logging not implemented. Check application logs.',
        'logs': []
    }), 200

# =============================================================================
# 🔍 Health check и служебные endpoint'ы
# =============================================================================

@app.route('/health')
def health_check():
    """Health check для Docker/Kubernetes"""
    status = {
        'status': 'healthy',
        'service': 'portal',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '1.0.0'
    }

    # Проверка БД
    try:
        db.session.execute('SELECT 1')
        status['database'] = 'ok'
    except Exception as e:
        status['database'] = f'error: {str(e)}'
        status['status'] = 'degraded'

    # Проверка Redis
    if redis_client:
        try:
            redis_client.ping()
            status['redis'] = 'ok'
        except Exception as e:
            status['redis'] = f'warning: {str(e)}'

    # Проверка Ollama
    try:
        resp = requests.get(f"{app.config['OLLAMA_HOST']}/api/tags", timeout=5)
        if resp.status_code == 200:
            status['ollama'] = 'ok'
        else:
            status['ollama'] = f'error: HTTP {resp.status_code}'
    except Exception as e:
        status['ollama'] = f'error: {str(e)}'
        status['status'] = 'degraded'

    http_code = 200 if status['status'] == 'healthy' else 503
    return jsonify(status), http_code


@app.route('/api/status')
@login_required
def api_status():
    """Статус приложения для авторизованных пользователей"""
    return jsonify({
        'user': current_user.to_dict(),
        'model': app.config['OLLAMA_MODEL'],
        'ollama_host': app.config['OLLAMA_HOST'],
        'session_id': get_session_id()[:12] + '...'
    }), 200

# =============================================================================
# ❌ Обработчики ошибок (устойчивые к отсутствию шаблонов)
# =============================================================================

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found', 'path': request.path}), 404
    return '404 — Page not found', 404, {'Content-Type': 'text/plain'}


@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Forbidden'}), 403
    return '403 — Access denied', 403, {'Content-Type': 'text/plain'}


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal error: {e}", exc_info=True)
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return '500 — Internal server error', 500, {'Content-Type': 'text/plain'}

# =============================================================================
# 🚀 Точка входа
# =============================================================================

if __name__ == '__main__':
    # Только для разработки! В production используйте Gunicorn
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

    logger.info(f"🚀 Starting OpenCode Assistant (debug={debug_mode})")
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5443)),
        debug=debug_mode,
        threaded=True
    )
