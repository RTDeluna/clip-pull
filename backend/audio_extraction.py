import logging
import math
import subprocess
from pathlib import Path

from downloader import check_ffmpeg_available, get_bundled_ffmpeg_path

logger = logging.getLogger("clippull")

# Each transcription provider has its own upload-size ceiling and transport:
# Gemini takes inline base64 audio inside a JSON request (its stated 20MB
# limit applies to the WHOLE request, so base64's 4/3 inflation of raw
# bytes has to be accounted for); OpenAI and Groq take a raw multipart file
# upload (their stated 25MB limit applies directly to the file on disk, no
# inflation). TARGET_CHUNK_BYTES is sized pre-inflation, comfortably under
# each REQUEST_LIMIT_BYTES, absorbing MP3 encoder overhead and the fact
# that ffmpeg's segment muxer splits on an approximate time boundary, not
# an exact byte count.
AUDIO_BITRATE_KBPS = 64
AUDIO_SAMPLE_RATE_HZ = 16000

TRANSCRIPTION_PROVIDER_CHUNK_PROFILES = {
    "gemini": {
        "request_limit_bytes": 20 * 1024 * 1024,
        "target_chunk_bytes": 14 * 1024 * 1024,
        "inflate_for_base64": True,
    },
    "openai": {
        "request_limit_bytes": 25 * 1024 * 1024,
        "target_chunk_bytes": 20 * 1024 * 1024,
        "inflate_for_base64": False,
    },
    "groq": {
        "request_limit_bytes": 25 * 1024 * 1024,
        "target_chunk_bytes": 20 * 1024 * 1024,
        "inflate_for_base64": False,
    },
}
DEFAULT_CHUNK_PROFILE = TRANSCRIPTION_PROVIDER_CHUNK_PROFILES["gemini"]


class AudioExtractionError(Exception):
    pass


def chunk_duration_seconds(bitrate_kbps: int = AUDIO_BITRATE_KBPS, target_bytes: int = None) -> int:
    """How many seconds of constant-bitrate audio fit in target_bytes."""
    if target_bytes is None:
        target_bytes = DEFAULT_CHUNK_PROFILE["target_chunk_bytes"]
    bytes_per_second = (bitrate_kbps * 1000) / 8
    return max(1, math.floor(target_bytes / bytes_per_second))


def extract_and_chunk_audio(
    video_path: str,
    work_dir: str,
    bitrate_kbps: int = AUDIO_BITRATE_KBPS,
    provider: str = "gemini",
) -> list[Path]:
    """Extracts a compressed mono audio track from video_path and splits it
    into chunks small enough for the configured transcription provider's
    upload limit, via a single ffmpeg invocation (extract + resample +
    encode + segment all at once). Blocking -- callers must run this
    inside run_in_executor, the same way downloader.py runs
    run_download/probe_total_bytes."""
    profile = TRANSCRIPTION_PROVIDER_CHUNK_PROFILES.get(provider, DEFAULT_CHUNK_PROFILE)
    request_limit_bytes = profile["request_limit_bytes"]
    target_chunk_bytes = profile["target_chunk_bytes"]
    inflate_for_base64 = profile["inflate_for_base64"]
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
    # Checked post-base64-inflation where applicable (4/3 of raw bytes),
    # since that's the actual request-size constraint the client sends
    # against for providers that inline-encode audio, not the raw chunk
    # size on disk.
    inflation = 4 / 3 if inflate_for_base64 else 1
    oversized = [c for c in chunks if c.stat().st_size * inflation > request_limit_bytes]
    if oversized:
        raise AudioExtractionError(
            "One or more audio chunks came out larger than the transcription "
            "service allows, even after compression."
        )
    return chunks
