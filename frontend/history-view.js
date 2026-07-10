const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const searchInput = document.getElementById("history-search");
const statusFilter = document.getElementById("history-status-filter");
const historyList = document.getElementById("history-list");

function renderHistoryRow(entry) {
  const row = document.createElement("li");
  row.className = "queue-row";
  row.innerHTML = `
    <div class="queue-row__top">
      <span class="queue-row__title"></span>
      <span class="queue-row__status"></span>
    </div>
    <div class="queue-row__size"></div>
    <button class="btn btn--ghost reveal-btn" type="button">Reveal</button>
  `;
  row.querySelector(".queue-row__title").textContent = entry.title || entry.url;
  const statusEl = row.querySelector(".queue-row__status");
  statusEl.textContent = entry.status === "done" ? "Done" : "Failed";
  statusEl.classList.add(
    entry.status === "done" ? "queue-row__status--done" : "queue-row__status--error"
  );
  row.querySelector(".queue-row__size").textContent =
    `${entry.total_size || "--"} · ${entry.finished_at}`;
  const revealBtn = row.querySelector(".reveal-btn");
  revealBtn.disabled = !entry.output_path;
  revealBtn.addEventListener("click", () => {
    if (entry.output_path) window.api.revealFile(entry.output_path);
  });
  return row;
}

async function loadHistory() {
  try {
    const params = new URLSearchParams();
    if (searchInput.value) params.set("q", searchInput.value);
    if (statusFilter.value) params.set("status", statusFilter.value);
    const response = await fetch(`${API_BASE}/history?${params.toString()}`);
    if (!response.ok) return;
    const body = await response.json();
    historyList.innerHTML = "";
    body.entries.forEach((entry) => historyList.appendChild(renderHistoryRow(entry)));
  } catch {
    // Backend unreachable or errored — leave the list as-is.
  }
}

let debounceTimer;
searchInput.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(loadHistory, 250);
});
statusFilter.addEventListener("change", loadHistory);

loadHistory();
