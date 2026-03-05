import os
import psycopg2
from psycopg2.extras import RealDictCursor


DATABASE_URL = os.getenv("DATABASE_URL", "")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não definido (crie um Postgres no Render e conecte no serviço).")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor, sslmode="require")

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tokens (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        token_json TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT NOW()
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cleanup_runs (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        moved_promocoes INTEGER DEFAULT 0,
        moved_notificacoes INTEGER DEFAULT 0,
        moved_quarentena INTEGER DEFAULT 0,
        moved_lixo INTEGER DEFAULT 0,
        trashed_lixo INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)
    conn.commit()
    cur.close()
    conn.close()

def upsert_user(email: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email=%s", (email,))
    row = cur.fetchone()
    if row:
        user_id = row["id"]
        cur.execute("UPDATE users SET updated_at=NOW() WHERE id=%s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        return user_id

    cur.execute("INSERT INTO users(email) VALUES(%s) RETURNING id", (email,))
    user_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return user_id

def save_token(user_id: int, token_json: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM tokens WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE tokens SET token_json=%s, updated_at=NOW() WHERE user_id=%s", (token_json, user_id))
    else:
        cur.execute("INSERT INTO tokens(user_id, token_json) VALUES(%s, %s)", (user_id, token_json))
    conn.commit()
    cur.close()
    conn.close()

def load_token(user_id: int) -> str | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT token_json FROM tokens WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["token_json"] if row else None

def list_active_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, email FROM users ORDER BY id ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def log_cleanup_run(user_id: int, resultado: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO cleanup_runs(
        user_id, moved_promocoes, moved_notificacoes, moved_quarentena, moved_lixo, trashed_lixo
    ) VALUES(%s,%s,%s,%s,%s,%s)
    """, (
        user_id,
        int(resultado.get("promocoes", {}).get("moved", 0)),
        int(resultado.get("notificacoes", {}).get("moved", 0)),
        int(resultado.get("quarentena", {}).get("moved", 0)),
        int(resultado.get("lixo", {}).get("moved", 0)),
        int(resultado.get("lixo", {}).get("trashed", 0)),
    ))
    conn.commit()
    cur.close()
    conn.close()

def last_runs(email: str, limit: int = 10):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email=%s", (email,))
    user = cur.fetchone()
    if not user:
        cur.close()
        conn.close()
        return []
    user_id = user["id"]
    cur.execute("""
    SELECT moved_promocoes, moved_notificacoes, moved_quarentena, moved_lixo, trashed_lixo, created_at
    FROM cleanup_runs
    WHERE user_id=%s
    ORDER BY created_at DESC
    LIMIT %s
    """, (user_id, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows
