import { showToast } from "./toast.js";

const statusEl = document.getElementById("extension-status");
const downloadBtn = document.getElementById("extension-download-btn");
const chromeExtensionsCode = document.getElementById("chrome-extensions-code");

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("extension-status--error", isError);
}

async function refreshPackageInfo() {
  const info = await window.api?.getExtensionPackageInfo?.();
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

chromeExtensionsCode.addEventListener("click", async () => {
  try {
    const result = await window.api.openChromeExtensions();
    if (!result?.ok) {
      throw new Error(result?.error || "could not open");
    }
    showToast("Opening chrome://extensions in your browser…", "success");
  } catch {
    try {
      await navigator.clipboard.writeText(chromeExtensionsCode.textContent);
      showToast("Couldn't open automatically — copied to clipboard instead", "warning");
    } catch {
      showToast("Failed to open or copy", "error");
    }
  }
});

refreshPackageInfo();
