import { showToast } from "./toast.js";
import { connectQueueSocket } from "./ws-client.js";
import { renderMarkdown } from "./markdown.js";

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
const MIC_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>`;
const SPARKLES_ICON = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2c.3 3.6 1.1 6 2.5 7.5S18.4 11.7 22 12c-3.6.3-6 1.1-7.5 2.5S12.3 18.4 12 22c-.3-3.6-1.1-6-2.5-7.5S6.6 12.3 2 12c3.6-.3 6-1.1 7.5-2.5S11.7 6.6 12 2z"></path></svg>`;
const DOCUMENT_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="8" y1="13" x2="16" y2="13"></line><line x1="8" y1="17" x2="16" y2="17"></line><line x1="8" y1="9" x2="10" y2="9"></line></svg>`;
const COPY_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`;
const CHEVRON_ICON = `<svg class="transcript-card__chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"></polyline></svg>`;

// Every rendered History row, keyed by its id -- lets transcript_update /
// summary_update WS events patch one row's live status/content in place
// instead of forcing a full loadHistory() refetch on every progress tick.
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

// -- Transcribe / Summarize: two independent, user-triggered jobs ----------
// Deliberately separate actions (not one bundled job): a user without an
// Anthropic key, or who simply doesn't want a summary, still gets a full
// transcript with no extra cost, wait, or dead-end UI.

async function transcribeEntry(entry, button) {
  const confirmed = confirm(
    "Transcription uses the Gemini API key configured in Settings and is " +
      "billed per use on your account. Long videos may take several " +
      "minutes. Continue?"
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
    applyTrackedEntry(entry.id, body.entry);
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
    button.disabled = false;
  }
}

async function summarizeEntry(entry, button) {
  const confirmed = confirm(
    "Summarizing uses the Anthropic API key configured in Settings and is " +
      "billed per use on your account. Continue?"
  );
  if (!confirmed) return;

  button.disabled = true;
  try {
    const response = await fetch(`${API_BASE}/history/${entry.id}/summarize`, { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showToast(body?.detail || "Failed to start summarization.", "error");
      button.disabled = false;
      return;
    }
    const body = await response.json();
    applyTrackedEntry(entry.id, body.entry);
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
    button.disabled = false;
  }
}

function applyTrackedEntry(historyId, entry) {
  const tracked = transcriptRows.get(historyId);
  if (!tracked) return;
  tracked.entry = entry;
  applyTranscriptState(tracked.row, tracked.entry);
}

function canTranscribe(entry) {
  return (
    entry.status === "done" &&
    Boolean(entry.output_path) &&
    (!entry.transcript_status || entry.transcript_status === "none" || entry.transcript_status === "error")
  );
}

function canSummarize(entry) {
  return (
    entry.transcript_status === "done" &&
    Boolean(entry.transcript) &&
    (!entry.summary_status || entry.summary_status === "none" || entry.summary_status === "error")
  );
}

function setProgress(row, kind, detail, percent) {
  const section = row.querySelector(`.${kind}-progress`);
  const label = section.querySelector(".job-progress__label");
  const track = section.querySelector(".progress-track");
  const fill = section.querySelector(".progress-fill");
  section.hidden = false;
  label.textContent = detail || (kind === "transcript" ? "Transcribing…" : "Summarizing…");
  if (typeof percent === "number") {
    track.classList.remove("progress-track--indeterminate");
    fill.style.width = `${percent}%`;
  } else {
    track.classList.add("progress-track--indeterminate");
    fill.style.width = "";
  }
}

function renderTranscriptLines(container, transcriptText) {
  container.innerHTML = "";
  const fragment = document.createDocumentFragment();
  transcriptText.split("\n").filter(Boolean).forEach((line) => {
    const match = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/);
    const lineEl = document.createElement("div");
    lineEl.className = "transcript-line";
    if (match) {
      const time = document.createElement("span");
      time.className = "transcript-line__time";
      time.textContent = match[1];
      const text = document.createElement("span");
      text.className = "transcript-line__text";
      text.textContent = match[2];
      lineEl.append(time, text);
    } else {
      lineEl.textContent = line;
    }
    fragment.appendChild(lineEl);
  });
  container.appendChild(fragment);
}

function applyTranscriptState(row, entry) {
  const transcribeBtn = row.querySelector(".transcribe-btn");
  const summarizeBtn = row.querySelector(".summarize-btn");
  const transcriptProgress = row.querySelector(".transcript-progress");
  const summaryProgress = row.querySelector(".summary-progress");
  const transcriptError = row.querySelector(".transcript-error");
  const summaryError = row.querySelector(".summary-error");
  const summaryCard = row.querySelector(".summary-card");
  const transcriptCard = row.querySelector(".transcript-card--transcript");

  const transcriptStatus = entry.transcript_status || "none";
  const summaryStatus = entry.summary_status || "none";

  transcribeBtn.hidden = !canTranscribe(entry);
  transcribeBtn.disabled = false;
  summarizeBtn.hidden = !canSummarize(entry);
  summarizeBtn.disabled = false;

  transcriptProgress.hidden = transcriptStatus !== "running";
  summaryProgress.hidden = summaryStatus !== "running";

  transcriptError.hidden = transcriptStatus !== "error";
  transcriptError.textContent = transcriptStatus === "error" ? entry.transcript_error || "Transcription failed" : "";
  summaryError.hidden = summaryStatus !== "error";
  summaryError.textContent = summaryStatus === "error" ? entry.summary_error || "Summarization failed" : "";

  summaryCard.hidden = summaryStatus !== "done" || !entry.summary;
  if (summaryStatus === "done" && entry.summary) {
    row.querySelector(".summary-content").innerHTML = renderMarkdown(entry.summary);
  }

  transcriptCard.hidden = transcriptStatus !== "done" || !entry.transcript;
  if (transcriptStatus === "done" && entry.transcript) {
    renderTranscriptLines(row.querySelector(".transcript-lines"), entry.transcript);
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

    <div class="transcript-feature">
      <div class="transcript-actions">
        <button class="transcribe-btn action-chip" type="button" hidden>${MIC_ICON}<span>Transcribe</span></button>
        <button class="summarize-btn action-chip action-chip--accent" type="button" hidden>${SPARKLES_ICON}<span>Summarize</span></button>
      </div>

      <div class="job-progress transcript-progress" hidden>
        <div class="job-progress__header">
          <span class="job-progress__icon">${MIC_ICON}</span>
          <span class="job-progress__label">Extracting audio…</span>
        </div>
        <div class="progress-track"><div class="progress-fill"></div></div>
      </div>
      <div class="job-progress summary-progress" hidden>
        <div class="job-progress__header">
          <span class="job-progress__icon">${SPARKLES_ICON}</span>
          <span class="job-progress__label">Summarizing…</span>
        </div>
        <div class="progress-track progress-track--indeterminate"><div class="progress-fill"></div></div>
      </div>

      <div class="job-error transcript-error" hidden></div>
      <div class="job-error summary-error" hidden></div>

      <div class="transcript-card summary-card" hidden>
        <div class="transcript-card__header">
          <span class="transcript-card__icon transcript-card__icon--accent">${SPARKLES_ICON}</span>
          <span class="transcript-card__title">Summary</span>
          <span class="ai-badge">AI-generated</span>
          <button class="icon-btn copy-summary-btn" type="button" aria-label="Copy summary" title="Copy summary">${COPY_ICON}</button>
        </div>
        <div class="transcript-card__body summary-content markdown-content"></div>
      </div>

      <div class="transcript-card transcript-card--transcript" hidden>
        <div class="transcript-card__header transcript-card__header--toggle" role="button" tabindex="0" aria-expanded="false">
          <span class="transcript-card__icon">${DOCUMENT_ICON}</span>
          <span class="transcript-card__title">Transcript</span>
          <button class="icon-btn copy-transcript-btn" type="button" aria-label="Copy transcript" title="Copy transcript">${COPY_ICON}</button>
          ${CHEVRON_ICON}
        </div>
        <div class="transcript-card__body transcript-card__body--collapsible" hidden>
          <div class="transcript-lines"></div>
        </div>
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

  const summarizeBtn = row.querySelector(".summarize-btn");
  summarizeBtn.addEventListener("click", () => summarizeEntry(tracked.entry, summarizeBtn));

  const transcriptToggle = row.querySelector(".transcript-card__header--toggle");
  const transcriptBody = row.querySelector(".transcript-card__body--collapsible");
  const toggleTranscript = () => {
    const expanded = transcriptBody.hidden;
    transcriptBody.hidden = !expanded;
    transcriptToggle.setAttribute("aria-expanded", String(expanded));
    transcriptToggle.classList.toggle("is-expanded", expanded);
  };
  transcriptToggle.addEventListener("click", toggleTranscript);
  transcriptToggle.addEventListener("keydown", (event) => {
    if (event.target.closest(".copy-transcript-btn")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggleTranscript();
    }
  });

  row.querySelector(".copy-summary-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    copyToClipboard(row.querySelector(".summary-content").textContent, "Summary");
  });
  row.querySelector(".copy-transcript-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    copyToClipboard(row.querySelector(".transcript-lines").textContent, "Transcript");
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
  if (event.type === "transcript_update" || event.type === "summary_update") {
    const tracked = transcriptRows.get(event.history_id);
    if (!tracked) return; // row isn't currently rendered (different filter/search)
    const kind = event.type === "transcript_update" ? "transcript" : "summary";
    if (event.status === "running") {
      setProgress(tracked.row, kind, event.detail, event.percent);
    }
    if (event.entry) {
      // Terminal state (running-start/done/error) -- carries the full row,
      // so patch it directly instead of waiting on a full refetch.
      tracked.entry = event.entry;
      applyTranscriptState(tracked.row, tracked.entry);
    }
    return;
  }
  if (event.type !== "history_added") return;
  // Briefly coalesces bursts (e.g. a batch finishing within milliseconds of
  // each other) into a single refetch instead of one per entry.
  clearTimeout(historyPushTimer);
  historyPushTimer = setTimeout(loadHistory, 150);
});
