import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Lesson, Profile


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS vocabulary (
                    user_id INTEGER NOT NULL, word TEXT NOT NULL,
                    due TEXT NOT NULL, stage INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id, word));
                CREATE TABLE IF NOT EXISTS quota (
                    user_id INTEGER NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY(user_id, day));
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY, model TEXT NOT NULL, day TEXT NOT NULL,
                    detail TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def profile(self, user_id: int) -> Profile:
        with self.connection() as db:
            row = db.execute("SELECT value FROM profile WHERE id=?", (user_id,)).fetchone()
        return Profile.model_validate_json(row[0]) if row else Profile()

    def save_profile(self, user_id: int, profile: Profile):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO profile VALUES (?,?)",
                       (user_id, profile.model_dump_json()))

    def history(self, user_id: int) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT role,content FROM turns WHERE user_id=? ORDER BY id DESC LIMIT 12",
                              (user_id,)).fetchall()
        return [{"role": role, "content": content} for role, content in reversed(rows)]

    def commit_lesson(self, user_id: int, text: str, lesson: Lesson):
        profile = self.profile(user_id)
        for key in ("level", "learning_notes", "plan", "homework", "diagnostic_stage"):
            setattr(profile, key, getattr(lesson, key))
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO profile VALUES (?,?)",
                       (user_id, profile.model_dump_json()))
            db.executemany("INSERT INTO turns(user_id,role,content) VALUES (?,?,?)", [
                (user_id, "user", text),
                (user_id, "assistant", lesson.feedback_ru + "\n" + lesson.reply_en),
            ])
            # Keep bounded dialogue; long-term observations live in profile.
            db.execute("DELETE FROM turns WHERE user_id=? AND id NOT IN "
                       "(SELECT id FROM turns WHERE user_id=? ORDER BY id DESC LIMIT 40)",
                       (user_id, user_id))

    def reserve_turn(self, user_id: int, limit: int) -> bool:
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO quota VALUES (?,?,0)", (user_id, day))
            result = db.execute("UPDATE quota SET count=count+1 WHERE user_id=? AND day=? AND count<?",
                                (user_id, day, limit))
            return result.rowcount == 1

    def record_usage(self, model: str, usage):
        detail = usage.model_dump() if usage else {}
        with self.connection() as db:
            db.execute("INSERT INTO usage(model,day,detail) VALUES (?,?,?)",
                       (model, datetime.now(timezone.utc).date().isoformat(), json.dumps(detail)))

    def usage_today(self) -> str:
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connection() as db:
            rows = db.execute("SELECT model,COUNT(*) FROM usage WHERE day=? GROUP BY model",
                              (day,)).fetchall()
        return "\n".join(f"{model}: {count} API-вызовов" for model, count in rows) or "Сегодня вызовов нет."

    def add_word(self, user_id: int, word: str):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO vocabulary(user_id,word,due) VALUES (?,?,?)",
                       (user_id, word.strip().lower(), datetime.now(timezone.utc).isoformat()))

    def words(self, user_id: int) -> list[str]:
        with self.connection() as db:
            return [r[0] for r in db.execute(
                "SELECT word FROM vocabulary WHERE user_id=? ORDER BY word LIMIT 200", (user_id,))]

    def due_word(self, user_id: int) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT word FROM vocabulary WHERE user_id=? AND due<=? ORDER BY due LIMIT 1",
                             (user_id, datetime.now(timezone.utc).isoformat())).fetchone()
            return row[0] if row else None

    def mark_word(self, user_id: int, word: str, remembered: bool):
        with self.connection() as db:
            row = db.execute("SELECT stage FROM vocabulary WHERE user_id=? AND word=?",
                             (user_id, word)).fetchone()
            if not row:
                return
            stage = min(row[0] + 1, 6) if remembered else 0
            days = [0, 1, 3, 7, 14, 30, 60][stage]
            due = datetime.now(timezone.utc) + (timedelta(days=days) if remembered else timedelta(minutes=10))
            db.execute("UPDATE vocabulary SET stage=?,due=? WHERE user_id=? AND word=?",
                       (stage, due.isoformat(), user_id, word))

    def forget(self, user_id: int):
        with self.connection() as db:
            db.execute("DELETE FROM profile WHERE id=?", (user_id,))
            for table in ("turns", "vocabulary"):
                db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        # Quota/usage contain no dialogue and are kept to prevent limit bypass.
