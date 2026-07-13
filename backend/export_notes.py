import re
from pathlib import Path

from downloader import sanitize_filename
from transcription import format_timestamp, parse_structured_notes

# Each stitched transcript line looks like "[HH:MM:SS] spoken text" (see
# stitch_transcript/format_timestamp in transcription.py). Lines that don't
# match this shape are ignored by every builder below.
TRANSCRIPT_LINE_PATTERN = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] (.*)$")

# The last SRT cue has no following cue to borrow an end time from, so give it a
# small fixed tail rather than a zero-length duration.
SRT_TAIL_SECONDS = 4

VALID_EXPORT_FORMATS = ("srt", "txt", "md")


class ExportError(Exception):
    """Raised by write_exports when an entry can't be exported (no transcript,
    no source path, or an unknown format). The route layer turns this into a
    400 with the message."""


def _parse_transcript_cues(transcript_text: str) -> list[tuple[int, str]]:
    """Parses a stitched transcript into (start_seconds, text) tuples, skipping
    any line that isn't timestamp-prefixed."""
    cues: list[tuple[int, str]] = []
    for line in (transcript_text or "").splitlines():
        match = TRANSCRIPT_LINE_PATTERN.match(line)
        if not match:
            continue
        hours, minutes, seconds, text = match.groups()
        total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        cues.append((total, text))
    return cues


def _srt_timestamp(total_seconds: int) -> str:
    """SRT uses HH:MM:SS,mmm (comma before milliseconds). We only have
    whole-second precision from the transcript, so mmm is always 000."""
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},000"


def build_srt(transcript_text: str) -> str:
    """Renders the transcript as SRT subtitle cues. Each cue ends where the next
    cue starts; the final cue gets a fixed +SRT_TAIL_SECONDS tail. Cue numbers
    are sequential and 1-based."""
    cues = _parse_transcript_cues(transcript_text)
    blocks: list[str] = []
    for index, (start, text) in enumerate(cues):
        if index + 1 < len(cues):
            end = cues[index + 1][0]
        else:
            end = start + SRT_TAIL_SECONDS
        blocks.append(
            f"{index + 1}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}"
        )
    return "\n\n".join(blocks)


def build_txt(transcript_text: str) -> str:
    """Renders just the spoken text, one segment per line, with the [HH:MM:SS]
    prefixes stripped off."""
    return "\n".join(text for _, text in _parse_transcript_cues(transcript_text))


def build_markdown(entry: dict) -> str:
    """Renders a full history entry as a Markdown lesson-notes document: a title
    heading, a Summary section (TL;DR + optional Key Points / Chapters), and the
    raw transcript. The `summary` field may be a structured-notes JSON string
    (new format) or legacy plain prose -- parse_structured_notes handles both,
    treating legacy/unparseable prose as a TL;DR-only block."""
    title = entry.get("title") or "Lesson Notes"
    transcript = entry.get("transcript") or ""
    notes = parse_structured_notes(entry.get("summary") or "")

    lines = [f"# {title}", "", "## Summary", ""]
    tldr = notes.get("tldr")
    if tldr:
        lines.extend([tldr, ""])

    key_points = notes.get("key_points") or []
    if key_points:
        lines.extend(["### Key Points", ""])
        for point in key_points:
            lines.append(f"- **[{format_timestamp(point['seconds'])}]** {point['text']}")
        lines.append("")

    chapters = notes.get("chapters") or []
    if chapters:
        lines.extend(["### Chapters", ""])
        for chapter in chapters:
            lines.append(f"- **[{format_timestamp(chapter['seconds'])}]** {chapter['title']}")
        lines.append("")

    lines.extend(["## Transcript", "", transcript])
    return "\n".join(lines)


def write_exports(entry: dict, formats: list[str]) -> list[Path]:
    """Writes the requested export formats (a subset of {"srt", "txt", "md"})
    into the video's own directory, using a sanitized copy of its filename stem
    as the base name. The directory and filename are always derived from the
    entry's own output_path -- never from any client-supplied path. Returns the
    written Paths. Raises ExportError if there's nothing to export."""
    transcript = entry.get("transcript")
    if not transcript:
        raise ExportError("This entry hasn't been transcribed yet, so there's nothing to export.")
    output_path = entry.get("output_path")
    if not output_path:
        raise ExportError("This entry has no downloaded file to export alongside.")

    source = Path(output_path)
    directory = source.parent
    stem = sanitize_filename(source.stem)

    builders = {
        "srt": lambda: build_srt(transcript),
        "txt": lambda: build_txt(transcript),
        "md": lambda: build_markdown(entry),
    }

    written: list[Path] = []
    for fmt in formats:
        builder = builders.get(fmt)
        if builder is None:
            raise ExportError(f"Unknown export format: {fmt}")
        target = directory / f"{stem}.{fmt}"
        target.write_text(builder(), encoding="utf-8")
        written.append(target)
    return written
