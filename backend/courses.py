"""Course Workspace: treat a folder of 2+ downloaded lessons as one knowledge
base. No new AI-client code and no new DB table -- a "course" is just the shared
output folder of several History entries, and its knowledge base is assembled on
the fly from each ready lesson's already-stored structured notes (the `summary`
column parsed via parse_structured_notes). The routes in transcription_routes.py
call the existing per-provider `.chat()` method with the context this module
assembles -- course chat is a different context string, not a new AI feature.
"""

from pathlib import Path

from downloader import sanitize_filename
from transcription import format_timestamp, parse_structured_notes

# Upper bound on how many finished downloads we scan when grouping into courses.
# HistoryStore.search()'s own default limit is only 200; a personal download
# history won't realistically exceed this, and it bounds the one-shot scan.
COURSE_HISTORY_SCAN_LIMIT = 5000

# Generous ceiling on the assembled course-notes context handed to a chat call.
# Structured notes are far smaller per-lesson than raw transcripts, so this
# comfortably covers real-world courses while still bounding token cost. Past
# it we truncate to whole lessons with a visible note (never a silent cutoff).
MAX_COURSE_CONTEXT_CHARS = 100000

# The single AI-generated part of a course study guide: a short thematic
# overview. Everything else in the digest is assembled deterministically from
# stored notes. Passed to client.chat() as the "question", with the assembled
# course context as its grounding, so no new client method is needed.
COURSE_OVERVIEW_INSTRUCTION = (
    "Write a 3 to 5 sentence overview that ties these lessons together "
    "thematically: what the course as a whole covers and how the lessons build "
    "on each other. Respond with only the overview paragraph — no heading, no "
    "preamble, no bullet points, no conversational framing."
)

# Question wrappers for the two chat modes. Both reuse the exact same
# client.chat(context, question, history) call; only this prompt framing
# differs. The wrapped string becomes the trailing user turn, with the labeled
# per-lesson notes riding in the chat system prompt as the grounding context.
_CHAT_MODE_PREFIX = (
    "Using the course lesson notes provided, answer the question below. Cite "
    "which lesson each part of your answer comes from, using that lesson's "
    "title, and include the relevant [HH:MM:SS] timestamp when it points to a "
    "specific moment.\n\nQuestion: "
)
_SEARCH_MODE_PREFIX = (
    "Using the course lesson notes provided, find the lessons most relevant to "
    "the query below. Respond with a compact bulleted list — one bullet per "
    "matching lesson giving its title, the relevant [HH:MM:SS] timestamp(s), "
    "and a one-line reason it matches. Do not write a conversational "
    "paragraph.\n\nQuery: "
)


def _lesson_title(entry: dict) -> str:
    """A lesson's display name: its title, falling back to its url when the
    download carried no title (url is NOT NULL in History), then a last-ditch
    literal so a block is never labeled with an empty string."""
    return entry.get("title") or entry.get("url") or "Untitled lesson"


def sort_course_entries(entries: list[dict]) -> list[dict]:
    """Stable oldest-first order (by autoincrement id), so lesson numbering is
    consistent between the assembled chat context and the study-guide sections
    -- both number the ready lessons 1..N off this same ordering."""
    return sorted(entries, key=lambda entry: entry.get("id") or 0)


def _group_done_entries_by_folder(entries: list[dict]) -> dict[Path, list[dict]]:
    """Groups finished-download entries by the parent folder of their
    output_path. Entries without an output_path are skipped (nothing to group
    on). The Path key normalizes slash/separator differences so the same folder
    always groups together regardless of how the path string was stored."""
    groups: dict[Path, list[dict]] = {}
    for entry in entries:
        output_path = entry.get("output_path")
        if not output_path:
            continue
        groups.setdefault(Path(output_path).parent, []).append(entry)
    return groups


def _fetch_done_entries(history_store) -> list[dict]:
    return history_store.search(status="done", limit=COURSE_HISTORY_SCAN_LIMIT)


def list_courses(history_store) -> list[dict]:
    """Every folder that holds 2+ finished downloads, derived fresh from
    History (no stored "course" entity). A single-video folder isn't a course
    (indistinguishable from the existing single-video experience), so groups
    with fewer than 2 entries are dropped. `ready_count` is how many of the
    folder's lessons already have structured notes (summary_status == 'done').
    """
    groups = _group_done_entries_by_folder(_fetch_done_entries(history_store))
    courses: list[dict] = []
    for folder, folder_entries in groups.items():
        if len(folder_entries) < 2:
            continue
        ready_count = sum(
            1 for entry in folder_entries if entry.get("summary_status") == "done"
        )
        courses.append(
            {
                "folder": str(folder),
                "name": folder.name,
                "lesson_count": len(folder_entries),
                "ready_count": ready_count,
            }
        )
    return courses


def entries_for_course(history_store, folder: str) -> list[dict]:
    """The finished-download entries whose output folder is exactly `folder`.
    Reuses the same parent-folder grouping key as list_courses, so a folder
    string returned by list_courses round-trips back to its own entries. Path
    comparison normalizes separator/slash differences."""
    target = Path(folder)
    return [
        entry
        for entry in _fetch_done_entries(history_store)
        if entry.get("output_path") and Path(entry["output_path"]).parent == target
    ]


def _render_lesson_context_block(index: int, entry: dict) -> str:
    """One ready lesson rendered as a labeled context block for chat:

        Lesson <n> — "<title>":
        TL;DR: <tldr>
        Key points: [HH:MM:SS] <text>, [HH:MM:SS] <text>, ...

    Key-point `seconds` are converted back to HH:MM:SS via the shared
    format_timestamp helper. TL;DR / Key points lines are omitted when the
    parsed notes have none, so a sparse lesson still yields a clean block."""
    notes = parse_structured_notes(entry.get("summary") or "")
    lines = [f'Lesson {index} — "{_lesson_title(entry)}":']
    tldr = notes.get("tldr")
    if tldr:
        lines.append(f"TL;DR: {tldr}")
    key_points = notes.get("key_points") or []
    if key_points:
        rendered = ", ".join(
            f"[{format_timestamp(point['seconds'])}] {point['text']}" for point in key_points
        )
        lines.append(f"Key points: {rendered}")
    return "\n".join(lines)


def assemble_course_context(entries: list[dict]) -> str:
    """Concatenates every ready lesson's notes into one labeled context string
    (see _render_lesson_context_block), in stable oldest-first order. Entries
    that aren't summarized yet (summary_status != 'done') are simply excluded --
    they're "not ready", not an error. If the result exceeds
    MAX_COURSE_CONTEXT_CHARS, it's truncated to whole lessons and a visible note
    stating how many of the ready lessons actually made it is appended (never a
    silent cutoff)."""
    ready = [
        entry
        for entry in sort_course_entries(entries)
        if entry.get("summary_status") == "done"
    ]
    blocks = [_render_lesson_context_block(i, entry) for i, entry in enumerate(ready, start=1)]
    if not blocks:
        return ""

    full = "\n\n".join(blocks)
    if len(full) <= MAX_COURSE_CONTEXT_CHARS:
        return full

    # Over the cap: keep as many whole lesson blocks as fit (accounting for the
    # "\n\n" joiner between them), so the appended note's count is exact.
    included: list[str] = []
    running = 0
    for block in blocks:
        added = len(block) + (2 if included else 0)
        if running + added > MAX_COURSE_CONTEXT_CHARS:
            break
        included.append(block)
        running += added
    if not included:
        # Pathological: even one lesson's block exceeds the cap. Hard-truncate
        # it so we still return usable context rather than an empty string.
        included = [blocks[0][:MAX_COURSE_CONTEXT_CHARS]]
    note = f"\n\n[Only the first {len(included)} of {len(blocks)} lessons were included due to length.]"
    return "\n\n".join(included) + note


def build_course_chat_question(question: str, mode: str) -> str:
    """Wraps the user's question with mode-specific prompt framing so the same
    client.chat() call cites source lessons (chat) or returns a compact list of
    matching lessons (search) -- no new client method or response contract, just
    different framing of the trailing user turn. Assumes `mode` is already
    validated to "chat" or "search" by the route."""
    prefix = _SEARCH_MODE_PREFIX if mode == "search" else _CHAT_MODE_PREFIX
    return f"{prefix}{question}"


def _render_lesson_notes_markdown(index: int, entry: dict) -> list[str]:
    """One ready lesson's stored notes as Markdown lines for the study guide.
    A course-specific renderer (rather than importing export_notes.build_markdown,
    which also dumps the full transcript this digest deliberately omits) that
    matches build_markdown's Key Points / Chapters formatting conventions
    verbatim (`- **[HH:MM:SS]** text`)."""
    notes = parse_structured_notes(entry.get("summary") or "")
    lines = [f"## Lesson {index} — {_lesson_title(entry)}", ""]
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
    return lines


def build_study_guide(
    course_name: str,
    ready_entries: list[dict],
    unsummarized_entries: list[dict],
    overview: str,
) -> str:
    """Assembles the study-guide Markdown: the AI-generated Overview paragraph,
    one deterministic section per ready lesson (numbered/titled), and -- if any
    lessons aren't summarized yet -- a "Not yet summarized" list of their titles
    (graceful degradation, not a block). Only `overview` comes from the AI; every
    lesson section is mechanical from stored notes."""
    lines = [f"# {course_name} — Study Guide", "", "## Overview", "", overview, ""]
    for index, entry in enumerate(ready_entries, start=1):
        lines.extend(_render_lesson_notes_markdown(index, entry))
    if unsummarized_entries:
        lines.extend(["## Not yet summarized", ""])
        for entry in unsummarized_entries:
            lines.append(f"- {_lesson_title(entry)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_study_guide(folder: str, entries: list[dict], overview: str) -> Path:
    """Writes "<Course Folder Name> - Study Guide.md" into the course folder
    itself, reusing sanitize_filename from downloader.py for the name (never a
    client-supplied path). Splits entries into ready/unsummarized on the same
    stable ordering assemble_course_context uses, so lesson numbers line up with
    the chat context. Returns the written Path; may raise OSError, which the
    route turns into a 400."""
    ordered = sort_course_entries(entries)
    ready = [entry for entry in ordered if entry.get("summary_status") == "done"]
    unsummarized = [entry for entry in ordered if entry.get("summary_status") != "done"]

    folder_path = Path(folder)
    course_name = folder_path.name
    document = build_study_guide(course_name, ready, unsummarized, overview)
    target = folder_path / f"{sanitize_filename(course_name)} - Study Guide.md"
    target.write_text(document, encoding="utf-8")
    return target
