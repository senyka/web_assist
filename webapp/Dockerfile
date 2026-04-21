FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    libldap2-dev \
    libsasl2-dev \
    python3-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копирование зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование приложения
COPY . .

# Создание директории для данных
RUN mkdir -p /app/data

# Экспорт порта
EXPOSE 80

# Запуск приложения
CMD ["gunicorn", "--bind", "0.0.0.0:80", "--workers", "4", "app:app"]
