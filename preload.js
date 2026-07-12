const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  chooseFolder: () => ipcRenderer.invoke("choose-folder"),
  revealFile: (filePath) => ipcRenderer.invoke("reveal-file", filePath),
  getExtensionPackageInfo: () => ipcRenderer.invoke("get-extension-package-info"),
  saveExtensionPackage: () => ipcRenderer.invoke("save-extension-package"),
  openChromeExtensions: () => ipcRenderer.invoke("open-chrome-extensions"),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  backendPort: 8934,
});
