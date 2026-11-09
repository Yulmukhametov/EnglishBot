import io
import shutil
import wave
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetFile, SendMessage, SendVoice
from aiogram.types import Chat, File, Message, Update, User, Voice

from englishbot.bot import build_dispatcher, send_text
from englishbot.config import Settings
from englishbot.models import AudioAnalysis, Lesson, Speech
from englishbot.store import Store


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    return buffer.getvalue()


class TelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetFile):
            return File(file_id="v", file_unique_id="u", file_path="voice.ogg")
        self.sent.append(method)
        return Message(message_id=len(self.sent), date=datetime.now(timezone.utc),
                       chat=Chat(id=42, type="private"), text=getattr(method, "text", None))

    async def stream_content(self, url, **kwargs):
        yield wav_bytes()


@pytest.fixture
async def setup(tmp_path):
    settings = Settings(_env_file=None, telegram_bot_token="123456:fake", openai_api_key="fake",
                        owner_id=42, database_path=tmp_path / "db.sqlite3")
    store = Store(settings.database_path)
    mentor = AsyncMock()
    mentor.teach.return_value = Lesson(
        feedback_ru="Правильно: I went.", reply_en="Where did you go?",
        level="A1 предварительно", learning_notes="Past simple", plan="Практика",
        homework="Расскажи о вчерашнем дне", diagnostic_stage="Знакомство")
    mentor.speak.side_effect = lambda text: Speech(audio=wav_bytes(), transcript=text + " <actual>")
    mentor.listen.return_value = AudioAnalysis(transcript="Yesterday I go.", speech_clear=True,
                                              uncertainty_ru="", pronunciation=[])
    session = TelegramSession()
    bot = Bot("123456:fake", session=session)
    dp = build_dispatcher(settings, store, mentor)
    yield settings, store, mentor, session, bot, dp
    await bot.session.close()


def update(text=None, *, user=42, chat_type="private", voice=False, duration=2, update_id=1):
    return Update(update_id=update_id, message=Message(
        message_id=update_id, date=datetime.now(timezone.utc),
        chat=Chat(id=42, type=chat_type), from_user=User(id=user, is_bot=False, first_name="Test"),
        text=text, voice=Voice(file_id="v", file_unique_id="u", duration=duration) if voice else None))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
@pytest.mark.parametrize("voice", [False, True])
async def test_full_exchange_order_and_actual_transcripts(setup, voice):
    _, store, mentor, session, bot, dp = setup
    await dp.feed_update(bot, update(None if voice else "Yesterday I go.", voice=voice))
    sent = [m for m in session.sent if isinstance(m, (SendMessage, SendVoice))]
    voices = [i for i, m in enumerate(sent) if isinstance(m, SendVoice)]
    assert len(voices) == 2
    first, second = voices
    assert sent[first + 1].text == "Правильно: I went. &lt;actual&gt;"
    assert "tg-spoiler" not in sent[first + 1].text
    assert sent[second + 1].text == '<span class="tg-spoiler">Where did you go? &lt;actual&gt;</span>'
    assert second == first + 2
    assert len(store.history(42)) == 2
    assert mentor.listen.await_count == int(voice)
    if voice:
        assert mentor.teach.call_args.args[3].transcript == "Yesterday I go."
    else:
        assert mentor.teach.call_args.args[3] is None


@pytest.mark.parametrize("kwargs", [{"user": 7}, {"chat_type": "group"}])
async def test_other_users_and_groups_never_reach_ai(setup, kwargs):
    _, store, mentor, session, bot, dp = setup
    await dp.feed_update(bot, update("Hello", **kwargs))
    mentor.teach.assert_not_awaited()
    assert session.sent == []
    assert store.history(42) == []


async def test_long_voice_rejected_before_paid_calls(setup):
    _, _, mentor, _, bot, dp = setup
    await dp.feed_update(bot, update(voice=True, duration=500))
    mentor.listen.assert_not_awaited()
    mentor.teach.assert_not_awaited()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
async def test_unclear_audio_does_not_become_lesson(setup):
    _, store, mentor, session, bot, dp = setup
    mentor.listen.return_value = AudioAnalysis(transcript="", speech_clear=False,
                                              uncertainty_ru="Шум", pronunciation=[])
    await dp.feed_update(bot, update(voice=True))
    mentor.teach.assert_not_awaited()
    assert store.history(42) == []
    assert "ещё раз" in session.sent[-1].text


async def test_tts_failure_still_sends_hidden_english(setup):
    _, _, mentor, session, bot, dp = setup
    mentor.speak.side_effect = RuntimeError("private provider error")
    await dp.feed_update(bot, update("Hello"))
    assert "tg-spoiler" in session.sent[-1].text
    assert "Where did you go?" in session.sent[-1].text
    assert all("private provider" not in (getattr(m, "text", "") or "") for m in session.sent)


async def test_html_and_long_spoilers_are_safe():
    message = AsyncMock()
    await send_text(message, '<script> & ' + '😀' * 5000, hidden=True)
    for call in message.answer.call_args_list:
        text = call.args[0]
        assert text.startswith('<span class="tg-spoiler">')
        assert text.endswith('</span>')
        assert len(text.encode("utf-16-le")) // 2 < 4096
        assert "<script>" not in text


async def test_forget_requires_confirmation(setup):
    _, store, _, _, bot, dp = setup
    store.add_word(42, "example")
    await dp.feed_update(bot, update("/forget"))
    assert store.words(42) == ["example"]
    await dp.feed_update(bot, update("/forget confirm", update_id=2))
    assert store.words(42) == []
