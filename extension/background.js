// background.js — Skool Video Downloader service worker.
// Pure capture-and-handoff: detect a link (via content.js), verify Clip.Pull
// is reachable, then POST it into Clip.Pull's own download queue. Clip.Pull's
// Queue tab owns all progress/history/retry from that point on — this worker
// no longer runs its own multi-minute download job, so there's nothing to
// resume across service-worker restarts and no long-lived port needed.

const CLIPPULL_BASE = "http://127.0.0.1:8934";

function notify(id, title, message) {
  chrome.notifications.create(id, {
    type: "basic",
    iconUrl: "Icons/icon128.png",
    title,
    message,
  });
}

async function getDefaultOutputFolder() {
  const res = await fetch(`${CLIPPULL_BASE}/settings`, { signal: AbortSignal.timeout(3000) });
  if (!res.ok) return null;
  const settings = await res.json();
  return settings.default_output_folder || null;
}

async function sendToClipPull({ url, referer, subfolder }) {
  // A single fetch to /settings doubles as both the "is Clip.Pull even
  // running" check and the default-folder lookup -- a separate /health
  // call first (removed) was pure sequential overhead: a network failure
  // here is exactly the same "not running" signal a failed health check
  // would have given, so it only added up to 2s of extra worst-case wait
  // before this request even started.
  let outputFolder;
  try {
    outputFolder = await getDefaultOutputFolder();
  } catch {
    return { ok: false, error: "not_running" };
  }
  if (!outputFolder) {
    return { ok: false, error: "no_output_folder" };
  }

  let res;
  try {
    res = await fetch(`${CLIPPULL_BASE}/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        urls_text: url,
        output_folder: outputFolder,
        referer: referer || null,
        subfolder: subfolder || null,
      }),
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    return { ok: false, error: "not_running" };
  }

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return { ok: false, error: "server_error", detail: text };
  }

  notify(`sent-${Date.now()}`, "Sent to Clip.Pull", "Check the Queue tab for progress.");
  return { ok: true };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "SEND_TO_CLIPPULL") return false;
  sendToClipPull(msg)
    .then(sendResponse)
    .catch((err) => sendResponse({ ok: false, error: "unknown", detail: err.message || String(err) }));
  return true; // async response
});
