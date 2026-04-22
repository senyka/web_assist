# Tr0jan Assistant - Веб интерфейс для AI ассистента

Веб-интерфейс для работы с локальной AI моделью Qwen-7B через Ollama с LDAP аутентификацией.

## Компоненты

1. **LightLDAP** - Сервер LDAP для аутентификации пользователей
2. **Ollama** - Локальный сервер для запуска модели Qwen-7B
3. **Web Application** - Flask веб-приложение с интерфейсом чата и админ панелью

## Быстрый старт

### Требования
- Docker и Docker Compose
- GPU (опционально, для ускорения работы модели)
- Минимум 8GB RAM для модели Qwen-7B

### Установка

1. Клонируйте репозиторий:
```bash
git clone <repository-url>
cd opencode-assistant
```

2. Загрузите модель Qwen-7B в Ollama:
```bash
docker compose run --rm ollama ollama pull qwen:7b
```

3. Запустите все сервисы:
```bash
docker compose up -d
```

4. Откройте веб-интерфейс:
```
http://localhost:8080
```

## Пользователи по умолчанию

### LDAP Admin
- **Username:** admin
- **Password:** adminpassword

### Тестовый пользователь
- **Username:** testuser
- **Password:** password123

## Структура проекта

```
/workspace
├── docker-compose.yml          # Конфигурация Docker Compose
├── ldap-init.ldif              # LDIF файл для инициализации LDAP
├── webapp/
│   ├── Dockerfile              # Dockerfile для веб-приложения
│   ├── requirements.txt        # Python зависимости
│   ├── app.py                  # Основное Flask приложение
│   └── templates/
│       ├── login.html          # Страница входа
│       ├── chat.html           # Интерфейс чата
│       └── admin.html          # Админ панель
├── ldap-data/                  # Данные LDAP (создается автоматически)
├── ldap-config/                # Конфигурация LDAP (создается автоматически)
├── ollama-data/                # Данные Ollama (создается автоматически)
└── webapp-data/                # Данные веб-приложения (создается автоматически)
```

## Функционал

### Для пользователей
- 🔐 LDAP аутентификация
- 💬 Чат с AI ассистентом (Qwen-7B)
- 📁 Управление сессиями (создание, переключение)
- 📜 История сообщений

### Для администраторов
- 📊 Аудит действий пользователей:
  - Имя пользователя
  - Время запроса
  - IP адрес
  - Тип запроса
  - Статус выполнения
- 👥 Список активных сессий
- 🔍 Просмотр деталей сессии с возможностью просмотра всех сообщений

## API Endpoints

### Публичные
- `POST /login` - Аутентификация
- `GET /logout` - Выход

### Защищенные (требуют аутентификации)
- `GET /chat` - Интерфейс чата
- `POST /api/chat` - Отправка сообщения
- `GET /api/sessions` - Список сессий пользователя
- `GET /api/messages/<session_id>` - Сообщения сессии

### Админ панель (только для администраторов)
- `GET /admin` - Админ панель
- `GET /api/admin/audit` - Журнал аудита
- `GET /api/admin/sessions` - Все активные сессии
- `GET /api/admin/session/<id>` - Детали сессии

## Конфигурация

### Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `SECRET_KEY` | Секретный ключ Flask | dev-secret-key |
| `DATABASE_URL` | URL базы данных | sqlite:///data/app.db |
| `LDAP_URI` | URI LDAP сервера | ldap://ldap:389 |
| `LDAP_BASE_DN` | Базовый DN LDAP | dc=opencode,dc=local |
| `LDAP_ADMIN_DN` | DN администратора LDAP | cn=admin,dc=opencode,dc=local |
| `LDAP_ADMIN_PASSWORD` | Пароль администратора LDAP | adminpassword |
| `OLLAMA_HOST` | Хост Ollama | http://ollama:11434 |

## Добавление новых пользователей в LDAP

1. Создайте LDIF файл с новым пользователем:
```ldif
dn: uid=newuser,ou=users,dc=opencode,dc=local
objectClass: inetOrgPerson
objectClass: posixAccount
uid: newuser
cn: New User
sn: User
uidNumber: 1001
gidNumber: 1001
homeDirectory: /home/newuser
userPassword: {SSHA}hashed_password
```

2. Добавьте пользователя:
```bash
docker exec lightldap ldapadd -x -D "cn=admin,dc=opencode,dc=local" -w adminpassword -f newuser.ldif
```

## Troubleshooting

### Модель не загружается
```bash
docker logs ollama
docker compose restart ollama
```

### Ошибки LDAP
```bash
docker logs lightldap
docker compose restart ldap
```

### Проверка состояния сервисов
```bash
docker compose ps
docker compose logs -f
```

## Безопасность

⚠️ **Важно:** Перед использованием в production:
1. Измените `SECRET_KEY` на случайную строку
2. Смените пароли по умолчанию
3. Настройте HTTPS
4. Ограничьте доступ к портам

## Лицензия

MIT License
