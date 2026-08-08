"""
Database configuration for DealDrift.

Loads Railway-hosted MySQL credentials from environment variables (via a
`.env` file in the project root) and exposes `get_connection()`, a thin
wrapper around `pymysql.connect()`.

No ORM — every other module in this project runs raw SQL through the
connection/cursor this returns.

Required environment variables (see .env.example):
    MYSQL_HOST
    MYSQL_PORT
    MYSQL_USER
    MYSQL_PASSWORD
    MYSQL_DATABASE

Optional:
    MYSQL_CONNECT_TIMEOUT (seconds, default 10)
"""

import os

import pymysql
import pymysql.cursors
from dotenv import load_dotenv

# Load variables from .env in the project root into the process environment.
# Safe to call more than once; does nothing if .env is missing.
load_dotenv()

_REQUIRED_VARS = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")


def _get_db_settings() -> dict:
    """Read and validate MySQL connection settings from the environment.

    Intentionally does NOT default host to 'localhost' — this project only
    talks to a Railway-hosted MySQL instance, so a missing MYSQL_HOST should
    fail loudly rather than silently trying a local database.
    """
    missing = [var for var in _REQUIRED_VARS if not os.getenv(var)]
    if missing:
        raise RuntimeError(
            "Missing required MySQL environment variable(s): "
            f"{', '.join(missing)}. Copy .env.example to .env and fill in "
            "your Railway MySQL credentials."
        )

    return {
        "host": os.environ["MYSQL_HOST"],
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.environ["MYSQL_USER"],
        "password": os.environ["MYSQL_PASSWORD"],
        "database": os.environ["MYSQL_DATABASE"],
        "connect_timeout": int(os.getenv("MYSQL_CONNECT_TIMEOUT", "10")),
    }


def get_connection() -> pymysql.connections.Connection:
    """Open and return a new pymysql connection to the Railway MySQL database.

    Each call opens a fresh connection — callers are responsible for closing
    it (or using it as a context manager) when done. Rows are returned as
    dicts (via DictCursor) so downstream code can reference columns by name.
    """
    settings = _get_db_settings()
    return pymysql.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        database=settings["database"],
        connect_timeout=settings["connect_timeout"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        charset="utf8mb4",
    )


if __name__ == "__main__":
    # Quick manual connectivity check: `python -m config.db_config`
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version;")
            result = cursor.fetchone()
            print(f"Connected to Railway MySQL. Server version: {result['version']}")
    finally:
        conn.close()
