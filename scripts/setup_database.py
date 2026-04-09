"""Скрипт для настройки базы данных."""
import asyncio
import asyncpg
import os
import sys
from pathlib import Path
from typing import Any, Dict

# Корень проекта (родитель каталога scripts), чтобы работал пакет src
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import config


def _database_connect_params() -> Dict[str, Any]:
    """
    Параметры подключения: как в docker-compose (DATABASE_*), иначе config.yaml.

    Внутри контейнера navtelecom-server хост БД — имя сервиса ``postgres``, не localhost.
    """
    if os.environ.get("DATABASE_HOST"):
        return {
            "host": os.environ["DATABASE_HOST"],
            "port": int(os.environ.get("DATABASE_PORT", "5432")),
            "user": os.environ.get("DATABASE_USER") or "navtelecom",
            "password": os.environ.get("DATABASE_PASSWORD") or "password",
            "database": os.environ.get("DATABASE_NAME") or "navtelecom_server",
        }
    db = config.database
    return {
        "host": db["host"],
        "port": int(db["port"]),
        "user": db["user"],
        "password": db.get("password") or "password",
        "database": db.get("name") or "navtelecom_server",
    }


async def setup_database():
    """Настройка базы данных."""
    try:
        params = _database_connect_params()
        # Целевая БД (в Docker уже создана через POSTGRES_DB)
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=params["database"],
        )
        
        print("Подключение к PostgreSQL установлено")
        
        # Чтение схемы
        schema_path = Path(__file__).parent.parent / 'database' / 'schema.sql'
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema_sql = f.read()
        
        # Выполнение SQL
        await conn.execute(schema_sql)
        
        print("База данных успешно настроена")
        
        await conn.close()
        
    except Exception as e:
        print(f"Ошибка настройки базы данных: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(setup_database())

