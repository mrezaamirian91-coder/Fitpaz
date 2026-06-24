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
                is_subscribed INTEGER DEFAULT 0,
                share_variant TEXT
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingredient_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_name TEXT,
                quantity TEXT,
                calories INTEGER,
                provider TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS share_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                variant TEXT,
                invited_at TEXT,
                responded INTEGER DEFAULT 0,
                responded_at TEXT,
                photo_file_id TEXT
            )
        """)
        conn.commit()

        # migration امن برای دیتابیس‌هایی که قبلاً بدون این ستون‌ها ساخته شدن
        for table in ("feedback", "recipe_log"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN provider TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # ستون از قبل وجود داره، مشکلی نیست

        try:
            conn.execute("ALTER TABLE users ADD COLUMN share_variant TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass


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


def log_ingredients(user_id: int, items: list, provider: str = None):
    """ذخیره‌ی ریز هر ماده‌ی تشخیص‌داده‌شده از روی عکس (اسم، مقدار، کالری) برای استفاده‌ی بعدی
    در تحلیل‌ها، شخصی‌سازی و شاخه‌های دیگر محصول."""
    if not items:
        return
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        for item in items:
            conn.execute(
                "INSERT INTO ingredient_log (user_id, item_name, quantity, calories, provider, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, item.get("name", ""), item.get("quantity", ""), item.get("calories", 0), provider, now),
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


def log_share_invite(user_id: int, variant: str) -> int:
    """ثبت یه دعوت اشتراک‌گذاری جدید (لحظه‌ای که دعوت نشون داده/فرستاده می‌شه)."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO share_log (user_id, variant, invited_at, responded) VALUES (?, ?, ?, 0)",
            (user_id, variant, now),
        )
        conn.commit()
        return cursor.lastrowid


def get_pending_share_invite(user_id: int):
    """آخرین دعوت بی‌پاسخ این کاربر رو برمی‌گردونه (یا None اگه نباشه)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM share_log WHERE user_id = ? AND responded = 0 ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_share_responded(invite_id: int, photo_file_id: str = None):
    """ثبت اینکه کاربر به دعوت پاسخ داد (عکس غذای پخته‌شده رو فرستاد)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE share_log SET responded = 1, responded_at = ?, photo_file_id = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), photo_file_id, invite_id),
        )
        conn.commit()


def get_share_stats_by_variant() -> dict:
    """نرخ پاسخ هر واریانت - همون چیزی که برای تصمیم نهایی تست A/B لازمه.
    خروجی نمونه: {"immediate": {"total": 20, "responded": 9, "rate_percent": 45.0}, ...}"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT variant, COUNT(*) as total, SUM(responded) as responded FROM share_log GROUP BY variant"
        ).fetchall()

    stats = {}
    for r in rows:
        variant = r["variant"] or "unknown"
        total = r["total"] or 0
        responded = r["responded"] or 0
        rate = round((responded / total) * 100, 1) if total else 0.0
        stats[variant] = {"total": total, "responded": responded, "rate_percent": rate}
    return stats
