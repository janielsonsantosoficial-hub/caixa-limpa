import os
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = os.getenv("DB_PATH", "caixa_limpa.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            user_id INTEGER PRIMARY KEY,
            token_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cleanup_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            moved INTEGER NOT NULL,
            ran_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)
        conn.commit()


def upsert_user(email: str) -> int:
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO users(email, created_at) VALUES(?, ?)", (email, now))
        conn.commit()
        cur.execute("SELECT id FROM users WHERE email = ?", (email,))
        row = cur.fetchone()
        return int(row["id"])


def save_token(user_id: int, token_json: str):
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO tokens(user_id, token_json, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET token_json=excluded.token_json, updated_at=excluded.updated_at
        """, (user_id, token_json, now))
        conn.commit()


def load_token(user_id: int) -> Optional[str]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT token_json FROM tokens WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return row["token_json"] if row else None


def list_active_users() -> List[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT u.id, u.email
        FROM users u
        JOIN tokens t ON t.user_id = u.id
        ORDER BY u.id ASC
        """)
        return [dict(r) for r in cur.fetchall()]


def log_cleanup_run(user_id: int, moved: int):
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO cleanup_runs(user_id, moved, ran_at) VALUES(?, ?, ?)", (user_id, moved, now))
        conn.commit()


def last_runs(email: str, limit: int = 10) -> List[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT r.moved, r.ran_at
        FROM cleanup_runs r
        JOIN users u ON u.id = r.user_id
        WHERE u.email = ?
        ORDER BY r.ran_at DESC
        LIMIT ?
        """, (email, limit))
        return [dict(r) for r in cur.fetchall()]
