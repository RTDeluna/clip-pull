import { connectQueueSocket } from "./ws-client.js";

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

function renderRow(entry) {
  let state = rows.get(entry.id);
  if (!state) {
    const el = document.createElement("li");
    el.className = "queue-row";
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
      <button class="retry-btn" hidden>Retry</button>
    `;
    queueList.appendChild(el);
    state = { el, maxPercent: 0, lastStatus: null };
    rows.set(entry.id, state);

    el.querySelector(".retry-btn").addEventListener("click", () => {
      retryEntry(entry.id);
    });
  }
  const row = state.el;

  if (state.lastStatus === "done") summaryCounts.done -= 1;
  if (state.lastStatus === "error") summaryCounts.error -= 1;
  if (entry.status === "done") summaryCounts.done += 1;
  if (entry.status === "error") summaryCounts.error += 1;
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

  row.querySelector(".queue-row__title").textContent = entry.title || entry.url;
  row.querySelector(".queue-row__duplicate-badge").hidden = !entry.previously_downloaded;
  const statusEl = row.querySelector(".queue-row__status");
  statusEl.textContent = statusLabel(entry);
  statusEl.className = "queue-row__status";
  if (entry.status === "done") statusEl.classList.add("queue-row__status--done");
  if (entry.status === "error") statusEl.classList.add("queue-row__status--error");

  row.querySelector(".progress-fill").style.width = `${displayPercent}%`;
  row.querySelector(".queue-row__size").textContent = formatSizeLine(entry, displayPercent);
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

  updateSummary();
}

function updateSummary() {
  const total = rows.size;
  queueSummary.textContent = total
    ? `${summaryCounts.done}/${total} downloaded${summaryCounts.error ? `, ${summaryCounts.error} failed` : ""}`
    : "";
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
    await fetch(`${API_BASE}/queue/${entryId}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ referer: refererInput.value || null }),
    });
  } catch (error) {
    alert("Retry failed: " + error.message);
  }
}

browseBtn.addEventListener("click", async () => {
  const folder = await window.api.chooseFolder();
  if (folder) {
    outputFolderInput.value = folder;
  }
});

startBtn.addEventListener("click", async () => {
  if (!outputFolderInput.value) {
    alert("Choose an output folder first.");
    return;
  }
  startBtn.disabled = true;
  invalidLinesEl.hidden = true;

  try {
    const response = await fetch(`${API_BASE}/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        urls_text: urlsInput.value,
        output_folder: outputFolderInput.value,
        referer: refererInput.value || null,
        subfolder: courseFolderInput.value || null,
      }),
    });
    const body = await response.json();

    if (body.invalid_lines && body.invalid_lines.length > 0) {
      invalidLinesEl.hidden = false;
      invalidLinesEl.textContent = `Skipped invalid lines:\n${body.invalid_lines.join("\n")}`;
    }

    body.entries.forEach(renderRow);
    urlsInput.value = "";
    renderGutter();
  } catch (error) {
    invalidLinesEl.hidden = false;
    invalidLinesEl.textContent = "Failed to reach the backend: " + error.message;
  } finally {
    startBtn.disabled = false;
  }
});

connectQueueSocket((event) => {
  if (event.type === "sync") {
    event.entries.forEach(renderRow);
  } else if (event.type === "update") {
    renderRow(event.entry);
  } else if (event.type === "update_batch") {
    event.entries.forEach(renderRow);
  } else if (event.type === "batch_complete") {
    if (window.Notification && Notification.permission === "granted") {
      new Notification("Batch complete", {
        body: `${event.summary.done} done, ${event.summary.error} failed`,
      });
    } else if (window.Notification && Notification.permission !== "denied") {
      Notification.requestPermission();
    }
  }
});
