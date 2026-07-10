# Multi-Stream Download Progress Accuracy — Design

## Purpose

High-quality Vimeo/Loom downloads come down as two separate yt-dlp streams — video-only, then audio-only — merged with ffmpeg at the end. When the upfront size lookahead (`probe_total_bytes`) can't determine both streams' sizes in advance (common for HLS/DASH sources), the app currently reports each stream's progress independently. This causes two user-visible problems:

1. **Live progress bar looks broken.** The percent bar is already clamped client-side so it can't visually go backward, but the "downloaded / total" size text isn't — so it can jump from e.g. "480MB / 500MB · 96%" down to "2MB / 18MB · 96%" the moment the audio stream starts. Percent and size disagree, reading as a bug.
2. **History records the wrong file size.** `download_entry` records whatever `total_size` was last set on the queue entry before completion — in the fallback case, that's the *last stream's* total (the small audio track), not the combined file size. A 674MB combined download can get recorded as 188MB.

Confirmed root cause via `backend/downloader.py`: `progress_hook`'s fallback branch (used whenever `expected_total_bytes` from `probe_total_bytes` is `None`) computes `percent`/`downloaded_size`/`total_size` from the *current* stream's own `downloaded`/`total` only, discarding what came before.

## Goals

- Downloaded/total size (live and in History) never appears to shrink or under-report across a multi-stream download, with or without a successful upfront probe.
- The user gets an in-UI explanation of why a download briefly shows "video" vs "audio" behavior, so it doesn't read as broken.
- No regression to the existing accurate-probe path (when `probe_total_bytes` succeeds, behavior is unchanged — it's already correct).

## Non-goals

- Not touching `probe_total_bytes` itself or its success rate — this design makes the *fallback* path correct rather than trying to make the probe succeed more often.
- Not adding a settings toggle or configurability for this — it's a correctness fix, always on.
- Not surfacing byte-level ffmpeg merge progress (percent-complete of the merge itself) — just a "this is happening" label.

## Design

### 1. Backend — cumulative size tracking (`backend/downloader.py`)

`progress_hook` already tracks `prior_streams_bytes` (bytes from completed prior streams) and `last_stream_total`, incrementing `prior_streams_bytes` whenever it detects `total != last_stream_total` (a stream transition). Today that accumulator is only used when `expected_total_bytes` (from the probe) is known. The fix unifies both branches to always use it:

```python
overall_downloaded = prior_streams_bytes + downloaded if downloaded is not None else None

if expected_total_bytes:
    overall_total = expected_total_bytes          # most accurate: known before download starts
elif total is not None:
    overall_total = prior_streams_bytes + total     # best-known-so-far: grows as each stream's own total arrives, never shrinks
else:
    overall_total = None

percent = round(min(overall_downloaded / overall_total, 1.0) * 100, 1) if overall_downloaded is not None and overall_total else 0.0
downloaded_size = format_bytes(overall_downloaded)
total_size = format_bytes(overall_total)
```

Because `prior_streams_bytes` only ever increases (it's incremented by the *completed* stream's full size at the moment the next stream is detected), `overall_downloaded` and `overall_total` are both monotonically non-decreasing across the whole download — independent of whether the probe succeeded. This fixes both the live display and, since `download_entry` reads `total_size` off the same queue entry for `record_history(...)`, the History record too — no separate change needed there.

### 2. Backend — `stage` field for the notification

Derive a `stage` value in the same `progress_hook`, using data yt-dlp's hook already provides in `d["info_dict"]`:

- `vcodec` present and not `"none"`, `acodec` missing/`"none"` → `stage = "video"`
- `acodec` present and not `"none"`, `vcodec` missing/`"none"` → `stage = "audio"`
- otherwise (progressive/combined single-stream format) → `stage = None` (no label shown — today's behavior, unchanged)

A second hook, `postprocessor_hook` (yt-dlp supports this alongside `progress_hooks`, not currently wired up), fires `stage = "merging"` when `d.get("postprocessor") == "Merger" and d.get("status") == "started"`. Wired into `build_ydl_opts` and threaded through `run_download` the same way `progress_hook` already is, using `loop.call_soon_threadsafe` to update the queue entry from the worker thread.

`stage` flows through `QueueManager.update_progress(...)` (new optional param, default `None` so existing calls — including the final `update_progress(entry_id, 100.0, None, 0)` completion call — naturally clear it) and `QueueEntry.to_dict()`, same path as `percent`/`speed`/`eta`. Also explicitly cleared in `reset_for_retry` and `mark_paused`.

### 3. Frontend — inline status label (`frontend/renderer.js`, `frontend/styles.css`)

In `renderRow`, when `entry.status === "downloading" && entry.stage`, show a small subtitle under the row's title/status line:

| `stage` | Label |
|---|---|
| `"video"` | "Downloading video…" |
| `"audio"` | "Downloading audio…" |
| `"merging"` | "Merging video + audio…" |

Hidden (no layout reserved) when `stage` is falsy or status isn't `"downloading"` — so ordinary single-stream downloads render exactly as they do today. No changes to the existing `maxPercent` client-side clamp; it remains a harmless defense-in-depth now that the backend percent is monotonic in both branches.

## Data Flow

1. `download_entry` starts; `probe_total_bytes` runs (as today) and may or may not return a value.
2. yt-dlp downloads the video-only stream. `progress_hook` fires repeatedly: `stage="video"`, cumulative `downloaded_size`/`total_size`/`percent` computed as above, pushed via `queue_manager.update_progress` → WebSocket broadcast → `renderRow`.
3. Video stream completes; `prior_streams_bytes` absorbs its final size. yt-dlp starts the audio-only stream. `progress_hook` detects the `total` change, `stage="audio"`; size/percent continue climbing from where they left off, not resetting.
4. Both streams done; yt-dlp's `postprocessor_hook` fires `status="started"` for `Merger`; `stage="merging"` is pushed (percent stays near 100, size stays at its last value — nothing new to report byte-wise).
5. `ydl.extract_info` returns; `download_entry` reads `final_total_size` off the queue entry (now the correct combined value) and passes it to `record_history`.

## Error Handling / Edge Cases

- **Single-stream (progressive) format**: `vcodec`/`acodec` both present → `stage` stays `None` throughout; behavior identical to today.
- **Probe succeeds**: `expected_total_bytes` branch unchanged — still the most accurate path, stage labels still show (they're independent of probe success).
- **A stream's `total` is momentarily or permanently unavailable** (right after a stream transition, before yt-dlp reports the new stream's `total_bytes`/`total_bytes_estimate` — or, rarely, never for that stream): `overall_total` is `None` for that stretch, so `total_size` renders as `"--"` (via the frontend's existing `formatSizeLine` fallback) rather than a wrong or smaller number, and `percent` is `0.0` for that tick. Since `downloaded_size` (built from `prior_streams_bytes`, already known) stays correct and high, and the frontend's existing `maxPercent` clamp holds the percent bar at its prior value, this reads as at most a brief "--" flicker on the total figure — never a visible regression. Resolves itself on the next throttled hook tick (≤0.25s) once the new stream's total arrives.
- **Postprocessor hook errors or never fires** (e.g. ffmpeg missing, merge skipped): `stage` simply never becomes `"merging"`; the final `update_progress(entry_id, 100.0, None, 0)` still clears it and marks done as today. Not a new failure mode.

## Testing

- `backend/tests/test_downloader.py`: update existing fallback-path assertions (currently asserting the old per-stream-reset numbers) to the new cumulative expectations; add cases for a stream-total-increases-then-a-new-smaller-stream-starts sequence, confirming `downloaded_size`/`total_size` never decrease.
- New tests: `stage` derivation from `info_dict` vcodec/acodec combinations (video-only, audio-only, progressive/neither), and the `postprocessor_hook` firing `stage="merging"` on `Merger`/`started`.
- `backend/tests/test_queue_manager.py`: `update_progress` accepts/stores `stage`; `reset_for_retry`/`mark_paused` clear it.
- Manual: run a real download of a video known to require probe fallback (long HLS-sourced video), watch the Queue row show "Downloading video…" → "Downloading audio…" → "Merging video + audio…" with size numbers that only climb, then confirm the History entry's recorded size matches the actual file size on disk.
