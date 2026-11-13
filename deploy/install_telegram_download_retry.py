"""Install a guarded local compatibility patch; preserve original adapter for rollback."""

import argparse
import ast
import os
import stat
import tempfile
from pathlib import Path

MARKER = "# EnglishBot: bounded inbound Telegram download retries v1"
OLD = "            file_obj = await source.get_file()\n            data = await file_obj.download_as_bytearray()"
NEW = "            file_obj, data = await _englishbot_download_media_v1(source)"


def patched_source(original, helper):
    if MARKER in original:
        if original.count(MARKER) == 1 and NEW in original and original.endswith(helper):
            return original
        raise ValueError("Обнаружена другая версия исправления. Автоматическая перезапись отменена.")
    # Match the exact pair of calls inside the intended method, not unrelated HTTP requests.
    tree = ast.parse(original)
    matches = [node for node in ast.walk(tree)
               if isinstance(node, ast.AsyncFunctionDef) and node.name == "_cache_inbound_av"]
    if len(matches) != 1:
        raise ValueError("Версия обработчика отличается: _cache_inbound_av не найден однозначно.")
    lines = original.splitlines(keepends=True)
    node = matches[0]
    start, end = node.lineno - 1, node.end_lineno
    method = "".join(lines[start:end])
    if method.count(OLD) != 1:
        raise ValueError("Код загрузки отличается от ожидаемого. Файлы не изменены.")
    lines[start:end] = [method.replace(OLD, NEW, 1)]
    result = "".join(lines).rstrip() + "\n\n" + MARKER + "\n" + helper
    ast.parse(result)
    return result


def install(adapter, helper):
    adapter = adapter.resolve(strict=True)
    original = adapter.read_text()
    updated = patched_source(original, helper)
    if updated == original:
        return "Исправление уже установлено."
    backup = adapter.with_name(adapter.name + ".englishbot-download-retry.bak")
    if backup.exists():
        raise ValueError("Резервная копия уже существует. Сначала нужно проверить предыдущее обновление.")
    # Never overwrite an existing backup; only write after source validation succeeds.
    with backup.open("x") as stream:
        os.chmod(backup, 0o600)
        stream.write(original)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=adapter.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, stat.S_IMODE(adapter.stat().st_mode))
        os.replace(temp_path, adapter)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return f"Исправление установлено: {adapter}\nИсходный файл сохранён: {backup}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", type=Path)
    args = parser.parse_args()
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    if args.adapter:
        adapter = args.adapter
    else:
        root = home / "hermes-agent"
        candidates = [root / "plugins/platforms/telegram/adapter.py",
                      root / "gateway/platforms/telegram.py"]
        found = list({path.resolve() for path in candidates if path.is_file()})
        if len(found) != 1:
            raise ValueError("Путь адаптера не определён однозначно. Укажите --adapter после проверки установки.")
        adapter = found[0]
    helper = Path(__file__).with_name("telegram_download_retry.py").read_text()
    print(install(adapter, helper))
    print("Теперь перезапустите только gateway этого пользователя.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"Установка остановлена: {exc}")
        raise SystemExit(1) from None
