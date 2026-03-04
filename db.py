import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from cryptography.fernet import Fernet

DATABASE_URL = os.getenv("DATABASE_URL", "")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não definido")
if not TOKEN_ENCRYPTION_KEY:
    raise RuntimeError("TOKEN_ENCRYPTION_KEY não definido")

fernet = Fernet(TOKEN_ENCRYPTION_KEY.encode("utf-8"))

def conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS gmail_tokens (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            token_enc BYTEA NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cleanup_runs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            moved_count INTEGER NOT NULL DEFAULT 0,
            ran_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        c.commit()

def upsert_user(email: str) -> int:
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO users (email) VALUES (%s)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id;
        """, (email,))
        user_id = cur.fetchone()[0]
        c.commit()
        return user_id

def set_user_active(email: str, active: bool):
    with conn() as c, c.cursor() as cur:
        cur.execute("UPDATE users SET is_active=%s WHERE email=%s", (active, email))
        c.commit()

def get_user(email: str):
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        return cur.fetchone()

def save_token(user_id: int, token_json: str):
    token_enc = fernet.encrypt(token_json.encode("utf-8"))
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO gmail_tokens (user_id, token_enc)
            VALUES (%s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET token_enc = EXCLUDED.token_enc, updated_at = NOW();
        """, (user_id, token_enc))
        c.commit()

def load_token(user_id: int) -> str | None:
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT token_enc FROM gmail_tokens WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        if not row:
            return None
        token_enc = row[0]
        token_json = fernet.decrypt(token_enc).decode("utf-8")
        return token_json

def log_cleanup_run(user_id: int, moved: int):
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO cleanup_runs (user_id, moved_count) VALUES (%s, %s)",
            (user_id, moved),
        )
        c.commit()

def list_active_users():
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, email FROM users WHERE is_active=true")
        return cur.fetchall()

def last_runs(email: str, limit: int = 10):
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
        SELECT cr.moved_count, cr.ran_at
        FROM cleanup_runs cr
        JOIN users u ON u.id = cr.user_id
        WHERE u.email=%s
        ORDER BY cr.ran_at DESC
        LIMIT %s
        """, (email, limit))
        return cur.fetchall()