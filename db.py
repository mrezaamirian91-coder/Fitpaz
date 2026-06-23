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
                provider TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recipe_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                recipe_name TEXT,
                calories INTEGER,
                provider TEXT,
                created_at TEXT
            )
        """)
        conn.commit()

        # migration امن برای دیتابیس‌هایی که قبلاً بدون ستون provider ساخته شدن
        for table in ("feedback", "recipe_log"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN provider TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # ستون از قبل وجود داره، مشکلی نیست


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


def log_feedback(user_id: int, recipe_name: str, rating: str, provider: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback (user_id, recipe_name, rating, provider, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, recipe_name, rating, provider, datetime.utcnow().isoformat()),
        )
        conn.commit()


def log_recipe(user_id: int, recipe_name: str, calories: int, provider: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO recipe_log (user_id, recipe_name, calories, provider, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, recipe_name, calories, provider, datetime.utcnow().isoformat()),
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


def get_feedback_stats_by_provider() -> dict:
    """آماره‌ی خام فیدبک به تفکیک مدل (OpenAI / Gemini) - برای تست A/B آینده.
    خروجی نمونه: {"openai": {"good": 12, "bad": 3}, "gemini": {"good": 8, "bad": 5}}"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT provider, rating, COUNT(*) as cnt FROM feedback GROUP BY provider, rating"
        ).fetchall()

    stats = {}
    for r in rows:
        provider = r["provider"] or "unknown"
        stats.setdefault(provider, {"good": 0, "bad": 0})
        if r["rating"] in ("good", "bad"):
            stats[provider][r["rating"]] = r["cnt"]
    return stats
