"""Prepare two voice notes and deliver four Telegram messages in a fixed order.

Only for the single numeric user in TELEGRAM_ALLOWED_USERS. Never prints credentials.
Uses an isolated environment, not Hermes's managed Python runtime.
"""
import argparse
import asyncio
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


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {"request_id", "feedback_ru", "reply_en"}:
        raise DeliveryError("Input must contain request_id, feedback_ru, reply_en only")
    if not isinstance(payload["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", payload["request_id"]):
        raise DeliveryError("Invalid request_id")
    for key in ("feedback_ru", "reply_en"):
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise DeliveryError(f"{key} must contain 1–2000 characters")
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
        value = html.escape(text)
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


async def synthesize(payload, directory):
    import edge_tts
    voice = "en-US-AndrewMultilingualNeural"
    paths = [directory / "feedback.mp3", directory / "reply.mp3"]
    # Generate both first. No Telegram messages are sent if synthesis fails.
    await asyncio.gather(
        edge_tts.Communicate(payload["feedback_ru"], voice).save(str(paths[0])),
        edge_tts.Communicate(payload["reply_en"], voice).save(str(paths[1])),
    )
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise DeliveryError("Speech generation did not produce audio")
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
                lambda: sender.send_text(payload["feedback_ru"]),
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
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    try:
        if args.input.stat().st_size > 30000:
            raise DeliveryError("Input file too large")
        payload = validate(json.loads(args.input.read_text(encoding="utf-8")))
        token, chat_id = credentials(home)
        state = deliver(payload, home / "english-lesson-delivery-state", Telegram(token, chat_id))
        print(json.dumps({"success": True, "message_ids": state["message_ids"]}))
    except DeliveryError as error:
        print(json.dumps({"success": False, "error": str(error)}))
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"success": False, "error": "Local preparation failed; no secret details logged"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
