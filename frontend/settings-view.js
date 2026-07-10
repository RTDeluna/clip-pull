const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const maxConcurrentInput = document.getElementById("setting-max-concurrent");
const fragmentConcurrencyInput = document.getElementById("setting-fragment-concurrency");
const aria2cEnabledInput = document.getElementById("setting-aria2c-enabled");
const aria2cDetectedNote = document.getElementById("aria2c-detected-note");
const skipDuplicatesInput = document.getElementById("setting-skip-duplicates");
const defaultFolderInput = document.getElementById("setting-default-folder");
const browseBtn = document.getElementById("setting-browse-btn");
const saveBtn = document.getElementById("settings-save-btn");

function applySettings(settings) {
  maxConcurrentInput.value = settings.max_concurrent_downloads;
  fragmentConcurrencyInput.value = settings.concurrent_fragment_downloads;
  aria2cEnabledInput.checked = settings.aria2c_enabled;
  aria2cDetectedNote.textContent = settings.aria2c_detected
    ? "(detected on this machine)"
    : "(not detected on PATH)";
  skipDuplicatesInput.checked = settings.skip_duplicates;
  defaultFolderInput.value = settings.default_output_folder || "";
}

async function loadSettings() {
  const response = await fetch(`${API_BASE}/settings`);
  applySettings(await response.json());
}

browseBtn.addEventListener("click", async () => {
  const folder = await window.api.chooseFolder();
  if (folder) defaultFolderInput.value = folder;
});

saveBtn.addEventListener("click", async () => {
  const response = await fetch(`${API_BASE}/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      max_concurrent_downloads: Number(maxConcurrentInput.value),
      concurrent_fragment_downloads: Number(fragmentConcurrencyInput.value),
      aria2c_enabled: aria2cEnabledInput.checked,
      skip_duplicates: skipDuplicatesInput.checked,
      default_output_folder: defaultFolderInput.value || null,
    }),
  });
  applySettings(await response.json());
});

loadSettings();
