# Vimeo Course Downloader — Design

## Purpose

A local desktop tool for downloading Vimeo videos (primarily lesson videos from a purchased course) in bulk. Paste a list of Vimeo links, pick an output folder, and the app downloads them all in parallel with live per-video progress bars.

## Goals / Success Criteria

- Paste many Vimeo URLs at once (one per line) and download them all without further manual steps per video.
- Real-time, per-video progress bars (percent, speed, ETA) — not a static spinner.
- Fast: multiple videos download concurrently, and each video's fragments download concurrently too.
- Native folder picker for choosing the output directory.
- One video failing doesn't stop the batch.

## Non-goals (for this iteration)

- No installer/packaging — run via a start script.
- No queue persistence across backend restarts (in-memory only).
- No login/cookie-based auth flows for private Vimeo accounts — only unlisted/embed-style links with an optional referer.
- No multi-user / remote access — strictly a local, single-user tool.

## Architecture

Two local processes on `localhost`, communicating over HTTP + WebSocket:

- **Electron app (UI)** — the window the user interacts with. Renders the paste-links form, the download queue, and per-video progress bars. Spawns the Python backend as a child process on launch and kills it on quit. Uses Electron's native `dialog.showOpenDialog` for the output-folder picker.
- **Python backend (FastAPI)** — owns the download queue and does the actual downloading via `yt-dlp` (used as a Python library, not shelled out).

## Components

### Backend (`backend/`)

- `main.py` — FastAPI app.
  - `POST /queue` — body: `{ urls: string[], output_folder: string, referer?: string }`. Parses/validates URLs, creates queue entries, returns immediately (202).
  - `WS /ws` — pushes progress events as JSON: `{ id, status, percent, speed, eta, title, error? }`.
  - `GET /queue` — current in-memory queue state (used on frontend load/reconnect to resync).
- `downloader.py` — wraps `yt_dlp`:
  - `asyncio.Semaphore(3)` limits concurrent video downloads (matches chosen default; not user-configurable in this iteration).
  - Each download runs in a thread-pool executor since `yt_dlp` is blocking.
  - `format` selection: best available quality/bitrate.
  - `concurrent_fragment_downloads` set (e.g. 5) for intra-video (DASH fragment) parallelism.
  - `progress_hooks` translate yt-dlp's callback data into progress events and push them to connected WebSocket clients.
  - Output filename: sanitized video title (from yt-dlp's extracted info), collision-suffixed if duplicate titles occur in one batch.
  - If a `referer` is supplied (globally or on a per-video retry), it's passed via yt-dlp's `http_headers`.
- `queue_manager.py` — in-memory list of queue entries: `{ id, url, title, status: queued|downloading|done|error, percent, speed, eta, error_reason, retry_count }`.

### Frontend (`frontend/`, loaded in the Electron window)

- Paste view: textarea (one Vimeo URL per line), output-folder text field + "Browse…" button (native picker via Electron IPC), optional referer field (shown/used only on retry of a blocked video), Start button.
- Queue view: one row per video — title (or "resolving…" until known), animated progress bar, percent/speed/ETA text, status badge, Retry button on error rows.
- `ws-client.js` — opens the WebSocket on load, matches incoming events to rows by `id`, updates DOM in place (no polling).
- Client-side validation: pasted lines must look like Vimeo URLs before submit; non-matching lines are flagged inline without blocking valid lines in the same paste.

## Data Flow

1. User pastes N URLs, confirms output folder, clicks Start.
2. Frontend `POST /queue`s the parsed list. Backend creates one queue entry per URL (`queued`) and responds immediately; frontend renders one row per video right away.
3. Backend's queue manager pulls up to 3 entries at a time (semaphore) and starts extraction+download for each in a worker thread.
4. As yt-dlp reports progress, the backend pushes a JSON event per tick over the WebSocket.
5. Frontend updates the matching row's progress bar/text live.
6. On completion the file lands in the chosen folder, named from the video's title; row flips to a "done" state.
7. When the whole batch finishes, Start re-enables and a summary shows (e.g. "18/20 downloaded, 2 failed").

## Error Handling

- **Per-video isolation**: each download is an independent async task; one failure doesn't stop the batch. Failed rows show "Failed — [reason]" with a Retry button.
- **403 / access-denied**: surfaced explicitly as "Blocked — this video may require the course site as referer," since the user wasn't certain whether their course's Vimeo embeds are referer-protected. Retry lets the user supply a referer domain for that video (or all remaining videos).
- **Invalid/non-Vimeo URLs**: validated client-side and server-side; flagged per-line without blocking the rest of the paste.
- **Backend restart**: queue state is in-memory only — accepted limitation for a personal, single-user tool. A restart mid-batch means re-pasting whatever hadn't finished.

## Testing / Verification

- Manual: run the app, paste a small batch of real Vimeo course links, verify progress bars update live, files land correctly named in the chosen folder, and a deliberately-bad URL produces a per-row error without breaking the rest of the batch.
- Backend: light unit coverage on `queue_manager.py` (state transitions) and URL validation, since those are pure logic and cheap to test. Download behavior itself (`downloader.py`) is verified manually against real Vimeo links rather than mocked, since yt-dlp's behavior against a live third-party service is the actual risk surface.

## Open Question Carried Forward

Whether the user's specific course platform referer-protects its Vimeo embeds is unconfirmed. The design handles this reactively (clear error + retry-with-referer) rather than requiring it upfront.
