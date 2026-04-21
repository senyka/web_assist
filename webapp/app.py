import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import ldap
import requests
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///data/app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# LDAP конфигурация
LDAP_URI = os.environ.get('LDAP_URI', 'ldap://localhost:389')
LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN', 'dc=opencode,dc=local')
LDAP_ADMIN_DN = os.environ.get('LDAP_ADMIN_DN', 'cn=admin,dc=opencode,dc=local')
LDAP_ADMIN_PASSWORD = os.environ.get('LDAP_ADMIN_PASSWORD', 'adminpassword')

# Ollama конфигурация
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
OLLAMA_MODEL = 'qwen:7b'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Модели базы данных
class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(500))
    request_type = db.Column(db.String(50))
    request_data = db.Column(db.Text)
    response_status = db.Column(db.String(20))

class Session(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), unique=True, nullable=False)
    username = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    messages = db.relationship('Message', backref='session', lazy=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('session.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' или 'assistant'
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def authenticate_ldap(username, password):
    """Аутентификация через LDAP"""
    try:
        conn = ldap.initialize(LDAP_URI)
        conn.set_option(ldap.OPT_REFERRALS, 0)
        
        user_dn = f"uid={username},{LDAP_BASE_DN}"
        
        try:
            conn.simple_bind_s(user_dn, password)
            
            # Сохраняем пользователя в БД если его нет
            user = User.query.filter_by(username=username).first()
            if not user:
                user = User(username=username, is_admin=(username == 'admin'))
                db.session.add(user)
                db.session.commit()
            
            conn.unbind()
            return user
        except ldap.INVALID_CREDENTIALS:
            conn.unbind()
            return None
        except ldap.NO_SUCH_OBJECT:
            conn.unbind()
            return None
    except Exception as e:
        print(f"LDAP error: {e}")
        return None

def log_audit(username, request_type, request_data, response_status, ip_address=None, user_agent=None):
    """Логирование действий в аудит"""
    audit = AuditLog(
        username=username,
        ip_address=ip_address or request.remote_addr,
        user_agent=user_agent or request.headers.get('User-Agent', ''),
        request_type=request_type,
        request_data=request_data[:1000] if request_data else '',
        response_status=response_status
    )
    db.session.add(audit)
    db.session.commit()

def get_ollama_response(messages):
    """Получение ответа от Ollama"""
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False
            },
            timeout=120
        )
        response.raise_for_status()
        return response.json().get('message', {}).get('content', '')
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('chat'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('chat'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = authenticate_ldap(username, password)
        
        if user:
            login_user(user)
            session.permanent = True
            
            # Создаем сессию
            db_session = Session(
                session_id=f"{username}_{datetime.utcnow().timestamp()}",
                username=username
            )
            db.session.add(db_session)
            db.session.commit()
            
            log_audit(username, 'LOGIN', f'User logged in', 'SUCCESS')
            
            next_page = request.args.get('next')
            return redirect(next_page or url_for('chat'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    log_audit(current_user.username, 'LOGOUT', 'User logged out', 'SUCCESS')
    logout_user()
    return redirect(url_for('login'))

@app.route('/chat')
@login_required
def chat():
    sessions = Session.query.filter_by(username=current_user.username, is_active=True).order_by(Session.last_activity.desc()).all()
    current_session_id = request.args.get('session_id')
    
    if not current_session_id and sessions:
        current_session_id = sessions[0].id
    
    return render_template('chat.html', sessions=sessions, current_session_id=current_session_id)

@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    data = request.get_json()
    message = data.get('message', '')
    session_id = data.get('session_id')
    
    if not message:
        return jsonify({'error': 'Message is required'}), 400
    
    # Получаем или создаем сессию
    if session_id:
        db_session = Session.query.get(session_id)
    else:
        db_session = Session(
            session_id=f"{current_user.username}_{datetime.utcnow().timestamp()}",
            username=current_user.username
        )
        db.session.add(db_session)
        db.session.commit()
    
    # Сохраняем сообщение пользователя
    user_message = Message(
        session_id=db_session.id,
        role='user',
        content=message
    )
    db.session.add(user_message)
    
    # Получаем историю сообщений
    messages = []
    history = Message.query.filter_by(session_id=db_session.id).order_by(Message.timestamp.asc()).all()
    for msg in history:
        messages.append({'role': msg.role, 'content': msg.content})
    
    # Добавляем текущее сообщение
    messages.append({'role': 'user', 'content': message})
    
    # Получаем ответ от модели
    response_content = get_ollama_response(messages)
    
    # Сохраняем ответ ассистента
    assistant_message = Message(
        session_id=db_session.id,
        role='assistant',
        content=response_content
    )
    db.session.add(assistant_message)
    
    # Обновляем активность сессии
    db_session.last_activity = datetime.utcnow()
    
    db.session.commit()
    
    # Логируем аудит
    log_audit(
        current_user.username,
        'CHAT_MESSAGE',
        message,
        'SUCCESS',
        ip_address=request.remote_addr,
        user_agent=request.headers.get('User-Agent', '')
    )
    
    return jsonify({
        'response': response_content,
        'session_id': db_session.id
    })

@app.route('/api/sessions')
@login_required
def api_sessions():
    sessions = Session.query.filter_by(username=current_user.username, is_active=True).order_by(Session.last_activity.desc()).all()
    return jsonify([{
        'id': s.id,
        'session_id': s.session_id,
        'created_at': s.created_at.isoformat(),
        'last_activity': s.last_activity.isoformat(),
        'message_count': len(s.messages)
    } for s in sessions])

@app.route('/api/messages/<int:session_id>')
@login_required
def api_messages(session_id):
    db_session = Session.query.filter_by(id=session_id, username=current_user.username).first_or_404()
    messages = Message.query.filter_by(session_id=session_id).order_by(Message.timestamp.asc()).all()
    return jsonify([{
        'id': m.id,
        'role': m.role,
        'content': m.content,
        'timestamp': m.timestamp.isoformat()
    } for m in messages])

# Админ панель
@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        flash('Доступ запрещен', 'error')
        return redirect(url_for('chat'))
    
    return render_template('admin.html')

@app.route('/api/admin/audit')
@login_required
def api_audit():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=per_page)
    
    return jsonify({
        'logs': [{
            'id': log.id,
            'username': log.username,
            'timestamp': log.timestamp.isoformat(),
            'ip_address': log.ip_address,
            'request_type': log.request_type,
            'request_data': log.request_data,
            'response_status': log.response_status
        } for log in logs.items],
        'total': logs.total,
        'pages': logs.pages,
        'current_page': page
    })

@app.route('/api/admin/sessions')
@login_required
def api_admin_sessions():
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    sessions = Session.query.filter_by(is_active=True).order_by(Session.last_activity.desc()).limit(100).all()
    
    return jsonify([{
        'id': s.id,
        'session_id': s.session_id,
        'username': s.username,
        'created_at': s.created_at.isoformat(),
        'last_activity': s.last_activity.isoformat(),
        'message_count': len(s.messages),
        'is_active': s.is_active
    } for s in sessions])

@app.route('/api/admin/session/<int:session_id>')
@login_required
def api_admin_session_detail(session_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    db_session = Session.query.get_or_404(session_id)
    messages = Message.query.filter_by(session_id=session_id).order_by(Message.timestamp.asc()).all()
    
    return jsonify({
        'session': {
            'id': db_session.id,
            'session_id': db_session.session_id,
            'username': db_session.username,
            'created_at': db_session.created_at.isoformat(),
            'last_activity': db_session.last_activity.isoformat(),
            'is_active': db_session.is_active
        },
        'messages': [{
            'id': m.id,
            'role': m.role,
            'content': m.content,
            'timestamp': m.timestamp.isoformat()
        } for m in messages]
    })

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=80, debug=True)
