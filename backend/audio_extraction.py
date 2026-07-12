import logging
import math
import subprocess
from pathlib import Path

from downloader import check_ffmpeg_available, get_bundled_ffmpeg_path

logger = logging.getLogger("clippull")

# Gemini's inline-audio request caps the WHOLE request (prompt + schema +
# base64-encoded audio) at 20MB. Base64 inflates raw bytes by 4/3, so the
# raw-chunk-size ceiling is lower than that stated limit -- GEMINI_INLINE_
# REQUEST_LIMIT_BYTES is the API's real limit; TARGET_CHUNK_BYTES is sized
# (pre-inflation) to land comfortably under it, also absorbing MP3 encoder
# overhead and the fact that ffmpeg's segment muxer splits on an
# approximate time boundary, not an exact byte count.
GEMINI_INLINE_REQUEST_LIMIT_BYTES = 20 * 1024 * 1024
TARGET_CHUNK_BYTES = 14 * 1024 * 1024
AUDIO_BITRATE_KBPS = 64
AUDIO_SAMPLE_RATE_HZ = 16000


class AudioExtractionError(Exception):
    pass


def chunk_duration_seconds(
    bitrate_kbps: int = AUDIO_BITRATE_KBPS, target_bytes: int = TARGET_CHUNK_BYTES
) -> int:
    """How many seconds of constant-bitrate audio fit in target_bytes."""
    bytes_per_second = (bitrate_kbps * 1000) / 8
    return max(1, math.floor(target_bytes / bytes_per_second))


def extract_and_chunk_audio(
    video_path: str,
    work_dir: str,
    bitrate_kbps: int = AUDIO_BITRATE_KBPS,
    target_chunk_bytes: int = TARGET_CHUNK_BYTES,
) -> list[Path]:
    """Extracts a compressed mono audio track from video_path and splits it
    into chunks small enough for Gemini's inline-audio request limit, via a
    single ffmpeg invocation (extract + resample + encode + segment all at
    once). Blocking -- callers must run this inside run_in_executor, the
    same way downloader.py runs run_download/probe_total_bytes."""
    if not check_ffmpeg_available():
        raise AudioExtractionError(
            "ffmpeg isn't available, so audio can't be extracted for transcription."
        )
    out_dir = Path(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    segment_time = chunk_duration_seconds(bitrate_kbps, target_chunk_bytes)
    output_pattern = str(out_dir / "chunk_%04d.mp3")

    ffmpeg = get_bundled_ffmpeg_path() or "ffmpeg"
    cmd = [
        ffmpeg,
        "-y",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", str(AUDIO_SAMPLE_RATE_HZ),
        "-b:a", f"{bitrate_kbps}k",
        "-f", "segment",
        "-segment_time", str(segment_time),
        "-reset_timestamps", "1",
        output_pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-10:])
        logger.error("ffmpeg audio extraction failed for %s: %s", video_path, stderr_tail)
        raise AudioExtractionError("Couldn't extract audio from this video for transcription.")

    chunks = sorted(out_dir.glob("chunk_*.mp3"))
    if not chunks:
        raise AudioExtractionError(
            "Audio extraction produced no output — this video may have no audio track."
        )
    # Checked post-base64-inflation (4/3 of raw bytes), since that's the
    # actual request-size constraint the client sends against, not the raw
    # chunk size on disk.
    oversized = [
        c for c in chunks if c.stat().st_size * 4 / 3 > GEMINI_INLINE_REQUEST_LIMIT_BYTES
    ]
    if oversized:
        raise AudioExtractionError(
            "One or more audio chunks came out larger than the transcription "
            "service allows, even after compression."
        )
    return chunks
