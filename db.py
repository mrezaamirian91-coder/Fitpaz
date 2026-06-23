import sqlite3
import json
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "fitpaz.db")


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                step TEXT DEFAULT 'new',
                goal TEXT,
                restrictions TEXT DEFAULT '[]',
                cuisine TEXT,
                usage_count INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_active TEXT,
                created_at TEXT,
                allow_daily_anchor INTEGER DEFAULT 0,
                is_subscribed INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                recipe_name TEXT,
                rating TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recipe_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                recipe_name TEXT,
                calories INTEGER,
                created_at TEXT
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_user(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if row is None:
            now = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO users (user_id, created_at, last_active) VALUES (?, ?, ?)",
                (user_id, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

        user = dict(row)
        user["restrictions"] = json.loads(user["restrictions"] or "[]")
        return user


def update_user(user_id: int, **fields):
    if not fields:
        return
    if "restrictions" in fields and isinstance(fields["restrictions"], list):
        fields["restrictions"] = json.dumps(fields["restrictions"], ensure_ascii=False)

    fields["last_active"] = datetime.utcnow().isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [user_id]

    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", values)
        conn.commit()


def days_since_last_active(user_id: int) -> int:
    user = get_user(user_id)
    last_active = datetime.fromisoformat(user["last_active"])
    delta = datetime.utcnow() - last_active
    return delta.days


def log_feedback(user_id: int, recipe_name: str, rating: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback (user_id, recipe_name, rating, created_at) VALUES (?, ?, ?, ?)",
            (user_id, recipe_name, rating, datetime.utcnow().isoformat()),
        )
        conn.commit()


def log_recipe(user_id: int, recipe_name: str, calories: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO recipe_log (user_id, recipe_name, calories, created_at) VALUES (?, ?, ?, ?)",
            (user_id, recipe_name, calories, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_weekly_stats(user_id: int) -> dict:
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT calories FROM recipe_log WHERE user_id = ? AND created_at >= ?",
            (user_id, week_ago),
        ).fetchall()

    if not rows:
        return {"count": 0, "avg_calories": 0}

    calories_list = [r["calories"] for r in rows if r["calories"]]
    avg = sum(calories_list) / len(calories_list) if calories_list else 0
    return {"count": len(rows), "avg_calories": round(avg)}


def get_all_active_users_for_reminder():
    """کاربرانی که اجازه لنگر روزانه داده‌اند و باید پیام روزانه دریافت کنند."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM users WHERE allow_daily_anchor = 1"
        ).fetchall()
    return [r["user_id"] for r in rows]
