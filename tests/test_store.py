from concurrent.futures import ThreadPoolExecutor

from englishbot.models import Lesson
from englishbot.store import Store


def test_quota_is_atomic_and_cannot_be_reset_by_forget(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.reserve_turn(1, 5), range(20)))
    assert sum(results) == 5
    store.forget(1)
    assert not store.reserve_turn(1, 5)


def test_learning_state_survives_restart_and_delete_is_scoped(tmp_path):
    path = tmp_path / "db.sqlite3"
    store = Store(path)
    profile = store.profile(1)
    profile.style = "strict"
    profile.goal = "Travel"
    store.save_profile(1, profile)
    lesson = Lesson(feedback_ru="Past tense", reply_en="Where did you go?", level="A2 provisional",
                    learning_notes="past tense", plan="Practice", homework="Tell a story",
                    diagnostic_stage="Sample speaking")
    store.commit_lesson(1, "I go yesterday", lesson)
    store.add_word(1, "journey")
    store.add_word(2, "other")
    restarted = Store(path)
    assert restarted.profile(1).style == "strict"
    assert restarted.profile(1).goal == "Travel"
    assert restarted.profile(1).homework == "Tell a story"
    assert restarted.history(1)[0]["content"] == "I go yesterday"
    assert restarted.due_word(1) == "journey"
    restarted.mark_word(1, "journey", True)
    assert restarted.due_word(1) is None
    restarted.forget(1)
    assert restarted.history(1) == []
    assert restarted.words(1) == []
    assert restarted.profile(1).level == "Не определён"
    assert restarted.words(2) == ["other"]
