"""Prepare two voice notes and deliver four Telegram messages in a fixed order.

Only for the single numeric user in TELEGRAM_ALLOWED_USERS. Never prints credentials.
Uses an isolated environment, not Hermes's managed Python runtime.
"""
import argparse
import asyncio
import difflib
import fcntl
import hashlib
import html
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class DeliveryError(Exception):
    pass


_MARKUP = re.compile(r"\*\*([^*\n]+)\*\*|~~([^~\n]+)~~")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:['’][A-Za-zА-Яа-яЁё]+)*")


def spoken_text(text):
    """Remove supported presentation markup without changing the spoken words."""
    return _MARKUP.sub(lambda match: match.group(1) or match.group(2), text)


def inline_text(text):
    """Allow paired bold/strikethrough; all user/model HTML stays escaped."""
    parts, position = [], 0
    for match in _MARKUP.finditer(text):
        tag = "b" if match.group(1) is not None else "s"
        content = match.group(1) if tag == "b" else match.group(2)
        parts.extend([html.escape(text[position:match.start()]),
                      f"<{tag}>" + html.escape(content) + f"</{tag}>"])
        position = match.end()
    parts.append(html.escape(text[position:]))
    return "".join(parts)


def formatted_text(text):
    """Render safe inline styles and contiguous > quote lines as Telegram blockquotes."""
    output, quote = [], []

    def flush_quote():
        if quote:
            output.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()

    for line in text.split("\n"):
        if line.startswith("> ") or line == ">":
            quote.append(inline_text(line[2:] if line.startswith("> ") else ""))
        else:
            flush_quote()
            output.append(inline_text(line))
    flush_quote()
    return "\n".join(output)


DEFAULT_SPEECH = {"rate_percent": 8, "pitch_hz": 12, "pause_ms": 100}


def speech_settings(home, changes=None):
    path = home / "english-lesson-voice.json"
    settings = dict(DEFAULT_SPEECH)
    if path.exists():
        settings.update(json.loads(path.read_text(encoding="utf-8")))
    if changes:
        settings.update(changes)
    for key, lower, upper in (("rate_percent", -20, 30), ("pitch_hz", -20, 40), ("pause_ms", 0, 400)):
        value = settings.get(key)
        if type(value) is not int or not lower <= value <= upper:
            raise DeliveryError(f"{key} must be an integer between {lower} and {upper}")
    if changes:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        save_state(path, settings)
    return settings


def feedback_display(payload):
    """Show word-level edits, then the complete correction, then the spoken explanation."""
    if "original_en" not in payload:
        return payload["feedback_ru"]
    before, after = payload["original_en"].split(), payload["corrected_en"].split()
    edits = []
    for action, i, j, k, m in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if action == "equal":
            edits.extend(before[i:j])
        else:
            if action in {"replace", "delete"}:
                edits.append("~~" + " ".join(before[i:j]) + "~~")
            if action in {"replace", "insert"}:
                edits.append("**" + " ".join(after[k:m]) + "**")
    heading = "**Твоя фраза:**" if before == after else "**Твоя фраза с исправлениями:**"
    result = heading + "\n" + " ".join(edits)
    result += "\n\n**Правильный вариант:**\n" + payload["corrected_en"]
    explanation = payload["feedback_ru"]
    if "natural_en" in payload:
        result += "\n\n**Как сказать в разговоре:**\n" + payload["natural_en"]
        explanation += "\n\n**О стиле:** " + payload["natural_note_ru"]
    return result + "\n\n**Разбор:**\n" + "\n".join("> " + line for line in explanation.split("\n"))


def feedback_speech(payload):
    result = payload["feedback_ru"]
    if "natural_en" in payload:
        result += " В разговоре можно сказать: " + payload["natural_en"] + " " + payload["natural_note_ru"]
    return result


def reply_display(payload):
    exercise = payload.get("gap_exercise")
    if not exercise:
        return payload["reply_en"]
    sentence = re.sub(r"_{3,}", "_____", exercise["sentence_en"])
    return ("**Fill in the gap:**\n" + sentence
            + "\n\n**Options:** " + " / ".join(exercise["options"]))


def speech_segments(text):
    """Route Cyrillic prose to Russian and Latin examples to English voices."""
    text = spoken_text(text)
    words = list(_WORD.finditer(text))
    if not words:
        raise DeliveryError("Speech text must contain Russian or English words")
    segments, start, language = [], 0, None
    for word in words:
        next_language = "en" if word.group()[0].isascii() else "ru"
        if language is not None and next_language != language:
            segments.append((language, text[start:word.start()].strip()))
            start = word.start()
        language = next_language
    segments.append((language, text[start:].strip()))
    if len(segments) > 32:
        raise DeliveryError("Too many language switches; use shorter complete examples")
    return segments


def validate(payload):
    if not isinstance(payload, dict):
        raise DeliveryError("Input must be an object")
    mode = payload.get("mode", "lesson")
    if mode not in {"lesson", "english_only", "text_ru"}:
        raise DeliveryError("Unknown response mode")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id):
        raise DeliveryError("Invalid request_id")
    if mode == "text_ru":
        if set(payload) != {"request_id", "mode", "text_ru"}:
            raise DeliveryError("text_ru mode needs only request_id, mode and text_ru")
        value = payload["text_ru"]
        if not isinstance(value, str) or not value.strip() or len(value.encode("utf-16-le")) // 2 > 4000:
            raise DeliveryError("Russian clarification must contain 1–4000 UTF-16 units")
        return payload
    required = {"request_id", "reply_en"}
    optional = {"mode", "gap_exercise"}
    if mode == "lesson":
        required.add("feedback_ru")
        optional.update({"original_en", "corrected_en", "natural_en", "natural_note_ru"})
    if not required <= set(payload) or set(payload) - required - optional:
        raise DeliveryError("Missing or unknown fields for response mode")
    if ("original_en" in payload) != ("corrected_en" in payload):
        raise DeliveryError("original_en and corrected_en must be provided together")
    if not isinstance(payload["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", payload["request_id"]):
        raise DeliveryError("Invalid request_id")
    for key in ("feedback_ru", "reply_en"):
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise DeliveryError(f"{key} must contain 1–2000 characters")
    for key in ("original_en", "corrected_en"):
        if key in payload and (not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > 1200):
            raise DeliveryError(f"{key} must contain 1–1200 characters")
    if ("natural_en" in payload) != ("natural_note_ru" in payload):
        raise DeliveryError("natural_en and natural_note_ru must be provided together")
    if "natural_en" in payload:
        if "original_en" not in payload:
            raise DeliveryError("Natural alternative requires the original and minimally corrected sentence")
        for key, limit in (("natural_en", 1200), ("natural_note_ru", 500)):
            value = payload[key]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise DeliveryError(f"{key} must contain 1–{limit} characters")
        if not re.search(r"[A-Za-z]", payload["natural_en"]) or re.search(r"[А-Яа-яЁё]", payload["natural_en"]):
            raise DeliveryError("Natural English alternative must be in English")
    if mode == "lesson" and len(spoken_text(feedback_display(payload)).encode("utf-16-le")) // 2 > 4000:
        raise DeliveryError("Formatted feedback too long; shorten the explanation")
    if "gap_exercise" in payload:
        exercise = payload["gap_exercise"]
        if not isinstance(exercise, dict) or set(exercise) != {"sentence_en", "options"}:
            raise DeliveryError("gap_exercise requires sentence_en and options")
        sentence, options = exercise["sentence_en"], exercise["options"]
        if not isinstance(sentence, str) or len(sentence) > 500 or len(re.findall(r"_{3,}", sentence)) != 1:
            raise DeliveryError("Gap sentence must contain exactly one _____ placeholder, up to 500 characters")
        if not re.search(r"[A-Za-z]", sentence) or re.search(r"[А-Яа-яЁё]", sentence):
            raise DeliveryError("Gap sentence must be in English")
        if not isinstance(options, list) or not 2 <= len(options) <= 6 or any(
                not isinstance(item, str) or not item.strip() or len(item) > 60 for item in options):
            raise DeliveryError("Gap exercise needs 2–6 short options")
    return payload


def credentials(home):
    from dotenv import dotenv_values
    values = dict(dotenv_values(home / ".env", interpolate=False))
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS", "TELEGRAM_ALLOW_ALL_USERS"):
        if key in os.environ:
            values[key] = os.environ[key]
    if str(values.get("TELEGRAM_ALLOW_ALL_USERS", "")).lower() in {"1", "true", "yes"}:
        raise DeliveryError("This delivery tool requires a single-user Telegram allowlist")
    allowed = str(values.get("TELEGRAM_ALLOWED_USERS", "")).strip()
    # Fail closed: no usernames, multiple IDs or recipient selected by the model.
    if not re.fullmatch(r"[1-9][0-9]*", allowed):
        raise DeliveryError("TELEGRAM_ALLOWED_USERS must be exactly one numeric Telegram user ID")
    token = str(values.get("TELEGRAM_BOT_TOKEN", "")).strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise DeliveryError("Telegram token is missing or has an invalid format")
    return token, int(allowed)


class Telegram:
    def __init__(self, token, chat_id):
        self._token = token
        self.chat_id = chat_id

    def request(self, method, fields, attachment=None):
        boundary = "englishbot-" + uuid.uuid4().hex
        parts = []
        for key, value in {"chat_id": self.chat_id, **fields}.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        if attachment:
            parts.extend([
                f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; filename="lesson.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode(),
                attachment.read_bytes(), b"\r\n",
            ])
        parts.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise DeliveryError(f"Telegram HTTP {error.code}; sequence stopped") from None
        except Exception:
            # Exception messages may contain the request URL and token.
            raise DeliveryError("Telegram response unavailable; delivery may have happened; do not retry automatically") from None
        message = result.get("result")
        if not result.get("ok") or not isinstance(message, dict):
            raise DeliveryError("Telegram did not confirm sending; sequence stopped")
        message_id = message.get("message_id")
        if not isinstance(message_id, int) or message_id <= 0 or message.get("chat", {}).get("id") != self.chat_id:
            raise DeliveryError("Telegram acknowledgement invalid; do not retry automatically")
        return message_id

    def send_voice(self, audio_path):
        # Telegram Bot API supports MP3 for sendVoice; no extra FFmpeg process needed.
        return self.request("sendVoice", {}, audio_path)

    def send_text(self, text, hidden=False):
        value = formatted_text(text)
        if hidden:
            value = '<span class="tg-spoiler">' + value + '</span>'
        return self.request("sendMessage", {"text": value, "parse_mode": "HTML"})


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


async def combine_speech(paths, destination, pause_ms=100):
    import imageio_ffmpeg
    # Re-encode to one MP3; byte-concatenating MP3 files can produce invalid durations/seeks.
    # Trusted, generated local names only (never model-provided file names).
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    async def run(*arguments):
        process = await asyncio.create_subprocess_exec(
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *arguments,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            code = await asyncio.wait_for(process.wait(), timeout=60)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if code:
            raise DeliveryError("Audio assembly failed")

    playlist = destination.with_suffix(".concat")
    entries = []
    pause = destination.with_name(destination.stem + "-pause.wav")
    if pause_ms:
        await run("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{pause_ms / 1000:.3f}",
                  "-c:a", "pcm_s16le", str(pause))
    # Trim only very quiet leading/trailing audio, keeping 40 ms of breathing room.
    # Preserve sentence pauses; add one short, controlled gap at language switches.
    trim = ("silenceremove=start_periods=1:start_duration=0.02:start_threshold=-50dB:start_silence=0.04,"
            "areverse,silenceremove=start_periods=1:start_duration=0.02:"
            "start_threshold=-50dB:start_silence=0.04,areverse")
    for index, path in enumerate(paths):
        cleaned = destination.with_name(f"{destination.stem}-trimmed-{index:02d}.wav")
        await run("-i", str(path), "-af", trim, "-ar", "24000", "-ac", "1",
                  "-c:a", "pcm_s16le", str(cleaned))
        if index and pause_ms:
            entries.append("file '" + pause.name + "'")
        entries.append("file '" + cleaned.name + "'")
    playlist.write_text("\n".join(entries) + "\n", encoding="utf-8")
    await run("-f", "concat", "-safe", "1", "-i", str(playlist), "-ar", "24000", "-ac", "1",
              "-c:a", "libmp3lame", "-b:a", "64k", str(destination))


async def synthesize(payload, directory, settings=None):
    import edge_tts
    voices = {"ru": "ru-RU-SvetlanaNeural", "en": "en-US-JennyNeural"}
    settings = dict(DEFAULT_SPEECH) if settings is None else settings
    paths = [directory / "feedback.mp3", directory / "reply.mp3"]
    semaphore = asyncio.Semaphore(2)

    async def piece(text, voice, target):
        async with semaphore:
            await edge_tts.Communicate(text, voice, rate=f"{settings['rate_percent']:+d}%",
                                       pitch=f"{settings['pitch_hz']:+d}Hz").save(str(target))
        if not target.is_file() or target.stat().st_size == 0:
            raise DeliveryError("Speech generation did not produce audio")

    segments = speech_segments(feedback_speech(payload)) if payload.get("mode", "lesson") == "lesson" else []
    pieces = [directory / f"feedback-part-{i:02d}.mp3" for i in range(len(segments))]
    exercise = payload.get("gap_exercise")
    if exercise:
        before, after = re.split(r"_{3,}", exercise["sentence_en"])
        reply_parts = [directory / "reply-before.mp3", directory / "reply-after.mp3"]
        reply_jobs = [
            piece("Choose the word that fits the gap. " + before.strip(), voices["en"], reply_parts[0]),
            piece(after.strip() + " Your options are: " + ", ".join(exercise["options"])
                  + ". Which word fits?", voices["en"], reply_parts[1]),
        ]
    else:
        reply_parts = []
        reply_jobs = [piece(spoken_text(payload["reply_en"]), voices["en"], paths[1])]
    # Both audio files must finish before any Telegram delivery starts.
    await asyncio.gather(
        *(piece(text, voices[language], path)
          for (language, text), path in zip(segments, pieces)),
        *reply_jobs,
    )
    if len(pieces) == 1:
        pieces[0].replace(paths[0])
    elif pieces:
        await combine_speech(pieces, paths[0], settings["pause_ms"])
    if reply_parts:
        # This is a pedagogical gap, distinct from the short language-switch pause.
        await combine_speech(reply_parts, paths[1], pause_ms=600)
    return paths


def deliver(payload, state_dir, sender, synthesizer=synthesize):
    payload = validate(payload)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serializes all lessons sent by this tool. Never interleave two four-message sequences.
    with (state_dir / "delivery.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeliveryError("Another lesson is being delivered; no messages sent by this call") from None
        path = state_dir / (payload["request_id"] + ".json")
        digest = hashlib.sha256(json.dumps({"lesson": payload, "recipient": sender.chat_id}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if path.exists():
            previous = json.loads(path.read_text())
            if previous["digest"] != digest:
                raise DeliveryError("request_id already belongs to different text")
            if previous["status"] == "complete":
                return previous  # Idempotent after all acknowledgements have been saved.
            raise DeliveryError("Previous attempt incomplete; inspect it before any retry. Do not create a new request_id automatically")
        state = {"request_id": payload["request_id"], "digest": digest, "status": "preparing", "message_ids": []}
        save_state(path, state)
        with tempfile.TemporaryDirectory(prefix="english-lesson-") as temp:
            mode = payload.get("mode", "lesson")
            if mode == "text_ru":
                # No speech engine, settings or audio files are needed for a clarification.
                steps = [lambda: sender.send_text(payload["text_ru"])]
            else:
                try:
                    paths = asyncio.run(synthesizer(payload, Path(temp)))
                except Exception:
                    state["status"] = "preparation_failed"
                    save_state(path, state)
                    raise DeliveryError("Speech preparation failed; no Telegram messages sent") from None
                steps = []
                if mode == "lesson":
                    steps.extend([
                        lambda: sender.send_voice(paths[0]),
                        lambda: sender.send_text(feedback_display(payload)),
                    ])
                steps.extend([
                    lambda: sender.send_voice(paths[1]),
                    lambda: sender.send_text(reply_display(payload), hidden=True),
                ])
            for index, step in enumerate(steps, 1):
                state.update(status="awaiting_ack", step=index)
                save_state(path, state)  # A crash after sending cannot cause a blind retry.
                try:
                    message_id = step()
                    if not isinstance(message_id, int) or message_id <= 0:
                        raise DeliveryError("Missing Telegram acknowledgement")
                except Exception:
                    state["status"] = "stopped_unconfirmed"
                    save_state(path, state)
                    raise DeliveryError(f"Stopped at step {index}; confirmed message_ids: {state['message_ids']}. Do not retry automatically") from None
                state["message_ids"].append(message_id)
                state["status"] = "complete" if index == len(steps) else "acknowledged"
                save_state(path, state)
        return state


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--input", type=Path)
    action.add_argument("--configure-voice", action="store_true")
    parser.add_argument("--rate", type=int)
    parser.add_argument("--pitch-hz", type=int)
    parser.add_argument("--pause-ms", type=int)
    args = parser.parse_args()
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    try:
        if args.configure_voice:
            changes = {key: value for key, value in {
                "rate_percent": args.rate, "pitch_hz": args.pitch_hz, "pause_ms": args.pause_ms,
            }.items() if value is not None}
            settings = speech_settings(home, changes)
            print(json.dumps({"success": True, "speech_settings": settings}))
            return
        if any(value is not None for value in (args.rate, args.pitch_hz, args.pause_ms)):
            raise DeliveryError("Voice options require --configure-voice")
        if args.input.stat().st_size > 30000:
            raise DeliveryError("Input file too large")
        payload = validate(json.loads(args.input.read_text(encoding="utf-8")))
        token, chat_id = credentials(home)
        async def configured_synthesis(lesson, directory):
            return await synthesize(lesson, directory, speech_settings(home))

        state = deliver(payload, home / "english-lesson-delivery-state", Telegram(token, chat_id), configured_synthesis)
        print(json.dumps({"success": True, "message_ids": state["message_ids"]}))
    except DeliveryError as error:
        print(json.dumps({"success": False, "error": str(error)}))
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"success": False, "error": "Local preparation failed; no secret details logged"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
