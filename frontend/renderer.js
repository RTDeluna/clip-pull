import { connectQueueSocket } from "./ws-client.js";
import { showToast } from "./toast.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

// A brief backend hiccup right after launch (see prefillDefaultOutputFolder's
// comment below about the packaged app's PyInstaller cold-start race) can
// make a single fetch attempt fail even though the backend is about to be
// ready -- retrying a couple of times with a short delay covers that window
// without requiring the user to notice and click Retry/Resume again
// themselves. Only retries a genuine network-level failure (fetch itself
// throwing); an HTTP error response (4xx/5xx) is a real answer from a
// reachable backend and is returned as-is, not retried.
async function fetchWithRetry(url, options, attempts = 3, delayMs = 600) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fetch(url, options);
    } catch (error) {
      if (attempt >= attempts) throw error;
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
}

const urlsInput = document.getElementById("urls");
const urlsGutter = document.getElementById("urls-gutter");
const invalidLinesEl = document.getElementById("invalid-lines");
const outputFolderInput = document.getElementById("output-folder");
const browseBtn = document.getElementById("browse-btn");
const refererInput = document.getElementById("referer");
const courseFolderInput = document.getElementById("course-folder");
const startBtn = document.getElementById("start-btn");
const queueList = document.getElementById("queue-list");
const queueSummary = document.getElementById("queue-summary");
const queueRefreshBtn = document.getElementById("queue-refresh-btn");
const connectionBanner = document.getElementById("connection-banner");

const rows = new Map();
const summaryCounts = { done: 0, error: 0 };

function statusLabel(entry) {
  if (entry.status === "error") return "Failed";
  if (entry.status === "done") return "Done";
  if (entry.status === "downloading") return "Downloading";
  if (entry.status === "pausing") return "Pausing…";
  if (entry.status === "paused") return "Paused";
  if (entry.status === "resuming") return "Resuming…";
  return "Queued";
}

const STAGE_LABELS = {
  video: "Downloading video…",
  audio: "Downloading audio…",
  merging: "Merging video + audio…",
  // Set while the backend is waiting out a backoff delay before an
  // automatic retry of a transient failure (network blip, temporary server
  // error) -- so a brief stall reads as "recovering," not "stuck."
  retrying: "Hit a snag — retrying…",
};

function stageLabel(entry) {
  if (entry.status !== "downloading") return null;
  return STAGE_LABELS[entry.stage] || null;
}

function formatSpeed(speed) {
  return speed ? speed : "--";
}

function formatEta(eta) {
  if (eta === null || eta === undefined) return "--";
  const totalSeconds = Math.max(0, Math.floor(eta));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  // A wildly-off estimate (e.g. right after a stream transition, before the
  // speed average has settled) previously showed as something like
  // "16666:40 left" -- not a helpful countdown at that point, so cap it.
  if (hours > 99) return "99h+ left";
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")} left`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")} left`;
}

function formatSizeLine(entry, displayPercent) {
  const downloaded = entry.downloaded_size || "--";
  // Some fragmented/HLS streams never report a total size -- real bytes are
  // still flowing (downloaded keeps climbing), but "X / -- · 0%" reads as a
  // stuck download. Drop the total/percent instead of showing a fake 0%.
  if (entry.status === "downloading" && entry.size_unknown) {
    return `${downloaded} downloaded`;
  }
  const total = entry.total_size || "--";
  return `${downloaded} / ${total} · ${Math.round(displayPercent)}%`;
}

// ── Speed-linked shimmer effect ──────────────────────────────────────
// The moving highlight that sweeps across an in-progress bar (see
// .queue-row--downloading .progress-fill::after in styles.css) runs faster
// as the download's actual throughput climbs, so the bar visibly "feels"
// like it's moving quicker on a fast connection and eases off on a slow
// one, instead of always sweeping at the same fixed pace regardless of
// real speed.
const SHIMMER_MIN_DURATION_S = 0.6; // fastest sweep, at/above the reference speed
const SHIMMER_MAX_DURATION_S = 2.2; // slowest sweep, at/near 0 B/s
const SHIMMER_REFERENCE_SPEED_BYTES = 5 * 1024 * 1024; // 5 MiB/s reads as "fast"

function shimmerDurationForSpeed(speedBytesPerSecond) {
  if (!speedBytesPerSecond || speedBytesPerSecond <= 0) {
    return SHIMMER_MAX_DURATION_S;
  }
  const ratio = Math.min(speedBytesPerSecond / SHIMMER_REFERENCE_SPEED_BYTES, 1);
  return SHIMMER_MAX_DURATION_S - ratio * (SHIMMER_MAX_DURATION_S - SHIMMER_MIN_DURATION_S);
}

// ── Perceived-speed smoothing ────────────────────────────────────────
// The backend only pushes an update every ~250ms, and yt-dlp's raw
// per-chunk numbers arrive in visible jumps. Snapping the bar straight to
// each new value looks like it's stalling between updates — every major
// download UI (browsers, Steam, installers) instead eases the displayed
// value continuously toward the latest real one, so motion never stops.
// This never shows a percent ahead of the real one, just smooths the
// path between two real, already-confirmed values.
let progressAnimationHandle = null;

function applyDisplayedPercent(state) {
  state.el.querySelector(".progress-fill").style.width = `${state.displayedPercent}%`;
  if (state.lastEntry) {
    state.el.querySelector(".queue-row__size").textContent = formatSizeLine(state.lastEntry, state.displayedPercent);
  }
}

function stepProgressAnimation() {
  let anyActive = false;
  for (const state of rows.values()) {
    if (state.targetPercent === undefined || state.displayedPercent === state.targetPercent) continue;
    const diff = state.targetPercent - state.displayedPercent;
    if (Math.abs(diff) < 0.1) {
      state.displayedPercent = state.targetPercent;
    } else {
      // Eases fastest when furthest away, so it always "catches up" well
      // before the next real update lands instead of trailing behind it.
      state.displayedPercent += diff * 0.22;
      anyActive = true;
    }
    applyDisplayedPercent(state);
  }
  progressAnimationHandle = anyActive ? requestAnimationFrame(stepProgressAnimation) : null;
}

function ensureProgressAnimation() {
  if (progressAnimationHandle === null) {
    progressAnimationHandle = requestAnimationFrame(stepProgressAnimation);
  }
}

function renderRow(entry, { announceCompletion = true } = {}) {
  let state = rows.get(entry.id);
  const isNewRow = !state;
  if (!state) {
    const el = document.createElement("li");
    el.className = "queue-row queue-row--enter";
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
    queueList.appendChild(el);
    state = {
      el,
      maxPercent: 0,
      lastStatus: null,
      displayedPercent: 0,
      targetPercent: undefined,
      lastEntry: null,
    };
    rows.set(entry.id, state);

    el.querySelector(".retry-btn").addEventListener("click", (event) => {
      retryEntry(entry.id, event.currentTarget);
    });
    el.querySelector(".pause-btn").addEventListener("click", (event) => {
      pauseEntry(entry.id, event.currentTarget);
    });
    el.querySelector(".resume-btn").addEventListener("click", (event) => {
      resumeEntry(entry.id, event.currentTarget);
    });
  }
  const row = state.el;

  if (state.lastStatus === "done") summaryCounts.done -= 1;
  if (state.lastStatus === "error") summaryCounts.error -= 1;
  if (entry.status === "done") summaryCounts.done += 1;
  if (entry.status === "error") summaryCounts.error += 1;

  // Fire once, the moment a download actually finishes — not on every
  // re-render with the same terminal status, and not for entries that
  // were already done/error at initial WS "sync" (those already finished
  // before this client connected, so there's nothing new to announce).
  if (announceCompletion && entry.status !== state.lastStatus) {
    if (entry.status === "done") {
      showToast(`Download complete: ${entry.title || entry.url}`, "success");
    } else if (entry.status === "error") {
      showToast(
        `Download failed: ${entry.title || entry.url}${entry.error_reason ? ` — ${entry.error_reason}` : ""}`,
        "error"
      );
    }
  }

  state.lastStatus = entry.status;

  // Vimeo's high-quality formats download as separate video+audio streams,
  // each reported by yt-dlp as its own 0-100% pass — without this, the bar
  // visibly resets partway through. A retry resets the entry to "queued"
  // with percent 0, which is the one case where going back to 0 is correct.
  if (entry.status === "queued") {
    state.maxPercent = 0;
  }
  const displayPercent = Math.max(entry.percent, state.maxPercent);
  state.maxPercent = displayPercent;

  row.classList.toggle("queue-row--downloading", entry.status === "downloading");
  row.style.setProperty("--shimmer-duration", `${shimmerDurationForSpeed(entry.speed_bytes)}s`);
  row.querySelector(".progress-track").classList.toggle(
    "progress-track--indeterminate",
    entry.status === "downloading" && Boolean(entry.size_unknown)
  );

  const titleText = entry.title || entry.url;
  const titleEl = row.querySelector(".queue-row__title");
  titleEl.textContent = titleText;
  titleEl.title = titleText;
  row.querySelector(".queue-row__duplicate-badge").hidden = !entry.previously_downloaded;
  const statusEl = row.querySelector(".queue-row__status");
  statusEl.textContent = statusLabel(entry);
  statusEl.className = "queue-row__status";
  if (entry.status === "done") statusEl.classList.add("queue-row__status--done");
  if (entry.status === "error") statusEl.classList.add("queue-row__status--error");
  if (entry.status === "paused" || entry.status === "pausing") {
    statusEl.classList.add("queue-row__status--paused");
  }

  const stageEl = row.querySelector(".queue-row__stage");
  const stage = stageLabel(entry);
  stageEl.textContent = stage || "";
  stageEl.hidden = !stage;

  state.lastEntry = entry;
  state.targetPercent = displayPercent;
  if (isNewRow || entry.status !== "downloading") {
    // Snap instead of easing: a freshly-appeared row shouldn't visibly grow
    // up from 0, and state changes (done/error/paused/reset to queued)
    // should read as immediate, not smoothed away.
    state.displayedPercent = displayPercent;
  } else {
    ensureProgressAnimation();
  }
  // Always refresh here, not just from the easing loop above: the loop only
  // repaints when displayedPercent is still catching up to a *changed*
  // target, so a size-unknown download (percent pinned at 0 the whole time,
  // see formatSizeLine) would otherwise never repaint its "X downloaded"
  // text as more bytes arrive, even though real progress is happening.
  applyDisplayedPercent(state);
  row.querySelector(".queue-row__speed").textContent = formatSpeed(entry.speed);
  row.querySelector(".queue-row__eta").textContent = formatEta(entry.eta);

  const errorEl = row.querySelector(".queue-row__error");
  const retryBtn = row.querySelector(".retry-btn");
  if (entry.status === "error") {
    errorEl.textContent = entry.error_reason || "Unknown error";
    retryBtn.hidden = false;
  } else {
    errorEl.textContent = "";
    retryBtn.hidden = true;
  }

  row.querySelector(".pause-btn").hidden = entry.status !== "downloading";
  row.querySelector(".resume-btn").hidden = entry.status !== "paused";

  updateSummary();
}

function updateSummary() {
  const total = rows.size;
  queueSummary.textContent = total
    ? `${summaryCounts.done}/${total} downloaded${summaryCounts.error ? `, ${summaryCounts.error} failed` : ""}`
    : "";
}

// Finished entries (done/error) are already recorded in History — the
// backend clears them out of the live queue a few seconds after they
// complete, and notifies us here so we can fade the row out instead of
// it just vanishing.
function removeRow(entryId) {
  const state = rows.get(entryId);
  if (!state) return;
  if (state.lastStatus === "done") summaryCounts.done -= 1;
  if (state.lastStatus === "error") summaryCounts.error -= 1;
  rows.delete(entryId);
  updateSummary();

  state.el.classList.add("queue-row--leaving");
  state.el.addEventListener("animationend", () => state.el.remove(), { once: true });
}

// Skeleton placeholders shaped like a real .queue-row, shown from page load
// until the first WebSocket "sync" resolves (or the connection-error banner
// takes over), so the queue never sits blank during that initial unknown
// window. clearQueueSkeletons is idempotent — safe to call once the first
// sync lands and again on any later disconnect.
function renderQueueSkeletons(count = 3) {
  // Guards against duplicate rows if this is ever called a second time
  // before clearQueueSkeletons runs (not reachable today -- only one call
  // site -- but cheap to make safe against a future reconnect wiring).
  if (queueList.querySelector(".queue-row--skeleton")) return;
  const titleWidths = ["60%", "48%", "54%"];
  const fragment = document.createDocumentFragment();
  for (let i = 0; i < count; i += 1) {
    const li = document.createElement("li");
    li.className = "queue-row queue-row--skeleton";
    li.setAttribute("aria-hidden", "true");
    li.innerHTML = `
      <div class="queue-row__top">
        <span class="skeleton skeleton--line" style="width: ${titleWidths[i % titleWidths.length]}"></span>
        <span class="skeleton skeleton--pill" style="width: 62px"></span>
      </div>
      <span class="skeleton skeleton--bar"></span>
      <span class="skeleton skeleton--line" style="width: 40%; margin-top: 12px"></span>
    `;
    fragment.appendChild(li);
  }
  queueList.appendChild(fragment);
}

function clearQueueSkeletons() {
  queueList.querySelectorAll(".queue-row--skeleton").forEach((el) => el.remove());
}

// A full resync with the backend's current queue state — unlike the
// WebSocket's live "update_batch"/"removed" pushes, this also reconciles
// away any row this client still shows that the backend no longer has
// (e.g. after a dropped connection), not just apply new updates.
async function refreshQueue() {
  const response = await fetch(`${API_BASE}/queue`);
  if (!response.ok) throw new Error("backend returned an error");
  const body = await response.json();
  const freshIds = new Set(body.entries.map((entry) => entry.id));
  Array.from(rows.keys()).forEach((id) => {
    if (!freshIds.has(id)) removeRow(id);
  });
  body.entries.forEach((entry) => renderRow(entry, { announceCompletion: false }));
}

queueRefreshBtn.addEventListener("click", async () => {
  queueRefreshBtn.disabled = true;
  queueRefreshBtn.classList.add("is-spinning");
  try {
    await refreshQueue();
    showToast("Queue refreshed", "success");
  } catch (error) {
    showToast("Failed to refresh the queue: " + error.message, "error");
  } finally {
    queueRefreshBtn.classList.remove("is-spinning");
    queueRefreshBtn.disabled = false;
  }
});

function isSupportedUrl(str) {
  try {
    const url = new URL(str.trim());
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function renderGutter() {
  const lines = urlsInput.value.split("\n");
  urlsGutter.innerHTML = lines
    .map((line, i) => {
      const trimmed = line.trim();
      let dotClass = "urls-gutter__dot";
      if (trimmed) {
        dotClass += isSupportedUrl(trimmed)
          ? " urls-gutter__dot--valid"
          : " urls-gutter__dot--invalid";
      }
      return `<div class="urls-gutter__line"><span class="${dotClass}"></span>${i + 1}</div>`;
    })
    .join("");
  urlsGutter.scrollTop = urlsInput.scrollTop;
}

urlsInput.addEventListener("input", renderGutter);
urlsInput.addEventListener("scroll", () => {
  urlsGutter.scrollTop = urlsInput.scrollTop;
});
new ResizeObserver(() => {
  urlsGutter.style.height = `${urlsInput.clientHeight}px`;
}).observe(urlsInput);
renderGutter();

async function retryEntry(entryId, button) {
  if (button) button.disabled = true;
  try {
    const response = await fetchWithRetry(`${API_BASE}/queue/${entryId}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ referer: refererInput.value || null }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showToast(body?.detail || "Failed to queue this retry.", "error");
      return;
    }
    showToast("Queued for retry", "success");
  } catch (error) {
    showToast("Retry failed: " + error.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

async function pauseEntry(entryId, button) {
  if (button) button.disabled = true;
  try {
    const response = await fetch(`${API_BASE}/queue/${entryId}/pause`, { method: "POST" });
    if (!response.ok) {
      showToast("Failed to pause this download.", "error");
      return;
    }
    showToast("Paused", "success");
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

async function resumeEntry(entryId, button) {
  if (button) button.disabled = true;
  try {
    const response = await fetchWithRetry(`${API_BASE}/queue/${entryId}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ referer: refererInput.value || null }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showToast(body?.detail || "Failed to resume this download.", "error");
      return;
    }
    showToast("Resumed", "success");
  } catch (error) {
    showToast("Resume failed: " + error.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

browseBtn.addEventListener("click", async () => {
  try {
    const folder = await window.api.chooseFolder();
    if (folder) {
      outputFolderInput.value = folder;
    }
  } catch (error) {
    showToast("Couldn't open the folder picker: " + error.message, "error");
  }
});

const duplicateOverlay = document.getElementById("duplicate-confirm-overlay");
const duplicateList = document.getElementById("duplicate-confirm-list");
const duplicateSkipBtn = document.getElementById("duplicate-confirm-skip");
const duplicateContinueBtn = document.getElementById("duplicate-confirm-continue");

// Long URLs otherwise wrap at an arbitrary character mid-word. Splitting
// right after each natural delimiter and inserting a <wbr> there lets the
// browser prefer breaking at those points instead.
function appendUrlWithBreakHints(li, url) {
  const parts = url.split(/(?<=[/?&=])/);
  parts.forEach((part, i) => {
    li.appendChild(document.createTextNode(part));
    if (i < parts.length - 1) {
      li.appendChild(document.createElement("wbr"));
    }
  });
}

function confirmDuplicates(duplicateUrls) {
  return new Promise((resolve) => {
    duplicateList.innerHTML = "";
    duplicateUrls.forEach((url) => {
      const li = document.createElement("li");
      appendUrlWithBreakHints(li, url);
      duplicateList.appendChild(li);
    });
    duplicateOverlay.hidden = false;
    duplicateContinueBtn.focus();

    function cleanup(decision) {
      duplicateOverlay.hidden = true;
      duplicateSkipBtn.removeEventListener("click", onSkip);
      duplicateContinueBtn.removeEventListener("click", onContinue);
      duplicateOverlay.removeEventListener("click", onBackdropClick);
      document.removeEventListener("keydown", onKeydown);
      resolve(decision);
    }
    function onSkip() {
      cleanup("skip_duplicates");
    }
    function onContinue() {
      cleanup("queue_all");
    }
    function onBackdropClick(event) {
      if (event.target === duplicateOverlay) cleanup(null);
    }
    function onKeydown(event) {
      if (event.key === "Escape") {
        cleanup(null);
        return;
      }
      // Trap Tab focus between the two buttons — they're the only
      // focusable elements in the modal — so it doesn't leak to the page
      // underneath while the overlay is open.
      if (event.key === "Tab") {
        if (event.shiftKey && document.activeElement === duplicateSkipBtn) {
          event.preventDefault();
          duplicateContinueBtn.focus();
        } else if (!event.shiftKey && document.activeElement === duplicateContinueBtn) {
          event.preventDefault();
          duplicateSkipBtn.focus();
        }
      }
    }

    duplicateSkipBtn.addEventListener("click", onSkip);
    duplicateContinueBtn.addEventListener("click", onContinue);
    duplicateOverlay.addEventListener("click", onBackdropClick);
    document.addEventListener("keydown", onKeydown);
  });
}

// Returns the parsed success body, or null after surfacing a backend error.
// Without the response.ok check, a 4xx/5xx {"detail": "..."} body would be
// read as a success shape and later throw a TypeError on body.entries, which
// the caller's catch then mislabels as "Failed to reach the backend".
async function postQueue(payload) {
  const response = await fetch(`${API_BASE}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    showToast(body?.detail || `The backend rejected the request (${response.status}).`, "error");
    return null;
  }
  return response.json();
}

startBtn.addEventListener("click", async () => {
  if (!outputFolderInput.value) {
    showToast("Choose an output folder first.", "error");
    return;
  }
  startBtn.disabled = true;
  invalidLinesEl.hidden = true;
  // Immediate feedback while the POST is in flight — the refresh buttons
  // elsewhere use an is-spinning class; a text button reads more clearly with
  // a label swap. Restored in the finally block below.
  const startBtnLabel = startBtn.textContent;
  startBtn.textContent = "Starting…";

  const payload = {
    urls_text: urlsInput.value,
    output_folder: outputFolderInput.value,
    referer: refererInput.value || null,
    subfolder: courseFolderInput.value || null,
  };

  try {
    let body = await postQueue(payload);
    // postQueue already surfaced the error toast; just abort the success path.
    if (!body) return;

    if (body.invalid_lines && body.invalid_lines.length > 0) {
      invalidLinesEl.hidden = false;
      invalidLinesEl.textContent = `Skipped invalid lines:\n${body.invalid_lines.join("\n")}`;
      showToast(`Skipped ${body.invalid_lines.length} invalid line${body.invalid_lines.length === 1 ? "" : "s"}`, "warning");
    }

    if (body.needs_confirmation) {
      const decision = await confirmDuplicates(body.duplicate_urls || []);
      if (!decision) {
        return;
      }
      body = await postQueue({ ...payload, duplicate_action: decision });
      if (!body) return;
    }

    if (body.entries.length) {
      showToast(`Added ${body.entries.length} link${body.entries.length === 1 ? "" : "s"} to the queue`, "success");
    } else if (body.skipped_duplicate_urls && body.skipped_duplicate_urls.length > 0) {
      const count = body.skipped_duplicate_urls.length;
      showToast(`Skipped ${count} duplicate link${count === 1 ? "" : "s"} — nothing left to queue`, "info");
    }

    body.entries.forEach(renderRow);
    urlsInput.value = "";
    renderGutter();
  } catch (error) {
    invalidLinesEl.hidden = false;
    invalidLinesEl.textContent = "Failed to reach the backend: " + error.message;
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    startBtn.disabled = false;
    startBtn.textContent = startBtnLabel;
  }
});

// Fill the queue with skeleton rows up front so it isn't blank during the
// initial-connection window before the first "sync" arrives (cleared in the
// sync handler below, or on a disconnect if the backend never comes up).
renderQueueSkeletons();

// Distinguishes a genuine drop (show "reconnecting…") from a first-launch
// attempt that hasn't connected yet (show "connecting…") in the banner below.
let hasEverConnected = false;

connectQueueSocket(
  (event) => {
    if (event.type === "sync") {
      clearQueueSkeletons();
      event.entries.forEach((entry) => renderRow(entry, { announceCompletion: false }));
    } else if (event.type === "update_batch") {
      event.entries.forEach(renderRow);
    } else if (event.type === "removed") {
      removeRow(event.entry_id);
    } else if (event.type === "batch_complete") {
      const { done, error } = event.summary;
      showToast(
        `Batch complete — ${done} done${error ? `, ${error} failed` : ""}`,
        error ? "warning" : "success"
      );
      if (window.Notification && Notification.permission === "granted") {
        new Notification("Batch complete", {
          body: `${done} done, ${error} failed`,
        });
      } else if (window.Notification && Notification.permission !== "denied") {
        Notification.requestPermission();
      }
    }
  },
  (status) => {
    // Without this, a WebSocket drop mid-download (network blip, backend
    // hiccup) left the Queue view silently frozen with zero indication
    // anything was wrong — the reconnect itself already happens
    // automatically (see ws-client.js), this just makes it visible.
    if (status === "connected") hasEverConnected = true;
    connectionBanner.hidden = status !== "disconnected";
    // "reconnecting…" only makes sense once we've actually been connected;
    // on a first-launch attempt that never succeeded yet, it's just connecting.
    if (status === "disconnected") {
      // The banner is now the loading affordance — drop any initial skeletons
      // so they don't sit stacked underneath it while the backend is down.
      clearQueueSkeletons();
      connectionBanner.textContent = hasEverConnected
        ? "Lost connection to CLIP.PULL's backend — reconnecting…"
        : "Connecting to CLIP.PULL's backend…";
    }
  }
);

// Persistent inline fallback shown next to the "Save to" field when the
// prefill gives up — a text line plus a button that re-triggers the prefill.
function showPrefillLoadError() {
  const container = outputFolderInput.closest(".field-group");
  if (container.querySelector(".load-error")) return;
  const el = document.createElement("div");
  el.className = "load-error";
  el.innerHTML = `
    <span class="load-error__msg">Couldn't load your saved folder.</span>
    <button class="btn btn--ghost load-error__retry" type="button">Retry</button>
  `;
  el.querySelector(".load-error__retry").addEventListener("click", () => {
    el.remove();
    prefillDefaultOutputFolder();
  });
  container.appendChild(el);
}

// The packaged backend is a PyInstaller onefile executable — it self-extracts
// to a temp directory on every launch, which (especially on first run, with
// antivirus scanning an unsigned exe) can take longer than main.js's
// waitForBackend() budget. A single fetch attempt right as this script loads
// can lose that race and silently never show the saved folder for the rest
// of the session — so retry a few times before giving up for real.
async function prefillDefaultOutputFolder(retriesLeft = 10) {
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) throw new Error(`settings fetch failed: ${response.status}`);
    const settings = await response.json();
    if (!outputFolderInput.value && settings.default_output_folder) {
      outputFolderInput.value = settings.default_output_folder;
    }
  } catch {
    if (retriesLeft > 0) {
      setTimeout(() => prefillDefaultOutputFolder(retriesLeft - 1), 500);
    } else if (!outputFolderInput.value) {
      // Retries exhausted and the field is still empty — surface it instead of
      // silently leaving no folder. (If the user already picked one via Browse
      // meanwhile, the prefill is moot, so stay quiet.)
      showToast(
        "Couldn't load your saved output folder — check that CLIP.PULL's backend is running, then choose a folder or retry.",
        "error"
      );
      showPrefillLoadError();
    }
  }
}

prefillDefaultOutputFolder();

// Picks up a default output folder set (or changed) in Settings after this
// page's own one-shot prefill above already ran — without this, saving a
// new default while the Queue tab's "Save to" field is empty would never
// show up here until the app was restarted. Never overwrites a folder the
// user already chose (typed or via Browse…).
document.addEventListener("clippull:settings-saved", (event) => {
  if (!outputFolderInput.value && event.detail.default_output_folder) {
    outputFolderInput.value = event.detail.default_output_folder;
  }
});
