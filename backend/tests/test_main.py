import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import main as main_module
from license_store import LicenseStore


class _FakeSocket:
    def __init__(self):
        self.received = []

    async def send_json(self, message: dict) -> None:
        self.received.append(message)


def test_health_returns_ok():
    client = TestClient(main_module.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_headers_present_for_cross_origin_request():
    client = TestClient(main_module.app)
    response = client.get("/health", headers={"Origin": "http://example.com"})
    assert response.headers.get("access-control-allow-origin") == "*"


def test_completed_download_broadcasts_history_added_over_websocket():
    # This is what makes a finished download show up in the History tab
    # instantly instead of only on the next manual refresh/search.
    socket = _FakeSocket()
    main_module.connection_manager.active.append(socket)
    try:
        async def run():
            row = main_module.record_history_and_broadcast(
                entry_id="abc123",
                batch_id=None,
                url="https://example.com/video",
                title="Example Video",
                output_path="/tmp/example.mp4",
                total_size="12.3 MB",
                status="done",
                error_reason=None,
                retry_count=0,
            )
            await asyncio.sleep(0.01)  # let the scheduled broadcast task run
            return row

        row = asyncio.run(run())
    finally:
        main_module.connection_manager.active.remove(socket)

    assert row["url"] == "https://example.com/video"
    assert socket.received == [{"type": "history_added", "entry": row}]


def test_retried_entry_broadcast_redacts_carried_over_summary_for_free_user():
    # A retry re-records into the SAME row (update_id) without touching the
    # summary columns -- if that row already had a completed Pro-only
    # summary from a previous attempt, the history_added broadcast for the
    # retry must redact it the same way GET /history does.
    socket = _FakeSocket()
    main_module.connection_manager.active.append(socket)
    try:
        async def run():
            first = main_module.record_history_and_broadcast(
                entry_id="e1", batch_id=None, url="https://example.com/video",
                title="V", output_path="/tmp/v.mp4", total_size="1MB",
                status="done", error_reason=None, retry_count=0,
            )
            main_module.history_store.update_summary(
                first["id"], status="done",
                summary=json.dumps({
                    "tldr": "short",
                    "key_points": [{"seconds": 1, "text": "p"}],
                    "chapters": [{"seconds": 0, "title": "c"}],
                }),
            )
            socket.received.clear()  # only inspect the retry's own broadcast
            with patch.object(main_module, "license_store", SimpleNamespace(is_pro=lambda: False)):
                main_module.record_history_and_broadcast(
                    entry_id="e1", batch_id=None, url="https://example.com/video",
                    title="V", output_path="/tmp/v.mp4", total_size="1MB",
                    status="done", error_reason=None, retry_count=1,
                    update_id=first["id"],
                )
            await asyncio.sleep(0.01)

        asyncio.run(run())
    finally:
        main_module.connection_manager.active.remove(socket)

    broadcast_summary = json.loads(socket.received[-1]["entry"]["summary"])
    assert broadcast_summary["tldr"] == "short"
    assert broadcast_summary["key_points"] == []
    assert broadcast_summary["chapters"] == []


# -- Auto-transcribe-on-download hook -------------------------------------


class _FakeAutoOrchestrator:
    def __init__(self, request_ok=True):
        self._request_ok = request_ok
        self.calls = []

    def request_transcription(self, history_id):
        self.calls.append(("request", history_id))
        return self._request_ok

    def mark_transcription_running(self, history_id):
        self.calls.append(("mark", history_id))

    async def transcribe_and_maybe_summarize(self, history_id, summarize):
        self.calls.append(("transcribe", history_id, summarize))


def _record_with_fakes(fakes):
    async def run():
        with patch.multiple(main_module, **fakes):
            row = main_module.record_history_and_broadcast(
                entry_id="abc", batch_id=None, url="https://x/v", title="V",
                output_path="/tmp/v.mp4", total_size="1MB", status="done",
                error_reason=None, retry_count=0,
            )
            await asyncio.sleep(0.01)  # let the scheduled auto-transcribe task run
            return row

    return asyncio.run(run())


def _auto_fakes(*, auto_transcribe, auto_summarize=False, is_pro=True, orchestrator=None):
    row = {"id": 7, "status": "done"}
    orchestrator = orchestrator or _FakeAutoOrchestrator()
    return {
        "history_store": SimpleNamespace(record=lambda **kwargs: row),
        "settings_store": SimpleNamespace(
            get=lambda: {
                "auto_transcribe_on_download": auto_transcribe,
                "auto_summarize_after_transcribe": auto_summarize,
            }
        ),
        "license_store": SimpleNamespace(is_pro=lambda: is_pro),
        "transcription_orchestrator": orchestrator,
    }, orchestrator


def test_auto_transcribe_fires_when_pro_and_enabled():
    fakes, orch = _auto_fakes(auto_transcribe=True, auto_summarize=True)
    _record_with_fakes(fakes)
    assert ("transcribe", 7, True) in orch.calls


def test_auto_transcribe_does_not_fire_when_setting_off():
    fakes, orch = _auto_fakes(auto_transcribe=False)
    _record_with_fakes(fakes)
    assert all(call[0] != "transcribe" for call in orch.calls)


def test_auto_transcribe_does_not_fire_when_not_pro():
    fakes, orch = _auto_fakes(auto_transcribe=True, is_pro=False)
    _record_with_fakes(fakes)
    assert all(call[0] != "transcribe" for call in orch.calls)


def test_auto_transcribe_failure_never_breaks_download_recording():
    row = {"id": 9, "status": "done"}

    class _BoomSettings:
        def get(self):
            raise RuntimeError("settings blew up")

    fakes = {
        "history_store": SimpleNamespace(record=lambda **kwargs: row),
        "settings_store": _BoomSettings(),
        "license_store": SimpleNamespace(is_pro=lambda: True),
    }
    # The download row must still be recorded/returned even though the
    # auto-trigger raised while deciding whether to run.
    returned = _record_with_fakes(fakes)
    assert returned == row


# -- License startup revalidation ------------------------------------------


def test_revalidate_license_does_nothing_when_no_key_cached():
    store = LicenseStore()
    with patch.object(main_module, "verify_license") as mock_verify:
        asyncio.run(main_module._revalidate_license(store))
    mock_verify.assert_not_called()


def test_revalidate_license_sets_invalid_on_gumroad_rejection():
    store = LicenseStore()
    store.set_active(license_key="REAL-KEY-1234", purchase_email="a@b.com")
    with patch.object(main_module, "verify_license", return_value={"success": False}):
        asyncio.run(main_module._revalidate_license(store))
    assert store.is_pro() is False


def test_revalidate_license_keeps_active_on_gumroad_confirmation():
    store = LicenseStore()
    store.set_active(license_key="REAL-KEY-1234", purchase_email="a@b.com")
    fake = {"success": True, "purchase": {"refunded": False, "chargebacked": False, "disputed": False}}
    with patch.object(main_module, "verify_license", return_value=fake):
        asyncio.run(main_module._revalidate_license(store))
    assert store.is_pro() is True


def test_revalidate_license_dev_key_skips_gumroad_and_stays_active():
    # Regression test: the dev bypass must cover this startup path too, not
    # just the /license/activate route -- otherwise a license activated
    # locally via CLIP_PULL_DEV_LICENSE_KEY gets silently revoked on the
    # very next backend restart, since Gumroad has never heard of it.
    store = LicenseStore()
    store.set_active(license_key="DEV-TEST-0000", purchase_email="dev@local.test")
    with patch.object(main_module, "DEV_LICENSE_KEY", "DEV-TEST-0000"), \
         patch.object(main_module, "verify_license") as mock_verify:
        asyncio.run(main_module._revalidate_license(store))
    mock_verify.assert_not_called()
    assert store.is_pro() is True


def test_revalidate_license_non_matching_key_still_goes_through_gumroad_when_dev_key_set():
    store = LicenseStore()
    store.set_active(license_key="REAL-KEY-1234", purchase_email="a@b.com")
    with patch.object(main_module, "DEV_LICENSE_KEY", "DEV-TEST-0000"), \
         patch.object(main_module, "verify_license", return_value={"success": False}) as mock_verify:
        asyncio.run(main_module._revalidate_license(store))
    mock_verify.assert_called_once()
    assert store.is_pro() is False
