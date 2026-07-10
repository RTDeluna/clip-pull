# Multi-Stream Download Progress Accuracy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Queue tab's progress bar and History's recorded file size so a multi-stream (video+audio, merged by ffmpeg) download never appears to shrink or under-report, and add an inline label ("Downloading video…" / "Downloading audio…" / "Merging video + audio…") explaining the stream transitions to the user.

**Architecture:** `backend/downloader.py`'s `progress_hook` already tracks `prior_streams_bytes` to fold completed streams' sizes into a running total, but only uses it when an upfront size probe succeeds. This plan unifies that into one cumulative computation used in both cases, adds a `stage` field derived from yt-dlp's per-format `info_dict` (plus a new `postprocessor_hooks` registration for the ffmpeg merge step) that flows through `QueueManager` to the frontend the same way `percent`/`speed`/`eta` already do, and renders it as a small subtitle in `renderer.js`.

**Tech Stack:** Python 3 / FastAPI / yt-dlp (backend), vanilla JS + WebSocket (frontend), pytest.

## Global Constraints

- Backend tests run from the `backend/` directory: `cd backend && python -m pytest tests/<file> -q`.
- No frontend automated test framework exists in this repo (no `test` script in `package.json`, no JS test runner installed) — frontend changes are verified manually via `npm start`, not automated tests.
- Match existing code style exactly: explicit `Optional[...]`-typed parameters (no `**kwargs`), comments only where they explain non-obvious *why* (existing files are full of these — follow the pattern), `nonlocal` closures for `progress_hook`'s per-download state.
- Spec: `docs/superpowers/specs/2026-07-10-multistream-progress-accuracy-design.md` — this plan implements it exactly; do not deviate from its cumulative-tracking formula or the `stage` value set (`"video"` / `"audio"` / `"merging"` / `None`).

---

### Task 1: `stage` field on `QueueEntry` / `QueueManager`

**Files:**
- Modify: `backend/queue_manager.py`
- Test: `backend/tests/test_queue_manager.py`

**Interfaces:**
- Produces: `QueueEntry.stage: Optional[str]` (default `None`), included in `to_dict()`. `QueueManager.set_stage(entry_id: str, stage: Optional[str]) -> None` — new method, same shape as existing `set_title`/`set_status`. `set_error`, `mark_paused`, `reset_for_retry` all clear `entry.stage = None` as part of their existing behavior.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_queue_manager.py`, after `test_get_returns_entry_by_id` (so it sits with the other basic-entry-shape tests):

```python
def test_new_entry_defaults_stage_to_none():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    assert entry.stage is None


def test_set_stage_updates_entry_stage():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_stage(entry.id, "video")
    assert manager.get(entry.id).stage == "video"


def test_to_dict_includes_stage_field():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_stage(entry.id, "audio")
    assert manager.get(entry.id).to_dict()["stage"] == "audio"
```

Modify the existing `test_set_error_sets_status_and_reason` to also verify it clears `stage`:

```python
def test_set_error_sets_status_and_reason():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_stage(entry.id, "video")
    manager.set_error(entry.id, "Blocked — referer required")
    updated = manager.get(entry.id)
    assert updated.status == "error"
    assert updated.error_reason == "Blocked — referer required"
    assert updated.stage is None
```

Modify the existing `test_mark_paused_sets_status_and_clears_speed_eta_but_keeps_progress` to also set and verify `stage` is cleared:

```python
def test_mark_paused_sets_status_and_clears_speed_eta_but_keeps_progress():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.update_progress(entry.id, 42.0, "1MiB/s", 30, "42MB", "100MB", 1048576.0)
    manager.set_stage(entry.id, "audio")
    manager.mark_paused(entry.id)
    updated = manager.get(entry.id)
    assert updated.status == "paused"
    assert updated.speed is None
    assert updated.speed_bytes is None
    assert updated.eta is None
    assert updated.percent == 42.0
    assert updated.downloaded_size == "42MB"
    assert updated.total_size == "100MB"
    assert updated.stage is None
```

Modify the existing `test_reset_for_retry_clears_progress_and_increments_retry_count` the same way:

```python
def test_reset_for_retry_clears_progress_and_increments_retry_count():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.update_progress(entry.id, 50.0, "1MiB/s", 10, "50MB", "100MB", 1048576.0)
    manager.set_stage(entry.id, "video")
    manager.set_error(entry.id, "some error")
    manager.reset_for_retry(entry.id)
    updated = manager.get(entry.id)
    assert updated.status == "queued"
    assert updated.percent == 0.0
    assert updated.speed is None
    assert updated.speed_bytes is None
    assert updated.eta is None
    assert updated.downloaded_size is None
    assert updated.total_size is None
    assert updated.error_reason is None
    assert updated.stage is None
    assert updated.retry_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_queue_manager.py -q`
Expected: FAIL — `AttributeError: 'QueueEntry' object has no attribute 'stage'` (and `AttributeError: 'QueueManager' object has no attribute 'set_stage'` for the dedicated test).

- [ ] **Step 3: Implement**

In `backend/queue_manager.py`, add the `stage` field to `QueueEntry` right after `total_size`:

```python
    downloaded_size: Optional[str] = None
    total_size: Optional[str] = None
    stage: Optional[str] = None
    error_reason: Optional[str] = None
```

Add it to `to_dict()` right after `"total_size"`:

```python
            "downloaded_size": self.downloaded_size,
            "total_size": self.total_size,
            "stage": self.stage,
            "error_reason": self.error_reason,
```

Add a new `set_stage` method right after `update_progress`:

```python
    def set_stage(self, entry_id: str, stage: Optional[str]) -> None:
        entry = self._entries[entry_id]
        entry.stage = stage
        self._notify(entry)
```

Update `set_error` to clear `stage`:

```python
    def set_error(self, entry_id: str, reason: str) -> None:
        entry = self._entries[entry_id]
        entry.status = "error"
        entry.error_reason = reason
        entry.stage = None
        self._notify(entry)
```

Update `mark_paused` to clear `stage`:

```python
    def mark_paused(self, entry_id: str) -> None:
        """Sets status to "paused" without touching percent/downloaded/total
        size — unlike reset_for_retry, a paused download should resume from
        where it left off, not restart at 0%."""
        entry = self._entries[entry_id]
        entry.status = "paused"
        entry.speed = None
        entry.speed_bytes = None
        entry.eta = None
        entry.stage = None
        self._notify(entry)
```

Update `reset_for_retry` to clear `stage`:

```python
    def reset_for_retry(self, entry_id: str) -> None:
        entry = self._entries[entry_id]
        entry.status = "queued"
        entry.percent = 0.0
        entry.speed = None
        entry.speed_bytes = None
        entry.eta = None
        entry.downloaded_size = None
        entry.total_size = None
        entry.stage = None
        entry.error_reason = None
        entry.retry_count += 1
        self._notify(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_queue_manager.py -q`
Expected: PASS — all tests including the 3 new ones and 3 modified ones.

- [ ] **Step 5: Commit**

```bash
git add backend/queue_manager.py backend/tests/test_queue_manager.py
git commit -m "feat: add stage field to QueueEntry for multi-stream download labeling"
```

---

### Task 2: Cumulative size/percent tracking + `stage` detection in `downloader.py`

**Files:**
- Modify: `backend/downloader.py`
- Test: `backend/tests/test_downloader.py`

**Interfaces:**
- Consumes: `QueueManager.set_stage(entry_id, stage)` from Task 1.
- Produces: `stream_stage(info_dict: dict) -> Optional[str]` — new module-level function, returns `"video"`, `"audio"`, or `None`. `progress_hook`'s cumulative `downloaded_size`/`total_size`/`percent` computation (no new public interface — internal to `download_entry`). `build_ydl_opts(...)` now also registers `progress_hook` under `"postprocessor_hooks"` (no signature change).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_downloader.py`, add `stream_stage` to the import block (it's alphabetically after `sanitize_filename`):

```python
from downloader import (
    CONCURRENT_FRAGMENT_DOWNLOADS,
    REFERER_BLOCKED_MESSAGE,
    DownloadOrchestrator,
    build_ydl_opts,
    check_aria2c_available,
    check_ffmpeg_available,
    format_bytes,
    format_speed,
    humanize_error_reason,
    is_referer_blocked_error,
    probe_total_bytes,
    resolve_output_folder,
    resolve_use_aria2c,
    sanitize_filename,
    stream_stage,
)
```

Add these new tests anywhere after the `format_bytes` tests (e.g. right after `test_format_bytes_returns_none_for_missing_value`):

```python
def test_stream_stage_returns_video_for_video_only_format():
    assert stream_stage({"vcodec": "avc1", "acodec": "none"}) == "video"


def test_stream_stage_returns_audio_for_audio_only_format():
    assert stream_stage({"vcodec": "none", "acodec": "mp4a"}) == "audio"


def test_stream_stage_returns_none_for_progressive_format():
    assert stream_stage({"vcodec": "avc1", "acodec": "mp4a"}) is None


def test_stream_stage_returns_none_when_codecs_missing():
    assert stream_stage({}) is None
```

Add this test right after `test_build_ydl_opts_omits_referer_header_when_not_provided`:

```python
def test_build_ydl_opts_registers_progress_hook_as_postprocessor_hook_too():
    def hook(d):
        pass

    opts = build_ydl_opts("out/%(title)s.%(ext)s", None, hook)
    assert opts["postprocessor_hooks"] == [hook]
```

Replace the existing `test_progress_hook_falls_back_to_per_stream_percent_when_probe_unavailable` test (the one whose docstring/behavior this plan changes) with:

```python
def test_progress_hook_tracks_cumulative_percent_and_size_across_streams_when_probe_unavailable():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    calls = []
    original_update_progress = manager.update_progress

    def capturing_update_progress(
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None, speed_bytes=None
    ):
        calls.append((percent, downloaded_size, total_size))
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size, speed_bytes
        )

    manager.update_progress = capturing_update_progress

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        # Video stream completes at 800 of 800.
        progress_hook({"status": "downloading", "downloaded_bytes": 800, "total_bytes": 800})
        time.sleep(0.26)  # clear the throttle window deterministically
        # Audio stream starts — its own total (200) is much smaller than the
        # video stream's. No probe_fn is injected, so there's no upfront
        # grand total; this exercises the fallback path.
        progress_hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 200})
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    hook_calls = [c for c in calls if c[1] is not None]
    # Both downloaded and total climb across the stream transition — neither
    # ever drops, unlike the old per-stream math (which reported 50.0% of
    # "200B" total right after reporting 100.0% of "800B").
    assert hook_calls == [
        (100.0, "800B", "800B"),
        (90.0, "900B", "1000B"),
    ]
```

Add this new test right after it, verifying the exact History bug from the bug report:

```python
def test_download_entry_records_correct_combined_size_in_history_across_streams():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    recorded = []

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        # Video stream completes, then a much-smaller audio stream starts —
        # no probe_fn, so History must still record the combined size, not
        # just the last (audio) stream's own total.
        progress_hook({"status": "downloading", "downloaded_bytes": 800, "total_bytes": 800})
        time.sleep(0.26)
        progress_hook({"status": "downloading", "downloaded_bytes": 200, "total_bytes": 200})
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(
        manager,
        download_fn=fake_download,
        record_history=lambda **kwargs: recorded.append(kwargs),
    )
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    assert recorded[0]["total_size"] == "1000B"
```

Add this new test right after `test_progress_hook_falls_back_when_probe_fn_returns_none`, verifying stage detection across a real transition:

Both tests below capture `stage` via a monkey-patched `set_stage` and assert only *after* `asyncio.run(...)` returns — not synchronously inside `fake_download`. `fake_download` runs in a worker thread via `run_in_executor`, and `progress_hook` schedules `set_stage` with `loop.call_soon_threadsafe`, which only *schedules* the call on the event loop with no guarantee it's run before the next line in the worker thread executes — asserting on `manager.get(entry.id).stage` from inside `fake_download` would be a race condition. This mirrors the existing `capturing_update_progress` pattern used throughout this file.

```python
def test_progress_hook_reports_video_then_audio_stage_across_stream_transition():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    stages = []
    original_set_stage = manager.set_stage

    def capturing_set_stage(entry_id, stage):
        stages.append(stage)
        original_set_stage(entry_id, stage)

    manager.set_stage = capturing_set_stage

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook({
            "status": "downloading",
            "downloaded_bytes": 400,
            "total_bytes": 800,
            "info_dict": {"vcodec": "avc1", "acodec": "none"},
        })
        time.sleep(0.26)  # clear the throttle window deterministically
        progress_hook({
            "status": "downloading",
            "downloaded_bytes": 100,
            "total_bytes": 200,
            "info_dict": {"vcodec": "none", "acodec": "mp4a"},
        })
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    # video -> audio -> cleared on completion.
    assert stages == ["video", "audio", None]


def test_progress_hook_reports_merging_stage_on_postprocessor_start():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    stages = []
    original_set_stage = manager.set_stage

    def capturing_set_stage(entry_id, stage):
        stages.append(stage)
        original_set_stage(entry_id, stage)

    manager.set_stage = capturing_set_stage

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
        progress_hook({"status": "started", "postprocessor": "Merger"})
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    assert stages == ["merging", None]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_downloader.py -q`
Expected: FAIL — `ImportError: cannot import name 'stream_stage'` (collection error blocks the whole file, which is expected at this point).

- [ ] **Step 3: Implement**

In `backend/downloader.py`, add `stream_stage` right after `format_bytes` (before `is_referer_blocked_error`):

```python
def stream_stage(info_dict: dict) -> Optional[str]:
    """Labels which half of a split video+audio download is currently in
    progress, from yt-dlp's per-format info_dict passed into progress_hook.
    None for an ordinary single-stream (progressive) format, where there's
    nothing to label."""
    vcodec = info_dict.get("vcodec")
    acodec = info_dict.get("acodec")
    has_video = bool(vcodec) and vcodec != "none"
    has_audio = bool(acodec) and acodec != "none"
    if has_video and not has_audio:
        return "video"
    if has_audio and not has_video:
        return "audio"
    return None
```

In `build_ydl_opts`, register the same hook for the ffmpeg merge step too:

```python
    opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": output_template,
        "concurrent_fragment_downloads": concurrent_fragment_downloads,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
    }
```

In `download_entry`, add `last_stage` alongside the other per-download state (right after `last_stream_total: Optional[int] = None`):

```python
            prior_streams_bytes = 0
            last_stream_total: Optional[int] = None
            last_stage: Optional[str] = None
```

Replace the entire `progress_hook` function body with:

```python
            def progress_hook(d: dict) -> None:
                nonlocal last_progress_time, smoothed_speed, prior_streams_bytes, last_stream_total, last_stage
                if entry_id in self._pause_requested:
                    raise DownloadPaused()

                # Registered as both a progress_hooks and postprocessor_hooks
                # callback (see build_ydl_opts) — this branch handles the
                # latter, which fires with a completely different dict shape
                # (no downloaded_bytes/total_bytes) when ffmpeg starts
                # merging the finished video+audio streams.
                if d.get("postprocessor") == "Merger" and d.get("status") == "started":
                    if last_stage != "merging":
                        last_stage = "merging"
                        loop.call_soon_threadsafe(self.queue_manager.set_stage, entry_id, "merging")
                    return

                if d.get("status") != "downloading":
                    return
                now = time.monotonic()
                if now - last_progress_time < PROGRESS_THROTTLE_SECONDS:
                    return
                last_progress_time = now
                downloaded = d.get("downloaded_bytes")
                total = d.get("total_bytes") or d.get("total_bytes_estimate")

                # A new total_bytes value (that isn't just the first reading)
                # means yt-dlp has moved on to the next stream — fold the
                # previous stream's full size into the running total so
                # overall progress keeps climbing instead of resetting.
                if total is not None and total != last_stream_total:
                    if last_stream_total is not None:
                        prior_streams_bytes += last_stream_total
                    last_stream_total = total

                # downloaded/total are always folded onto prior_streams_bytes,
                # whether or not the upfront probe knew the true grand total —
                # this keeps both numbers climbing across a stream transition
                # instead of snapping down to just the new (much smaller)
                # stream's own numbers, which used to read as a "reset."
                overall_downloaded = (
                    prior_streams_bytes + downloaded if downloaded is not None else None
                )
                if expected_total_bytes:
                    overall_total = expected_total_bytes
                elif total is not None:
                    overall_total = prior_streams_bytes + total
                else:
                    overall_total = None

                if overall_downloaded is not None and overall_total:
                    percent = round(min(overall_downloaded / overall_total, 1.0) * 100, 1)
                else:
                    percent = 0.0
                downloaded_size = format_bytes(overall_downloaded)
                total_size = format_bytes(overall_total)

                stage = stream_stage(d.get("info_dict") or {})
                if stage != last_stage:
                    last_stage = stage
                    loop.call_soon_threadsafe(self.queue_manager.set_stage, entry_id, stage)

                # yt-dlp's raw per-chunk speed swings wildly between calls
                # (fragment boundaries, brief network hiccups) even when the
                # real throughput is steady — an exponential moving average
                # reads as fast and stable instead of erratic, matching how
                # browsers/other download managers smooth their speed readout.
                raw_speed = d.get("speed")
                if raw_speed:
                    smoothed_speed = (
                        raw_speed if smoothed_speed is None else 0.3 * raw_speed + 0.7 * smoothed_speed
                    )
                speed = format_speed(smoothed_speed)
                eta_raw = d.get("eta")
                eta = int(eta_raw) if eta_raw is not None else None
                loop.call_soon_threadsafe(
                    self.queue_manager.update_progress,
                    entry_id, percent, speed, eta, downloaded_size, total_size, smoothed_speed,
                )
```

In the success path (right after `if title: self.queue_manager.set_title(entry_id, title)`), clear the stage:

```python
                if title:
                    self.queue_manager.set_title(entry_id, title)
                self.queue_manager.set_stage(entry_id, None)
                self.queue_manager.update_progress(entry_id, 100.0, None, 0)
                self.queue_manager.set_status(entry_id, "done")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_downloader.py -q`
Expected: PASS — all tests, including the new ones. Then run the full backend suite to confirm no regressions elsewhere:

Run: `cd backend && python -m pytest -q`
Expected: PASS — all tests across every file.

- [ ] **Step 5: Commit**

```bash
git add backend/downloader.py backend/tests/test_downloader.py
git commit -m "fix: track cumulative size/percent across multi-stream downloads, add stage detection"
```

---

### Task 3: Inline stage label in the Queue tab

**Files:**
- Modify: `frontend/renderer.js`
- Modify: `frontend/styles.css`

**Interfaces:**
- Consumes: `entry.stage` (`"video"` / `"audio"` / `"merging"` / `null`, from Task 1/2's WebSocket payload) and `entry.status`.

- [ ] **Step 1: Add the stage label element to the row template**

In `frontend/renderer.js`, inside `renderRow`, the row's `innerHTML` currently is:

```js
    el.innerHTML = `
      <div class="queue-row__top">
        <span class="queue-row__title"></span>
        <span class="queue-row__duplicate-badge" hidden>Already downloaded</span>
        <span class="queue-row__status"></span>
      </div>
      <div class="progress-track"><div class="progress-fill"></div></div>
      <div class="queue-row__size"></div>
      <div class="queue-row__meta">
        <span class="queue-row__speed"></span>
        <span class="queue-row__eta" title="Estimated time remaining"></span>
      </div>
      <div class="queue-row__error"></div>
      <button class="pause-btn" type="button" hidden>Pause</button>
      <button class="resume-btn" type="button" hidden>Resume</button>
      <button class="retry-btn" type="button" hidden>Retry</button>
    `;
```

Add a `queue-row__stage` line right after `queue-row__top`'s closing `</div>`:

```js
    el.innerHTML = `
      <div class="queue-row__top">
        <span class="queue-row__title"></span>
        <span class="queue-row__duplicate-badge" hidden>Already downloaded</span>
        <span class="queue-row__status"></span>
      </div>
      <div class="queue-row__stage" hidden></div>
      <div class="progress-track"><div class="progress-fill"></div></div>
      <div class="queue-row__size"></div>
      <div class="queue-row__meta">
        <span class="queue-row__speed"></span>
        <span class="queue-row__eta" title="Estimated time remaining"></span>
      </div>
      <div class="queue-row__error"></div>
      <button class="pause-btn" type="button" hidden>Pause</button>
      <button class="resume-btn" type="button" hidden>Resume</button>
      <button class="retry-btn" type="button" hidden>Retry</button>
    `;
```

- [ ] **Step 2: Render the label from `entry.stage`**

Near the top of `renderer.js`, right after the existing `statusLabel` function, add:

```js
const STAGE_LABELS = {
  video: "Downloading video…",
  audio: "Downloading audio…",
  merging: "Merging video + audio…",
};

function stageLabel(entry) {
  if (entry.status !== "downloading") return null;
  return STAGE_LABELS[entry.stage] || null;
}
```

In `renderRow`, right after the existing block that sets `.queue-row__status` (the block ending with `if (entry.status === "error") statusEl.classList.add(...)`), add:

```js
  const stageEl = row.querySelector(".queue-row__stage");
  const stage = stageLabel(entry);
  stageEl.textContent = stage || "";
  stageEl.hidden = !stage;
```

- [ ] **Step 3: Style the label**

In `frontend/styles.css`, add right after the `.queue-row__title` rule (matching `.queue-row__size`'s existing font conventions):

```css
.queue-row__stage {
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
}
```

- [ ] **Step 4: Manually verify**

There's no automated frontend test runner in this repo — verify by hand:

1. Run `npm start` (dev mode).
2. Paste a URL for a long video likely to require the video+audio fallback path (any lengthy Vimeo/Loom lesson without an advertised `filesize` works — HLS-sourced videos are the common case), choose an output folder, and start the download.
3. Watch the Queue row: confirm a small "Downloading video…" line appears under the title while the video stream downloads, switches to "Downloading audio…" when the audio stream starts (with the size/percent numbers continuing to climb, never dropping), and briefly shows "Merging video + audio…" right before the row completes.
4. Confirm the label disappears once the row shows "Done", and that ordinary single-stream downloads (if you have a source that doesn't split streams) show no label at all.
5. Switch to the History tab and confirm the recorded file size matches the actual file size on disk (right-click the file → Properties, compare to what History shows).

- [ ] **Step 5: Commit**

```bash
git add frontend/renderer.js frontend/styles.css
git commit -m "feat: show inline video/audio/merging stage label on downloading queue rows"
```

---

## Self-Review Notes

- **Spec coverage:** Cumulative size/percent (spec §1) → Task 2 Step 3. `stage` field + derivation + postprocessor hook (spec §2) → Tasks 1 & 2. Inline label (spec §3) → Task 3. History accuracy (spec's "falls out naturally") → covered explicitly by the new `test_download_entry_records_correct_combined_size_in_history_across_streams` test in Task 2, proving it rather than assuming it.
- **Blast radius check:** Of the ~25 existing tests in `test_downloader.py` that monkey-patch `manager.update_progress`, only one (`test_progress_hook_falls_back_to_per_stream_percent_when_probe_unavailable`) actually needed its assertions changed — `update_progress`'s signature is untouched throughout this plan (stage flows through the separate `set_stage` method instead), so no other existing wrapper/test needed modification. Verified by hand-tracing each affected test's fixture data against the new formula before writing this plan.
- **Race condition caught during review:** the first draft of the two new stage-detection tests asserted `manager.get(entry.id).stage` synchronously inside `fake_download` (which runs in a worker thread) right after calling `progress_hook`, which schedules `set_stage` via `loop.call_soon_threadsafe` — a scheduling call with no ordering guarantee relative to the worker thread's next line. Rewritten to capture via a monkey-patched `set_stage` and assert only after `asyncio.run(...)` returns, matching the file's existing safe pattern.
