# CLIP.PULL Course Downloader

A Chrome extension that detects the video on a course lesson page and sends it straight into **Clip.Pull**'s download queue — no copy-pasting links.

> ⚡ Supports Skool Native (Mux), YouTube, Vimeo, Loom, Wistia, and Bunny Stream — on Skool, Loom, Teachable, Kajabi, Thinkific, Circle, Systeme.io, ClickFunnels, GHL, and effectively any other course/funnel site, since detection works from the page's video embeds rather than needing custom support for each platform.

---

## 🧩 How It Works

1. Open Clip.Pull (the desktop app) and set a default output folder in its **Settings** tab.
2. Navigate to any lesson page with a video.
3. Click the **CLIP.PULL** extension icon — it detects the video provider automatically.
4. Hit **Send to Clip.Pull**. The video is added to Clip.Pull's Queue tab, where you can watch it download, retry failures, and browse history.

If detection fails (slow-loading or unsupported player), use the **Manual URL** input to paste the video link directly. It's sent to Clip.Pull the same way.

This extension does **not** download anything itself — it's a capture tool. Clip.Pull must be running (with a default output folder configured) for a send to succeed.

---

## 🚀 Features

- 🌐 **Any course/funnel platform** — Skool and Loom get instant detection on page load; every other site (Teachable, Kajabi, Thinkific, Circle, Systeme.io, ClickFunnels, GHL, or anywhere else) is scanned on demand the moment you click the icon, with no per-platform setup needed
- 🎬 **Skool Native (Mux)** — detects Skool's built-in video player (Mux-powered, since July 2025)
- 📺 **YouTube** — detects embedded YouTube videos
- 🎞️ **Vimeo** — full support including lazy-loaded iframes and Universal Embed (`data-vimeo-id`)
- 🌀 **Loom** — detects Loom links and embedded players (including lazy-loaded `data-src`)
- 🟣 **Wistia** — supports both Skool-specific and general Wistia embeds
- 🐰 **Bunny Stream** — detects `iframe.mediadelivery.net` and `.b-cdn.net` CDN embeds
- 📁 **Direct MP4** — falls back to any raw `<video>` element on the page
- 🖼️ **Video Preview** — preview any detected video before sending
- 📋 **Copy Video Link** — one-click copy the direct video URL
- 🔁 **Auto-Retry Detection** — retries after a delay if video loads after page paint
- 🔗 **Manual URL Fallback** — paste a video URL manually if auto-detection fails
- 📂 **Course-Aware Folders** — downloads land nested under the course name automatically (Skool)
- ⚡ **SPA-Aware** — detects navigation on Skool's Next.js single-page app and re-runs detection

---

## 🛠️ Installation (Developer Mode)

The easiest way to get this extension is from Clip.Pull's own **Extension** tab — it has a "Download Extension" button and walks through these same steps.

1. Download and unzip the extension package (from Clip.Pull's Extension tab, or from this `extension/` folder directly if you're running from source).
2. Open Chrome → `chrome://extensions/`
3. Enable **Developer Mode** (top right)
4. Click **Load unpacked** → select the unzipped folder
5. Navigate to any course lesson page and click the extension icon

---

## 🔒 Privacy

The extension reads the current tab only when you click its icon, and sends the
detected video URL only to Clip.Pull's local server on your own machine
(`http://127.0.0.1:8934`) — nothing leaves your device. It does **not** read
cookies or auth tokens, and does not maintain its own download history. See
the full [Privacy Policy](PRIVACY.md) for details.

---

## 📜 License

See the [LICENSE](LICENSE) file for details.

---

## ⚠️ Disclaimer

This extension is an independent tool and is not affiliated with, authorized by, or endorsed by Skool, Loom, Teachable, Kajabi, Thinkific, Circle, Systeme.io, ClickFunnels, GoHighLevel, or any other platform it can detect video on.
Please use this tool responsibly and respect copyright laws when downloading content.
