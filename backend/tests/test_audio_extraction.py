from collections import namedtuple
from unittest.mock import patch

import pytest

from audio_extraction import (
    AUDIO_BITRATE_KBPS,
    TRANSCRIPTION_PROVIDER_CHUNK_PROFILES,
    AudioExtractionError,
    chunk_duration_seconds,
    extract_and_chunk_audio,
)

FakeCompletedProcess = namedtuple("FakeCompletedProcess", ["returncode", "stderr"])


def test_chunk_duration_seconds_stays_under_target_bytes():
    seconds = chunk_duration_seconds(bitrate_kbps=64, target_bytes=20 * 1024 * 1024)
    bytes_per_second = (64 * 1000) / 8
    assert seconds * bytes_per_second <= 20 * 1024 * 1024


def test_chunk_duration_seconds_never_returns_zero_or_negative():
    assert chunk_duration_seconds(bitrate_kbps=320, target_bytes=1) == 1


def test_extract_and_chunk_audio_raises_when_ffmpeg_missing(tmp_path):
    with patch("audio_extraction.check_ffmpeg_available", return_value=False):
        with pytest.raises(AudioExtractionError, match="ffmpeg isn't available"):
            extract_and_chunk_audio("video.mp4", str(tmp_path))


def test_extract_and_chunk_audio_raises_on_nonzero_ffmpeg_exit(tmp_path):
    with patch("audio_extraction.check_ffmpeg_available", return_value=True), \
         patch("audio_extraction.get_bundled_ffmpeg_path", return_value=None), \
         patch(
             "audio_extraction.subprocess.run",
             return_value=FakeCompletedProcess(returncode=1, stderr="some ffmpeg error"),
         ):
        with pytest.raises(AudioExtractionError, match="Couldn't extract audio"):
            extract_and_chunk_audio("video.mp4", str(tmp_path))


def test_extract_and_chunk_audio_raises_when_no_chunks_produced(tmp_path):
    with patch("audio_extraction.check_ffmpeg_available", return_value=True), \
         patch("audio_extraction.get_bundled_ffmpeg_path", return_value=None), \
         patch(
             "audio_extraction.subprocess.run",
             return_value=FakeCompletedProcess(returncode=0, stderr=""),
         ):
        with pytest.raises(AudioExtractionError, match="no audio track"):
            extract_and_chunk_audio("video.mp4", str(tmp_path))


def test_extract_and_chunk_audio_returns_sorted_chunk_paths(tmp_path):
    (tmp_path / "chunk_0001.mp3").write_bytes(b"x" * 100)
    (tmp_path / "chunk_0000.mp3").write_bytes(b"x" * 100)
    with patch("audio_extraction.check_ffmpeg_available", return_value=True), \
         patch("audio_extraction.get_bundled_ffmpeg_path", return_value=None), \
         patch(
             "audio_extraction.subprocess.run",
             return_value=FakeCompletedProcess(returncode=0, stderr=""),
         ):
        chunks = extract_and_chunk_audio("video.mp4", str(tmp_path))
    assert [c.name for c in chunks] == ["chunk_0000.mp3", "chunk_0001.mp3"]


def test_extract_and_chunk_audio_raises_when_a_chunk_is_still_too_large(tmp_path):
    (tmp_path / "chunk_0000.mp3").write_bytes(b"x" * (26 * 1024 * 1024))
    with patch("audio_extraction.check_ffmpeg_available", return_value=True), \
         patch("audio_extraction.get_bundled_ffmpeg_path", return_value=None), \
         patch(
             "audio_extraction.subprocess.run",
             return_value=FakeCompletedProcess(returncode=0, stderr=""),
         ):
        with pytest.raises(AudioExtractionError, match="larger than the transcription"):
            extract_and_chunk_audio("video.mp4", str(tmp_path))


def test_target_chunk_bytes_stays_under_gemini_limit_after_base64_inflation():
    # Gemini's limit applies to the base64-encoded request, which is 4/3
    # larger than the raw chunk bytes on disk.
    profile = TRANSCRIPTION_PROVIDER_CHUNK_PROFILES["gemini"]
    assert profile["target_chunk_bytes"] * 4 / 3 < profile["request_limit_bytes"]


@pytest.mark.parametrize("provider", ["openai", "groq"])
def test_target_chunk_bytes_stays_under_request_limit_for_raw_multipart_providers(provider):
    # OpenAI/Groq take a raw multipart upload, not base64-inlined JSON, so
    # no inflation applies -- the target should still land safely under
    # their stated limit with margin for encoder/segment-boundary slop.
    profile = TRANSCRIPTION_PROVIDER_CHUNK_PROFILES[provider]
    assert profile["target_chunk_bytes"] < profile["request_limit_bytes"]


def test_extract_and_chunk_audio_raises_for_a_chunk_that_was_fine_under_the_old_whisper_limit(tmp_path):
    # 16MB raw was comfortably under Whisper's 25MB raw-byte cap, but at
    # 4/3 inflation (~21.3MB) it exceeds Gemini's 20MB encoded-request limit.
    (tmp_path / "chunk_0000.mp3").write_bytes(b"x" * (16 * 1024 * 1024))
    with patch("audio_extraction.check_ffmpeg_available", return_value=True), \
         patch("audio_extraction.get_bundled_ffmpeg_path", return_value=None), \
         patch(
             "audio_extraction.subprocess.run",
             return_value=FakeCompletedProcess(returncode=0, stderr=""),
         ):
        with pytest.raises(AudioExtractionError, match="larger than the transcription"):
            extract_and_chunk_audio("video.mp4", str(tmp_path))


def test_extract_and_chunk_audio_applies_base64_inflation_only_for_gemini(tmp_path):
    # 16MB raw is fine for OpenAI/Groq's un-inflated 25MB raw-multipart
    # limit, but at Gemini's 4/3 base64 inflation (~21.3MB) it exceeds
    # Gemini's 20MB encoded-request limit -- same raw chunk, different
    # outcome depending on which provider is configured.
    (tmp_path / "chunk_0000.mp3").write_bytes(b"x" * (16 * 1024 * 1024))
    with patch("audio_extraction.check_ffmpeg_available", return_value=True), \
         patch("audio_extraction.get_bundled_ffmpeg_path", return_value=None), \
         patch(
             "audio_extraction.subprocess.run",
             return_value=FakeCompletedProcess(returncode=0, stderr=""),
         ):
        chunks = extract_and_chunk_audio("video.mp4", str(tmp_path), provider="openai")
        assert len(chunks) == 1
        with pytest.raises(AudioExtractionError, match="larger than the transcription"):
            extract_and_chunk_audio("video.mp4", str(tmp_path), provider="gemini")


def test_extract_and_chunk_audio_uses_bundled_ffmpeg_path_when_available(tmp_path):
    (tmp_path / "chunk_0000.mp3").write_bytes(b"x" * 100)
    captured_cmd = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return FakeCompletedProcess(returncode=0, stderr="")

    with patch("audio_extraction.check_ffmpeg_available", return_value=True), \
         patch("audio_extraction.get_bundled_ffmpeg_path", return_value="C:/bundled/ffmpeg.exe"), \
         patch("audio_extraction.subprocess.run", side_effect=fake_run):
        extract_and_chunk_audio("video.mp4", str(tmp_path))
    assert captured_cmd["cmd"][0] == "C:/bundled/ffmpeg.exe"
