import os
import json
from datetime import datetime, timezone

import psycopg2
from psycopg2.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL")


def _conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada no ambiente do Render.")
    return psycopg2.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    label_quarantine TEXT,
                    label_importantes TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    email TEXT PRIMARY KEY,
                    token_encrypted TEXT NOT NULL,
                    has_refresh_token BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cleanup_runs (
                    id SERIAL PRIMARY KEY,
                    email TEXT NOT NULL,
                    moved_count INT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
        conn.commit()


def upsert_user(email: str, label_quarantine: str, label_importantes: str):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (email, label_quarantine, label_importantes)
                VALUES (%s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    label_quarantine = EXCLUDED.label_quarantine,
                    label_importantes = EXCLUDED.label_importantes;
            """, (email, label_quarantine, label_importantes))
        conn.commit()


def save_token(email: str, token_encrypted: str, has_refresh_token: bool):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tokens (email, token_encrypted, has_refresh_token, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (email) DO UPDATE SET
                    token_encrypted = EXCLUDED.token_encrypted,
                    has_refresh_token = EXCLUDED.has_refresh_token,
                    updated_at = NOW();
            """, (email, token_encrypted, has_refresh_token))
        conn.commit()


def load_token(email: str):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT token_encrypted, has_refresh_token FROM tokens WHERE email=%s;", (email,))
            row = cur.fetchone()
            return row if row else None


def list_active_users(limit: int = 200):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.email, u.label_quarantine, u.label_importantes
                FROM users u
                ORDER BY u.created_at DESC
                LIMIT %s;
            """, (limit,))
            return cur.fetchall()


def log_cleanup_run(email: str, moved_count: int):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cleanup_runs (email, moved_count)
                VALUES (%s, %s);
            """, (email, moved_count))
        conn.commit()


def last_runs(limit: int = 50):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT email, moved_count, created_at
                FROM cleanup_runs
                ORDER BY created_at DESC
                LIMIT %s;
            """, (limit,))
            return cur.fetchall()


