import { connectQueueSocket } from "./ws-client.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const urlsInput = document.getElementById("urls");
const invalidLinesEl = document.getElementById("invalid-lines");
const outputFolderInput = document.getElementById("output-folder");
const browseBtn = document.getElementById("browse-btn");
const refererInput = document.getElementById("referer");
const startBtn = document.getElementById("start-btn");
const queueList = document.getElementById("queue-list");
const queueSummary = document.getElementById("queue-summary");

const rows = new Map();

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
  const minutes = Math.floor(eta / 60);
  const seconds = eta % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function renderRow(entry) {
  let row = rows.get(entry.id);
  if (!row) {
    row = document.createElement("li");
    row.className = "queue-row";
    row.innerHTML = `
      <div class="queue-row__top">
        <span class="queue-row__title"></span>
        <span class="queue-row__status"></span>
      </div>
      <div class="progress-track"><div class="progress-fill"></div></div>
      <div class="queue-row__meta">
        <span class="queue-row__speed"></span>
        <span class="queue-row__eta"></span>
      </div>
      <div class="queue-row__error"></div>
      <button class="retry-btn" hidden>Retry</button>
    `;
    queueList.appendChild(row);
    rows.set(entry.id, row);

    row.querySelector(".retry-btn").addEventListener("click", () => {
      retryEntry(entry.id);
    });
  }

  row.querySelector(".queue-row__title").textContent = entry.title || entry.url;
  const statusEl = row.querySelector(".queue-row__status");
  statusEl.textContent = statusLabel(entry);
  statusEl.className = "queue-row__status";
  if (entry.status === "done") statusEl.classList.add("queue-row__status--done");
  if (entry.status === "error") statusEl.classList.add("queue-row__status--error");

  row.querySelector(".progress-fill").style.width = `${entry.percent}%`;
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
  const entries = Array.from(rows.keys()).length;
  const done = Array.from(queueList.querySelectorAll(".queue-row__status--done")).length;
  const failed = Array.from(queueList.querySelectorAll(".queue-row__status--error")).length;
  queueSummary.textContent = entries
    ? `${done}/${entries} downloaded${failed ? `, ${failed} failed` : ""}`
    : "";
}

async function retryEntry(entryId) {
  await fetch(`${API_BASE}/queue/${entryId}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ referer: refererInput.value || null }),
  });
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

  const response = await fetch(`${API_BASE}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      urls_text: urlsInput.value,
      output_folder: outputFolderInput.value,
      referer: refererInput.value || null,
    }),
  });
  const body = await response.json();

  if (body.invalid_lines && body.invalid_lines.length > 0) {
    invalidLinesEl.hidden = false;
    invalidLinesEl.textContent = `Skipped invalid lines:\n${body.invalid_lines.join("\n")}`;
  }

  body.entries.forEach(renderRow);
  urlsInput.value = "";
  startBtn.disabled = false;
});

connectQueueSocket((event) => {
  if (event.type === "sync") {
    event.entries.forEach(renderRow);
  } else if (event.type === "update") {
    renderRow(event.entry);
  }
});
