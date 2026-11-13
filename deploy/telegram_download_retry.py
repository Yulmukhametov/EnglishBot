"""Small, bounded retry helper for inbound Telegram GETs only."""

import asyncio as _englishbot_asyncio
import logging as _englishbot_logging


async def _englishbot_download_media_v1(source):
    from telegram.error import BadRequest, NetworkError

    log = _englishbot_logging.getLogger(__name__)
    # Bound the whole operation, including getFile, download and retry backoff.
    async with _englishbot_asyncio.timeout(90):
        for attempt in range(3):
            try:
                async with _englishbot_asyncio.timeout(40):
                    file_obj = await source.get_file(
                        connect_timeout=10, read_timeout=20,
                        write_timeout=20, pool_timeout=5,
                    )
                    data = await file_obj.download_as_bytearray(
                        connect_timeout=10, read_timeout=30,
                        write_timeout=20, pool_timeout=5,
                    )
                return file_obj, data
            except BadRequest:
                # BadRequest subclasses NetworkError in PTB. Invalid files are not transient.
                raise
            except (NetworkError, TimeoutError):
                if attempt == 2:
                    raise
                # Do not log signed file URLs, credentials or exception bodies.
                log.warning("[Telegram] Inbound media download interrupted; retry %d/2", attempt + 1)
                await _englishbot_asyncio.sleep(attempt + 1)
