from types import SimpleNamespace
from unittest.mock import AsyncMock

from englishbot.ai import Mentor
from englishbot.config import Settings
from englishbot.models import AudioAnalysis, Lesson, Profile, PronunciationNote
from englishbot.store import Store


async def test_audio_is_sent_and_low_confidence_notes_are_excluded(tmp_path):
    settings = Settings(_env_file=None, telegram_bot_token="fake", openai_api_key="fake", owner_id=1)
    mentor = Mentor(settings, Store(tmp_path / "db.sqlite3"))
    try:
        completion = SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"transcript":"I go","speech_clear":true,"uncertainty_ru":"","pronunciation":[]}'))])
        create = AsyncMock(return_value=completion)
        mentor.client.chat.completions.create = create
        await mentor.listen(b"actual audio")
        sent = create.call_args.kwargs["messages"][1]["content"][0]
        assert sent["type"] == "input_audio"
        assert sent["input_audio"]["data"] == "YWN0dWFsIGF1ZGlv"
        lesson = Lesson(feedback_ru="", reply_en="Hi", level="Unknown", learning_notes="",
                        plan="", homework="", diagnostic_stage="")
        parse = AsyncMock(return_value=SimpleNamespace(usage=None, choices=[SimpleNamespace(
            message=SimpleNamespace(parsed=lesson))]))
        mentor.client.chat.completions.parse = parse
        audio = AudioAnalysis(transcript="I go", speech_clear=True, uncertainty_ru="",
                              pronunciation=[PronunciationNote(word="secretword", explanation_ru="guess",
                                                               confidence="low")])
        await mentor.teach(Profile(), [], "I go", audio)
        context = parse.call_args.kwargs["messages"][1]["content"]
        assert "secretword" not in context
        assert '"experimental_audio_observations": []' in context
    finally:
        await mentor.close()
