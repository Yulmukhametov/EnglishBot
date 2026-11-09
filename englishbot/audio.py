import asyncio
from pathlib import Path


async def convert(ffmpeg: str, source: Path, destination: Path, *, opus: bool = False):
    args = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vn", "-ac", "1"]
    args += ["-c:a", "libopus", "-b:a", "48k"] if opus else ["-ar", "16000", "-c:a", "pcm_s16le"]
    process = await asyncio.create_subprocess_exec(
        *args, str(destination), stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE)
    try:
        _, error = await asyncio.wait_for(process.communicate(), timeout=45)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError("Audio conversion failed")  # Do not leak paths or audio contents.
