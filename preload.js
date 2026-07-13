const { contextBridge, ipcRenderer } = require("electron");

// Handed down from main.js via BrowserWindow's webPreferences.additionalArguments
// (the supported way to pass startup data to a preload script under
// contextIsolation). Only the WebSocket connection needs this explicitly --
// regular fetch() calls to the backend get the same token injected
// transparently by main.js's onBeforeSendHeaders, so renderer code never
// touches this directly except in ws-client.js.
function readApiToken() {
  const prefix = "--clip-pull-token=";
  const arg = process.argv.find((a) => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : null;
}

contextBridge.exposeInMainWorld("api", {
  chooseFolder: () => ipcRenderer.invoke("choose-folder"),
  revealFile: (filePath) => ipcRenderer.invoke("reveal-file", filePath),
  copyText: (text) => ipcRenderer.invoke("copy-text", text),
  getExtensionPackageInfo: () => ipcRenderer.invoke("get-extension-package-info"),
  saveExtensionPackage: () => ipcRenderer.invoke("save-extension-package"),
  saveTextFile: (content, defaultFilename, filters) =>
    ipcRenderer.invoke("save-text-file", { content, defaultFilename, filters }),
  openChromeExtensions: () => ipcRenderer.invoke("open-chrome-extensions"),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  backendPort: 8934,
  apiToken: readApiToken(),
});
