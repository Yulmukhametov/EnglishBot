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
    return ("**Твоя фраза с исправлениями:**\n" + " ".join(edits)
            + "\n\n**Правильный вариант:**\n" + payload["corrected_en"]
            + "\n\n**Разбор:**\n" + "\n".join("> " + line for line in payload["feedback_ru"].split("\n")))


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
    required = {"request_id", "feedback_ru", "reply_en"}
    optional = {"original_en", "corrected_en"}
    if not isinstance(payload, dict) or not required <= set(payload) or set(payload) - required - optional:
        raise DeliveryError("Input needs request_id, feedback_ru, reply_en; original_en/corrected_en are optional")
    if ("original_en" in payload) != ("corrected_en" in payload):
        raise DeliveryError("original_en and corrected_en must be provided together")
    if not isinstance(payload["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", payload["request_id"]):
        raise DeliveryError("Invalid request_id")
    for key in ("feedback_ru", "reply_en"):
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise DeliveryError(f"{key} must contain 1–2000 characters")
    for key in ("original_en", "corrected_en"):
        if key in payload and (not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > 1200):
            raise DeliveryError(f"{key} must contain 1–1200 characters")
    if len(spoken_text(feedback_display(payload)).encode("utf-16-le")) // 2 > 4000:
        raise DeliveryError("Formatted feedback too long; shorten the explanation")
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
    pause = destination.with_name("language-pause.wav")
    if pause_ms:
        await run("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{pause_ms / 1000:.3f}",
                  "-c:a", "pcm_s16le", str(pause))
    # Trim only very quiet leading/trailing audio, keeping 40 ms of breathing room.
    # Preserve sentence pauses; add one short, controlled gap at language switches.
    trim = ("silenceremove=start_periods=1:start_duration=0.02:start_threshold=-50dB:start_silence=0.04,"
            "areverse,silenceremove=start_periods=1:start_duration=0.02:"
            "start_threshold=-50dB:start_silence=0.04,areverse")
    for index, path in enumerate(paths):
        cleaned = destination.with_name(f"trimmed-{index:02d}.wav")
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

    segments = speech_segments(payload["feedback_ru"])
    pieces = [directory / f"feedback-part-{i:02d}.mp3" for i in range(len(segments))]
    # Both audio files must finish before any Telegram delivery starts.
    await asyncio.gather(
        *(piece(text, voices[language], path)
          for (language, text), path in zip(segments, pieces)),
        piece(spoken_text(payload["reply_en"]), voices["en"], paths[1]),
    )
    if len(pieces) == 1:
        pieces[0].replace(paths[0])
    else:
        await combine_speech(pieces, paths[0], settings["pause_ms"])
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
            try:
                paths = asyncio.run(synthesizer(payload, Path(temp)))
            except Exception:
                state["status"] = "preparation_failed"
                save_state(path, state)
                raise DeliveryError("Speech preparation failed; no Telegram messages sent") from None
            steps = [
                lambda: sender.send_voice(paths[0]),
                lambda: sender.send_text(feedback_display(payload)),
                lambda: sender.send_voice(paths[1]),
                lambda: sender.send_text(payload["reply_en"], hidden=True),
            ]
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
                state["status"] = "complete" if index == 4 else "acknowledged"
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
        settings = speech_settings(home)

        async def configured_synthesis(lesson, directory):
            return await synthesize(lesson, directory, settings)

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
