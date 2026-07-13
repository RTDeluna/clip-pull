# Privacy Policy — CLIP.PULL Course Downloader

_Last updated: 2026-07-13_

CLIP.PULL Course Downloader ("the extension") is a browser extension that helps
you send videos from lessons you have legitimate access to into Clip.Pull, a
desktop download manager that runs entirely on your own computer. This policy
explains exactly what data the extension touches and where it goes.

## What the extension accesses

When you click the extension icon, the extension reads the **current tab's
content** to detect an embedded video. Specifically it looks at page markup
such as `<iframe>`/`<video>` elements, Open Graph meta tags, and (on Skool)
the page's `__NEXT_DATA__` JSON to determine:

- the video provider (Skool Native/Mux, YouTube, Vimeo, Loom, Wistia, Bunny Stream, or a direct MP4),
- the video URL,
- the lesson title and course name, where available (used for the filename and destination folder), and
- a thumbnail (used for the in-popup preview only).

On `skool.com` and `loom.com`, detection code runs automatically as soon as the
page loads (declared in the manifest), so the popup can respond instantly.
On every other site, nothing runs until you click the extension icon — that
click is what temporarily grants the extension access to read the page you're
currently on (via Chrome's `activeTab` permission), and only that one tab, only
for that one action. The extension never runs in the background on sites
outside `skool.com`/`loom.com`, and never reads a page without you clicking
the icon first.

## What data is sent off your device

**Nothing leaves your device.** The detected video URL, referer, and course
name are sent to `http://127.0.0.1:8934` — a local server run by the Clip.Pull
desktop app on your own machine, not a remote or cloud service. If Clip.Pull
isn't running, the request never leaves the browser at all; the popup simply
tells you to open the app.

- No account credentials, cookies, passwords, browsing history, or personal
  identifiers are collected or transmitted.
- No data is sent to any third-party server, analytics service, or the
  extension developer.

## What is stored locally

The extension does not maintain its own download history — Clip.Pull's Queue
and History tabs are the single source of truth for everything sent to it.
Nothing about detected videos is persisted by the extension itself beyond the
current popup session.

## What the extension does NOT do

- It does **not** read or transmit your cookies or authentication tokens.
- It does **not** track your browsing across sites.
- It does **not** sell or share data with third parties for advertising.
- It does **not** run in the background on any site other than `skool.com`/`loom.com` — everywhere else, it only reads the current tab the instant you click the icon, never before and never automatically.
- It does **not** contact any server other than the local Clip.Pull app on your own machine.

## Permissions and why they are needed

- **`activeTab`** — read the current page (only when you click the icon) to detect the video. This is what lets the extension work on any course/funnel site, not just the ones listed below, without ever running unattended in the background on pages you haven't asked it to check.
- **`scripting`** — inject the same detection code used on Skool/Loom into the current tab on demand, for sites not covered by the automatic host access below.
- **`storage`** — reserved for future extension settings; no personal data is currently stored.
- **`notifications`** — show a brief confirmation toast after a successful handoff to Clip.Pull.
- **Host access to `skool.com` / `loom.com`** — the two pages the extension is allowed to run detection on automatically, without needing a click first.
- **Host access to `127.0.0.1:8934`** — talking to the local Clip.Pull app on your own machine.

## Responsible use

This extension is an independent tool and is not affiliated with, authorized by,
or endorsed by Skool, Loom, Teachable, Kajabi, Thinkific, Circle, Systeme.io,
ClickFunnels, GoHighLevel, or any video provider or platform it can detect
video on. Download only content you are authorized to access and respect all
applicable copyright laws and each platform's terms of service.
