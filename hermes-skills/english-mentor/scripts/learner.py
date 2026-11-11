"""Persistent single-owner learning profile and evidence-based diagnostic workflow.

No AI/network calls: the coach supplies qualitative observations; objective tasks are graded here.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class LearningError(Exception):
    pass


TASKS = [
    dict(id="introduction", skill="writing", kind="open", input="text",
         prompt="Напиши по-английски 3–5 предложений о себе: чем занимаешься и зачем тебе английский. Если сложно, начни с одного предложения."),
    dict(id="present_simple", skill="grammar", kind="choice", input="text",
         prompt="Выбери вариант и напиши слово: She usually ___ to work by bus. Варианты: go / goes / going.",
         answers=["goes"], topic="present_simple_agreement"),
    dict(id="past_story", skill="spoken_language", kind="open", input="voice_or_text",
         prompt="Расскажи по-английски, что ты делал вчера, в 3–5 предложениях. Лучше голосом; если неудобно, можно текстом."),
    dict(id="past_simple", skill="grammar", kind="choice", input="text",
         prompt="Выбери вариант и напиши слово: Yesterday I ___ a new word. Варианты: learn / learned / learning.",
         answers=["learned", "learnt"], topic="past_simple"),
    dict(id="listening_short", skill="listening", kind="listening", input="text_or_voice",
         prompt="Послушай короткую запись и ответь на вопрос, не открывая расшифровку. Потом укажи, открывал ли ты текст.",
         passage="Anna has an English lesson every Tuesday. It starts at six in the evening. She takes the bus to get there.",
         question="On which day does Anna have her English lesson?",
         answers=["tuesday", "on tuesday", "every tuesday", "вторник", "во вторник", "каждый вторник"],
         topic="listening_details"),
    dict(id="opinion", skill="writing", kind="open", input="text",
         prompt="Ответь по-английски в нескольких предложениях: Would you rather travel alone or with friends? Why? Если это пока сложно — так и скажи, это тоже полезный результат диагностики."),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, name, limit=2000, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise LearningError(f"Invalid {name}: expected text up to {limit} characters")
    return value.strip()


def exact_keys(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        raise LearningError("Missing or unknown input fields")


def normalize(value):
    return unicodedata.normalize("NFKC", value).lower().strip().rstrip(".!? ").strip()


class LearningStore:
    def __init__(self, path, owner):
        self.path, self.owner = Path(path), owner
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles (owner INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    owner INTEGER NOT NULL, event_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    phase TEXT NOT NULL, task_id TEXT, value TEXT NOT NULL,
                    digest TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(owner,event_id));
            """)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def profile(self, db):
        row = db.execute("SELECT value FROM profiles WHERE owner=?", (self.owner,)).fetchone()
        if row:
            return json.loads(row["value"])
        return dict(goal="", minutes_per_day=15, interests=[], style="friendly", background="",
                    run_id=uuid.uuid4().hex, assessment=None, plan=[], minutes_confirmed=False, created=now())

    def save(self, db, profile):
        db.execute("INSERT OR REPLACE INTO profiles VALUES (?,?)", (self.owner, json.dumps(profile, ensure_ascii=False)))

    def set_profile(self, data):
        exact_keys(data, [], ["goal", "minutes_per_day", "interests", "style", "background"])
        if not data:
            raise LearningError("Profile update is empty")
        updates = {}
        for key in ("goal", "background"):
            if key in data:
                updates[key] = text(data[key], key, 1000, empty=(key == "background"))
        if "minutes_per_day" in data:
            value = data["minutes_per_day"]
            if type(value) is not int or not 5 <= value <= 120:
                raise LearningError("minutes_per_day must be 5–120")
            updates["minutes_per_day"] = value
            updates["minutes_confirmed"] = True
        if "interests" in data:
            if not isinstance(data["interests"], list) or len(data["interests"]) > 10:
                raise LearningError("interests must be a list of up to 10 topics")
            updates["interests"] = [text(item, "interest", 100) for item in data["interests"]]
        if "style" in data:
            if data["style"] not in {"friendly", "strict", "casual"}:
                raise LearningError("Unknown teaching style")
            updates["style"] = data["style"]
        with self.db() as db:
            profile = self.profile(db)
            profile.update(updates)
            if profile["plan"]:
                profile["plan"] = self.build_plan(profile, self.attempts(db))
            self.save(db, profile)
        return profile

    def attempts(self, db):
        return [dict(json.loads(row["value"]), created=row["created"])
                for row in db.execute("SELECT value,created FROM attempts WHERE owner=? ORDER BY rowid", (self.owner,))]

    def pending(self, profile, attempts):
        completed = {item["task_id"] for item in attempts
                     if item["phase"] == "diagnostic" and item["run_id"] == profile["run_id"]}
        return next((task for task in TASKS if task["id"] not in completed), None)

    def next_task(self):
        with self.db() as db:
            profile = self.profile(db)
            self.save(db, profile)  # Persist run identity before the first answer.
            if not profile["goal"]:
                return dict(stage="onboarding", question="Для чего тебе английский и сколько минут в день готов заниматься? Можно ответить по-русски.")
            if not profile.get("minutes_confirmed"):
                return dict(stage="onboarding", question="Сколько минут в день готов заниматься? Например, 15 минут.")
            attempts = self.attempts(db)
        task = self.pending(profile, attempts)
        if not task:
            return dict(stage="ready_for_assessment" if not profile["assessment"] else "learning", task=None)
        # Answer keys are kept in code and never returned with a learner-facing task.
        public = {key: value for key, value in task.items() if key not in {"answers", "topic"}}
        return dict(stage="diagnostic", index=TASKS.index(task) + 1, total=len(TASKS), task=public)

    def record(self, data):
        exact_keys(data, ["event_id", "phase", "response", "source", "notes", "errors"],
                   ["task_id", "used_transcript", "declined", "answer"])
        event_id = text(data["event_id"], "event_id", 100)
        response = text(data["response"], "response", 4000)
        notes = text(data["notes"], "notes", 1500, empty=True)
        answer = text(data.get("answer", response), "answer", 4000)
        if answer not in response:
            raise LearningError("Extracted answer must appear verbatim in the learner response")
        if data["source"] not in {"text", "voice_transcript"} or data["phase"] not in {"diagnostic", "practice"}:
            raise LearningError("Unknown phase or input source")
        for key in ("used_transcript", "declined"):
            if key in data and type(data[key]) is not bool:
                raise LearningError(f"{key} must be boolean")
        if not isinstance(data["errors"], list) or len(data["errors"]) > 8:
            raise LearningError("errors must contain up to 8 observations")
        errors = []
        for error in data["errors"]:
            exact_keys(error, ["topic", "original", "corrected", "explanation"])
            if not isinstance(error["topic"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,60}", error["topic"]):
                raise LearningError("Error topic must be a stable lowercase tag")
            if "pronunc" in error["topic"] or "accent" in error["topic"]:
                raise LearningError("Pronunciation cannot be inferred from a transcript")
            item = {key: text(value, key, 600) for key, value in error.items()}
            if item["original"] not in response:
                raise LearningError("Error evidence must be an exact substring of the learner response")
            errors.append(item)
        task_id = data.get("task_id")
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT digest,value FROM attempts WHERE owner=? AND event_id=?",
                                  (self.owner, event_id)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise LearningError("event_id already belongs to another answer")
                return json.loads(previous["value"])
            profile = self.profile(db)
            if not profile["goal"] or not profile.get("minutes_confirmed"):
                raise LearningError("Save the learner goal and daily time first")
            item = dict(event_id=event_id, phase=data["phase"], task_id=task_id, response=response,
                        source=data["source"], notes=notes, errors=errors, run_id=profile["run_id"],
                        result="observed", used_transcript=data.get("used_transcript"),
                        declined=data.get("declined", False))
            if data["phase"] == "diagnostic":
                task = self.pending(profile, self.attempts(db))
                if not task or task_id != task["id"]:
                    raise LearningError("Answer does not match the current diagnostic task")
                if task["kind"] == "listening" and "used_transcript" not in data:
                    raise LearningError("Ask whether the learner opened the transcript before recording listening")
                if item["declined"]:
                    item["result"] = "declined"
                elif task["kind"] in {"choice", "listening"}:
                    item["result"] = "correct" if normalize(answer) in task["answers"] else "needs_review"
                    if task["kind"] == "listening" and data["used_transcript"]:
                        item["result"] = "assisted"
            elif task_id is not None:
                raise LearningError("Practice does not take a diagnostic task_id")
            db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
                       (self.owner, event_id, profile["run_id"], data["phase"], task_id,
                        json.dumps(item, ensure_ascii=False), digest, now()))
            if profile["plan"]:
                profile["plan"] = self.build_plan(profile, self.attempts(db))
            self.save(db, profile)
        return item

    def error_summary(self, attempts):
        topics = {}
        for attempt in attempts:
            # One answer mentioning the same topic twice is still one occurrence.
            for tag in {error["topic"] for error in attempt["errors"]}:
                topics[tag] = topics.get(tag, 0) + 1
        return sorted((dict(topic=tag, answers=count) for tag, count in topics.items()),
                      key=lambda entry: (-entry["answers"], entry["topic"]))

    def build_plan(self, profile, attempts):
        priorities = [item["topic"] for item in self.error_summary(attempts)][:2]
        focuses = ["Знакомство и разговор под твою цель", "Практика основной трудности",
                   "Словарь по интересам", "Понимание короткого рассказа на слух",
                   "Повторение в новом контексте", "Разговорная ситуация из твоей цели",
                   "Проверка изученного и корректировка плана"]
        minutes = profile["minutes_per_day"]
        practice = max(2, minutes // 2)
        review = max(1, minutes // 5)
        listening = minutes - practice - review
        return [dict(day=index + 1, focus=focus, goal=profile["goal"],
                     interests=profile["interests"], priority_topics=priorities,
                     activities=[dict(kind="practice", minutes=practice),
                                 dict(kind="review", minutes=review),
                                 dict(kind="listening", minutes=listening)])
                for index, focus in enumerate(focuses)]

    def finalize(self, data):
        exact_keys(data, ["level", "confidence", "summary", "strengths", "priorities", "evidence_ids"])
        if data["level"] not in {"A1", "A2", "B1", "B2", "undetermined"} or data["confidence"] not in {"low", "medium"}:
            raise LearningError("Initial assessment must be provisional; choose A1–B2 or undetermined, low/medium confidence")
        assessment = dict(level=data["level"], confidence=data["confidence"],
                          summary=text(data["summary"], "summary", 1500), created=now(), provisional=True)
        for key in ("strengths", "priorities"):
            if not isinstance(data[key], list) or len(data[key]) > 6:
                raise LearningError(f"{key} must contain up to 6 observations")
            assessment[key] = [text(value, key, 300) for value in data[key]]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            profile, attempts = self.profile(db), self.attempts(db)
            if profile["assessment"]:
                if any(profile["assessment"].get(key) != value for key, value in data.items()):
                    raise LearningError("Initial assessment already saved; do not upgrade from the same answers")
                return dict(assessment=profile["assessment"], plan=profile["plan"])
            if self.pending(profile, attempts) is not None:
                raise LearningError("Complete all diagnostic steps before the initial assessment")
            diagnostic = {item["event_id"]: item for item in attempts
                          if item["run_id"] == profile["run_id"] and item["phase"] == "diagnostic"}
            evidence = data["evidence_ids"]
            if not isinstance(evidence, list) or len(set(evidence)) < 3 or any(item not in diagnostic for item in evidence):
                raise LearningError("Assessment must cite at least 3 existing diagnostic answers")
            usable = [item for item in diagnostic.values() if item["result"] not in {"declined", "assisted"}]
            if len(usable) < 3 and data["level"] != "undetermined":
                raise LearningError("Insufficient unassisted answers; level must remain undetermined")
            assessment["evidence_ids"] = evidence
            listening = next(item for item in diagnostic.values() if item["task_id"] == "listening_short")
            assessment["listening_evidence"] = listening["result"]
            assessment["pronunciation"] = "not_assessed"
            assessment["spoken_audio"] = "not_assessed"  # Transcripts do not measure sounds or fluency.
            profile["assessment"] = assessment
            profile["plan"] = self.build_plan(profile, attempts)
            self.save(db, profile)
        return dict(assessment=assessment, plan=profile["plan"])

    def state(self):
        with self.db() as db:
            profile = self.profile(db)
            self.save(db, profile)
            attempts = self.attempts(db)
        return dict(profile=profile, answers_recorded=len(attempts), recent_answers=attempts[-8:],
                    diagnostic_answers=[item for item in attempts if item["phase"] == "diagnostic"
                                        and item["run_id"] == profile["run_id"]],
                    errors=self.error_summary(attempts), next=self.next_task())


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["state", "next-task", "set-profile", "record", "finalize"])
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    try:
        from ordered_delivery import credentials
        home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
        _, owner = credentials(home)
        store = LearningStore(home / "english-learning" / "learner.sqlite3", owner)
        if args.action in {"state", "next-task"}:
            if args.input:
                raise LearningError("This action does not take an input file")
            result = store.state() if args.action == "state" else store.next_task()
        else:
            if not args.input or args.input.stat().st_size > 40000:
                raise LearningError("Provide a JSON input file up to 40 KB")
            data = json.loads(args.input.read_text(encoding="utf-8"))
            method = {"set-profile": store.set_profile, "record": store.record, "finalize": store.finalize}[args.action]
            result = method(data)
        print(json.dumps({"success": True, "data": result}, ensure_ascii=False))
    except LearningError as error:
        print(json.dumps({"success": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"success": False, "error": "Learning storage operation failed; no secret details logged"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
