"""Compare Scribe responses on one cached inbound recording, without changing Hermes.

Run with the installed delivery environment (python-dotenv). Makes two billable
speech-to-text requests; does not call the tutor or send Telegram messages.
"""

import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values


def latest_audio(home):
    candidates = []
    for folder in (home / "cache/audio", home / "audio_cache"):
        if folder.is_symlink():
            continue
        for path in folder.glob("audio_*"):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in {".ogg", ".opus", ".mp3", ".wav", ".m4a"}:
                continue
            stat = path.stat()
            if stat.st_mtime >= time.time() - 86400 and 0 < stat.st_size <= 2_000_000:
                candidates.append(path)
    if not candidates:
        raise ValueError("Не найдена недавняя входящая аудиозапись до 2 МБ в кэше Hermes.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def transcribe(audio, key, language):
    boundary = "hermes-stt-check-" + secrets.token_hex(16)
    body = bytearray()
    fields = {"model_id": "scribe_v2", "tag_audio_events": "false", "diarize": "false"}
    if language:
        fields["language_code"] = language
    for name, value in fields.items():
        body.extend(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    body.extend(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="recording{audio.suffix}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        .encode()
    )
    body.extend(audio.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        "https://api.elevenlabs.io/v1/speech-to-text",
        data=bytes(body),
        headers={"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return {"language": result.get("language_code"), "text": result.get("text", "")[:4000]}


def error_detail(response, key):
    """Only expose known error fields, bounded and with credential redaction."""
    try:
        payload = json.loads(response.read(16384))
    except (OSError, ValueError):
        return "Ответ без JSON-описания; тело ответа скрыто."
    if not isinstance(payload, dict):
        return "Нестандартный формат ошибки; тело ответа скрыто."
    detail = payload.get("detail", payload)
    if isinstance(detail, dict):
        selected = {name: detail[name] for name in ("status", "code", "message")
                    if isinstance(detail.get(name), (str, int))}
        text = json.dumps(selected, ensure_ascii=False) if selected else "Нет описания ошибки."
    elif isinstance(detail, str):
        text = json.dumps(detail, ensure_ascii=False)
    else:
        text = "Нет текстового описания ошибки."
    text = text.replace(key, "[KEY REDACTED]")
    text = re.sub(r"\bsk[-_][A-Za-z0-9_-]+", "[KEY REDACTED]", text)
    return text[:1200]


def main():
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    audio = latest_audio(home)
    key = os.environ.get("ELEVENLABS_API_KEY") or dotenv_values(home / ".env").get(
        "ELEVENLABS_API_KEY"
    )
    if not key:
        raise ValueError("ELEVENLABS_API_KEY не найден. Значения ключей проверка не выводит.")
    launcher = Path.home() / ".local/bin/hermes"
    for setting in ("stt.provider", "stt.elevenlabs.model_id", "stt.elevenlabs.language_code"):
        result = subprocess.run(
            [str(launcher), "config", "get", setting], capture_output=True, text=True, timeout=30
        )
        print(f"{setting}: {result.stdout.strip()[:200]}", flush=True)
    print(f"Последняя входящая запись: {audio.name}; {audio.stat().st_size} байт", flush=True)
    print("Два прямых запроса к ElevenLabs: автоопределение и подсказка ru.", flush=True)
    for language in (None, "ru"):
        print(f"\nРежим: {language or 'auto'}", flush=True)
        try:
            result = transcribe(audio, key, language)
        except urllib.error.HTTPError as exc:
            # No raw body, request object or headers; only redacted error fields.
            print(f"ElevenLabs HTTP {exc.code}: {error_detail(exc, key)}", flush=True)
            return 1
        except (OSError, ValueError):
            print("Не удалось получить корректный ответ ElevenLabs; ключ не выводится.", flush=True)
            return 1
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Ошибка локальной проверки."
        print(message)
        raise SystemExit(1) from None
