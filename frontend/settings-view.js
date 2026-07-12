import { showToast } from "./toast.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const maxConcurrentInput = document.getElementById("setting-max-concurrent");
const fragmentConcurrencyInput = document.getElementById("setting-fragment-concurrency");
const aria2cEnabledInput = document.getElementById("setting-aria2c-enabled");
const aria2cDetectedNote = document.getElementById("aria2c-detected-note");
const skipDuplicatesInput = document.getElementById("setting-skip-duplicates");
const defaultFolderInput = document.getElementById("setting-default-folder");
const browseBtn = document.getElementById("setting-browse-btn");
const openrouterApiKeyInput = document.getElementById("setting-openrouter-api-key");
const anthropicApiKeyInput = document.getElementById("setting-anthropic-api-key");
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
  openrouterApiKeyInput.value = settings.openrouter_api_key || "";
  anthropicApiKeyInput.value = settings.anthropic_api_key || "";
}

document.querySelectorAll("[data-external-link]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    window.api.openExternal(link.dataset.externalLink);
  });
});

// See the matching comment in renderer.js's prefillDefaultOutputFolder —
// the packaged PyInstaller backend's cold-start can outlast main.js's
// waitForBackend() budget, so a single attempt right at page load can lose
// that race and leave the form blank for the whole session. Retry first.
async function loadSettings(retriesLeft = 10) {
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) throw new Error(`settings fetch failed: ${response.status}`);
    applySettings(await response.json());
  } catch {
    if (retriesLeft > 0) {
      setTimeout(() => loadSettings(retriesLeft - 1), 500);
    }
    // Retries exhausted — leave the form at whatever state it's already in.
  }
}

browseBtn.addEventListener("click", async () => {
  try {
    const folder = await window.api.chooseFolder();
    if (folder) defaultFolderInput.value = folder;
  } catch (error) {
    showToast("Couldn't open the folder picker: " + error.message, "error");
  }
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
        openrouter_api_key: openrouterApiKeyInput.value || null,
        anthropic_api_key: anthropicApiKeyInput.value || null,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showToast("Failed to save settings" + (body ? ": " + JSON.stringify(body.detail) : "."), "error");
      return;
    }
    const savedSettings = await response.json();
    applySettings(savedSettings);
    // The Queue tab's "Save to" field only prefills from /settings once, at
    // page load — if the default output folder is set or changed after
    // that (the common case, since Settings is visited after launch), it
    // would otherwise never reach Queue until the app restarts.
    document.dispatchEvent(new CustomEvent("clippull:settings-saved", { detail: savedSettings }));
    showToast("Settings saved", "success");
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  }
});

loadSettings();
