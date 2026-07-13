import json

import pytest

from export_notes import ExportError, build_markdown, build_srt, build_txt, write_exports

SAMPLE_TRANSCRIPT = (
    "[00:00:00] Welcome to the lesson.\n"
    "This line has no timestamp and should be skipped.\n"
    "[00:00:05] Today we cover exporting.\n"
    "[00:01:02] Thanks for watching."
)


# -- build_srt ----------------------------------------------------------


def test_build_srt_numbers_cues_and_chains_end_times():
    srt = build_srt(SAMPLE_TRANSCRIPT)
    blocks = srt.split("\n\n")
    assert len(blocks) == 3  # the un-timestamped line is skipped

    assert blocks[0] == (
        "1\n00:00:00,000 --> 00:00:05,000\nWelcome to the lesson."
    )
    # Cue 2 ends exactly where cue 3 starts (00:01:02 -> 62s).
    assert blocks[1] == (
        "2\n00:00:05,000 --> 00:01:02,000\nToday we cover exporting."
    )
    # Final cue has no successor, so it gets the fixed +4s tail (62 -> 66s).
    assert blocks[2] == (
        "3\n00:01:02,000 --> 00:01:06,000\nThanks for watching."
    )


def test_build_srt_uses_comma_millisecond_separator():
    assert ",000 -->" in build_srt(SAMPLE_TRANSCRIPT)


# -- build_txt ----------------------------------------------------------


def test_build_txt_strips_timestamps_and_skips_unmatched_lines():
    assert build_txt(SAMPLE_TRANSCRIPT) == (
        "Welcome to the lesson.\n"
        "Today we cover exporting.\n"
        "Thanks for watching."
    )


# -- build_markdown -----------------------------------------------------


def _structured_summary():
    return json.dumps({
        "tldr": "A concise overview.",
        "key_points": [{"seconds": 125, "text": "The key point"}],
        "chapters": [{"seconds": 0, "title": "Introduction"}],
    })


def test_build_markdown_renders_structured_summary():
    entry = {
        "title": "My Lesson",
        "transcript": SAMPLE_TRANSCRIPT,
        "summary": _structured_summary(),
    }
    md = build_markdown(entry)
    assert md.startswith("# My Lesson")
    assert "## Summary" in md
    assert "A concise overview." in md
    assert "### Key Points" in md
    assert "- **[00:02:05]** The key point" in md
    assert "### Chapters" in md
    assert "- **[00:00:00]** Introduction" in md
    assert "## Transcript" in md
    # The raw transcript is included verbatim (timestamps intact).
    assert "[00:00:00] Welcome to the lesson." in md


def test_build_markdown_handles_legacy_plain_prose_summary():
    entry = {
        "title": "Legacy Lesson",
        "transcript": SAMPLE_TRANSCRIPT,
        "summary": "This is an old plain-prose summary.",
    }
    md = build_markdown(entry)
    assert "This is an old plain-prose summary." in md
    # No structured lists for a legacy summary.
    assert "### Key Points" not in md
    assert "### Chapters" not in md


def test_build_markdown_falls_back_to_default_title():
    entry = {"title": None, "transcript": SAMPLE_TRANSCRIPT, "summary": None}
    assert build_markdown(entry).startswith("# Lesson Notes")


# -- write_exports ------------------------------------------------------


def _entry(tmp_path, filename="Lesson 1.mp4", transcript=SAMPLE_TRANSCRIPT):
    return {
        "title": "Lesson 1",
        "transcript": transcript,
        "summary": _structured_summary(),
        "output_path": str(tmp_path / filename),
    }


def test_write_exports_writes_all_formats_into_source_directory(tmp_path):
    entry = _entry(tmp_path)
    paths = write_exports(entry, ["srt", "txt", "md"])

    assert len(paths) == 3
    for path in paths:
        assert path.exists()
        assert path.parent == tmp_path
        assert path.stem == "Lesson 1"
    suffixes = {p.suffix for p in paths}
    assert suffixes == {".srt", ".txt", ".md"}


def test_write_exports_sanitizes_the_filename_stem(tmp_path):
    entry = _entry(tmp_path, filename="Clip|One.mp4")
    paths = write_exports(entry, ["txt"])
    # The invalid "|" is replaced by sanitize_filename before writing.
    assert paths[0].name == "Clip_One.txt"
    assert paths[0].exists()


def test_write_exports_content_matches_builders(tmp_path):
    entry = _entry(tmp_path)
    (srt_path,) = write_exports(entry, ["srt"])
    assert srt_path.read_text(encoding="utf-8") == build_srt(SAMPLE_TRANSCRIPT)


def test_write_exports_raises_when_no_transcript(tmp_path):
    entry = _entry(tmp_path, transcript="")
    with pytest.raises(ExportError):
        write_exports(entry, ["srt"])


def test_write_exports_raises_when_no_output_path():
    entry = {"title": "X", "transcript": SAMPLE_TRANSCRIPT, "summary": None, "output_path": None}
    with pytest.raises(ExportError):
        write_exports(entry, ["srt"])


def test_write_exports_raises_on_unknown_format(tmp_path):
    entry = _entry(tmp_path)
    with pytest.raises(ExportError):
        write_exports(entry, ["pdf"])
