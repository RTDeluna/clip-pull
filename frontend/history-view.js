import { showToast } from "./toast.js";
import { connectQueueSocket } from "./ws-client.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const searchInput = document.getElementById("history-search");
const statusFilter = document.getElementById("history-status-filter");
const historyList = document.getElementById("history-list");
const historySummary = document.getElementById("history-summary");
const clearBtn = document.getElementById("history-clear-btn");
const refreshBtn = document.getElementById("history-refresh-btn");

const LINK_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l2-2a5 5 0 0 0-7.07-7.07l-1 1"></path><path d="M14 11a5 5 0 0 0-7.07 0l-2 2a5 5 0 0 0 7.07 7.07l1-1"></path></svg>`;
const FOLDER_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`;
const X_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"></path><path d="M6 6l12 12"></path></svg>`;
const VIDEO_ICON = `<svg class="history-row__icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M10 9.5v5l4.5-2.5z" fill="currentColor" stroke="none"></path></svg>`;
const ERROR_ICON = `<svg class="history-row__icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5"></path><path d="M12 16h.01"></path></svg>`;

// Every rendered History row, keyed by its id -- lets transcript_update WS
// events patch one row's live status/content in place instead of forcing a
// full loadHistory() refetch on every chunk-transcription progress tick.
const transcriptRows = new Map();

function removeRowAnimated(row) {
  row.classList.add("queue-row--leaving");
  row.addEventListener(
    "animationend",
    () => {
      row.remove();
      pruneEmptyDateHeadings();
      updateSummaryCount();
    },
    { once: true }
  );
}

function pruneEmptyDateHeadings() {
  historyList.querySelectorAll(".history-date-heading").forEach((heading) => {
    const next = heading.nextElementSibling;
    if (!next || next.classList.contains("history-date-heading")) {
      heading.remove();
    }
  });
}

function updateSummaryCount() {
  const count = historyList.querySelectorAll(".history-row").length;
  historySummary.textContent = count ? `${count} ${count === 1 ? "entry" : "entries"}` : "";
}

function parseFinishedDate(finishedAt) {
  if (!finishedAt) return null;
  const date = new Date(finishedAt.replace(" ", "T") + "Z");
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatTime(date) {
  if (!date) return "";
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function dateGroupLabel(date) {
  if (!date) return "Unknown date";
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const dayDiff = Math.round((startOfDay(new Date()) - startOfDay(date)) / 86400000);
  if (dayDiff === 0) return "Today";
  if (dayDiff === 1) return "Yesterday";
  return date.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" });
}

function renderDateHeading(label) {
  const li = document.createElement("li");
  li.className = "history-date-heading";
  li.textContent = label;
  return li;
}

async function copyToClipboard(text, label = "Link") {
  try {
    await navigator.clipboard.writeText(text);
    showToast(`${label} copied`, "success");
  } catch {
    showToast(`Failed to copy ${label.toLowerCase()}`, "error");
  }
}

async function deleteEntry(id, row) {
  try {
    const response = await fetch(`${API_BASE}/history/${id}`, { method: "DELETE" });
    if (!response.ok) {
      showToast("Failed to delete this entry.", "error");
      return;
    }
    removeRowAnimated(row);
    showToast("Removed from history", "success");
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  }
}

async function resolveOutputFolder() {
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (response.ok) {
      const settings = await response.json();
      if (settings.default_output_folder) return settings.default_output_folder;
    }
  } catch {
    // Fall through to the folder picker below.
  }
  return window.api.chooseFolder();
}

async function retryFromHistory(entry, button) {
  button.disabled = true;
  try {
    const outputFolder = await resolveOutputFolder();
    if (!outputFolder) return;

    const response = await fetch(`${API_BASE}/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        urls_text: entry.url,
        output_folder: outputFolder,
        retry_of_history_id: entry.id,
      }),
    });
    if (!response.ok) {
      showToast("Failed to queue this retry.", "error");
      return;
    }
    showToast("Queued for retry", "success");
    document.querySelector('.tab-btn[data-view="view-queue"]')?.click();
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function transcribeEntry(entry, button) {
  const confirmed = confirm(
    "Transcription uses the OpenAI and Anthropic API keys configured in " +
      "Settings and is billed per use on your account. Long videos may " +
      "take several minutes. Continue?"
  );
  if (!confirmed) return;

  button.disabled = true;
  try {
    const response = await fetch(`${API_BASE}/history/${entry.id}/transcribe`, { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showToast(body?.detail || "Failed to start transcription.", "error");
      button.disabled = false;
      return;
    }
    const body = await response.json();
    const tracked = transcriptRows.get(entry.id);
    if (tracked) {
      tracked.entry = body.entry;
      applyTranscriptState(tracked.row, tracked.entry);
    }
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
    button.disabled = false;
  }
}

function canTranscribe(entry) {
  return (
    entry.status === "done" &&
    Boolean(entry.output_path) &&
    (!entry.transcript_status || entry.transcript_status === "none" || entry.transcript_status === "error")
  );
}

function setTranscriptStatusText(row, text) {
  const statusEl = row.querySelector(".history-row__transcript-status");
  statusEl.textContent = text || "";
  statusEl.hidden = !text;
}

function applyTranscriptState(row, entry) {
  const transcribeBtn = row.querySelector(".transcribe-btn");
  const viewBtn = row.querySelector(".view-transcript-btn");
  const panel = row.querySelector(".history-row__transcript-panel");
  const errorEl = row.querySelector(".history-row__transcript-error");
  const status = entry.transcript_status || "none";

  transcribeBtn.hidden = !canTranscribe(entry);
  viewBtn.hidden = status !== "done";
  errorEl.hidden = status !== "error";
  errorEl.textContent = status === "error" ? entry.transcript_error || "Transcription failed" : "";

  setTranscriptStatusText(row, status === "running" ? "Transcribing…" : "");

  if (status === "done") {
    const summaryEl = row.querySelector(".history-row__transcript-summary");
    const transcriptEl = row.querySelector(".history-row__transcript-text");
    if (entry.summary) {
      summaryEl.textContent = entry.summary;
      summaryEl.hidden = false;
      row.querySelector(".history-row__transcript-no-summary").hidden = true;
    } else {
      summaryEl.hidden = true;
      row.querySelector(".history-row__transcript-no-summary").hidden = false;
    }
    transcriptEl.textContent = entry.transcript || "";
  } else {
    panel.hidden = true;
    viewBtn.textContent = "View transcript";
  }
}

function renderHistoryRow(entry) {
  const isError = entry.status === "error";

  const row = document.createElement("li");
  row.className = "queue-row history-row queue-row--enter";
  row.innerHTML = `
    <div class="history-row__inner">
      <span class="history-row__icon${isError ? " history-row__icon--error" : ""}">
        ${isError ? ERROR_ICON : VIDEO_ICON}
      </span>
      <div class="history-row__body">
        <div class="history-row__title"></div>
        <div class="history-row__meta"></div>
      </div>
      <div class="history-row__actions">
        <button class="history-icon-btn history-copy-btn" type="button" aria-label="Copy link" title="Copy link">${LINK_ICON}</button>
        <button class="history-icon-btn history-reveal-btn" type="button" aria-label="Show in folder" title="Show in folder">${FOLDER_ICON}</button>
        <button class="history-icon-btn history-delete-btn history-icon-btn--delete" type="button" aria-label="Remove from history" title="Remove from history">${X_ICON}</button>
      </div>
    </div>
    <button class="retry-btn" type="button" hidden>Retry</button>
    <button class="transcribe-btn" type="button" hidden>Transcribe</button>
    <div class="history-row__transcript-error" hidden></div>
    <div class="history-row__transcript-status" hidden></div>
    <button class="view-transcript-btn" type="button" hidden>View transcript</button>
    <div class="history-row__transcript-panel" hidden>
      <div class="history-row__transcript-section">
        <div class="history-row__transcript-section-header">
          <span>Summary</span>
          <button class="history-icon-btn copy-summary-btn" type="button" aria-label="Copy summary" title="Copy summary">${LINK_ICON}</button>
        </div>
        <p class="history-row__transcript-no-summary" hidden>No summary — add an Anthropic API key in Settings to enable summaries.</p>
        <p class="history-row__transcript-summary"></p>
      </div>
      <div class="history-row__transcript-section">
        <div class="history-row__transcript-section-header">
          <span>Transcript</span>
          <button class="history-icon-btn copy-transcript-btn" type="button" aria-label="Copy transcript" title="Copy transcript">${LINK_ICON}</button>
        </div>
        <pre class="history-row__transcript-text"></pre>
      </div>
    </div>
  `;

  const tracked = { row, entry };
  transcriptRows.set(entry.id, tracked);

  const titleEl = row.querySelector(".history-row__title");
  titleEl.textContent = entry.title || entry.url;
  titleEl.classList.toggle("history-row__title--error", isError);
  titleEl.classList.toggle("history-row__title--clickable", Boolean(entry.output_path));
  if (entry.output_path) {
    titleEl.addEventListener("click", () => window.api.revealFile(entry.output_path));
  }

  const metaEl = row.querySelector(".history-row__meta");
  if (isError) {
    metaEl.textContent = entry.error_reason || "Download failed";
  } else {
    const parts = [];
    if (entry.total_size) parts.push(entry.total_size);
    const time = formatTime(parseFinishedDate(entry.finished_at));
    if (time) parts.push(time);
    metaEl.textContent = parts.length ? parts.join(" · ") : entry.url;
  }

  const retryBtn = row.querySelector(".retry-btn");
  if (isError) {
    retryBtn.hidden = false;
    retryBtn.addEventListener("click", () => retryFromHistory(entry, retryBtn));
  }

  row.querySelector(".history-copy-btn").addEventListener("click", () => copyToClipboard(entry.url, "Link"));

  const revealBtn = row.querySelector(".history-reveal-btn");
  revealBtn.disabled = !entry.output_path;
  revealBtn.addEventListener("click", () => {
    if (entry.output_path) window.api.revealFile(entry.output_path);
  });

  row.querySelector(".history-delete-btn").addEventListener("click", () => deleteEntry(entry.id, row));

  const transcribeBtn = row.querySelector(".transcribe-btn");
  transcribeBtn.addEventListener("click", () => transcribeEntry(tracked.entry, transcribeBtn));

  const viewBtn = row.querySelector(".view-transcript-btn");
  const panel = row.querySelector(".history-row__transcript-panel");
  viewBtn.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    viewBtn.textContent = panel.hidden ? "View transcript" : "Hide transcript";
  });

  row.querySelector(".copy-summary-btn").addEventListener("click", () => {
    copyToClipboard(row.querySelector(".history-row__transcript-summary").textContent, "Summary");
  });
  row.querySelector(".copy-transcript-btn").addEventListener("click", () => {
    copyToClipboard(row.querySelector(".history-row__transcript-text").textContent, "Transcript");
  });

  applyTranscriptState(row, entry);

  return row;
}

async function loadHistory() {
  try {
    const params = new URLSearchParams();
    if (searchInput.value) params.set("q", searchInput.value);
    if (statusFilter.value) params.set("status", statusFilter.value);
    const response = await fetch(`${API_BASE}/history?${params.toString()}`);
    if (!response.ok) return false;
    const body = await response.json();
    historyList.innerHTML = "";
    transcriptRows.clear();
    let lastGroup = null;
    body.entries.forEach((entry) => {
      const group = dateGroupLabel(parseFinishedDate(entry.finished_at));
      if (group !== lastGroup) {
        historyList.appendChild(renderDateHeading(group));
        lastGroup = group;
      }
      historyList.appendChild(renderHistoryRow(entry));
    });
    updateSummaryCount();
    return true;
  } catch {
    // Backend unreachable or errored — leave the list as-is.
    return false;
  }
}

clearBtn.addEventListener("click", async () => {
  if (!historyList.children.length) return;
  const scoped = searchInput.value || statusFilter.value;
  const message = scoped
    ? "Clear the entries matching your current search/filter?"
    : "Clear all download history? This can't be undone.";
  if (!confirm(message)) return;

  try {
    const params = new URLSearchParams();
    if (searchInput.value) params.set("q", searchInput.value);
    if (statusFilter.value) params.set("status", statusFilter.value);
    const response = await fetch(`${API_BASE}/history?${params.toString()}`, { method: "DELETE" });
    if (!response.ok) {
      showToast("Failed to clear history.", "error");
      return;
    }
    showToast("History cleared", "success");
    await loadHistory();
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  }
});

refreshBtn.addEventListener("click", async () => {
  refreshBtn.disabled = true;
  refreshBtn.classList.add("is-spinning");
  const ok = await loadHistory();
  showToast(ok ? "History refreshed" : "Failed to refresh history", ok ? "success" : "error");
  refreshBtn.classList.remove("is-spinning");
  refreshBtn.disabled = false;
});

let debounceTimer;
searchInput.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(loadHistory, 250);
});
statusFilter.addEventListener("change", loadHistory);

// The packaged backend is a PyInstaller onefile executable whose cold-start
// can outlast main.js's waitForBackend budget (see the matching comment in
// renderer.js's prefillDefaultOutputFolder) -- a single failed attempt right
// as this script loads would otherwise leave History looking like "no
// history yet" instead of "failed to load," for the rest of the session.
async function loadHistoryOnStartup(retriesLeft = 10) {
  const ok = await loadHistory();
  if (!ok && retriesLeft > 0) {
    setTimeout(() => loadHistoryOnStartup(retriesLeft - 1), 500);
  }
}

loadHistoryOnStartup();

// Every finished download is pushed here the instant it's recorded — the
// History tab always reflects it live, whether or not it's the active tab.
let historyPushTimer;
connectQueueSocket((event) => {
  if (event.type === "transcript_update") {
    const tracked = transcriptRows.get(event.history_id);
    if (!tracked) return; // row isn't currently rendered (different filter/search)
    if (event.entry) {
      // Terminal state (running-start/done/error) -- carries the full row,
      // so patch it directly instead of waiting on a full refetch.
      tracked.entry = event.entry;
      applyTranscriptState(tracked.row, tracked.entry);
    }
    if (event.status === "running") {
      setTranscriptStatusText(tracked.row, event.detail || "Transcribing…");
    }
    return;
  }
  if (event.type !== "history_added") return;
  // Briefly coalesces bursts (e.g. a batch finishing within milliseconds of
  // each other) into a single refetch instead of one per entry.
  clearTimeout(historyPushTimer);
  historyPushTimer = setTimeout(loadHistory, 150);
});
