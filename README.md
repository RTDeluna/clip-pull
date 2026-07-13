# CLIP.PULL

Paste video links, pick a folder, and download them in parallel with live progress bars. Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), CLIP.PULL is a desktop app (Windows/macOS) for saving course videos hosted on Vimeo, Loom, and similar platforms — plus a companion Chrome extension that auto-detects videos on course lesson pages (Skool, Loom, Teachable, Kajabi, Thinkific, Circle, and most other course/funnel sites) and sends them straight into the app.

---

## Table of contents

- [What it does](#what-it-does)
- [Supported sites](#supported-sites)
- [Requirements](#requirements)
- [Installing](#installing)
- [Quick start](#quick-start)
- [The Queue tab](#the-queue-tab)
- [The History tab](#the-history-tab)
- [The Settings tab](#the-settings-tab)
- [The Extension tab (CLIP.PULL Course Downloader)](#the-extension-tab-clippull-course-downloader)
- [Tips & troubleshooting](#tips--troubleshooting)
- [Running from source / building installers](#running-from-source--building-installers)
- [Third-party software](#third-party-software)
- [Releasing](#releasing)

---

## What it does

- Downloads one or many video links at once, in parallel, with a live per-item progress bar (percent, speed, size, ETA).
- Remembers every completed and failed download in a searchable **History** tab, with one click to reveal the file in your file explorer or retry a failed one.
- Supports a **referer domain** for videos that are embedded on a course site and reject direct requests without it.
- Detects links you've already downloaded and asks whether to skip or re-queue them.
- Ships a Chrome extension that watches Skool and Loom pages automatically, and can scan any other course/funnel page (Teachable, Kajabi, Thinkific, Circle, Systeme.io, ClickFunnels, GHL, and more) on demand with one click, for a playable video — Skool Native (Mux), YouTube, Vimeo, Loom, Wistia, or Bunny Stream — and sends it to CLIP.PULL's queue with no copy-pasting required.

## Supported sites

The **Queue tab** downloads from any link [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports — that covers YouTube, Vimeo, Loom, Wistia, and well over a thousand other sites out of the box, with nothing platform-specific required. Just paste the URL.

The **Chrome extension** is more targeted: it watches the page you're on for a playable video and sends the link to CLIP.PULL for you, one click, no copy-pasting. It currently detects:

- [x] Vimeo
- [x] Loom
- [x] YouTube
- [x] Wistia
- [x] Bunny Stream
- [x] Skool Native video (Mux) — Skool's own built-in player, launched July 2025
- [x] Any other direct `<video>` element embed, as a last-resort fallback

If a page's video isn't on that list, paste its direct URL into the Queue tab instead — it'll work as long as yt-dlp has an extractor for that site.

## Requirements

- **Windows 10/11** or **macOS**.
- **ffmpeg** — bundled with the Windows installer, nothing to install yourself. High-quality downloads come down as separate video and audio streams that ffmpeg merges; without it, downloads fall back to a lower-quality pre-merged format instead of failing. (See [Third-party software](#third-party-software) below.)
- **aria2c** (optional). If it's on your `PATH`, CLIP.PULL can use it as a faster multi-connection downloader — toggle it in Settings. The app works fine without it.

## Installing

**Windows** is available now — download `CLIP.PULL.Setup.exe` from the [latest release](https://github.com/RTDeluna/clip-pull/releases/latest) and run it.

> This build is currently **unsigned**. Windows SmartScreen may warn that the publisher is unrecognized — click **More info → Run anyway**.

**macOS is not released yet.** Building it requires running the packaging step on an actual Mac (PyInstaller doesn't cross-compile the bundled backend), which hasn't happened yet.

No Python install is required — the backend is bundled as a standalone executable inside the app.

## Quick start

1. Open CLIP.PULL. It starts on the **Queue** tab.
2. Paste one or more video URLs into the text box, one per line.
3. Click **Browse…** and choose (or confirm) the folder downloads should save to.
4. Click **Start Download**. Each link appears in the queue on the right with a live progress bar.
5. When a download finishes, it moves into the **History** tab. Click a finished entry's title (or the folder icon) to reveal the file on disk.

## The Queue tab

This is the main screen — paste links, configure a batch, and watch it run.

- **Video links** — one URL per line. A colored dot next to each line number shows whether it looks like a valid URL before you submit.
- **Save to** — the destination folder. If you've set a **default output folder** in Settings, this is prefilled automatically.
- **Referer domain (optional)** — some course platforms embed their Vimeo/Loom videos in a way that blocks direct downloads unless the request looks like it came from the course site. If a download fails with a referer-related error, paste the course site's URL here (e.g. `https://your-course-site.com`) and retry.
- **Course/batch folder name (optional)** — puts this batch's downloads into a named subfolder inside the destination folder, e.g. `Advanced Marketing Course`.
- **Start Download** — queues every valid line. Invalid lines are skipped and listed below the box.
- **Already downloaded?** — if any pasted links were downloaded successfully before, CLIP.PULL asks whether to **skip duplicates** or **queue anyway**. (You can also turn on automatic skipping in Settings.)

Per-item queue controls, while a download is running or after it fails:
- **Pause / Resume** — pause an in-progress download and pick it back up later.
- **Retry** — re-attempt a failed download (uses the referer you last entered, if any).

You'll get an in-app toast (and, if you grant permission, a native OS notification) when the whole batch finishes.

## The History tab

Every completed or failed download lands here, grouped by date (Today, Yesterday, then by full date).

- **Search** — filter by title or URL.
- **Status filter** — show only **Done** or only **Failed** entries.
- Each row has three icon actions: **copy the source link**, **show the file in your folder**, and **remove the entry from history**.
- Failed entries show a **Retry** button that re-queues the same link (you'll be prompted for a folder if no default output folder is set).
- **Clear all** removes everything — or just the entries matching your current search/filter, if one is active. This can't be undone.

## The Settings tab

- **Max concurrent downloads** (1–10, default 3) — how many videos download at the same time.
- **Fragment concurrency** (1–32, default 8) — how many pieces of a single video download in parallel.
- **Use aria2c when available** — on by default; only takes effect if aria2c is actually installed and on your `PATH` (the app tells you whether it detected it).
- **Skip already-downloaded links automatically** — off by default. When on, re-pasting a previously-downloaded link silently skips it instead of asking.
- **Default output folder** — prefills the Queue tab's "Save to" field, and is required for the Chrome extension to send links to CLIP.PULL (see below).

Click **Save Settings** to apply changes.

## The Extension tab (CLIP.PULL Course Downloader)

A companion Chrome extension that watches Skool and Loom pages automatically, and can scan any other course/funnel page on demand (Teachable, Kajabi, Thinkific, Circle, Systeme.io, ClickFunnels, GHL, and effectively any other course site) — sending whatever video it finds straight to CLIP.PULL's queue, no copying links by hand.

**Setup:**

1. Open the **Extension** tab in CLIP.PULL and click **Download Extension** to save the `.zip`.
2. Unzip it somewhere permanent (don't delete the folder afterward — Chrome loads the extension directly from it).
3. Open `chrome://extensions` (click the code snippet in the app to copy it, then paste into your address bar).
4. Enable **Developer Mode** (top right toggle).
5. Click **Load unpacked** and select the unzipped folder.

**Using it:**

1. Make sure CLIP.PULL is running and a **Default output folder** is set in Settings — the extension needs both to hand off a link.
2. Visit a course lesson page with a video — Skool and Loom are detected automatically; any other site (Teachable, Kajabi, Thinkific, Circle, Systeme.io, ClickFunnels, GHL, etc.) is scanned the moment you click the icon.
3. Click the extension icon to send the video to CLIP.PULL.
4. Switch to CLIP.PULL's **Queue** tab to watch it download. You'll also get a browser notification confirming it was sent.

If the extension can't reach CLIP.PULL, make sure the app is open — it talks to the app over `http://127.0.0.1:8934`, so nothing leaves your machine.

## Tips & troubleshooting

- **Download fails immediately with a referer/blocked error** — the source site requires its own domain as the referer. Add the course site's URL to the **Referer domain** field on the Queue tab and retry.
- **Downloads are slow** — install aria2c and enable it in Settings, or raise **Fragment concurrency**.
- **A video comes down at lower quality than expected** — this happens if the bundled ffmpeg couldn't be found (e.g. a very old install). Reinstalling the latest version restores full-quality merged downloads.
- **The extension says CLIP.PULL isn't running** — open the app first; the extension only works while it's running.
- **The extension says no output folder is set** — set a **Default output folder** in the app's Settings tab.
- **History or Settings seem to reset between launches** — this was a known issue in early packaged builds where the local database wasn't stored in a stable location; it's fixed as of this build. If you still see it, please report it.

## Running from source / building installers

For development or building your own installers:

```bash
npm install                # install Electron + electron-builder
npm start                  # run in dev mode (spawns `python main.py` from backend/)
```

Dev mode requires Python 3 with `backend/requirements.txt` installed (`pip install -r backend/requirements.txt`) plus either ffmpeg on your `PATH` or a local copy fetched via `npm run fetch:ffmpeg` (see below).

To produce a distributable, unsigned installer for the platform you're on:

```bash
npm run dist
```

This fetches a bundled ffmpeg build (skipped if already downloaded), builds the standalone backend executable with PyInstaller, packages the Chrome extension, and runs electron-builder — producing an NSIS installer on Windows or a `.zip` on macOS. Because PyInstaller doesn't cross-compile, building a real macOS artifact requires running this command on an actual Mac (with Python + PyInstaller installed there too); running it on Windows only produces the Windows installer.

## Third-party software

The Windows installer bundles [ffmpeg](https://ffmpeg.org) (a static `ffmpeg.exe`, no source changes) so downloads merge at full quality without requiring a separate install. The bundled build is licensed under the [GNU LGPL v3](https://www.gnu.org/licenses/lgpl-3.0.html) — see `backend/vendor/FFMPEG_LICENSE.txt` after running `npm run fetch:ffmpeg`, or the license text at the link above. ffmpeg's source is available at [ffmpeg.org](https://ffmpeg.org) and from the build's own source at [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds).

## Releasing

Every release follows [Semantic Versioning](https://semver.org/): given `MAJOR.MINOR.PATCH`,

- **patch** — bug fixes, no user-visible behavior change (e.g. `fix: ...` commits)
- **minor** — new, backward-compatible functionality (e.g. `feat: ...` commits)
- **major** — breaking changes (data format, removed functionality, etc.)

`scripts/release.ps1` runs the whole checklist in one go: it bumps `package.json` and commits + tags it (`v<version>` — kept purely for source history, so a bug report tied to a specific build can be traced back to its exact commit), builds the installer (`npm run dist`), pushes the commit and tag, then publishes the `.exe` to a single persistent GitHub release (tag `release`) as a version-less `CLIP.PULL.Setup.exe` asset — replaced in place each time, so the website's download link never needs to change. It refuses to run on a dirty working tree, off `master`, or if `master` is behind `origin/master`, and rolls back the version bump if the build fails.

```powershell
npm run release:patch   # bug fixes
npm run release:minor   # new features
npm run release:major   # breaking changes

# equivalent direct form:
scripts\release.ps1 -Bump patch
```

Requires `gh` (GitHub CLI) authenticated with `repo` scope (`gh auth status` to check).
