import { connectQueueSocket } from "./ws-client.js";
import { showToast } from "./toast.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

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

const rows = new Map();
const summaryCounts = { done: 0, error: 0 };

function statusLabel(entry) {
  if (entry.status === "error") return "Failed";
  if (entry.status === "done") return "Done";
  if (entry.status === "downloading") return "Downloading";
  if (entry.status === "paused") return "Paused";
  return "Queued";
}

function formatSpeed(speed) {
  return speed ? speed : "--";
}

function formatEta(eta) {
  if (eta === null || eta === undefined) return "--";
  const totalSeconds = Math.max(0, Math.floor(eta));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")} left`;
}

function formatSizeLine(entry, displayPercent) {
  const downloaded = entry.downloaded_size || "--";
  const total = entry.total_size || "--";
  return `${downloaded} / ${total} · ${Math.round(displayPercent)}%`;
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
      lastTotalSize: null,
      displayedPercent: 0,
      targetPercent: undefined,
      lastEntry: null,
    };
    rows.set(entry.id, state);

    el.querySelector(".retry-btn").addEventListener("click", () => {
      retryEntry(entry.id);
    });
    el.querySelector(".pause-btn").addEventListener("click", () => {
      pauseEntry(entry.id);
    });
    el.querySelector(".resume-btn").addEventListener("click", () => {
      resumeEntry(entry.id);
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
  // total_size is the denominator of that pass — it only changes when a new
  // stream starts (e.g. the small video-only pass finished at 100% and the
  // much larger audio/merge pass just began). Carrying the old ratcheted
  // percent across that boundary is what froze the bar at 100% while the
  // real, much bigger download had barely started — so reset the ratchet
  // whenever the stream we're tracking changes.
  if (entry.total_size !== state.lastTotalSize) {
    state.maxPercent = 0;
    state.lastTotalSize = entry.total_size;
  }
  const displayPercent = Math.max(entry.percent, state.maxPercent);
  state.maxPercent = displayPercent;

  row.classList.toggle("queue-row--downloading", entry.status === "downloading");

  row.querySelector(".queue-row__title").textContent = entry.title || entry.url;
  row.querySelector(".queue-row__duplicate-badge").hidden = !entry.previously_downloaded;
  const statusEl = row.querySelector(".queue-row__status");
  statusEl.textContent = statusLabel(entry);
  statusEl.className = "queue-row__status";
  if (entry.status === "done") statusEl.classList.add("queue-row__status--done");
  if (entry.status === "error") statusEl.classList.add("queue-row__status--error");
  if (entry.status === "paused") statusEl.classList.add("queue-row__status--paused");

  state.lastEntry = entry;
  state.targetPercent = displayPercent;
  if (isNewRow || entry.status !== "downloading") {
    // Snap instead of easing: a freshly-appeared row shouldn't visibly grow
    // up from 0, and state changes (done/error/paused/reset to queued)
    // should read as immediate, not smoothed away.
    state.displayedPercent = displayPercent;
    applyDisplayedPercent(state);
  } else {
    ensureProgressAnimation();
  }
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

async function retryEntry(entryId) {
  try {
    const response = await fetch(`${API_BASE}/queue/${entryId}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ referer: refererInput.value || null }),
    });
    if (!response.ok) {
      showToast("Failed to queue this retry.", "error");
      return;
    }
    showToast("Queued for retry", "success");
  } catch (error) {
    showToast("Retry failed: " + error.message, "error");
  }
}

async function pauseEntry(entryId) {
  try {
    const response = await fetch(`${API_BASE}/queue/${entryId}/pause`, { method: "POST" });
    if (!response.ok) {
      showToast("Failed to pause this download.", "error");
      return;
    }
    showToast("Paused", "success");
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  }
}

async function resumeEntry(entryId) {
  try {
    const response = await fetch(`${API_BASE}/queue/${entryId}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ referer: refererInput.value || null }),
    });
    if (!response.ok) {
      showToast("Failed to resume this download.", "error");
      return;
    }
    showToast("Resumed", "success");
  } catch (error) {
    showToast("Resume failed: " + error.message, "error");
  }
}

browseBtn.addEventListener("click", async () => {
  const folder = await window.api.chooseFolder();
  if (folder) {
    outputFolderInput.value = folder;
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

async function postQueue(payload) {
  const response = await fetch(`${API_BASE}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json();
}

startBtn.addEventListener("click", async () => {
  if (!outputFolderInput.value) {
    showToast("Choose an output folder first.", "error");
    return;
  }
  startBtn.disabled = true;
  invalidLinesEl.hidden = true;

  const payload = {
    urls_text: urlsInput.value,
    output_folder: outputFolderInput.value,
    referer: refererInput.value || null,
    subfolder: courseFolderInput.value || null,
  };

  try {
    let body = await postQueue(payload);

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
  }
});

connectQueueSocket((event) => {
  if (event.type === "sync") {
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
});

async function prefillDefaultOutputFolder() {
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) return;
    const settings = await response.json();
    if (!outputFolderInput.value && settings.default_output_folder) {
      outputFolderInput.value = settings.default_output_folder;
    }
  } catch {
    // Backend not ready yet, or unreachable — Queue view still works,
    // just without a prefilled folder; the user can still Browse manually.
  }
}

prefillDefaultOutputFolder();
