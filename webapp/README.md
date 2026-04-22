# OpenCode Assistant — Веб-интерфейс для локального AI

[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![Flask](https://img.shields.io/badge/flask-%23000.svg?style=flat&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Ollama](https://img.shields.io/badge/ollama-000000?style=flat&logo=ollama&logoColor=white)](https://ollama.com/)
[![LLDAP](https://img.shields.io/badge/ldap-000000?style=flat&logo=ldap&logoColor=white)](https://github.com/lldap/lldap)

Веб-приложение для работы с локальной языковой моделью **Qwen-7B** через **Ollama** с корпоративной аутентификацией через **LLDAP** (Lightweight LDAP).

---

## 🧩 Архитектура

```
┌─────────────────────────────────────────┐
│              Docker Compose             │
├─────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐ │
│  │  Ollama │  │ Portal  │  │ LLDAP*  │ │
│  │  :11434 │  │ :5443   │  │ :3890   │ │
│  │  AI API │  │ Flask   │  │ Auth    │ │
│  └────┬────┘  └────┬────┘  └────┬────┘ │
│       │           │           │        │
│  ┌────▼───────────▼───────────▼────┐  │
│  │         internal network        │  │
│  └─────────────────────────────────┘  │
└────────────────────────────────────────┘
* LLDAP может быть запущен отдельно
```

### Компоненты

| Сервис | Контейнер | Порт | Описание |
|--------|-----------|------|----------|
| **Ollama** | `ollama` | `11434` | Сервер для запуска LLM (Qwen, Llama, etc.) |
| **Portal** | `web-assist` | `5443` | Flask-приложение: чат, сессии, админ-панель |
| **LLDAP** | *внешний* | `3890` | Lightweight LDAP для аутентификации пользователей |

---

## 🚀 Быстрый старт

### Требования

- ✅ Docker 24+ и Docker Compose v2+
- ✅ Минимум **16 ГБ ОЗУ** для `qwen:7b` (или 8 ГБ для `qwen:1.8b`)
- ✅ NVIDIA GPU + `nvidia-container-toolkit` — опционально, но рекомендуется
- ✅ Запущенный **LLDAP** на том же сервере или доступный по сети

### 1. Клонирование репозитория

```bash
git clone https://github.com/senyka/web_assist.git
cd web_assist
```

### 2. Настройка окружения

Скопируйте пример файла окружения и отредактируйте под вашу инфраструктуру:

```bash
cp .env.example .env
nano .env  # или ваш редактор
```

#### Пример `.env`

```bash
# ===== Flask =====
SECRET_KEY=your-32-char-secret-key-here-change-in-prod
FLASK_DEBUG=false

# ===== Ollama =====
OLLAMA_MODEL=qwen:7b
# Для слабых машин используйте квантованную версию:
# OLLAMA_MODEL=qwen:7b-q4_K_M  # ~2.1 ГБ вместо 4.7 ГБ
# OLLAMA_MODEL=qwen:1.8b        # ~1.2 ГБ, быстрее

# Путь к моделям на хосте (опционально, для бэкапов)
# OLLAMA_MODELS_HOST_PATH=/opt/ollama/models

# ===== LLDAP (внешний) =====
# Пароль должен совпадать с настройками вашего LLDAP
LLDAP_ADMIN_PASSWORD=PassAdmin
LLDAP_ADMIN_USER=Admin

# LDAP connection settings
LDAP_URI=ldap://lldap:3890
LDAP_BASE_DN=dc=assist,dc=com
LDAP_ADMIN_DN=uid=admin,ou=people,dc=assist,dc=com
```

> ⚠️ **Важно**: Если в `SECRET_KEY` есть символ `$`, экранируйте его удвоением: `$$`  
> Пример: `SECRET_KEY=abc$$def` → в приложении будет `abc$def`

### 3. Запуск стека

```bash
# Сборка образов (первый раз)
docker compose build

# Запуск всех сервисов
docker compose up -d
```

### 4. Ожидание загрузки модели

Первый запуск скачает модель (~4.7 ГБ для `qwen:7b`). Следите за прогрессом:

```bash
docker compose logs -f ollama
```

Ожидаемый вывод:
```
⬇️  Pulling model: qwen:7b
pulling manifest
pulling 7b...: 45% ▕███████▏ 2.1GB/4.7GB
...
✅ Pull complete
🔄 Starting Ollama in foreground...
```

### 5. Доступ к приложению

| Сервис | URL | Примечание |
|--------|-----|------------|
| 🔐 **Portal** | [`http://localhost:5443`](http://localhost:5443) | Веб-интерфейс, вход через LLDAP |
| 👥 **LLDAP UI** | `http://<host-ip>:17170` | Управление пользователями (если порт опубликован) |
| 🤖 **Ollama API** | `http://localhost:11434` | Прямой доступ к AI API |

> ⚠️ Используйте **`http://`**, а не `https://` — SSL не настроен по умолчанию.

---

## 🔗 Подключение к внешнему LLDAP

Этот проект **не включает** сервис LLDAP — он предполагается запущенным отдельно.

### Вариант А: LLDAP на том же хосте (через `host-gateway`)

1. Убедитесь, что в `docker-compose.yml` LLDAP есть проброс порта:
```yaml
ports:
  - "3890:3890"  # LDAP
```

2. В `docker-compose.yml` этого проекта добавьте в сервис `portal`:
```yaml
extra_hosts:
  - "lldap:host-gateway"  # Резолвит 'lldap' в IP хоста (Linux)
```

### Вариант Б: Общая Docker-сеть (рекомендуется)

1. Создайте общую сеть:
```bash
docker network create dash-panel-net
```

2. В `docker-compose.yml` **LLDAP** добавьте:
```yaml
services:
  lldap:
    networks:
      - web-assist-net

networks:
  web-assist-net:
    external: true
```

3. В `docker-compose.yml` **этого проекта**:
```yaml
networks:
  internal:
    external: true
    name: web-assist-net
```

✅ Преимущество: контейнеры видят друг друга по имени без проброса портов.

### Проверка соединения с LLDAP

```bash
docker compose exec web-assist python3 -c "
import ldap, os
try:
    conn = ldap.initialize(os.environ['LDAP_URI'])
    conn.set_option(ldap.OPT_REFERRALS, 0)
    conn.simple_bind_s(os.environ['LDAP_ADMIN_DN'], os.environ['LDAP_ADMIN_PASSWORD'])
    print('✅ LDAP connected')
    conn.unbind()
except Exception as e:
    print(f'❌ LDAP error: {e}')
"
```

---

## 🤖 Автозагрузка модели

При старте контейнера `ollama`:

1. ✅ Сервер Ollama запускается
2. ⬇️ Автоматически скачивается модель из `OLLAMA_MODEL` (если ещё не в кэше)
3. 🔥 Опционально: выполняется "прогрев" одним тестовым запросом
4. 🎯 Контейнер переходит в статус `healthy` только когда модель готова

### Переменные окружения Ollama

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `OLLAMA_MODEL` | Имя модели для автозагрузки | `qwen:7b` |
| `OLLAMA_WARMUP` | Прогрев модели после загрузки (`true`/`false`) | `true` |

### Управление моделями вручную

```bash
# Скачать дополнительную модель
docker compose exec ollama ollama pull llama3.2

# Список моделей
docker compose exec ollama ollama list

# Удалить модель
docker compose exec ollama ollama rm qwen:7b

# Запустить тестовый запрос
docker compose exec ollama ollama run qwen:7b "Привет! Кто ты?"
```

### 💡 Советы по выбору модели

| Модель | Размер | ОЗУ | Скорость | Рекомендация |
|--------|--------|-----|----------|--------------|
| `qwen:7b` | 4.7 ГБ | ~14 ГБ | 🐢 | Полноценное качество |
| `qwen:7b-q4_K_M` | 2.1 ГБ | ~6 ГБ | 🚀 | Оптимальный баланс |
| `qwen:1.8b` | 1.2 ГБ | ~3 ГБ | ⚡ | Слабые машины / тесты |

---

## 🔐 Аутентификация и сессии

### Вход в систему

- Пользователи аутентифицируются через **LLDAP**
- Структура DN: `uid=<username>,ou=people,dc=assist,dc=com`
- После успешного входа создаётся запись в локальной БД (если пользователя нет)

### Управление сессиями

#### Для пользователей

- **Выйти**: Нажмите 👤 имя пользователя → "🚪 Выйти" в навбаре
- **Управление сессиями**: Кнопка "🔐 Сессии" открывает список активных сессий
  - Завершить любую свою сессию (кроме текущей)
  - "Завершить другие сессии" — выход со всех устройств, кроме текущего

#### Для администраторов

Админы видят **все сессии всех пользователей** и могут:

| Действие | Описание |
|----------|----------|
| ✕ Завершить | Завершить конкретную сессию любого пользователя |
| 🔥 Завершить ВСЕ | Массовое завершение всех сессий (кроме текущей админа) |

### 🔌 API Endpoints: Сессии

| Метод | Путь | Описание | Доступ |
|-------|------|----------|--------|
| `POST` | `/logout` | Завершить текущую сессию | Авторизованный |
| `GET` | `/api/sessions` | Список сессий | Авторизованный |
| `DELETE` | `/api/sessions/<id>` | Завершить сессию по ID | Владелец или админ |
| `POST` | `/api/sessions/terminate-all` | Массовое завершение | Авторизованный |

### 💾 Хранение сессий

- **По умолчанию**: в памяти (`active_sessions` dict) + SQLite (`sessions` таблица)
- **Для продакшена**: рекомендуется заменить на Redis:

```python
# Пример конфигурации Flask-Session с Redis
from flask_session import Session
import redis

app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_REDIS'] = redis.from_url('redis://redis:6379')
Session(app)
```

---

## 🌐 API Endpoints

### Публичные

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/` | Редирект на `/login` если не авторизован |
| `GET` | `/login` | Страница входа |
| `POST` | `/login` | Аутентификация (form: `username`, `password`) |
| `GET` | `/health` | Health check для Docker |

### Защищённые (требуется авторизация)

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/chat` | Интерфейс чата с AI |
| `POST` | `/api/chat` | Отправка сообщения в чат |
| `GET` | `/api/sessions` | Список активных сессий |
| `DELETE` | `/api/sessions/<id>` | Завершение сессии |

### Админ-панель (только `is_admin`)

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/admin` | Панель управления |
| `GET` | `/api/admin/users` | Список пользователей |
| `GET` | `/api/admin/audit` | Журнал аудита |

---

## ⚙️ Конфигурация

### Переменные окружения

#### Приложение (Portal)

| Переменная | Описание | Пример | Обязательна |
|------------|----------|--------|-------------|
| `SECRET_KEY` | Ключ сессий Flask | `abc123...` | ✅ |
| `FLASK_ENV` | Режим работы (`production`/`development`) | `production` | ❌ |
| `FLASK_DEBUG` | Включить отладку (`true`/`false`) | `false` | ❌ |
| `DATABASE_URL` | Строка подключения к БД | `sqlite:////app/data/app.db` | ✅ |
| `LDAP_URI` | Адрес LDAP-сервера | `ldap://lldap:3890` | ✅ |
| `LDAP_BASE_DN` | Базовый домен LDAP | `dc=assist,dc=com` | ✅ |
| `LDAP_ADMIN_DN` | DN для bind-операций | `uid=admin,ou=people,...` | ✅ |
| `LDAP_ADMIN_PASSWORD` | Пароль для bind | `***` | ✅ |
| `OLLAMA_HOST` | Адрес Ollama API | `http://ollama:11434` | ✅ |
| `OLLAMA_MODEL` | Модель по умолчанию | `qwen:7b` | ❌ |

#### Ollama

| Переменная | Описание | Пример |
|------------|----------|--------|
| `OLLAMA_HOST` | Интерфейс для прослушивания | `0.0.0.0` |
| `OLLAMA_MODELS` | Путь к хранилищу моделей | `/root/.ollama/models` |
| `OLLAMA_NUM_PARALLEL` | Параллельные запросы | `1` |

### Структура томов

| Том | Назначение | Путь в контейнере |
|-----|------------|-------------------|
| `ollama_data` | Модели Ollama, кэш | `/root/.ollama` |
| `./webapp/data` | SQLite БД приложения | `/app/data` |

> 💡 Чтобы сохранить модели на хосте (для бэкапа), замените в `docker-compose.yml`:
> ```yaml
> volumes:
>   - /opt/ollama/models:/root/.ollama  # вместо ollama_data
> ```

---

## 🚀 GPU-ускорение (NVIDIA)

### Требования

1. Установите [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
2. Убедитесь, что драйверы работают:
```bash
nvidia-smi
```

### Включение GPU в Docker Compose

Раскомментируйте блок в секции `ollama`:

```yaml
ollama:
  # ...
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

### Проверка использования GPU

```bash
# Внутри контейнера
docker compose exec ollama nvidia-smi

# Или следите за логами — должно быть:
# time=... level=INFO source=types.go:60 msg="inference compute" id=0 library=cuda ...
```

> ⚠️ **Intel/AMD iGPU**: Используйте `devices: - /dev/dri:/dev/dri` и `group_add: - video` (как в конфиге по умолчанию).

---

## 🔍 Диагностика

```bash
# Статус всех сервисов
docker compose ps

# Логи конкретного сервиса
docker compose logs -f ollama
docker compose logs -f web-assist
docker compose logs -f lldap  # если в этом compose

# Проверка Ollama API
curl http://localhost:11434/api/tags

# Проверка LDAP из контейнера portal
docker compose exec web-assist python3 -c "
import ldap, os
conn = ldap.initialize(os.environ['LDAP_URI'])
conn.simple_bind_s(os.environ['LDAP_ADMIN_DN'], os.environ['LDAP_ADMIN_PASSWORD'])
print('✅ LDAP OK')
"

# Проверка связи portal → ollama
docker compose exec web-assist python3 -c "
import requests
r = requests.get(os.environ['OLLAMA_HOST'] + '/api/tags', timeout=5)
print('✅ Ollama API:', r.status_code)
"

# Проверка прав на запись БД
docker compose exec web-assist touch /app/data/test.txt && echo "✅ DB write OK"
```

### Частые проблемы

| Симптом | Возможная причина | Решение |
|---------|------------------|---------|
| `WARN: variable not set` | `$` в `.env` без экранирования | Замените `$` на `$$` |
| `unable to open database file` | Папка для БД не существует | Создайте `mkdir -p ./webapp/data` |
| `LDAP connection refused` | LLDAP не в той сети | Добавьте `extra_hosts` или shared network |
| `model not found` | Модель ещё скачивается | Подождите или проверьте `docker compose logs ollama` |
| `permission denied: /dev/dri` | Нет прав на GPU | `sudo usermod -aG video $USER` + перезагрузка |
| Страница не грузится | Доступ по `https://` вместо `http://` | Используйте `http://localhost:5443` |

---

## 🧹 Обслуживание

### Обновление моделей

```bash
# Обновить модель до последней версии
docker compose exec ollama ollama pull qwen:7b

# Перезапустить Ollama для применения
docker compose restart ollama
```

### Бэкап данных

```bash
# Бэкап моделей Ollama
tar -czf ollama_backup_$(date +%F).tar.gz -C /var/lib/docker/volumes/web_assist-main_ollama_data/_data .

# Бэкап БД приложения
cp ./webapp/data/app.db ./backup/app_$(date +%F).db
```

### Очистка

```bash
# Остановить всё
docker compose down

# Удалить тома (⚠️ потеря данных!)
docker compose down -v

# Удалить образы для освобождения места
docker compose build --no-cache

# Очистить неиспользуемые ресурсы Docker
docker system prune -af
```

---

## 🔒 Безопасность

### ✅ Перед запуском в production

1. **Сгенерируйте уникальный `SECRET_KEY`**:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

2. **Отключите debug-режим** в `.env`:
```bash
FLASK_DEBUG=false
```

3. **Используйте Gunicorn вместо Flask dev-server**:

В `webapp/Dockerfile`:
```dockerfile
# В requirements.txt добавьте: gunicorn==21.2.0
CMD ["gunicorn", "--bind", "0.0.0.0:5443", "--workers", "2", "--timeout", "120", "app:app"]
```

4. **Добавьте reverse proxy с HTTPS** (Nginx/Caddy/Traefik)

5. **Ограничьте доступ к портам** через фаервол:
```bash
# Пример для UFW
sudo ufw allow 5443/tcp  # Portal
sudo ufw allow 11434/tcp # Ollama (только если нужен внешний доступ)
# sudo ufw deny 3890     # LDAP — только внутри Docker
```

6. **Не коммитьте `.env` в репозиторий**:
```bash
echo ".env" >> .gitignore
```

### 🔐 LDAP Security

- Используйте `ldaps://` или STARTTLS для шифрования трафика
- Храните пароли в секретах, а не в `.env` (Docker secrets / Vault)
- Регулярно ротируйте `LLDAP_ADMIN_PASSWORD`

---

## 📁 Структура проекта

```
web_assist/
├── docker-compose.yml          # Оркестрация сервисов
├── .env.example                # Шаблон переменных окружения
├── .env                        # Локальные настройки (в .gitignore)
├── README.md                   # Этот файл
│
├── scripts/
│   └── init-ollama.sh          # Скрипт автозагрузки модели
│
├── Dockerfile.ollama           # Кастомный образ Ollama с curl
│
└── webapp/
    ├── Dockerfile              # Образ Flask-приложения
    ├── requirements.txt        # Python-зависимости
    ├── app.py                  # Основное приложение
    ├── models.py               # SQLAlchemy модели
    │
    ├── data/                   # SQLite БД (монтируется)
    │   └── app.db
    │
    ├── static/                 # CSS, JS, изображения
    │   ├── css/
    │   └── js/
    │
    └── templates/              # Jinja2 шаблоны
        ├── base.html           # Базовый шаблон
        ├── login.html          # Страница входа
        ├── chat.html           # Интерфейс чата
        └── admin.html          # Админ-панель
```

---

## 🔄 Обновление приложения

```bash
# 1. Получить последние изменения
git pull origin main

# 2. Пересобрать образы
docker compose build --no-cache

# 3. Перезапустить стек
docker compose up -d --force-recreate

# 4. Проверить миграции БД (если есть)
docker compose exec web-assist python3 -c "from app import db; db.create_all()"
```

---

## 🤝 Разработка

### Запуск в режиме разработки

```bash
# Переопределите переменные для dev
echo "FLASK_DEBUG=true" >> .env

# Запустите с пересборкой
docker compose up -d --build

# Следите за логами с перезагрузкой при изменениях
docker compose logs -f web-assist
```

### Добавление зависимостей

1. Добавьте пакет в `webapp/requirements.txt`
2. Пересоберите образ:
```bash
docker compose build web-assist
docker compose up -d --force-recreate web-assist
```

### Локальный запуск без Docker (для отладки)

```bash
# Требования на хосте
sudo apt install libldap2-dev libsasl2-dev python3-dev gcc

# Виртуальное окружение
python3 -m venv venv
source venv/bin/activate
pip install -r webapp/requirements.txt

# Запуск (предварительно настройте .env)
export $(cat .env | xargs)
python webapp/app.py
```

---

## 📜 Лицензия

MIT — используйте, изменяйте и распространяйте свободно.

---

## 🆘 Поддержка

- 🐛 **Баги**: Создайте issue с логами и шагами воспроизведения
- 💡 **Идеи**: Предложите фичу в Discussions
- 🔧 **Вопросы**: Проверьте секцию [Диагностика](#-диагностика) или создайте issue

---

> 🎯 **Совет**: Первый запуск может занять 5–20 минут (скачивание модели).  
> Последующие запуски будут мгновенными — модель остаётся в томе `ollama_data`.

**Готово к работе!** 🚀 Открывайте [`http://localhost:5443`](http://localhost:5443) и начинайте диалог с вашим локальным AI-ассистентом.
