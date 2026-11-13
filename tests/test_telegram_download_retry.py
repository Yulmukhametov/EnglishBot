import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "deploy" / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load("download_helper", "telegram_download_retry.py")
installer = load("download_installer", "install_telegram_download_retry.py")
HELPER_SOURCE = Path(helper.__file__).read_text()
ADAPTER = '''class Adapter:
    async def _cache_inbound_av(self, source):
        try:
            file_obj = await source.get_file()
            data = await file_obj.download_as_bytearray()
            return file_obj, data
        except Exception:
            raise
'''


@pytest.fixture
def errors(monkeypatch):
    class NetworkError(Exception):
        pass

    class BadRequest(NetworkError):
        pass

    class Forbidden(Exception):
        pass

    module = types.ModuleType("telegram.error")
    module.NetworkError, module.BadRequest, module.Forbidden = NetworkError, BadRequest, Forbidden
    monkeypatch.setitem(sys.modules, "telegram.error", module)
    monkeypatch.setitem(sys.modules, "telegram", types.ModuleType("telegram"))
    return module


def source_with(download):
    file = types.SimpleNamespace(download_as_bytearray=download)
    return types.SimpleNamespace(get_file=AsyncMock(return_value=file)), file


@pytest.mark.asyncio
async def test_transient_download_retries_fetches_file_again_and_returns_once(errors, monkeypatch):
    download = AsyncMock(side_effect=[errors.NetworkError("signed-secret-url"), bytearray(b"voice")])
    source, file = source_with(download)
    sleep = AsyncMock()
    monkeypatch.setattr(helper._englishbot_asyncio, "sleep", sleep)
    result = await helper._englishbot_download_media_v1(source)
    assert result == (file, bytearray(b"voice"))
    assert source.get_file.await_count == 2
    assert download.await_count == 2
    sleep.assert_awaited_once_with(1)
    assert source.get_file.call_args.kwargs["connect_timeout"] == 10
    assert download.call_args.kwargs["read_timeout"] == 30


@pytest.mark.asyncio
async def test_get_file_timeout_is_retried(errors, monkeypatch):
    source, file = source_with(AsyncMock(return_value=b"voice"))
    source.get_file.side_effect = [TimeoutError(), file]
    monkeypatch.setattr(helper._englishbot_asyncio, "sleep", AsyncMock())
    assert (await helper._englishbot_download_media_v1(source))[1] == b"voice"
    assert source.get_file.await_count == 2


@pytest.mark.asyncio
async def test_failure_is_bounded_to_three_attempts_and_logs_no_urls(errors, monkeypatch, caplog):
    source, _ = source_with(AsyncMock(side_effect=errors.NetworkError("signed-secret-url")))
    sleep = AsyncMock()
    monkeypatch.setattr(helper._englishbot_asyncio, "sleep", sleep)
    with pytest.raises(errors.NetworkError):
        await helper._englishbot_download_media_v1(source)
    assert source.get_file.await_count == 3
    assert sleep.await_count == 2
    assert "signed-secret-url" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["BadRequest", "Forbidden"])
async def test_permanent_errors_not_retried(errors, error_name, monkeypatch):
    error = getattr(errors, error_name)
    source, _ = source_with(AsyncMock(side_effect=error()))
    sleep = AsyncMock()
    monkeypatch.setattr(helper._englishbot_asyncio, "sleep", sleep)
    with pytest.raises(error):
        await helper._englishbot_download_media_v1(source)
    assert source.get_file.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_does_not_retry(errors):
    source, _ = source_with(AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await helper._englishbot_download_media_v1(source)
    assert source.get_file.await_count == 1


@pytest.mark.asyncio
async def test_overall_deadline_stops_hung_download(errors, monkeypatch):
    async def hung(**kwargs):
        await asyncio.Event().wait()

    source, _ = source_with(AsyncMock(side_effect=hung))
    real_timeout = asyncio.timeout
    budgets = []

    def short_timeout(seconds):
        budgets.append(seconds)
        return real_timeout(0.01 if seconds == 90 else 10)

    monkeypatch.setattr(helper._englishbot_asyncio, "timeout", short_timeout)
    with pytest.raises(TimeoutError):
        await helper._englishbot_download_media_v1(source)
    assert budgets == [90, 40]
    assert source.get_file.await_count == 1


def test_patch_backup_and_reapplication(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(ADAPTER)
    installer.install(adapter, HELPER_SOURCE)
    assert adapter.with_name("adapter.py.englishbot-download-retry.bak").read_text() == ADAPTER
    patched = adapter.read_text()
    assert patched.count(installer.NEW) == 1
    assert patched.endswith(HELPER_SOURCE)
    assert "уже установлено" in installer.install(adapter, HELPER_SOURCE)
    assert adapter.read_text() == patched


@pytest.mark.parametrize("source", [ADAPTER.replace("source.get_file()", "source.get_file(read_timeout=5)"),
                                   "class Adapter: pass\n"])
def test_unknown_version_refused_without_writing(tmp_path, source):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(source)
    with pytest.raises(ValueError):
        installer.install(adapter, HELPER_SOURCE)
    assert adapter.read_text() == source
    assert not adapter.with_name("adapter.py.englishbot-download-retry.bak").exists()


def test_existing_backup_not_overwritten(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(ADAPTER)
    backup = adapter.with_name("adapter.py.englishbot-download-retry.bak")
    backup.write_text("existing")
    with pytest.raises(ValueError):
        installer.install(adapter, HELPER_SOURCE)
    assert backup.read_text() == "existing"
    assert adapter.read_text() == ADAPTER
