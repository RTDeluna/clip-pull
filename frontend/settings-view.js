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
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) return;
    applySettings(await response.json());
  } catch {
    // Leave the form at whatever state it's already in.
  }
}

browseBtn.addEventListener("click", async () => {
  const folder = await window.api.chooseFolder();
  if (folder) defaultFolderInput.value = folder;
});

saveBtn.addEventListener("click", async () => {
  try {
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
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      alert("Failed to save settings" + (body ? ": " + JSON.stringify(body.detail) : "."));
      return;
    }
    applySettings(await response.json());
  } catch (error) {
    alert("Failed to reach the backend: " + error.message);
  }
});

loadSettings();
