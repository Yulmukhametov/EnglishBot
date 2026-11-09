import asyncio
import html
import logging
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import ValidationError

from .ai import Mentor
from .audio import convert
from .config import Settings
from .store import Store

HELP = """Я AI-наставник английского. Голос создан искусственным интеллектом.
Отправь текст или голосовое до {seconds} секунд.
Я объясню ошибки по-русски, затем продолжу разговор на английском.
После каждого голосового будет его текст. Английский текст — под спойлером.
Произношение: экспериментальные замечания по аудио, без гарантии полноты.

/start — знакомство и диагностика
/style friendly | strict | casual — стиль
/goal твоя цель — цель и время на занятия
/plan — составить или обновить план
/homework — задание или проверка присланной работы
/progress — сохранённые наблюдения и уровень
/usage — число API-вызовов за сегодня (UTC)
/word слово или выражение — сохранить в словарь
/words — словарь
/review — упражнение со словом для повторения
/forget — удалить учебные данные
/help — справка

Текст и аудио отправляются OpenAI, сообщения проходят через Telegram.
На компьютере хранятся история, словарь и учебные заметки; аудиофайлы временные.
/forget удаляет учебные данные локально, но не сообщения в Telegram и данные провайдеров.
"""


async def send_text(message: Message, text: str, *, hidden: bool = False):
    # At most 3000 UTF-16 code units, even with emoji. Escape all model output.
    for offset in range(0, len(text), 1500):
        body = html.escape(text[offset:offset + 1500])
        if hidden:
            body = '<span class="tg-spoiler">' + body + '</span>'
        await message.answer(body, parse_mode="HTML")


class PrivateOwner(BaseMiddleware):
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        self.lock = asyncio.Lock()

    async def __call__(self, handler, event, data):
        user = event.from_user
        message = event.message if isinstance(event, CallbackQuery) else event
        if not user or user.id != self.owner_id or not message or message.chat.type != "private":
            if isinstance(event, CallbackQuery):
                await event.answer("Нет доступа")
            return
        if self.lock.locked():
            if isinstance(event, CallbackQuery):
                await event.answer("Подожди завершения ответа")
            else:
                await event.answer("Ещё готовлю предыдущий ответ. Отправь следующее сообщение после него.")
            return
        async with self.lock:
            return await handler(event, data)


def build_dispatcher(settings: Settings, store: Store, mentor: Mentor) -> Dispatcher:
    dp = Dispatcher()
    gate = PrivateOwner(settings.owner_id)
    dp.message.outer_middleware(gate)
    dp.callback_query.outer_middleware(gate)

    async def deliver(message: Message, text: str, *, hidden: bool, label: str):
        try:
            speech = await mentor.speak(text)
            with TemporaryDirectory(prefix="englishbot-") as temp:
                source, target = Path(temp) / "speech.wav", Path(temp) / "speech.ogg"
                source.write_bytes(speech.audio)
                await convert(settings.ffmpeg_path, source, target, opus=True)
                voice = target.read_bytes()
        except Exception as error:
            logging.warning("Speech fallback: %s", type(error).__name__)
            await message.answer(label + ": голос сейчас недоступен; отправляю текст.")
            await send_text(message, text, hidden=hidden)
            return
        await message.answer_voice(BufferedInputFile(voice, filename="mentor.ogg"), caption=label)
        await send_text(message, speech.transcript, hidden=hidden)

    async def lesson(message: Message, text: str | None = None, task: str = "dialogue"):
        if not store.reserve_turn(settings.owner_id, settings.daily_turn_limit):
            await message.answer("Дневной лимит обращений исчерпан. Он обновится в 00:00 UTC.")
            return
        await message.answer("Слушаю и готовлю ответ…" if message.voice else "Готовлю ответ…")
        audio = None
        try:
            if message.voice:
                if message.voice.file_size and message.voice.file_size > 10 * 1024 * 1024:
                    await message.answer("Файл слишком большой. Отправь голосовое до 10 МБ.")
                    return
                with TemporaryDirectory(prefix="englishbot-") as temp:
                    source, target = Path(temp) / "input.ogg", Path(temp) / "input.wav"
                    await message.bot.download(message.voice, destination=source)
                    await convert(settings.ffmpeg_path, source, target)
                    audio = await mentor.listen(target.read_bytes())
                if not audio.speech_clear or not audio.transcript.strip():
                    await message.answer("Не удалось уверенно разобрать речь. Запиши ещё раз ближе к микрофону.")
                    return
                text = audio.transcript
                await send_text(message, "Я услышал: " + text)
            if not text or not text.strip():
                return
            response = await mentor.teach(store.profile(settings.owner_id),
                                          store.history(settings.owner_id), text, audio, task)
            store.commit_lesson(settings.owner_id, text, response)
            if response.feedback_ru.strip():
                await deliver(message, response.feedback_ru, hidden=False, label="Разбор")
            await deliver(message, response.reply_en, hidden=True, label="Продолжаем разговор")
            return True
        except AuthenticationError:
            await message.answer("OpenAI отклонил ключ API. Проверь OPENAI_API_KEY в файле .env.")
        except RateLimitError:
            await message.answer("OpenAI ограничил запросы или закончился баланс API. Проверь кабинет OpenAI.")
        except (APIConnectionError, APITimeoutError):
            await message.answer("Не удалось дождаться OpenAI. Проверь интернет и повтори сообщение позже.")
        except APIStatusError as error:
            logging.warning("OpenAI HTTP status: %s", error.status_code)
            await message.answer("Ошибка OpenAI. Проверь доступность моделей из .env в своём API-аккаунте.")
        except Exception as error:
            logging.warning("Lesson failed: %s", type(error).__name__)
            await message.answer("Не удалось завершить ответ. Попробуй ещё раз. Автоматически повторять запрос не буду.")

    @dp.message(Command("help"))
    async def help_command(message: Message):
        await message.answer(HELP.format(seconds=settings.max_voice_seconds))

    @dp.message(Command("start"))
    async def start(message: Message):
        await message.answer(HELP.format(seconds=settings.max_voice_seconds))
        await message.answer("Начнём со знакомства. Для чего тебе английский и сколько минут в день готов заниматься? "
                             "Можно ответить по-русски. Затем я предложу короткий рассказ на английском.")

    @dp.message(Command("style"))
    async def style(message: Message):
        value = (message.text or "").split(maxsplit=1)
        if len(value) != 2 or value[1] not in ("friendly", "strict", "casual"):
            await message.answer("Пример: /style casual. Варианты: friendly, strict, casual.")
            return
        profile = store.profile(settings.owner_id)
        profile.style = value[1]
        store.save_profile(settings.owner_id, profile)
        await message.answer("Стиль изменён: " + value[1])

    @dp.message(Command("goal"))
    async def goal(message: Message):
        value = (message.text or "").split(maxsplit=1)
        if len(value) != 2 or len(value[1]) > 1000:
            await message.answer("Пример: /goal Путешествия, 20 минут в день. До 1000 символов.")
            return
        profile = store.profile(settings.owner_id)
        profile.goal = value[1]
        profile.plan = ""  # Old plan may no longer fit.
        store.save_profile(settings.owner_id, profile)
        await message.answer("Цель сохранена. /plan — составить план с учётом новой цели.")

    @dp.message(Command("plan", "homework"))
    async def learning_task(message: Message):
        command = (message.text or "").split()[0].split("@")[0][1:]
        request = (message.text or "").split(maxsplit=1)
        content = request[1] if len(request) > 1 else ""
        await lesson(message, "Составь или обнови " + ("мой план обучения." if command == "plan"
                                                       else "одно домашнее задание. Если прислана работа — проверь её.")
                     + "\n" + content, task=command)

    @dp.message(Command("usage"))
    async def usage(message: Message):
        await send_text(message, store.usage_today() + "\nДенежные расходы смотри в OpenAI Usage. "
                        "Число обращений не равно числу API-вызовов.")

    @dp.message(Command("progress"))
    async def progress(message: Message):
        profile = store.profile(settings.owner_id)
        await send_text(message, f"Предварительный уровень: {profile.level}\n"
                        f"Цель: {profile.goal}\nЭтап: {profile.diagnostic_stage}\n"
                        f"Наблюдения: {profile.learning_notes or 'Пока нет'}\n"
                        f"План: {profile.plan or 'Пока нет'}\n"
                        f"Домашнее задание: {profile.homework or 'Пока нет'}")

    @dp.message(Command("word"))
    async def word(message: Message):
        value = (message.text or "").split(maxsplit=1)
        if len(value) != 2 or not 1 <= len(value[1].strip()) <= 100:
            await message.answer("Пример: /word take a break. До 100 символов.")
            return
        store.add_word(settings.owner_id, value[1])
        await message.answer("Добавлено. /review — повторить слово в упражнении.")

    @dp.message(Command("words"))
    async def words(message: Message):
        await send_text(message, "\n".join(store.words(settings.owner_id)) or "Словарь пока пуст. /word example")

    @dp.message(Command("review"))
    async def review(message: Message):
        word = store.due_word(settings.owner_id)
        if not word:
            await message.answer("Пока нет слов для повторения. Добавь слово через /word.")
            return
        completed = await lesson(message, "Дай одно короткое упражнение на активное использование выражения: " + word,
                                 task="vocabulary review")
        if not completed:
            return
        card = await message.answer("После выполнения оцени, удалось ли вспомнить слово. Ответ на упражнение отправь отдельно.",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                 InlineKeyboardButton(text="Вспомнил", callback_data="review_yes"),
                                 InlineKeyboardButton(text="Повторить", callback_data="review_no"),
                             ]]))
        # Store the selected word on the callback's message, not mutable global state.
        review_words[card.message_id] = word

    review_words: dict[int, str] = {}

    @dp.callback_query(F.data.in_({"review_yes", "review_no"}))
    async def reviewed(query: CallbackQuery):
        word = review_words.pop(query.message.message_id, None)
        if not word:
            await query.answer("Упражнение устарело. Запусти /review.")
            return
        store.mark_word(settings.owner_id, word, query.data == "review_yes")
        await query.message.edit_reply_markup(reply_markup=None)
        await query.answer("Дата повторения обновлена")

    @dp.message(Command("forget"))
    async def forget(message: Message):
        if (message.text or "").split(maxsplit=1)[-1] != "confirm":
            await message.answer("Удалить локальные историю, профиль и словарь? Для подтверждения: /forget confirm")
            return
        store.forget(settings.owner_id)
        review_words.clear()
        await message.answer("Учебные данные удалены. Счётчики запросов сохранены. /start — начать заново.")

    @dp.message(F.voice)
    async def voice(message: Message):
        if message.voice.duration > settings.max_voice_seconds:
            await message.answer(f"Пока принимаю голосовые до {settings.max_voice_seconds} секунд.")
            return
        await lesson(message)

    @dp.message(F.text)
    async def text(message: Message):
        if message.text.startswith("/"):
            await message.answer("Неизвестная команда. /help — список команд.")
            return
        if len(message.text) > settings.max_text_chars:
            await message.answer(f"Сократи сообщение до {settings.max_text_chars} символов.")
            return
        await lesson(message, message.text)

    @dp.message()
    async def unsupported(message: Message):
        await message.answer("Отправь текст или голосовое сообщение Telegram.")

    return dp


async def run():
    try:
        settings = Settings()
    except ValidationError:
        raise SystemExit("Проверь .env: TELEGRAM_BOT_TOKEN, OPENAI_API_KEY и числовой OWNER_ID обязательны.") from None
    if not shutil.which(settings.ffmpeg_path):
        raise SystemExit("FFmpeg не найден. Установи его или укажи FFMPEG_PATH в .env.")
    store = Store(settings.database_path)
    mentor = Mentor(settings, store)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await build_dispatcher(settings, store, mentor).start_polling(
            bot, allowed_updates=["message", "callback_query"], handle_as_tasks=False)
    finally:
        await mentor.close()
        await bot.session.close()


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Never enable HTTP debug logs: URLs may contain the Telegram token.
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
