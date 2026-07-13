import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_clients import AIClientError
from courses import (
    MAX_COURSE_CONTEXT_CHARS,
    assemble_course_context,
    build_course_chat_question,
    build_study_guide,
    list_courses,
)
from history_store import HistoryStore
from license_store import LicenseStore
from settings_store import SettingsStore
from transcription import TranscriptionOrchestrator
from transcription_routes import (
    COURSE_NO_READY_MESSAGE,
    COURSE_NOT_A_COURSE_MESSAGE,
    build_transcription_router,
)
from usage_store import UsageStore


def _notes(tldr, key_points=(), chapters=()):
    """A structured-notes JSON string, exactly as the summary column stores it."""
    return json.dumps(
        {
            "tldr": tldr,
            "key_points": [{"seconds": s, "text": t} for s, t in key_points],
            "chapters": [{"seconds": s, "title": t} for s, t in chapters],
        }
    )


def _entry(entry_id, title, summary_status="none", summary=None, url="https://vimeo.com/x"):
    """A bare History-shaped dict for the pure context/digest functions (which
    take entry dicts directly, no DB needed)."""
    return {
        "id": entry_id,
        "title": title,
        "url": url,
        "summary_status": summary_status,
        "summary": summary,
    }


# -- list_courses ----------------------------------------------------------


def _seed_lesson(history_store, folder, name, ready=False, summary=None):
    entry = history_store.record(
        entry_id=None, batch_id=None, url=f"https://vimeo.com/{name}", title=name,
        output_path=str(Path(folder) / f"{name}.mp4"), total_size=None,
        status="done", error_reason=None, retry_count=0,
    )
    if ready:
        history_store.update_summary(
            entry["id"], status="done", summary=summary or _notes(f"tldr for {name}")
        )
    return history_store.get(entry["id"])


def test_list_courses_groups_by_folder_excludes_single_video_and_counts_ready():
    history_store = HistoryStore()
    # Alpha: 2 lessons, 1 ready.
    _seed_lesson(history_store, "C:/courses/Alpha", "a1", ready=True)
    _seed_lesson(history_store, "C:/courses/Alpha", "a2", ready=False)
    # Solo: a single video -> not a course.
    _seed_lesson(history_store, "C:/courses/Solo", "s1", ready=True)
    # Beta: 3 lessons, all ready.
    _seed_lesson(history_store, "C:/courses/Beta", "b1", ready=True)
    _seed_lesson(history_store, "C:/courses/Beta", "b2", ready=True)
    _seed_lesson(history_store, "C:/courses/Beta", "b3", ready=True)

    by_name = {c["name"]: c for c in list_courses(history_store)}

    assert set(by_name) == {"Alpha", "Beta"}
    assert by_name["Alpha"]["lesson_count"] == 2
    assert by_name["Alpha"]["ready_count"] == 1
    assert by_name["Beta"]["lesson_count"] == 3
    assert by_name["Beta"]["ready_count"] == 3
    assert Path(by_name["Alpha"]["folder"]).name == "Alpha"


def test_list_courses_skips_entries_without_output_path():
    history_store = HistoryStore()
    _seed_lesson(history_store, "C:/courses/Alpha", "a1", ready=True)
    _seed_lesson(history_store, "C:/courses/Alpha", "a2", ready=True)
    # An entry with no output_path can't be grouped and is ignored.
    history_store.record(
        entry_id=None, batch_id=None, url="https://vimeo.com/np", title="No Path",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )

    by_name = {c["name"]: c for c in list_courses(history_store)}
    assert set(by_name) == {"Alpha"}
    assert by_name["Alpha"]["lesson_count"] == 2


# -- assemble_course_context ----------------------------------------------


def test_assemble_course_context_labels_lessons_and_formats_timestamps():
    entries = [
        _entry(
            1, "Intro", summary_status="done",
            summary=_notes("Intro summary", key_points=[(125, "first"), (500, "second")]),
        ),
        _entry(
            2, "Advanced", summary_status="done",
            summary=_notes("Adv summary", key_points=[(60, "adv point")]),
        ),
    ]

    context = assemble_course_context(entries)

    assert 'Lesson 1 — "Intro":' in context
    assert "TL;DR: Intro summary" in context
    assert "Key points: [00:02:05] first, [00:08:20] second" in context
    assert 'Lesson 2 — "Advanced":' in context
    assert "Key points: [00:01:00] adv point" in context


def test_assemble_course_context_excludes_not_ready_entries():
    entries = [
        _entry(1, "Ready", summary_status="done", summary=_notes("ready tldr")),
        _entry(2, "Pending", summary_status="running", summary=None),
        _entry(3, "Errored", summary_status="error", summary=None),
    ]

    context = assemble_course_context(entries)

    # Only the ready lesson is present, and it's numbered 1 (not-ready lessons
    # don't consume a lesson number).
    assert 'Lesson 1 — "Ready":' in context
    assert "Pending" not in context
    assert "Errored" not in context


def test_assemble_course_context_falls_back_to_url_when_no_title():
    entries = [
        _entry(1, None, summary_status="done", summary=_notes("t1"), url="https://vimeo.com/42"),
        _entry(2, "Has Title", summary_status="done", summary=_notes("t2")),
    ]
    context = assemble_course_context(entries)
    assert 'Lesson 1 — "https://vimeo.com/42":' in context


def test_assemble_course_context_truncates_at_cap_with_accurate_note():
    big = "a" * 45000  # each lesson block ~45k chars; 3 of them exceed the 100k cap
    entries = [
        _entry(1, "T1", summary_status="done", summary=_notes(big)),
        _entry(2, "T2", summary_status="done", summary=_notes(big)),
        _entry(3, "T3", summary_status="done", summary=_notes(big)),
    ]

    context = assemble_course_context(entries)

    assert len(context) <= MAX_COURSE_CONTEXT_CHARS + 200  # cap + the short note
    assert 'Lesson 1 — "T1":' in context
    assert 'Lesson 2 — "T2":' in context
    assert "T3" not in context  # third lesson didn't fit
    assert "[Only the first 2 of 3 lessons were included due to length.]" in context


def test_assemble_course_context_empty_when_no_ready_lessons():
    entries = [_entry(1, "Pending", summary_status="none", summary=None)]
    assert assemble_course_context(entries) == ""


# -- build_course_chat_question -------------------------------------------


def test_build_course_chat_question_chat_mode_asks_to_cite_lesson():
    wrapped = build_course_chat_question("What is pricing?", "chat")
    assert "Cite which lesson" in wrapped
    assert wrapped.endswith("Question: What is pricing?")


def test_build_course_chat_question_search_mode_asks_for_compact_list():
    wrapped = build_course_chat_question("pricing", "search")
    assert "compact bulleted list" in wrapped
    assert wrapped.endswith("Query: pricing")


# -- build_study_guide (byte-correct deterministic sections) ---------------


def test_build_study_guide_is_byte_correct():
    ready = [
        _entry(
            1, "Intro", summary_status="done",
            summary=_notes(
                "Intro tldr",
                key_points=[(125, "first point"), (500, "second point")],
                chapters=[(0, "Start")],
            ),
        ),
        _entry(
            2, "Advanced", summary_status="done",
            summary=_notes("Adv tldr", key_points=[(60, "adv point")]),
        ),
    ]
    unsummarized = [_entry(3, "Bonus", summary_status="none", summary=None)]

    document = build_study_guide("My Course", ready, unsummarized, "OVERVIEW HERE")

    expected = (
        "# My Course — Study Guide\n"
        "\n"
        "## Overview\n"
        "\n"
        "OVERVIEW HERE\n"
        "\n"
        "## Lesson 1 — Intro\n"
        "\n"
        "Intro tldr\n"
        "\n"
        "### Key Points\n"
        "\n"
        "- **[00:02:05]** first point\n"
        "- **[00:08:20]** second point\n"
        "\n"
        "### Chapters\n"
        "\n"
        "- **[00:00:00]** Start\n"
        "\n"
        "## Lesson 2 — Advanced\n"
        "\n"
        "Adv tldr\n"
        "\n"
        "### Key Points\n"
        "\n"
        "- **[00:01:00]** adv point\n"
        "\n"
        "## Not yet summarized\n"
        "\n"
        "- Bonus\n"
    )
    assert document == expected
    assert "## Transcript" not in document  # notes only, never the transcript dump


# -- Route scaffolding -----------------------------------------------------


def _make_client(pro=False):
    history_store = HistoryStore()
    settings_store = SettingsStore()
    license_store = LicenseStore()
    if pro:
        license_store.set_active(license_key="TEST-PRO-KEY", purchase_email=None)
    orchestrator = TranscriptionOrchestrator(
        history_store, settings_store, UsageStore(), license_store
    )
    app = FastAPI()
    app.include_router(
        build_transcription_router(history_store, orchestrator, license_store, settings_store)
    )
    return TestClient(app), history_store, orchestrator


class _FakeChatClient:
    answer = "Lesson 1 covers pricing."
    received = None

    def __init__(self, api_key):
        self.api_key = api_key
        self.last_usage = {
            "provider": "anthropic",
            "model": "fake-model",
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "audio_seconds": None,
        }

    def chat(self, context_text, question, history=None):
        type(self).received = {
            "context_text": context_text,
            "question": question,
            "history": history,
        }
        return type(self).answer


class _FailingChatClient:
    def __init__(self, api_key):
        pass

    def chat(self, context_text, question, history=None):
        raise AIClientError("rate limited", provider="anthropic", status_code=429)


def _seed_course(history_store, folder, ready_count=2, total=2):
    """Seeds `total` lessons in `folder`; the first `ready_count` are summarized."""
    for i in range(total):
        _seed_lesson(
            history_store, folder, f"lesson{i + 1}",
            ready=i < ready_count,
            summary=_notes(f"tldr {i + 1}", key_points=[(125, f"point {i + 1}")]),
        )


def _wire_fake_client(orchestrator, answer="Lesson 1 covers pricing."):
    orchestrator.settings_store.update(anthropic_api_key="sk-ant")
    orchestrator.summarization_client_classes = {"anthropic": _FakeChatClient}
    _FakeChatClient.answer = answer
    _FakeChatClient.received = None


# -- GET /courses ----------------------------------------------------------


def test_get_courses_lists_multi_video_folders():
    client, history_store, _ = _make_client(pro=False)
    _seed_course(history_store, "C:/courses/Alpha", ready_count=1, total=2)
    _seed_lesson(history_store, "C:/courses/Solo", "s1", ready=True)  # single -> excluded

    response = client.get("/courses")

    assert response.status_code == 200
    names = {c["name"] for c in response.json()["courses"]}
    assert names == {"Alpha"}


# -- POST /courses/chat ----------------------------------------------------


def test_course_chat_returns_402_before_any_db_read_when_not_pro():
    client, history_store, _ = _make_client(pro=False)

    def _boom(*args, **kwargs):
        raise AssertionError("history was read before the Pro gate")

    history_store.search = _boom  # would blow up if the route read History first

    response = client.post(
        "/courses/chat", json={"folder": "C:/courses/Alpha", "question": "q?"}
    )

    assert response.status_code == 402
    assert "Pro" in response.json()["detail"]


def test_course_chat_returns_400_for_invalid_mode():
    client, _, _ = _make_client(pro=True)
    response = client.post(
        "/courses/chat", json={"folder": "C:/x", "question": "q?", "mode": "nope"}
    )
    assert response.status_code == 400
    assert "mode" in response.json()["detail"]


def test_course_chat_returns_400_when_fewer_than_two_lessons():
    client, history_store, _ = _make_client(pro=True)
    _seed_lesson(history_store, "C:/courses/Alpha", "only", ready=True)

    response = client.post(
        "/courses/chat", json={"folder": "C:/courses/Alpha", "question": "q?"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == COURSE_NOT_A_COURSE_MESSAGE


def test_course_chat_returns_400_when_no_ready_lessons():
    client, history_store, _ = _make_client(pro=True)
    _seed_course(history_store, "C:/courses/Alpha", ready_count=0, total=2)

    response = client.post(
        "/courses/chat", json={"folder": "C:/courses/Alpha", "question": "q?"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == COURSE_NO_READY_MESSAGE


def test_course_chat_returns_400_when_no_api_key():
    client, history_store, _ = _make_client(pro=True)
    _seed_course(history_store, "C:/courses/Alpha", ready_count=2, total=2)
    # summarization_provider defaults to anthropic; no anthropic_api_key set.

    response = client.post(
        "/courses/chat", json={"folder": "C:/courses/Alpha", "question": "q?"}
    )

    assert response.status_code == 400
    assert "Anthropic API key" in response.json()["detail"]


def test_course_chat_chat_mode_returns_answer_and_cites_instruction():
    client, history_store, orchestrator = _make_client(pro=True)
    _seed_course(history_store, "C:/courses/Alpha", ready_count=2, total=2)
    _wire_fake_client(orchestrator, answer="Pricing is in Lesson 1.")

    response = client.post(
        "/courses/chat",
        json={"folder": "C:/courses/Alpha", "question": "where is pricing?", "mode": "chat"},
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "Pricing is in Lesson 1."}
    received = _FakeChatClient.received
    assert 'Lesson 1 — "lesson1":' in received["context_text"]
    assert "Cite which lesson" in received["question"]
    assert received["question"].endswith("Question: where is pricing?")


def test_course_chat_search_mode_reframes_question():
    client, history_store, orchestrator = _make_client(pro=True)
    _seed_course(history_store, "C:/courses/Alpha", ready_count=2, total=2)
    _wire_fake_client(orchestrator, answer="- lesson1")

    response = client.post(
        "/courses/chat",
        json={"folder": "C:/courses/Alpha", "question": "pricing", "mode": "search"},
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "- lesson1"}
    assert "compact bulleted list" in _FakeChatClient.received["question"]


def test_course_chat_records_usage_with_null_history_id():
    client, history_store, orchestrator = _make_client(pro=True)
    _seed_course(history_store, "C:/courses/Alpha", ready_count=2, total=2)
    _wire_fake_client(orchestrator)

    client.post("/courses/chat", json={"folder": "C:/courses/Alpha", "question": "q?"})

    rows = orchestrator.usage_store._conn.execute(
        "SELECT operation, history_id FROM ai_usage"
    ).fetchall()
    assert any(r["operation"] == "course_chat" and r["history_id"] is None for r in rows)


def test_course_chat_caps_history_to_last_20_turns():
    client, history_store, orchestrator = _make_client(pro=True)
    _seed_course(history_store, "C:/courses/Alpha", ready_count=2, total=2)
    _wire_fake_client(orchestrator)
    long_history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(30)
    ]

    response = client.post(
        "/courses/chat",
        json={"folder": "C:/courses/Alpha", "question": "q?", "history": long_history},
    )

    assert response.status_code == 200
    assert len(_FakeChatClient.received["history"]) == 20
    assert _FakeChatClient.received["history"][0] == {"role": "user", "content": "m10"}


def test_course_chat_returns_502_on_ai_client_error():
    client, history_store, orchestrator = _make_client(pro=True)
    _seed_course(history_store, "C:/courses/Alpha", ready_count=2, total=2)
    orchestrator.settings_store.update(anthropic_api_key="sk-ant")
    orchestrator.summarization_client_classes = {"anthropic": _FailingChatClient}

    response = client.post(
        "/courses/chat", json={"folder": "C:/courses/Alpha", "question": "q?"}
    )

    assert response.status_code == 502
    assert "rate-limited" in response.json()["detail"]


# -- POST /courses/digest --------------------------------------------------


def test_course_digest_returns_402_when_not_pro(tmp_path):
    client, history_store, _ = _make_client(pro=False)
    course = tmp_path / "My Course"
    course.mkdir()
    _seed_course(history_store, str(course), ready_count=2, total=2)

    response = client.post("/courses/digest", json={"folder": str(course)})

    assert response.status_code == 402
    assert "Pro" in response.json()["detail"]


def test_course_digest_returns_400_when_fewer_than_two_lessons(tmp_path):
    client, history_store, _ = _make_client(pro=True)
    course = tmp_path / "My Course"
    course.mkdir()
    _seed_lesson(history_store, str(course), "only", ready=True)

    response = client.post("/courses/digest", json={"folder": str(course)})

    assert response.status_code == 400
    assert response.json()["detail"] == COURSE_NOT_A_COURSE_MESSAGE


def test_course_digest_returns_400_when_no_ready_lessons(tmp_path):
    client, history_store, _ = _make_client(pro=True)
    course = tmp_path / "My Course"
    course.mkdir()
    _seed_course(history_store, str(course), ready_count=0, total=2)

    response = client.post("/courses/digest", json={"folder": str(course)})

    assert response.status_code == 400
    assert response.json()["detail"] == COURSE_NO_READY_MESSAGE


def test_course_digest_returns_400_when_no_api_key(tmp_path):
    client, history_store, _ = _make_client(pro=True)
    course = tmp_path / "My Course"
    course.mkdir()
    _seed_course(history_store, str(course), ready_count=2, total=2)

    response = client.post("/courses/digest", json={"folder": str(course)})

    assert response.status_code == 400
    assert "Anthropic API key" in response.json()["detail"]


def test_course_digest_writes_file_and_records_usage(tmp_path):
    client, history_store, orchestrator = _make_client(pro=True)
    course = tmp_path / "My Course"
    course.mkdir()
    # Two ready lessons + one unsummarized -> graceful "Not yet summarized".
    _seed_course(history_store, str(course), ready_count=2, total=3)
    _wire_fake_client(orchestrator, answer="A thematic overview paragraph.")

    response = client.post("/courses/digest", json={"folder": str(course)})

    assert response.status_code == 200
    expected_path = course / "My Course - Study Guide.md"
    assert response.json()["path"] == str(expected_path)
    assert expected_path.exists()

    content = expected_path.read_text(encoding="utf-8")
    assert content.startswith("# My Course — Study Guide\n")
    assert "## Overview\n\nA thematic overview paragraph.\n" in content
    assert "## Lesson 1 — lesson1" in content
    assert "## Lesson 2 — lesson2" in content
    assert "- **[00:02:05]** point 1" in content
    assert "## Not yet summarized\n\n- lesson3\n" in content
    assert "## Transcript" not in content  # notes only, never the transcript

    # The overview call is recorded under course_digest with a null history_id.
    rows = orchestrator.usage_store._conn.execute(
        "SELECT operation, history_id FROM ai_usage"
    ).fetchall()
    assert any(r["operation"] == "course_digest" and r["history_id"] is None for r in rows)


def test_course_digest_returns_502_on_ai_client_error(tmp_path):
    client, history_store, orchestrator = _make_client(pro=True)
    course = tmp_path / "My Course"
    course.mkdir()
    _seed_course(history_store, str(course), ready_count=2, total=2)
    orchestrator.settings_store.update(anthropic_api_key="sk-ant")
    orchestrator.summarization_client_classes = {"anthropic": _FailingChatClient}

    response = client.post("/courses/digest", json={"folder": str(course)})

    assert response.status_code == 502
    assert "rate-limited" in response.json()["detail"]
    # A failed AI call must not leave a stray study-guide file behind.
    assert not (course / "My Course - Study Guide.md").exists()
