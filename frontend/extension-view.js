import { showToast } from "./toast.js";

const statusEl = document.getElementById("extension-status");
const downloadBtn = document.getElementById("extension-download-btn");
const chromeExtensionsCode = document.getElementById("chrome-extensions-code");

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("extension-status--error", isError);
}

async function refreshPackageInfo() {
  let info;
  try {
    info = await window.api?.getExtensionPackageInfo?.();
  } catch (error) {
    setStatus("Failed to reach the app: " + error.message, true);
    downloadBtn.disabled = true;
    return;
  }
  if (info?.filename) {
    setStatus(`Ready to download: ${info.filename}`);
    downloadBtn.disabled = false;
  } else {
    setStatus('Not built yet — run "npm run build:extension" first.', true);
    downloadBtn.disabled = true;
  }
}

downloadBtn.addEventListener("click", async () => {
  downloadBtn.disabled = true;
  try {
    const result = await window.api.saveExtensionPackage();
    if (result?.ok) {
      setStatus(`Saved to ${result.path}`);
      showToast("Extension package saved", "success");
      window.api.revealFile(result.path);
    } else if (result?.error !== "cancelled") {
      setStatus("Could not save the extension package.", true);
      showToast("Could not save the extension package.", "error");
    }
  } catch (error) {
    setStatus("Failed to reach the app: " + error.message, true);
    showToast("Failed to reach the app: " + error.message, "error");
  } finally {
    downloadBtn.disabled = false;
  }
});

async function openChromeExtensions() {
  try {
    const result = await window.api.openChromeExtensions();
    if (!result?.ok) {
      throw new Error(result?.error || "could not open");
    }
    showToast("Opening chrome://extensions in your browser…", "success");
  } catch {
    try {
      await window.api.copyText(chromeExtensionsCode.textContent);
      showToast("Couldn't open automatically — copied to clipboard instead", "warning");
    } catch {
      showToast("Failed to open or copy", "error");
    }
  }
}

chromeExtensionsCode.addEventListener("click", (event) => {
  event.preventDefault();
  openChromeExtensions();
});
chromeExtensionsCode.addEventListener("keydown", (event) => {
  if (event.key === " ") {
    event.preventDefault();
    openChromeExtensions();
  }
});

refreshPackageInfo();
// The popup can be opened repeatedly without a page reload -- re-check status
// each time instead of relying on the one-time check above, so it doesn't go
// stale if the extension zip gets (re)built while the app is running.
document.addEventListener("clippull:extension-popup-opened", refreshPackageInfo);
