import { showToast } from "./toast.js";
import { connectQueueSocket } from "./ws-client.js";
import { renderMarkdown } from "./markdown.js";
import { showConfirmModal } from "./confirm-modal.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

// A brief backend hiccup right after launch (see prefillDefaultOutputFolder
// in renderer.js) can make a single fetch attempt fail even though the
// backend is about to be ready -- retrying a couple of times with a short
// delay covers that window without requiring the user to click Retry again
// themselves. Only retries a genuine network-level failure (fetch itself
// throwing); an HTTP error response (4xx/5xx) is a real answer from a
// reachable backend and is returned as-is, not retried. Duplicated (not
// imported) from renderer.js's identical helper -- this app has no
// shared-code mechanism between view modules, so a small duplicate is the
// pragmatic choice here, same as PROVIDER_DISPLAY_NAMES below.
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

const searchInput = document.getElementById("history-search");
const statusFilter = document.getElementById("history-status-filter");
const historyList = document.getElementById("history-list");
const historySummary = document.getElementById("history-summary");
const clearBtn = document.getElementById("history-clear-btn");
const retryFailedBtn = document.getElementById("history-retry-failed-btn");
const refreshBtn = document.getElementById("history-refresh-btn");
const batchBtn = document.getElementById("history-batch-btn");
const batchSummarizeInput = document.getElementById("history-batch-summarize");
const batchLock = document.getElementById("history-batch-lock");

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
const DOWNLOAD_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>`;
const CHAT_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8z"></path></svg>`;
const SEARCH_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.3-4.3"></path></svg>`;

// Every rendered History row, keyed by its id -- lets transcript_update /
// summary_update WS events patch one row's live status/content in place
// instead of forcing a full loadHistory() refetch on every progress tick.
const transcriptRows = new Map();

// The History list can grow into the hundreds after months of use, and
// building every row's DOM up front (each with collapsible summary/
// transcript cards, a chat panel, export buttons, etc.) -- not the fetch,
// which is a cheap local SQLite query -- is the real cost of a "long
// scroll". Only the first HISTORY_PAGE_SIZE entries are rendered eagerly;
// the rest sit in pendingHistoryEntries and are revealed a page at a time
// as the user scrolls near the bottom (see setupLoadMoreSentinel), so
// opening History or changing the search/filter never pays for rows the
// user hasn't scrolled to. Rows are only ever added, never unmounted once
// rendered -- course banner clustering (renderCourseBanners) reorders rows
// across the whole rendered list, and WS live-updates target a rendered
// row directly, so keeping every mounted row real and permanent avoids a
// much larger class of bugs than a fully recycling virtual list would.
const HISTORY_PAGE_SIZE = 40;
let pendingHistoryEntries = [];
let historyLoadMoreObserver = null;
let loadMoreSentinel = null;
// Which date group renderEntriesChunk last emitted a heading for -- has to
// survive across page boundaries so a heading is never repeated or skipped
// depending on where a given page happens to start.
let lastRenderedDateGroup = null;
// The true total from the last fetch, used for the "N entries" summary --
// counting rendered .history-row elements would undercount while
// pendingHistoryEntries still holds unrendered rows.
let totalHistoryCount = 0;

// Per-row chat conversations, keyed by history entry id: [{ role, content }].
// Module-level (not per-render) on purpose -- a row's DOM is rebuilt whenever
// a transcript_update/summary_update WS event re-renders it via
// applyTranscriptState, so keeping the messages here means an in-progress
// conversation survives those unrelated updates instead of being wiped. The
// full history/transcript prior turns are re-sent to the backend on each new
// question (there's no server-side chat persistence). In-memory only: a page
// reload / app restart starts fresh, an accepted simplification for v1.
const chatConversations = new Map();

// Course Workspace chat conversations, keyed by the course's output FOLDER
// path (a string), separate from chatConversations above (keyed by a numeric
// history entry id). Kept in its own Map on purpose so a folder-string key can
// never collide with a video's numeric id key. Same in-memory-only, resent-on-
// each-turn model as single-video chat.
const courseChatConversations = new Map();

// Latest GET /courses result: [{ folder, name, lesson_count, ready_count }].
// Cached at module level so renderCourseBanners() can repaint banners whenever
// the history list is rebuilt or Pro status resolves, without refetching.
let courses = [];

// Cost-disclosure gate: the billing-confirm modal is shown before the FIRST
// course-level AI action (chat, search, or digest) per session, then this flag
// suppresses it for the rest of the session — mirroring the design doc's
// "reuse the existing billing-confirm pattern before the first course-level AI
// action per session".
let courseBillingConfirmed = false;

// Mirrors settings-view.js's GUMROAD_PRO_URL — there's no shared-code
// mechanism between views in this app, so the small constant is duplicated
// rather than imported. Kept in sync with main.js's ALLOWED_EXTERNAL_URLS.
const GUMROAD_PRO_URL = "https://gumroad.com/l/clippull-pro-placeholder";

// Duplicated (not imported) from the backend's provider map — five entries,
// no shared frontend/backend module, so a local lookup is the pragmatic
// choice over any import machinery.
const PROVIDER_DISPLAY_NAMES = {
  gemini: "Gemini",
  anthropic: "Anthropic",
  openai: "OpenAI",
  groq: "Groq",
  openrouter: "OpenRouter",
};

// The rich Lesson Notes display (key points, chapters, export) is Pro-gated
// on the frontend as a UX nicety; the backend's export endpoint is the hard
// gate. Best-effort only: a failed /license fetch simply leaves this false,
// so free users still get the TL;DR line + upgrade nudge. Refetched on
// clippull:license-changed (see bottom of file) so activating/deactivating
// Pro in Settings takes effect here immediately, no reload needed.
let isPro = false;

// Configured AI providers, used only to name the right provider in the
// pre-run confirm dialogs. Best-effort; null falls back to provider-agnostic
// copy so a failed /settings fetch never blocks transcribe/summarize.
let transcriptionProvider = null;
let summarizationProvider = null;

async function loadProStatus() {
  try {
    const response = await fetch(`${API_BASE}/license`);
    if (!response.ok) throw new Error(`license fetch failed: ${response.status}`);
    const entry = await response.json();
    isPro = Boolean(entry?.pro);
  } catch (error) {
    console.warn("Couldn't load Pro status:", error);
    isPro = false;
  }
  // Pro status resolves asynchronously and can land after some rows have
  // already rendered with the free-tier UI, so re-apply the Pro-gated bits
  // now that it's known: the batch button and every tracked row's chat/export
  // gating (applyTranscriptState is idempotent and never touches chat state).
  applyBatchProState();
  transcriptRows.forEach((tracked) => applyTranscriptState(tracked));
  // Course banners carry their own Pro lock badge / locked-open behavior, so
  // repaint them too once Pro status is known (idempotent — see the function).
  renderCourseBanners();
}

async function loadProviderSettings() {
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) throw new Error(`settings fetch failed: ${response.status}`);
    const settings = await response.json();
    transcriptionProvider = settings.transcription_provider || null;
    summarizationProvider = settings.summarization_provider || null;
  } catch (error) {
    console.warn("Couldn't load provider settings:", error);
  }
}

function removeRowAnimated(row) {
  row.classList.add("queue-row--leaving");
  row.addEventListener(
    "animationend",
    () => {
      row.remove();
      pruneEmptyDateHeadings();
      totalHistoryCount = Math.max(0, totalHistoryCount - 1);
      updateSummaryCount();
    },
    { once: true }
  );
}

function pruneEmptyDateHeadings() {
  historyList.querySelectorAll(".history-date-heading").forEach((heading) => {
    const next = heading.nextElementSibling;
    // A heading can also be immediately followed by the load-more sentinel
    // (every row under it got clustered into a course banner elsewhere, and
    // the rest of the list is still unrendered in pendingHistoryEntries) --
    // that's just as empty as being followed by nothing or another heading.
    if (
      !next ||
      next.classList.contains("history-date-heading") ||
      next.classList.contains("history-load-more-sentinel")
    ) {
      heading.remove();
    }
  });
}

function updateSummaryCount() {
  historySummary.textContent = totalHistoryCount
    ? `${totalHistoryCount} ${totalHistoryCount === 1 ? "entry" : "entries"}`
    : "";
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
    // Routed through the main process (window.api.copyText -> Electron's
    // native clipboard module) instead of navigator.clipboard.writeText,
    // which rejects with "Document is not focused" whenever DevTools or
    // another window has focus instead of the app content.
    await window.api.copyText(text);
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

async function retryFromHistory(entry, button, row) {
  button.disabled = true;
  try {
    // Reuse the exact folder this download was originally queued against --
    // the whole point of retrying is picking up where it left off, not
    // silently redirecting it to whatever the current default happens to be
    // (or, worse, popping a folder-picker dialog for a failed batch item the
    // user already told the app where to put). Only falls back to the
    // current default/picker for older History rows recorded before
    // output_folder was captured on failure too.
    const outputFolder = entry.output_folder || (await resolveOutputFolder());
    if (!outputFolder) return;

    const response = await fetchWithRetry(`${API_BASE}/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        urls_text: entry.url,
        output_folder: outputFolder,
        retry_of_history_id: entry.id,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showToast(body?.detail || "Failed to queue this retry.", "error");
      return;
    }
    // Deliberately stays on the History tab instead of jumping to Queue --
    // that let you retry at most one failed download per visit, since the
    // very first click yanked you away before you could click Retry on any
    // other failed row. Staying put lets you retry as many failed downloads
    // as you want in one pass; the entry is already moving in the Queue tab
    // for whenever you want to check on it.
    showToast("Queued for retry — check the Queue tab", "success");
    // The failed entry is now redownloading in the Queue, so leaving its old
    // "error" row sitting in History would just be a stale duplicate of what
    // the Queue tab is already tracking live -- drop it now instead of
    // waiting for the retry to finish. history_store.record() already falls
    // back to inserting a fresh row when its update_id target is gone (see
    // its docstring: "e.g. cleared mid-retry"), so the retry's eventual
    // outcome still lands in History -- it just won't be stitched onto this
    // now-removed row.
    removeRowAnimated(row);
    try {
      await fetch(`${API_BASE}/history/${entry.id}`, { method: "DELETE" });
    } catch {
      // Best-effort: if this fails the DB row just lingers until the retry
      // completes and updates it in place, which is harmless.
    }
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

// The app supports several transcription/summarization providers (chosen in
// Settings), so the confirm copy names whichever is currently configured
// instead of hardcoding one. Falls back to provider-agnostic wording when the
// provider is unknown (e.g. the best-effort /settings fetch hasn't landed).
function billedKeyClause(provider) {
  const name = PROVIDER_DISPLAY_NAMES[provider];
  return name
    ? `uses the ${name} API key configured in Settings`
    : "uses the API key for your configured provider in Settings";
}

async function transcribeEntry(entry, button) {
  const confirmed = await showConfirmModal({
    title: "Start transcription?",
    message:
      `Transcription ${billedKeyClause(transcriptionProvider)} and is ` +
      "billed per use on your account. Long videos may take several minutes.",
    confirmLabel: "Transcribe",
    cancelLabel: "Cancel",
  });
  if (!confirmed) return;

  // Immediate feedback: the transcript_update "running" WS event that hides
  // this button can take a moment to arrive, so show the state right on click.
  button.disabled = true;
  button.querySelector("span").textContent = "Transcribing…";
  try {
    const response = await fetch(`${API_BASE}/history/${entry.id}/transcribe`, { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showToast(body?.detail || "Failed to start transcription.", "error");
      button.disabled = false;
      button.querySelector("span").textContent = "Transcribe";
      return;
    }
    const body = await response.json();
    applyTrackedEntry(entry.id, body.entry);
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
    button.disabled = false;
    button.querySelector("span").textContent = "Transcribe";
  }
}

async function summarizeEntry(entry, button) {
  const confirmed = await showConfirmModal({
    title: "Start summarization?",
    message:
      `Summarizing ${billedKeyClause(summarizationProvider)} and is ` +
      "billed per use on your account.",
    confirmLabel: "Summarize",
    cancelLabel: "Cancel",
  });
  if (!confirmed) return;

  // Immediate feedback: the summary_update "running" WS event that hides this
  // button can take a moment to arrive, so show the state right on click.
  button.disabled = true;
  button.querySelector("span").textContent = "Summarizing…";
  try {
    const response = await fetch(`${API_BASE}/history/${entry.id}/summarize`, { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showToast(body?.detail || "Failed to start summarization.", "error");
      button.disabled = false;
      button.querySelector("span").textContent = "Summarize";
      return;
    }
    const body = await response.json();
    applyTrackedEntry(entry.id, body.entry);
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
    button.disabled = false;
    button.querySelector("span").textContent = "Summarize";
  }
}

// Export the notes for one entry in a single format. The frontend Pro gate
// (see the export row's visibility in applyTranscriptState) is a UX nicety;
// the backend's 402 is the real gate, so a 402 here still surfaces a friendly
// upgrade prompt as a fallback. Matches the response.ok / body.detail /
// showToast shape used for the other POST actions in this codebase.
async function exportNotes(entry, button) {
  const format = button.dataset.format;
  const label = button.querySelector("span").textContent;
  button.disabled = true;
  button.querySelector("span").textContent = "Exporting…";
  try {
    const response = await fetch(`${API_BASE}/history/${entry.id}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ formats: [format] }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const fallback =
        response.status === 402
          ? "Exporting notes is a CLIP.PULL Pro feature — upgrade to unlock it."
          : "Failed to export notes.";
      showToast(body?.detail || fallback, "error");
      return;
    }
    const body = await response.json();
    const paths = Array.isArray(body.paths) ? body.paths : [];
    showToast(`Exported ${label} — showing it in your folder.`, "success");
    if (paths.length) window.api.revealFile(paths[0]);
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = label;
  }
}

function applyTrackedEntry(historyId, entry) {
  const tracked = transcriptRows.get(historyId);
  if (!tracked) return;
  tracked.entry = entry;
  applyTranscriptState(tracked);
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

function wordCountLabel(text) {
  const count = text.trim().split(/\s+/).filter(Boolean).length;
  return `${count.toLocaleString()} word${count === 1 ? "" : "s"}`;
}

// -- Structured Lesson Notes ------------------------------------------------
// A completed summary is a JSON string:
//   { tldr, key_points: [{seconds, text}], chapters: [{seconds, title}] }
// with a fallback form where key_points/chapters are empty. Genuinely old
// rows (or a model that ignored the format) instead hold plain prose that
// isn't valid JSON at all. parseSummary handles all three: anything that
// doesn't parse to an object with a string `tldr` is treated as legacy
// plain-text and rendered exactly as before.
function parseSummary(rawSummary) {
  if (typeof rawSummary === "string") {
    try {
      const parsed = JSON.parse(rawSummary);
      if (parsed && typeof parsed === "object" && typeof parsed.tldr === "string") {
        return {
          structured: true,
          tldr: parsed.tldr,
          keyPoints: Array.isArray(parsed.key_points) ? parsed.key_points : [],
          chapters: Array.isArray(parsed.chapters) ? parsed.chapters : [],
        };
      }
    } catch {
      // Not JSON — fall through to the legacy plain-text branch below.
    }
  }
  return { structured: false, text: rawSummary || "" };
}

function formatSeconds(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hh = String(Math.floor(s / 3600)).padStart(2, "0");
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function timestampToSeconds(text) {
  const parts = text.split(":").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}

// Plain-text form of a summary, used for both the at-a-glance word count and
// the copy-to-clipboard action so neither depends on the (lazily rendered)
// card DOM — and so the upsell nudge never leaks into a copied summary.
function summaryPlainText(entry) {
  const parsed = parseSummary(entry.summary);
  if (!parsed.structured) return parsed.text;
  const parts = [parsed.tldr];
  if (parsed.keyPoints.length) {
    parts.push("", "Key Points");
    parsed.keyPoints.forEach((kp) => parts.push(`[${formatSeconds(kp.seconds)}] ${kp.text || ""}`));
  }
  if (parsed.chapters.length) {
    parts.push("", "Chapters");
    parsed.chapters.forEach((ch) => parts.push(`[${formatSeconds(ch.seconds)}] ${ch.title || ""}`));
  }
  return parts.join("\n");
}

function renderUpsellNudge() {
  const nudge = document.createElement("button");
  nudge.type = "button";
  nudge.className = "summary-upsell";
  nudge.innerHTML = `${SPARKLES_ICON}<span>Upgrade to Pro to see key points, chapters, and export notes</span>`;
  nudge.addEventListener("click", () => window.api.openExternal(GUMROAD_PRO_URL));
  return nudge;
}

// One "Key Points" / "Chapters" section: a heading plus a clickable chip per
// item. Clicking a chip scrolls the row's transcript to that moment.
function renderNotesGroup(title, items, getText, tracked) {
  const group = document.createElement("div");
  group.className = "notes-group";
  const heading = document.createElement("div");
  heading.className = "notes-group__title";
  heading.textContent = title;
  group.appendChild(heading);
  items.forEach((item) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "note-chip";
    const time = document.createElement("span");
    time.className = "note-chip__time";
    time.textContent = formatSeconds(item.seconds);
    const text = document.createElement("span");
    text.className = "note-chip__text";
    text.textContent = getText(item) || "";
    chip.append(time, text);
    chip.addEventListener("click", () => jumpToTranscriptSeconds(tracked, Number(item.seconds) || 0));
    group.appendChild(chip);
  });
  return group;
}

// Renders the summary card body. Legacy plain-text is rendered exactly as
// before (a markdown block). Structured notes always show the TL;DR; the
// richer key-point/chapter sections are Pro-only, with an upgrade nudge for
// everyone else.
function renderSummarySection(container, tracked) {
  const entry = tracked.entry;
  const parsed = parseSummary(entry.summary);
  container.innerHTML = "";

  if (!parsed.structured) {
    container.innerHTML = renderMarkdown(parsed.text);
    return;
  }

  const tldr = document.createElement("div");
  tldr.className = "summary-tldr";
  tldr.innerHTML = renderMarkdown(parsed.tldr);
  container.appendChild(tldr);

  if (!isPro) {
    container.appendChild(renderUpsellNudge());
    return;
  }

  if (parsed.keyPoints.length) {
    container.appendChild(renderNotesGroup("Key Points", parsed.keyPoints, (kp) => kp.text, tracked));
  }
  if (parsed.chapters.length) {
    container.appendChild(renderNotesGroup("Chapters", parsed.chapters, (ch) => ch.title, tracked));
  }
}

// "Jump to moment": there's no embedded player (files open externally), so a
// jump means scrolling the transcript to the nearest line at or before the
// target time and briefly flashing it. Opens the transcript card first so the
// lines exist (they render lazily on first expand).
function jumpToTranscriptSeconds(tracked, targetSeconds) {
  const { row } = tracked;
  tracked.transcriptCollapsible.expand();
  const container = row.querySelector(".transcript-lines");
  const lines = container.querySelectorAll(".transcript-line[data-seconds]");
  let target = null;
  lines.forEach((line) => {
    if (Number(line.dataset.seconds) <= targetSeconds) target = line;
  });
  if (!target) target = lines[0];
  if (!target) return;
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.classList.add("transcript-line--flash");
  setTimeout(() => target.classList.remove("transcript-line--flash"), 1600);
}

// Both result cards (Summary, Transcript) share this: collapsed by default so
// a history full of past summaries/transcripts doesn't force every row to lay
// out and paint its full content on load, and the (potentially expensive --
// markdown parsing, per-line transcript DOM) render is deferred until the
// card is actually opened, not just visually hidden behind `hidden`.
function makeCollapsibleCard(card, renderContent) {
  const toggle = card.querySelector(".transcript-card__header--toggle");
  const body = card.querySelector(".transcript-card__body--collapsible");
  let rendered = false;
  const renderNow = () => {
    renderContent();
    rendered = true;
  };
  const setExpanded = (expanded) => {
    body.hidden = !expanded;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.classList.toggle("is-expanded", expanded);
    if (expanded && !rendered) renderNow();
  };
  toggle.addEventListener("click", () => setExpanded(body.hidden));
  toggle.addEventListener("keydown", (event) => {
    if (event.target.closest("button")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setExpanded(body.hidden);
    }
  });
  return {
    ensureRendered: () => {
      if (!rendered) renderNow();
    },
    // Open the card programmatically (and render it) — used by the
    // jump-to-moment chips to reveal the transcript before scrolling to it.
    expand: () => setExpanded(true),
    // Content changed underneath (e.g. a WS update landed) -- re-render now
    // if the card is already open, otherwise just drop the stale cache so
    // the next expand picks up the fresh content instead of the old one.
    markStale: () => {
      rendered = false;
      if (!body.hidden) renderNow();
    },
  };
}

function renderTranscriptLines(container, transcriptText) {
  container.innerHTML = "";
  const fragment = document.createDocumentFragment();
  transcriptText.split("\n").filter(Boolean).forEach((line) => {
    const match = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/);
    const lineEl = document.createElement("div");
    lineEl.className = "transcript-line";
    if (match) {
      // data-seconds lets a key-point/chapter chip find the nearest line at or
      // before a target time to scroll to (see jumpToTranscriptSeconds).
      const seconds = timestampToSeconds(match[1]);
      if (seconds !== null) lineEl.dataset.seconds = String(seconds);
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

function applyTranscriptState(tracked) {
  const { row, entry } = tracked;
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
  // Surface the "you pay your own provider" disclosure inline, up front, next
  // to whichever AI action is currently offered — so the cost isn't a surprise
  // that only appears in the confirm modal after the click.
  row.querySelector(".transcript-cost-note").hidden = transcribeBtn.hidden && summarizeBtn.hidden;
  // Restore the resting labels — a button may be re-shown after a failed run
  // that had swapped in the transient "…" label on click.
  transcribeBtn.querySelector("span").textContent = "Transcribe";
  summarizeBtn.querySelector("span").textContent = "Summarize";

  transcriptProgress.hidden = transcriptStatus !== "running";
  summaryProgress.hidden = summaryStatus !== "running";

  transcriptError.hidden = transcriptStatus !== "error";
  transcriptError.textContent = transcriptStatus === "error" ? entry.transcript_error || "Transcription failed" : "";
  summaryError.hidden = summaryStatus !== "error";
  summaryError.textContent = summaryStatus === "error" ? entry.summary_error || "Summarization failed" : "";

  // Card content itself is intentionally NOT rendered here -- only
  // visibility and the at-a-glance word count. makeCollapsibleCard defers
  // the actual markdown/transcript render until the card is expanded (see
  // its comment), so a history full of old summaries stays cheap to load.
  const summaryReady = summaryStatus === "done" && Boolean(entry.summary);
  summaryCard.hidden = !summaryReady;
  // Word count from the human-readable notes text, not the raw JSON string —
  // parseSummary reduces a structured summary to its readable content first.
  row.querySelector(".summary-card .transcript-card__meta").textContent = summaryReady
    ? wordCountLabel(summaryPlainText(entry))
    : "";
  if (summaryReady) tracked.summaryCollapsible.markStale();

  const transcriptReady = transcriptStatus === "done" && Boolean(entry.transcript);
  transcriptCard.hidden = !transcriptReady;
  row.querySelector(".transcript-card--transcript .transcript-card__meta").textContent = transcriptReady
    ? wordCountLabel(entry.transcript)
    : "";
  if (transcriptReady) tracked.transcriptCollapsible.markStale();

  // Export needs a transcript (same condition as the transcript card). Pro-
  // gated here as a UX nicety; the backend still returns 402 for non-Pro.
  row.querySelector(".transcript-export").hidden = !(isPro && transcriptReady);

  // Chat needs a finished transcript too, and is Pro-gated the same way: Pro
  // users get the chat panel, everyone else with a ready transcript gets an
  // upgrade nudge instead (mirrors the Lesson Notes tldr-vs-structured gate).
  row.querySelector(".transcript-chat").hidden = !(isPro && transcriptReady);
  row.querySelector(".chat-upsell").hidden = !(!isPro && transcriptReady);
}

// -- Transcript chat (Pro) --------------------------------------------------
// A small per-row Q&A panel over the finished transcript. The whole
// conversation for a row lives in chatConversations (keyed by entry id); this
// just paints it. User turns are plain text (textContent, so nothing in a
// question can inject markup); assistant turns are LLM markdown rendered
// through the same escaping renderer the summaries use.
function renderChatMessages(tracked) {
  const container = tracked.row.querySelector(".chat-messages");
  const conversation = chatConversations.get(tracked.entry.id) || [];
  container.innerHTML = "";

  if (!conversation.length) {
    const empty = document.createElement("p");
    empty.className = "chat-empty";
    empty.textContent = "Ask a question about this video — answers come from its transcript.";
    container.appendChild(empty);
  } else {
    const fragment = document.createDocumentFragment();
    conversation.forEach((message) => {
      const isUser = message.role === "user";
      const msg = document.createElement("div");
      msg.className = `chat-message chat-message--${isUser ? "user" : "assistant"}`;
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble";
      if (isUser) {
        bubble.textContent = message.content;
      } else {
        bubble.classList.add("markdown-content");
        bubble.innerHTML = renderMarkdown(message.content);
      }
      msg.appendChild(bubble);
      fragment.appendChild(msg);
    });
    container.appendChild(fragment);
  }
  // Keep the newest turn in view.
  container.scrollTop = container.scrollHeight;
}

function setChatThinking(tracked, thinking) {
  const indicator = tracked.row.querySelector(".chat-thinking");
  indicator.hidden = !thinking;
}

// One chat turn: POST the new question plus the full prior conversation, then
// append both the question and the answer to this row's local state. Matches
// the response.ok / body.detail / showToast error shape used elsewhere; a 402
// (shouldn't happen behind the Pro gate) falls back to the upgrade nudge copy.
async function sendChatMessage(tracked) {
  const { row } = tracked;
  const input = row.querySelector(".chat-input");
  const sendBtn = row.querySelector(".chat-send-btn");
  const question = input.value.trim();
  if (!question) return;

  const historyId = tracked.entry.id;
  const conversation = chatConversations.get(historyId) || [];
  chatConversations.set(historyId, conversation);
  // The `history` we send is the conversation BEFORE this turn's question.
  const priorHistory = conversation.map((m) => ({ role: m.role, content: m.content }));

  // Show the question immediately, then wait on the answer.
  conversation.push({ role: "user", content: question });
  input.value = "";
  renderChatMessages(tracked);
  setChatThinking(tracked, true);
  input.disabled = true;
  sendBtn.disabled = true;

  try {
    const response = await fetch(`${API_BASE}/history/${historyId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: priorHistory }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const fallback =
        response.status === 402
          ? "Chatting with your transcript is a CLIP.PULL Pro feature — upgrade to unlock it."
          : "Couldn't get an answer for that question.";
      showToast(body?.detail || fallback, "error");
      // Roll the optimistic question back out and restore it to the input so
      // the user doesn't lose what they typed and a retry doesn't double it.
      conversation.pop();
      input.value = question;
      renderChatMessages(tracked);
      return;
    }
    const body = await response.json();
    conversation.push({ role: "assistant", content: body.answer || "" });
    renderChatMessages(tracked);
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
    conversation.pop();
    input.value = question;
    renderChatMessages(tracked);
  } finally {
    setChatThinking(tracked, false);
    input.disabled = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

// -- Course Workspace (Pro) -------------------------------------------------
// A folder of 2+ downloaded lessons is treated as one course. GET /courses
// lists them; each gets a banner inserted above its first History row, and Pro
// users can open an inline workspace (search + chat + study-guide digest) that
// talks to POST /courses/chat and POST /courses/digest. A course's identity is
// just its shared output-folder path — no new entity. Everything here mirrors
// the single-video transcript-chat plumbing, keyed by folder instead of id.

// Normalize a folder/file path for comparison: forward slashes, no trailing
// slash, lower-cased (Windows paths are case-insensitive). Lets us match a
// History row to the course folder it lives in.
function normalizeFolderPath(path) {
  return String(path || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

// The parent folder of a downloaded file's output_path, normalized.
function entryFolderPath(outputPath) {
  const norm = String(outputPath || "").replace(/\\/g, "/").replace(/\/+$/, "");
  const idx = norm.lastIndexOf("/");
  return normalizeFolderPath(idx >= 0 ? norm.slice(0, idx) : "");
}

function entryBelongsToCourse(entry, folder) {
  if (!entry || !entry.output_path) return false;
  return entryFolderPath(entry.output_path) === normalizeFolderPath(folder);
}

// Cost-disclosure confirm before the first course-level AI action per session.
// After one confirm, the flag short-circuits so later actions run straight
// through — matching the design doc's "before the first ... per session".
async function ensureCourseBillingConfirmed() {
  if (courseBillingConfirmed) return true;
  const confirmed = await showConfirmModal({
    title: "Use your AI provider for this course?",
    message:
      `Course chat, search, and study guides ${billedKeyClause(summarizationProvider)} and are ` +
      "billed per use on your account. They draw on every summarized lesson in the course.",
    confirmLabel: "Continue",
    cancelLabel: "Cancel",
  });
  if (confirmed) courseBillingConfirmed = true;
  return confirmed;
}

// Repaint the course banners. Idempotent: clears any existing banners first,
// then for each course, actually CLUSTERS every one of its lessons into one
// contiguous block (not just a banner in front of the first match) -- the
// rest of the list stays in its normal newest-first order, but a course's
// own rows are pulled together under one banner instead of staying scattered
// wherever they'd otherwise fall by download date. Reads the cached
// `courses` + `isPro`, so it's safe to call after loadHistory (rows
// changed), loadCourses (list changed), or loadProStatus (lock state
// changed). Courses with fewer than 2 currently-rendered rows (the rest
// filtered/searched out, or not loaded yet) are simply skipped for this pass.
function renderCourseBanners() {
  // A repaint (called on every loadCourses -- including the WS-driven
  // refreshes this feature's own "Summarize remaining" nudge triggers)
  // rebuilds every banner from scratch, which would otherwise silently
  // collapse an open workspace out from under a user mid-chat. Capture
  // which course folders are currently expanded first, then re-expand
  // them after rebuilding -- the conversation itself already survives in
  // courseChatConversations, this just restores the open/closed state.
  const openFolders = new Set();
  historyList.querySelectorAll(".course-banner").forEach((el) => {
    const workspace = el.querySelector(".course-workspace");
    if (workspace && !workspace.hidden && el.dataset.courseFolder) {
      openFolders.add(el.dataset.courseFolder);
    }
    el.remove();
  });
  if (Array.isArray(courses) && courses.length) {
    courses.forEach((course) => {
      // Backend only returns folders with 2+ lessons; guard defensively anyway.
      if (!course || (course.lesson_count || 0) < 2) return;
      // transcriptRows iterates in the same newest-first order rows were
      // appended in loadHistory, which (before any clustering below) is
      // still the current DOM order -- so this naturally collects a
      // course's rows newest-first, matching the rest of the list.
      const matchingRows = [];
      for (const tracked of transcriptRows.values()) {
        // Only target rows still attached to the list: a single-entry
        // delete removes the row from the DOM but leaves it in
        // transcriptRows, and insertBefore on a detached node would throw.
        if (tracked.row.parentNode === historyList && entryBelongsToCourse(tracked.entry, course.folder)) {
          matchingRows.push(tracked.row);
        }
      }
      if (matchingRows.length < 2) return;
      const [firstRow, ...restRows] = matchingRows;
      const banner = renderCourseBanner(course);
      historyList.insertBefore(banner, firstRow);
      // Pull every other lesson in this course up to sit immediately after
      // the first, in the same relative (newest-first) order they already
      // had -- this is what actually groups the course into one visual
      // block instead of leaving a banner over just one scattered row.
      let insertAfter = firstRow;
      restRows.forEach((row) => {
        historyList.insertBefore(row, insertAfter.nextSibling);
        insertAfter = row;
      });
      if (openFolders.has(course.folder)) {
        reopenCourseWorkspace(banner);
      }
    });
  }
  // Clustering can strand a date heading with no rows left directly under
  // it (its rows got pulled into a course block elsewhere) -- drop any
  // heading immediately followed by another heading, the load-more
  // sentinel, or nothing.
  historyList.querySelectorAll(".history-date-heading").forEach((heading) => {
    const next = heading.nextElementSibling;
    if (
      !next ||
      next.classList.contains("history-date-heading") ||
      next.classList.contains("history-load-more-sentinel")
    ) {
      heading.remove();
    }
  });
}

// Re-expands a freshly-rebuilt banner's workspace to match its pre-repaint
// open state (see renderCourseBanners). Mirrors the Open button's own
// click handler rather than duplicating its logic inline.
function reopenCourseWorkspace(banner) {
  const openBtn = banner.querySelector(".course-banner__open");
  if (openBtn && !banner.classList.contains("is-locked")) {
    openBtn.click();
  }
}

function renderCourseBanner(course) {
  const lessonCount = course.lesson_count || 0;
  const readyCount = course.ready_count || 0;
  const remaining = Math.max(0, lessonCount - readyCount);

  const li = document.createElement("li");
  li.className = "course-banner";
  li.dataset.courseFolder = course.folder;
  li.innerHTML = `
    <div class="course-banner__header">
      <span class="course-banner__icon" aria-hidden="true">📚</span>
      <div class="course-banner__info">
        <span class="course-banner__name"></span>
        <span class="course-banner__counts"></span>
      </div>
      <span class="course-banner__lock" hidden>Pro</span>
      <button class="course-banner__open action-chip action-chip--accent" type="button" aria-expanded="false">${SPARKLES_ICON}<span>Open</span></button>
    </div>
    <div class="course-workspace" hidden>
      <div class="course-nudge" hidden>
        <span class="course-nudge__text"></span>
        <button class="course-nudge__btn action-chip" type="button">${SPARKLES_ICON}<span>Summarize remaining</span></button>
      </div>
      <div class="course-search">
        <span class="course-search__label">Search this course</span>
        <form class="course-search__form">
          <input class="course-search__input text-input" type="text" placeholder="Which lesson covers…?" aria-label="Search this course" autocomplete="off" />
          <button class="course-search__btn action-chip" type="submit">${SEARCH_ICON}<span>Search</span></button>
        </form>
        <div class="course-search__result markdown-content" hidden></div>
      </div>
      <div class="course-chat">
        <span class="course-chat__label">Chat about this course</span>
        <div class="chat-panel course-chat__panel">
          <div class="chat-messages course-chat__messages"></div>
          <div class="chat-thinking course-chat__thinking" hidden>
            <span class="chat-thinking__dots" aria-hidden="true"><span></span><span></span><span></span></span>
            <span>Thinking…</span>
          </div>
          <form class="chat-input-row course-chat__form">
            <input class="chat-input text-input course-chat__input" type="text" placeholder="Ask about this course…" aria-label="Ask a question about this course" autocomplete="off" />
            <button class="chat-send-btn btn btn--primary course-chat__send" type="submit">Send</button>
          </form>
        </div>
      </div>
      <div class="course-actions">
        <button class="course-digest-btn action-chip" type="button">${DOCUMENT_ICON}<span>Generate study guide</span></button>
      </div>
    </div>
  `;

  const lessonWord = lessonCount === 1 ? "lesson" : "lessons";
  li.querySelector(".course-banner__name").textContent = course.name || "Course";
  li.querySelector(".course-banner__counts").textContent =
    `${lessonCount} ${lessonWord} · ${readyCount} summarized`;

  const openBtn = li.querySelector(".course-banner__open");
  const workspace = li.querySelector(".course-workspace");

  // Pro gating lives at the banner level: non-Pro sees the lock badge + dimmed
  // Open, and clicking routes to the upgrade page instead of opening. The panel
  // (and every control in it) is only reachable by Pro users, so nothing inside
  // is re-gated. The backend independently 402s regardless.
  li.classList.toggle("is-locked", !isPro);
  li.querySelector(".course-banner__lock").hidden = isPro;

  if (remaining > 0) {
    const nudge = li.querySelector(".course-nudge");
    nudge.hidden = false;
    li.querySelector(".course-nudge__text").textContent =
      `${remaining} ${remaining === 1 ? "lesson isn't" : "lessons aren't"} summarized yet.`;
    const nudgeBtn = li.querySelector(".course-nudge__btn");
    nudgeBtn.addEventListener("click", () => summarizeRemainingLessons(nudgeBtn));
  }

  openBtn.addEventListener("click", () => {
    if (!isPro) {
      window.api.openExternal(GUMROAD_PRO_URL);
      return;
    }
    const nowOpen = workspace.hidden;
    workspace.hidden = !nowOpen;
    openBtn.setAttribute("aria-expanded", String(nowOpen));
    openBtn.querySelector("span").textContent = nowOpen ? "Close" : "Open";
    if (nowOpen) {
      renderCourseChatMessages(course.folder, workspace);
      workspace.querySelector(".course-search__input").focus();
    }
  });

  li.querySelector(".course-search__form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitCourseSearch(course, workspace);
  });
  li.querySelector(".course-chat__form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendCourseChatMessage(course.folder, workspace);
  });
  li.querySelector(".course-digest-btn").addEventListener("click", (event) => {
    generateCourseDigest(course, event.currentTarget);
  });

  return li;
}

// Search is a stateless, one-shot mode of course chat: empty history every
// time, mode "search". The backend pre-formats the answer as a bulleted list,
// so it renders through the same markdown renderer the summaries use.
async function submitCourseSearch(course, workspace) {
  const input = workspace.querySelector(".course-search__input");
  const btn = workspace.querySelector(".course-search__btn");
  const label = btn.querySelector("span");
  const result = workspace.querySelector(".course-search__result");
  const query = input.value.trim();
  if (!query) return;
  if (!(await ensureCourseBillingConfirmed())) return;

  const originalLabel = label.textContent;
  input.disabled = true;
  btn.disabled = true;
  label.textContent = "Searching…";
  result.hidden = false;
  result.classList.add("course-search__result--loading");
  result.textContent = "Searching this course…";
  try {
    const response = await fetch(`${API_BASE}/courses/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: course.folder, question: query, history: [], mode: "search" }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const fallback =
        response.status === 402
          ? "Course Workspace is a CLIP.PULL Pro feature — upgrade to unlock it."
          : "Couldn't search this course.";
      showToast(body?.detail || fallback, "error");
      result.hidden = true;
      return;
    }
    const body = await response.json();
    result.classList.remove("course-search__result--loading");
    result.innerHTML = renderMarkdown(body.answer || "");
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
    result.hidden = true;
  } finally {
    result.classList.remove("course-search__result--loading");
    input.disabled = false;
    btn.disabled = false;
    label.textContent = originalLabel;
  }
}

// Paints the course chat conversation (kept in courseChatConversations, keyed
// by folder). Same escaping rules as single-video chat: user turns are
// textContent, assistant turns are markdown-rendered.
function renderCourseChatMessages(folder, workspace) {
  const container = workspace.querySelector(".course-chat__messages");
  const conversation = courseChatConversations.get(folder) || [];
  container.innerHTML = "";

  if (!conversation.length) {
    const empty = document.createElement("p");
    empty.className = "chat-empty";
    empty.textContent = "Ask a question about this course — answers draw on every summarized lesson.";
    container.appendChild(empty);
  } else {
    const fragment = document.createDocumentFragment();
    conversation.forEach((message) => {
      const isUser = message.role === "user";
      const msg = document.createElement("div");
      msg.className = `chat-message chat-message--${isUser ? "user" : "assistant"}`;
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble";
      if (isUser) {
        bubble.textContent = message.content;
      } else {
        bubble.classList.add("markdown-content");
        bubble.innerHTML = renderMarkdown(message.content);
      }
      msg.appendChild(bubble);
      fragment.appendChild(msg);
    });
    container.appendChild(fragment);
  }
  container.scrollTop = container.scrollHeight;
}

function setCourseChatThinking(workspace, thinking) {
  workspace.querySelector(".course-chat__thinking").hidden = !thinking;
}

// One course-chat turn: POST the new question plus the running history, then
// append the question and answer to this folder's local state. Mirrors
// sendChatMessage's optimistic-then-rollback shape exactly, keyed by folder.
async function sendCourseChatMessage(folder, workspace) {
  const input = workspace.querySelector(".course-chat__input");
  const sendBtn = workspace.querySelector(".course-chat__send");
  const question = input.value.trim();
  if (!question) return;
  if (!(await ensureCourseBillingConfirmed())) return;

  const conversation = courseChatConversations.get(folder) || [];
  courseChatConversations.set(folder, conversation);
  // The `history` we send is the conversation BEFORE this turn's question.
  const priorHistory = conversation.map((m) => ({ role: m.role, content: m.content }));

  conversation.push({ role: "user", content: question });
  input.value = "";
  renderCourseChatMessages(folder, workspace);
  setCourseChatThinking(workspace, true);
  input.disabled = true;
  sendBtn.disabled = true;

  try {
    const response = await fetch(`${API_BASE}/courses/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, question, history: priorHistory, mode: "chat" }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const fallback =
        response.status === 402
          ? "Course Workspace is a CLIP.PULL Pro feature — upgrade to unlock it."
          : "Couldn't get an answer for that question.";
      showToast(body?.detail || fallback, "error");
      // Roll the optimistic question back out and restore it to the input.
      conversation.pop();
      input.value = question;
      renderCourseChatMessages(folder, workspace);
      return;
    }
    const body = await response.json();
    conversation.push({ role: "assistant", content: body.answer || "" });
    renderCourseChatMessages(folder, workspace);
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
    conversation.pop();
    input.value = question;
    renderCourseChatMessages(folder, workspace);
  } finally {
    setCourseChatThinking(workspace, false);
    input.disabled = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

// Generate the course study guide: confirm (its own tailored billing copy),
// POST /courses/digest, then reveal the returned file. A confirmed digest also
// satisfies the per-session cost disclosure, so a later chat won't re-prompt.
async function generateCourseDigest(course, button) {
  const confirmed = await showConfirmModal({
    title: "Generate study guide?",
    message:
      `This builds a single study guide from every summarized lesson in "${course.name}". It ` +
      `${billedKeyClause(summarizationProvider)} and is billed per use on your account.`,
    confirmLabel: "Generate",
    cancelLabel: "Cancel",
  });
  if (!confirmed) return;
  courseBillingConfirmed = true;

  const label = button.querySelector("span");
  const originalLabel = label.textContent;
  button.disabled = true;
  label.textContent = "Generating…";
  try {
    const response = await fetch(`${API_BASE}/courses/digest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: course.folder }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const fallback =
        response.status === 402
          ? "Course Workspace is a CLIP.PULL Pro feature — upgrade to unlock it."
          : "Couldn't generate the study guide.";
      showToast(body?.detail || fallback, "error");
      return;
    }
    const body = await response.json();
    showToast("Study guide ready — showing it in your folder.", "success");
    if (body.path) window.api.revealFile(body.path);
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    button.disabled = false;
    label.textContent = originalLabel;
  }
}

// "Summarize remaining lessons" nudge: reuses the SAME app-wide batch-process
// action as the History "Transcribe all" button (there's no folder-scoped
// batch parameter in the API for v1), so it transcribes every not-yet-
// transcribed download and summarizes each. The confirm copy is honest that it
// runs library-wide, not just this course.
async function summarizeRemainingLessons(button) {
  const confirmed = await showConfirmModal({
    title: "Summarize remaining lessons?",
    message:
      "This transcribes every downloaded video that doesn't have a transcript yet, then summarizes each one. " +
      `It ${billedKeyClause(transcriptionProvider)} and is billed per use on your account. ` +
      "It runs across your whole library, not only this course.",
    confirmLabel: "Summarize",
    cancelLabel: "Cancel",
  });
  if (!confirmed) return;
  await postBatchProcess(true, button, button.querySelector("span"));
}

async function loadCourses() {
  try {
    const response = await fetch(`${API_BASE}/courses`);
    if (!response.ok) throw new Error(`courses fetch failed: ${response.status}`);
    const body = await response.json();
    courses = Array.isArray(body.courses) ? body.courses : [];
  } catch (error) {
    // Best-effort, same lightweight degradation as loadProStatus: on failure
    // just show no course banners rather than blocking the History view.
    console.warn("Couldn't load courses:", error);
    courses = [];
  }
  renderCourseBanners();
}

// Debounced course-list refresh: course membership/ready_count change on new
// downloads and on completed summaries, which arrive as bursty WS events.
let courseRefreshTimer;
function scheduleCourseRefresh() {
  clearTimeout(courseRefreshTimer);
  courseRefreshTimer = setTimeout(loadCourses, 200);
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
      <p class="transcript-cost-note" hidden>Uses your own API key — billed per use by your provider, not by CLIP.PULL.</p>

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
        <div class="transcript-card__header transcript-card__header--toggle" role="button" tabindex="0" aria-expanded="false">
          <span class="transcript-card__icon transcript-card__icon--accent">${SPARKLES_ICON}</span>
          <span class="transcript-card__title">Summary</span>
          <span class="ai-badge">AI-generated</span>
          <span class="transcript-card__meta"></span>
          <button class="icon-btn copy-summary-btn" type="button" aria-label="Copy summary" title="Copy summary">${COPY_ICON}</button>
          ${CHEVRON_ICON}
        </div>
        <div class="transcript-card__body transcript-card__body--collapsible" hidden>
          <div class="summary-content markdown-content"></div>
        </div>
      </div>

      <div class="transcript-card transcript-card--transcript" hidden>
        <div class="transcript-card__header transcript-card__header--toggle" role="button" tabindex="0" aria-expanded="false">
          <span class="transcript-card__icon">${DOCUMENT_ICON}</span>
          <span class="transcript-card__title">Transcript</span>
          <span class="transcript-card__meta"></span>
          <button class="icon-btn copy-transcript-btn" type="button" aria-label="Copy transcript" title="Copy transcript">${COPY_ICON}</button>
          ${CHEVRON_ICON}
        </div>
        <div class="transcript-card__body transcript-card__body--collapsible" hidden>
          <div class="transcript-lines"></div>
        </div>
      </div>

      <div class="transcript-export" hidden>
        <span class="transcript-export__label">Export notes</span>
        <div class="transcript-export__buttons">
          <button class="export-btn action-chip" type="button" data-format="srt">${DOWNLOAD_ICON}<span>SRT</span></button>
          <button class="export-btn action-chip" type="button" data-format="txt">${DOWNLOAD_ICON}<span>TXT</span></button>
          <button class="export-btn action-chip" type="button" data-format="md">${DOWNLOAD_ICON}<span>MD</span></button>
        </div>
      </div>

      <div class="transcript-chat" hidden>
        <button class="chat-toggle action-chip action-chip--accent" type="button" aria-expanded="false">${CHAT_ICON}<span>Chat with transcript</span></button>
        <div class="chat-panel" hidden>
          <div class="chat-messages"></div>
          <div class="chat-thinking" hidden>
            <span class="chat-thinking__dots" aria-hidden="true"><span></span><span></span><span></span></span>
            <span>Thinking…</span>
          </div>
          <form class="chat-input-row">
            <input class="chat-input text-input" type="text" placeholder="Ask about this video…" aria-label="Ask a question about this video" autocomplete="off" />
            <button class="chat-send-btn btn btn--primary" type="submit">Send</button>
          </form>
        </div>
      </div>
      <button class="chat-upsell summary-upsell" type="button" hidden>${SPARKLES_ICON}<span>Upgrade to Pro to chat with this video's transcript</span></button>
    </div>
  `;

  const tracked = { row, entry };
  transcriptRows.set(entry.id, tracked);

  const titleEl = row.querySelector(".history-row__title");
  const titleText = entry.title || entry.url;
  titleEl.textContent = titleText;
  titleEl.title = titleText;
  titleEl.classList.toggle("history-row__title--error", isError);
  titleEl.classList.toggle("history-row__title--clickable", Boolean(entry.output_path));
  if (entry.output_path) {
    titleEl.addEventListener("click", () => window.api.revealFile(entry.output_path));
  }

  const metaEl = row.querySelector(".history-row__meta");
  metaEl.classList.toggle("history-row__meta--wrap", isError);
  if (isError) {
    const metaText = entry.error_reason || "Download failed";
    metaEl.textContent = metaText;
    metaEl.title = "";
  } else {
    const parts = [];
    if (entry.total_size) parts.push(entry.total_size);
    const time = formatTime(parseFinishedDate(entry.finished_at));
    if (time) parts.push(time);
    const metaText = parts.length ? parts.join(" · ") : entry.url;
    metaEl.textContent = metaText;
    metaEl.title = metaText;
  }

  const retryBtn = row.querySelector(".retry-btn");
  if (isError) {
    retryBtn.hidden = false;
    retryBtn.addEventListener("click", () => retryFromHistory(entry, retryBtn, row));
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

  tracked.summaryCollapsible = makeCollapsibleCard(row.querySelector(".summary-card"), () => {
    renderSummarySection(row.querySelector(".summary-content"), tracked);
  });
  tracked.transcriptCollapsible = makeCollapsibleCard(row.querySelector(".transcript-card--transcript"), () => {
    renderTranscriptLines(row.querySelector(".transcript-lines"), tracked.entry.transcript || "");
  });

  row.querySelectorAll(".export-btn").forEach((btn) => {
    btn.addEventListener("click", () => exportNotes(tracked.entry, btn));
  });

  row.querySelector(".copy-summary-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    // Copied from the entry (not the DOM) so the upsell nudge never lands in
    // the clipboard and the copy works whether or not the card is expanded.
    copyToClipboard(summaryPlainText(tracked.entry), "Summary");
  });
  row.querySelector(".copy-transcript-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    tracked.transcriptCollapsible.ensureRendered();
    copyToClipboard(row.querySelector(".transcript-lines").textContent, "Transcript");
  });

  // Chat: the toggle reveals the panel (and paints any conversation already
  // held for this row); the form's submit doubles as Enter-to-send.
  const chatToggle = row.querySelector(".chat-toggle");
  const chatPanel = row.querySelector(".chat-panel");
  chatToggle.addEventListener("click", () => {
    const nowOpen = chatPanel.hidden;
    chatPanel.hidden = !nowOpen;
    chatToggle.setAttribute("aria-expanded", String(nowOpen));
    chatToggle.querySelector("span").textContent = nowOpen ? "Hide chat" : "Chat with transcript";
    if (nowOpen) {
      renderChatMessages(tracked);
      row.querySelector(".chat-input").focus();
    }
  });
  row.querySelector(".chat-input-row").addEventListener("submit", (event) => {
    event.preventDefault();
    sendChatMessage(tracked);
  });
  row.querySelector(".chat-upsell").addEventListener("click", () => window.api.openExternal(GUMROAD_PRO_URL));

  applyTranscriptState(tracked);

  return row;
}

// Builds a fragment of date heading(s) + rows for one page of entries,
// continuing lastRenderedDateGroup across page boundaries so a heading is
// never repeated or skipped depending on where a given page happens to start.
function renderEntriesChunk(entries) {
  const fragment = document.createDocumentFragment();
  entries.forEach((entry) => {
    const group = dateGroupLabel(parseFinishedDate(entry.finished_at));
    if (group !== lastRenderedDateGroup) {
      fragment.appendChild(renderDateHeading(group));
      lastRenderedDateGroup = group;
    }
    fragment.appendChild(renderHistoryRow(entry));
  });
  return fragment;
}

// Sentinel <li> kept as the last child of #history-list while entries remain
// unrendered — an IntersectionObserver with a generous rootMargin reveals
// the next page well before the user actually reaches the bottom, so
// scrolling never visibly outruns rendering.
function setupLoadMoreSentinel() {
  loadMoreSentinel = document.createElement("li");
  loadMoreSentinel.className = "history-load-more-sentinel";
  loadMoreSentinel.setAttribute("aria-hidden", "true");
  historyList.appendChild(loadMoreSentinel);
  historyLoadMoreObserver = new IntersectionObserver(
    (observedEntries) => {
      if (observedEntries.some((e) => e.isIntersecting)) revealNextHistoryPage();
    },
    { rootMargin: "800px 0px" }
  );
  historyLoadMoreObserver.observe(loadMoreSentinel);
}

function revealNextHistoryPage() {
  if (!pendingHistoryEntries.length) return;
  const nextPage = pendingHistoryEntries.slice(0, HISTORY_PAGE_SIZE);
  pendingHistoryEntries = pendingHistoryEntries.slice(HISTORY_PAGE_SIZE);
  historyList.insertBefore(renderEntriesChunk(nextPage), loadMoreSentinel);
  // Newly-mounted rows may belong to an already-rendered course — repaint
  // banners so they get clustered in too (see renderCourseBanners' own
  // idempotency note).
  renderCourseBanners();
  if (!pendingHistoryEntries.length) teardownLoadMoreObserver();
}

function teardownLoadMoreObserver() {
  if (historyLoadMoreObserver) {
    historyLoadMoreObserver.disconnect();
    historyLoadMoreObserver = null;
  }
  if (loadMoreSentinel) {
    loadMoreSentinel.remove();
    loadMoreSentinel = null;
  }
  pendingHistoryEntries = [];
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
    teardownLoadMoreObserver();
    lastRenderedDateGroup = null;
    totalHistoryCount = body.entries.length;
    // A search/filter that matches nothing needs its own copy — an empty list
    // otherwise falls back to #history-list:empty::before ("no downloads yet"),
    // which is misleading when downloads exist but none match. Rendering a real
    // placeholder element also stops that :empty rule from firing.
    if (body.entries.length === 0 && (searchInput.value || statusFilter.value)) {
      const li = document.createElement("li");
      li.className = "list-empty";
      li.textContent = "No downloads match your current search or filter.";
      historyList.appendChild(li);
      updateSummaryCount();
      return true;
    }
    const firstPage = body.entries.slice(0, HISTORY_PAGE_SIZE);
    pendingHistoryEntries = body.entries.slice(HISTORY_PAGE_SIZE);
    historyList.appendChild(renderEntriesChunk(firstPage));
    if (pendingHistoryEntries.length) setupLoadMoreSentinel();
    updateSummaryCount();
    // Rows were just rebuilt, so re-insert the course banners above them using
    // the cached course list (loadCourses() refreshes that list separately).
    renderCourseBanners();
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
  const confirmed = await showConfirmModal({
    title: scoped ? "Clear these entries?" : "Clear all history?",
    message,
    confirmLabel: "Clear",
    cancelLabel: "Cancel",
    tone: "danger",
  });
  if (!confirmed) return;

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
  loadCourses();
  showToast(ok ? "History refreshed" : "Failed to refresh history", ok ? "success" : "error");
  refreshBtn.classList.remove("is-spinning");
  refreshBtn.disabled = false;
});

// Re-queues every entry currently sitting in History with status "error" in
// one pass, reusing the exact same /queue + retry_of_history_id call the
// single-row Retry button already makes -- fetched fresh from the server
// (not scraped from whatever's currently rendered/filtered) so this always
// acts on the true, complete set of failed downloads. Mirrors the single-row
// retry's own choices: one shared output folder resolved once (not
// per-entry), stays on the History tab, and best-effort removes each
// retried entry's now-stale row instead of waiting for it to update in place.
retryFailedBtn.addEventListener("click", async () => {
  retryFailedBtn.disabled = true;
  const originalLabel = retryFailedBtn.textContent;
  try {
    const response = await fetch(`${API_BASE}/history?status=error&limit=500`);
    if (!response.ok) {
      showToast("Couldn't check for failed downloads.", "error");
      return;
    }
    const body = await response.json();
    const failedEntries = body.entries || [];
    if (!failedEntries.length) {
      showToast("No failed downloads to retry.", "info");
      return;
    }

    const confirmed = await showConfirmModal({
      title: "Retry all failed downloads?",
      message:
        `This will re-queue ${failedEntries.length} failed ` +
        `${failedEntries.length === 1 ? "download" : "downloads"}. ` +
        "You'll be asked to pick an output folder if you don't have a default one set.",
      confirmLabel: "Retry all",
      cancelLabel: "Cancel",
    });
    if (!confirmed) return;

    const outputFolder = await resolveOutputFolder();
    if (!outputFolder) return;

    retryFailedBtn.textContent = "Retrying…";
    let succeeded = 0;
    let failed = 0;
    for (const entry of failedEntries) {
      try {
        const retryResponse = await fetch(`${API_BASE}/queue`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            urls_text: entry.url,
            output_folder: outputFolder,
            retry_of_history_id: entry.id,
          }),
        });
        if (!retryResponse.ok) {
          failed += 1;
          continue;
        }
        succeeded += 1;
        try {
          await fetch(`${API_BASE}/history/${entry.id}`, { method: "DELETE" });
        } catch {
          // Best-effort, same as the single-row retry: harmless if this
          // fails, the row just lingers until the retry updates it in place.
        }
      } catch {
        failed += 1;
      }
    }

    if (succeeded) {
      showToast(
        failed
          ? `Queued ${succeeded} for retry, ${failed} couldn't be queued — check the Queue tab.`
          : `Queued ${succeeded} ${succeeded === 1 ? "download" : "downloads"} for retry — check the Queue tab.`,
        failed ? "warning" : "success"
      );
    } else {
      showToast("Couldn't queue any retries.", "error");
    }
    await loadHistory();
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    retryFailedBtn.textContent = originalLabel;
    retryFailedBtn.disabled = false;
  }
});

// -- Batch "transcribe all eligible" ----------------------------------------
// One all-eligible action (no per-row multi-select for this phase): the server
// decides which downloaded-but-not-transcribed entries to start when entry_ids
// is null. Pro-gated in the UI like the rest of the AI features -- non-Pro sees
// a locked control that routes to the upgrade page instead of running.
function applyBatchProState() {
  batchBtn.classList.toggle("is-locked", !isPro);
  batchSummarizeInput.disabled = !isPro;
  if (batchLock) batchLock.hidden = isPro;
}

// The POST + response handling for the app-wide batch action, shared by the
// "Transcribe all" button here and the Course Workspace "summarize remaining"
// nudge. `button`/`labelEl` are disabled and swapped to "Starting…" for the
// duration; behavior is identical to the button's original inline logic.
async function postBatchProcess(summarize, button, labelEl) {
  const originalLabel = labelEl.textContent;
  button.disabled = true;
  labelEl.textContent = "Starting…";
  try {
    const response = await fetch(`${API_BASE}/history/batch-process`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry_ids: null, summarize }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const fallback =
        response.status === 402
          ? "Batch transcription is a CLIP.PULL Pro feature — upgrade to unlock it."
          : "Couldn't start batch processing.";
      showToast(body?.detail || fallback, "error");
      return;
    }
    const body = await response.json();
    const started = Array.isArray(body.started) ? body.started : [];
    const skipped = Array.isArray(body.skipped) ? body.skipped : [];
    if (started.length === 0) {
      showToast("Nothing to process — everything's already transcribed.", "info");
    } else {
      const noun = started.length === 1 ? "video" : "videos";
      let message = `Started ${summarize ? "processing" : "transcribing"} ${started.length} ${noun}.`;
      if (skipped.length) message += ` Skipped ${skipped.length} that ${skipped.length === 1 ? "wasn't" : "weren't"} eligible.`;
      showToast(message, "success");
    }
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    button.disabled = false;
    labelEl.textContent = originalLabel;
  }
}

batchBtn.addEventListener("click", async () => {
  if (!isPro) {
    window.api.openExternal(GUMROAD_PRO_URL);
    return;
  }
  const summarize = batchSummarizeInput.checked;
  const confirmed = await showConfirmModal({
    title: "Transcribe all eligible videos?",
    message:
      "This starts transcribing every downloaded video that doesn't have a transcript yet" +
      (summarize ? ", then summarizes each one" : "") +
      `. It ${billedKeyClause(transcriptionProvider)} and is billed per use on your account.`,
    confirmLabel: "Transcribe all",
    cancelLabel: "Cancel",
  });
  if (!confirmed) return;

  await postBatchProcess(summarize, batchBtn, batchBtn.querySelector(".history-batch__label"));
});
applyBatchProState();

let debounceTimer;
searchInput.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(loadHistory, 250);
});
statusFilter.addEventListener("change", loadHistory);

// Skeleton placeholders shaped like a real history row (icon circle, title +
// meta lines, action-icon circles), shown synchronously before the very first
// fetch so the view never opens genuinely blank. Only used on the first-ever
// load: subsequent search/filter loads keep the old list visible while the new
// fetch is in flight (stale-while-revalidate in loadHistory), so no skeleton
// churn is added there. Cleared by loadHistory's innerHTML reset on success, or
// explicitly before the retry/empty states below so nothing stacks on them.
function renderHistorySkeletons(count = 4) {
  // Guards against duplicate rows if this is ever called a second time
  // before clearHistorySkeletons runs (not reachable today -- only one
  // call site -- but cheap to make safe against future changes).
  if (historyList.querySelector(".history-row--skeleton")) return;
  const titleWidths = ["68%", "80%", "56%", "72%"];
  const metaWidths = ["40%", "34%", "46%", "38%"];
  const fragment = document.createDocumentFragment();
  for (let i = 0; i < count; i += 1) {
    const li = document.createElement("li");
    li.className = "queue-row history-row history-row--skeleton";
    li.setAttribute("aria-hidden", "true");
    li.innerHTML = `
      <div class="history-row__inner">
        <span class="skeleton skeleton--circle" style="width: 38px; height: 38px"></span>
        <div class="history-row__body">
          <span class="skeleton skeleton--line" style="width: ${titleWidths[i % titleWidths.length]}"></span>
          <span class="skeleton skeleton--line" style="width: ${metaWidths[i % metaWidths.length]}; margin-top: 8px"></span>
        </div>
        <div class="history-row__actions">
          <span class="skeleton skeleton--circle" style="width: 32px; height: 32px"></span>
          <span class="skeleton skeleton--circle" style="width: 32px; height: 32px"></span>
          <span class="skeleton skeleton--circle" style="width: 32px; height: 32px"></span>
        </div>
      </div>
    `;
    fragment.appendChild(li);
  }
  historyList.appendChild(fragment);
}

function clearHistorySkeletons() {
  historyList.querySelectorAll(".history-row--skeleton").forEach((el) => el.remove());
}

// Persistent inline fallback so an exhausted startup load doesn't leave the
// list looking like an empty history — a text line plus a Retry button. A
// successful loadHistory() clears the list (and this element) on its own.
function showHistoryLoadError() {
  if (historyList.querySelector(".load-error")) return;
  // The skeletons were the "still loading" state; now that we've given up,
  // clear them so the retry affordance isn't stacked on top of them.
  clearHistorySkeletons();
  const li = document.createElement("li");
  li.className = "load-error";
  li.innerHTML = `
    <span class="load-error__msg">Couldn't load your download history.</span>
    <button class="btn btn--ghost load-error__retry" type="button">Retry</button>
  `;
  li.querySelector(".load-error__retry").addEventListener("click", () => {
    li.remove();
    loadHistoryOnStartup();
  });
  historyList.appendChild(li);
}

// The packaged backend is a PyInstaller onefile executable whose cold-start
// can outlast main.js's waitForBackend budget (see the matching comment in
// renderer.js's prefillDefaultOutputFolder) -- a single failed attempt right
// as this script loads would otherwise leave History looking like "no
// history yet" instead of "failed to load," for the rest of the session.
async function loadHistoryOnStartup(retriesLeft = 10) {
  const ok = await loadHistory();
  if (ok) return;
  if (retriesLeft > 0) {
    setTimeout(() => loadHistoryOnStartup(retriesLeft - 1), 500);
  } else {
    showToast(
      "Couldn't load your download history — check that CLIP.PULL's backend is running, then try again.",
      "error"
    );
    showHistoryLoadError();
  }
}

// Paint skeleton rows first so the very first open shows shaped placeholders
// instead of a blank list while the initial fetch is in flight.
renderHistorySkeletons();
loadHistoryOnStartup();
// Best-effort, fire-and-forget: both only drive UI niceties (the Pro-gated
// Lesson Notes display and the provider name in the confirm dialogs), so a
// failure degrades gracefully rather than blocking the History view.
loadProStatus();
loadProviderSettings();
// Best-effort course list for the Course Workspace banners; renders whatever
// rows are already present and repaints as history/pro status resolve.
loadCourses();
// Activating/deactivating Pro in Settings fires this so the cached isPro
// flag here doesn't go stale until a full app reload -- see the matching
// comment on renderLicense's dispatch in settings-view.js.
document.addEventListener("clippull:license-changed", loadProStatus);

// Every finished download is pushed here the instant it's recorded — the
// History tab always reflects it live, whether or not it's the active tab.
let historyPushTimer;
connectQueueSocket((event) => {
  if (event.type === "transcript_update" || event.type === "summary_update") {
    const tracked = transcriptRows.get(event.history_id);
    if (!tracked) {
      // Row isn't currently mounted — either a different filter/search, or
      // (now that History pages in lazily) still sitting in
      // pendingHistoryEntries. Patch the cached entry in place for the
      // latter case, so scrolling to it later renders the live state
      // instead of what the initial fetch saw.
      if (event.entry) {
        const pendingIndex = pendingHistoryEntries.findIndex((e) => e.id === event.history_id);
        if (pendingIndex !== -1) pendingHistoryEntries[pendingIndex] = event.entry;
      }
      return;
    }
    const kind = event.type === "transcript_update" ? "transcript" : "summary";
    if (event.status === "running") {
      setProgress(tracked.row, kind, event.detail, event.percent);
    }
    if (event.entry) {
      // Terminal state (running-start/done/error) -- carries the full row,
      // so patch it directly instead of waiting on a full refetch.
      tracked.entry = event.entry;
      applyTranscriptState(tracked);
      // A finished summary bumps a course's ready_count, so refresh the
      // banners' "N summarized" counts / nudge state.
      if (event.type === "summary_update") scheduleCourseRefresh();
    }
    return;
  }
  if (event.type !== "history_added") return;
  // Briefly coalesces bursts (e.g. a batch finishing within milliseconds of
  // each other) into a single refetch instead of one per entry.
  clearTimeout(historyPushTimer);
  historyPushTimer = setTimeout(loadHistory, 150);
  // A new download can create a course or bump its lesson_count.
  scheduleCourseRefresh();
});
