import { showToast } from "./toast.js";
import { replayOnboarding } from "./onboarding.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const maxConcurrentInput = document.getElementById("setting-max-concurrent");
const fragmentConcurrencyInput = document.getElementById("setting-fragment-concurrency");
const aria2cEnabledInput = document.getElementById("setting-aria2c-enabled");
const aria2cDetectedNote = document.getElementById("aria2c-detected-note");
const skipDuplicatesInput = document.getElementById("setting-skip-duplicates");
const autoTranscribeInput = document.getElementById("setting-auto-transcribe");
const autoSummarizeInput = document.getElementById("setting-auto-summarize");
const timeSavedRateInput = document.getElementById("setting-time-saved-rate");
const defaultFolderInput = document.getElementById("setting-default-folder");
const browseBtn = document.getElementById("setting-browse-btn");
const apiKeyInputs = {
  gemini: document.getElementById("setting-gemini-api-key"),
  anthropic: document.getElementById("setting-anthropic-api-key"),
  openai: document.getElementById("setting-openai-api-key"),
  groq: document.getElementById("setting-groq-api-key"),
  openrouter: document.getElementById("setting-openrouter-api-key"),
};
const saveBtn = document.getElementById("settings-save-btn");
const settingsPanel = saveBtn.closest(".panel");

// Duplicated (not imported) from the backend's provider map — same five
// entries history-view.js keeps its own copy of. There's no shared
// frontend/backend module in this project, so a local lookup is the
// pragmatic choice over any import machinery.
const PROVIDER_DISPLAY_NAMES = {
  gemini: "Gemini",
  anthropic: "Anthropic",
  openai: "OpenAI",
  groq: "Groq",
  openrouter: "OpenRouter",
};

// CLIP.PULL Pro license panel.
const licenseStatusEl = document.getElementById("license-status");
const licenseStatusText = document.getElementById("license-status-text");
const licenseKeyInput = document.getElementById("setting-license-key");
const licenseActivateBtn = document.getElementById("license-activate-btn");
const licenseDeactivateBtn = document.getElementById("license-deactivate-btn");
const licenseUpgradeBtn = document.getElementById("license-upgrade-btn");
// Kept in sync with main.js's ALLOWED_EXTERNAL_URLS allow-list (the backend
// half of this feature adds the matching entry there).
const GUMROAD_PRO_URL = "https://gumroad.com/l/clippull-pro-placeholder";

// Cached Pro status, set from GET /license (renderLicense). Drives the Pro
// gating on the auto-transcribe/-summarize toggles below: the backend makes
// these Pro-only, so free users see them shown-but-disabled with a small
// "Pro" note rather than a working control that would just 402.
let isPro = false;

// The dependent "…and summarize" toggle only makes sense once there's a
// transcript to summarize, so it's disabled (and visually de-emphasized)
// whenever auto-transcribe is off -- or whenever the whole feature is locked.
function updateAutoSummarizeState() {
  const enabled = isPro && autoTranscribeInput.checked;
  autoSummarizeInput.disabled = !enabled;
  autoSummarizeInput.closest(".field-label").classList.toggle("field-label--disabled", !enabled);
}

// Shown-but-disabled Pro gating for the auto-transcribe toggles, matching how
// the rest of the AI features gate on Pro elsewhere in the app.
function applyProGating() {
  autoTranscribeInput.disabled = !isPro;
  autoTranscribeInput.closest(".field-label").classList.toggle("field-label--disabled", !isPro);
  document.querySelectorAll(".pro-lock-note").forEach((note) => {
    note.hidden = isPro;
  });
  updateAutoSummarizeState();
}

autoTranscribeInput.addEventListener("change", updateAutoSummarizeState);

// AI feature -> which provider it's currently set to use. Each provider
// group is a row of pill buttons (see index.html's .provider-pills); the
// group's data-provider-group attribute doubles as the settings field name
// (transcription_provider / summarization_provider) so no separate mapping
// table is needed between the DOM and the PATCH body.
const PROVIDER_GROUPS = Array.from(document.querySelectorAll("[data-provider-group]"));
const FEATURE_LABELS = { transcription_provider: "Transcription", summarization_provider: "Summarization" };

function getSelectedProvider(group) {
  return group.querySelector(".provider-pill[aria-pressed='true']")?.dataset.provider;
}

function selectProvider(group, provider) {
  group.querySelectorAll(".provider-pill").forEach((pill) => {
    const selected = pill.dataset.provider === provider;
    pill.setAttribute("aria-pressed", String(selected));
  });
}

// Each API key row is collapsible (mirrors the Transcript/Summary result
// cards in History) -- collapsed by default so five password fields aren't
// all sitting open at once, and expanded automatically once its provider is
// actually selected above, so picking a provider and pasting in its key
// reads as one continuous flow instead of a hunt through a flat list.
function setApiKeyRowExpanded(row, expanded) {
  const header = row.querySelector(".api-key-row__header");
  const body = row.querySelector(".api-key-row__body");
  body.hidden = !expanded;
  header.setAttribute("aria-expanded", String(expanded));
}

document.querySelectorAll(".api-key-row").forEach((row) => {
  const header = row.querySelector(".api-key-row__header");
  const body = row.querySelector(".api-key-row__body");
  const toggle = () => setApiKeyRowExpanded(row, body.hidden);
  header.addEventListener("click", toggle);
  header.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  });
});

// Every API key row shows which currently-selected feature(s) rely on it
// (e.g. "Used by Transcription") so switching providers above immediately
// explains why a given key does or doesn't matter right now. A provider
// that becomes used also auto-expands its row -- but only ever expands,
// never collapses, so switching away from a provider never hides a key
// someone might be mid-edit on.
function updateUsedByTags() {
  const usedBy = {};
  PROVIDER_GROUPS.forEach((group) => {
    const provider = getSelectedProvider(group);
    if (!provider) return;
    (usedBy[provider] ||= []).push(FEATURE_LABELS[group.dataset.providerGroup]);
  });
  Object.entries(apiKeyInputs).forEach(([provider]) => {
    const tag = document.querySelector(`[data-used-by="${provider}"]`);
    if (!tag) return;
    const features = usedBy[provider];
    tag.hidden = !features;
    if (features) {
      tag.textContent = `Used by ${features.join(" & ")}`;
      const row = document.querySelector(`.api-key-row[data-key-provider="${provider}"]`);
      if (row) setApiKeyRowExpanded(row, true);
    }
  });
}

PROVIDER_GROUPS.forEach((group) => {
  group.querySelectorAll(".provider-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      selectProvider(group, pill.dataset.provider);
      updateUsedByTags();
    });
  });
});

function applySettings(settings) {
  maxConcurrentInput.value = settings.max_concurrent_downloads;
  fragmentConcurrencyInput.value = settings.concurrent_fragment_downloads;
  aria2cEnabledInput.checked = settings.aria2c_enabled;
  aria2cDetectedNote.textContent = settings.aria2c_detected
    ? "(detected on this machine)"
    : "(not detected on PATH)";
  skipDuplicatesInput.checked = settings.skip_duplicates;
  autoTranscribeInput.checked = settings.auto_transcribe_on_download;
  autoSummarizeInput.checked = settings.auto_summarize_after_transcribe;
  updateAutoSummarizeState();
  timeSavedRateInput.value = settings.time_saved_hourly_rate ?? "";
  defaultFolderInput.value = settings.default_output_folder || "";
  Object.entries(apiKeyInputs).forEach(([provider, input]) => {
    input.value = settings[`${provider}_api_key`] || "";
  });
  PROVIDER_GROUPS.forEach((group) => {
    selectProvider(group, settings[group.dataset.providerGroup]);
  });
  updateUsedByTags();
}

document.querySelectorAll("[data-external-link]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    window.api.openExternal(link.dataset.externalLink);
  });
});

document.querySelectorAll("[data-toggle-visibility]").forEach((button) => {
  const target = document.getElementById(button.dataset.toggleVisibility);
  if (!target) return;
  const label = button.getAttribute("aria-label").replace(/^(Show|Hide)/, "").trim();
  button.addEventListener("click", () => {
    const nowVisible = target.type === "password";
    target.type = nowVisible ? "text" : "password";
    button.dataset.visible = String(nowVisible);
    button.setAttribute("aria-pressed", String(nowVisible));
    button.setAttribute("aria-label", `${nowVisible ? "Hide" : "Show"} ${label}`);
  });
});

// -- API key testing -------------------------------------------------------
// Each key row has a "Test key" button that validates the CURRENT value in
// that input (not the saved one), so a user can confirm a freshly-pasted key
// works before committing it with Save. Mirrors the in-flight label-swap +
// response.ok / body.detail / showToast pattern used elsewhere in this file.
function setTestResult(provider, state, text) {
  const el = document.querySelector(`[data-test-result="${provider}"]`);
  if (!el) return;
  if (!state) {
    el.hidden = true;
    el.textContent = "";
    delete el.dataset.state;
    return;
  }
  el.hidden = false;
  el.dataset.state = state; // "success" | "error"
  el.textContent = text;
}

async function testKey(provider, button) {
  const input = apiKeyInputs[provider];
  const value = (input?.value || "").trim();
  if (!value) {
    showToast("Enter a key first.", "warning");
    return;
  }
  const name = PROVIDER_DISPLAY_NAMES[provider] || provider;
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "Testing…";
  setTestResult(provider, null);
  try {
    const response = await fetch(`${API_BASE}/settings/test-key`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, api_key: value }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      // 503 (couldn't reach the provider to test) or 400 (unknown provider) —
      // a transport/config problem, not a verdict on the key itself, so
      // surface it as a toast and leave the inline indicator clear.
      showToast(body?.detail || "Couldn't test this key right now.", "error");
      return;
    }
    if (body?.valid) {
      setTestResult(provider, "success", "Key works");
      showToast(`${name} key works!`, "success");
    } else {
      // A 200 with valid:false is a normal "tested and rejected" result.
      const detail = body?.detail || "That key was rejected.";
      setTestResult(provider, "error", detail);
      showToast(detail, "error");
    }
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

document.querySelectorAll("[data-test-provider]").forEach((button) => {
  button.addEventListener("click", () => testKey(button.dataset.testProvider, button));
});

// A stale "Key works" / rejection indicator shouldn't linger once the key it
// described has been edited — clear it the moment the field changes.
Object.entries(apiKeyInputs).forEach(([provider, input]) => {
  input.addEventListener("input", () => setTestResult(provider, null));
});

// Re-show the first-run welcome tour on demand.
document.getElementById("show-onboarding-btn")?.addEventListener("click", replayOnboarding);

// Persistent inline fallback so an exhausted load doesn't leave the form
// silently blank — a text line plus a button that re-triggers loadSettings.
function showSettingsLoadError() {
  if (settingsPanel.querySelector(".load-error")) return;
  const el = document.createElement("div");
  el.className = "load-error";
  el.innerHTML = `
    <span class="load-error__msg">Couldn't load your settings.</span>
    <button class="btn btn--ghost load-error__retry" type="button">Retry</button>
  `;
  el.querySelector(".load-error__retry").addEventListener("click", () => {
    el.remove();
    loadSettings();
  });
  settingsPanel.prepend(el);
}

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
    } else {
      // Retries exhausted — surface it instead of leaving the form blank.
      showToast(
        "Couldn't load your settings — check that CLIP.PULL's backend is running, then try again.",
        "error"
      );
      showSettingsLoadError();
    }
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
    const apiKeyFields = Object.fromEntries(
      Object.entries(apiKeyInputs).map(([provider, input]) => [`${provider}_api_key`, input.value || null])
    );
    const providerFields = Object.fromEntries(
      PROVIDER_GROUPS.map((group) => [group.dataset.providerGroup, getSelectedProvider(group)])
    );
    const response = await fetch(`${API_BASE}/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        max_concurrent_downloads: Number(maxConcurrentInput.value),
        concurrent_fragment_downloads: Number(fragmentConcurrencyInput.value),
        aria2c_enabled: aria2cEnabledInput.checked,
        skip_duplicates: skipDuplicatesInput.checked,
        auto_transcribe_on_download: autoTranscribeInput.checked,
        auto_summarize_after_transcribe: autoSummarizeInput.checked,
        time_saved_hourly_rate: timeSavedRateInput.value ? Number(timeSavedRateInput.value) : null,
        default_output_folder: defaultFolderInput.value || null,
        ...apiKeyFields,
        ...providerFields,
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

// The license key itself is write-only conceptually (GET /license never
// returns it), so this only reflects status — it never prefills the input.
function renderLicense(entry) {
  const status = entry?.status ?? "none";
  isPro = Boolean(entry?.pro);
  licenseStatusEl.dataset.status = status;
  if (isPro) {
    licenseStatusText.textContent = entry.purchase_email
      ? `Pro — activated (${entry.purchase_email})`
      : "Pro — activated";
  } else if (status === "invalid") {
    licenseStatusText.textContent = "Free — license inactive";
  } else {
    licenseStatusText.textContent = "Free";
  }
  licenseDeactivateBtn.hidden = !isPro;
  licenseUpgradeBtn.hidden = isPro;
  applyProGating();
  // History's own Pro gating (Lesson Notes, export, batch transcription) is
  // cached independently and only ever fetched once at that module's load --
  // without this, activating/deactivating Pro here has no effect until the
  // whole app is reloaded. Mirrors the existing clippull:settings-saved
  // cross-view sync pattern used for the default output folder.
  document.dispatchEvent(new CustomEvent("clippull:license-changed", { detail: { pro: isPro } }));
}

// Failure detail is a friendly string per the API contract; fall back to
// stringifying just in case a validation object comes through instead.
function licenseErrorDetail(body) {
  if (!body || body.detail == null) return null;
  return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
}

// Best-effort only: this is a new, lower-stakes panel, so a single attempt is
// enough — a failure degrades to a neutral state rather than throwing and
// taking down the rest of the Settings view (unlike loadSettings' retry loop).
async function loadLicense() {
  try {
    const response = await fetch(`${API_BASE}/license`);
    if (!response.ok) throw new Error(`license fetch failed: ${response.status}`);
    renderLicense(await response.json());
  } catch (error) {
    console.warn("Couldn't load license status:", error);
    isPro = false;
    licenseStatusEl.dataset.status = "none";
    licenseStatusText.textContent = "License status unavailable";
    licenseDeactivateBtn.hidden = true;
    licenseUpgradeBtn.hidden = false;
    applyProGating();
  }
}

licenseActivateBtn.addEventListener("click", async () => {
  const key = licenseKeyInput.value.trim();
  if (!key) {
    showToast("Enter a license key first.", "warning");
    return;
  }
  const originalLabel = licenseActivateBtn.textContent;
  licenseActivateBtn.disabled = true;
  licenseActivateBtn.textContent = "Activating…";
  try {
    const response = await fetch(`${API_BASE}/license/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ license_key: key }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const detail = licenseErrorDetail(body);
      showToast("Couldn't activate license" + (detail ? ": " + detail : "."), "error");
      return;
    }
    const { entry } = await response.json();
    licenseKeyInput.value = "";
    renderLicense(entry);
    showToast("CLIP.PULL Pro activated — thanks for your support!", "success");
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    licenseActivateBtn.disabled = false;
    licenseActivateBtn.textContent = originalLabel;
  }
});

licenseDeactivateBtn.addEventListener("click", async () => {
  const originalLabel = licenseDeactivateBtn.textContent;
  licenseDeactivateBtn.disabled = true;
  licenseDeactivateBtn.textContent = "Deactivating…";
  try {
    const response = await fetch(`${API_BASE}/license/deactivate`, { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const detail = licenseErrorDetail(body);
      showToast("Couldn't deactivate license" + (detail ? ": " + detail : "."), "error");
      return;
    }
    const { entry } = await response.json();
    renderLicense(entry);
    showToast("License deactivated.", "info");
  } catch (error) {
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    licenseDeactivateBtn.disabled = false;
    licenseDeactivateBtn.textContent = originalLabel;
  }
});

licenseUpgradeBtn.addEventListener("click", () => {
  window.api.openExternal(GUMROAD_PRO_URL);
});

loadSettings();
loadLicense();
