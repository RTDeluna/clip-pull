const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  chooseFolder: () => ipcRenderer.invoke("choose-folder"),
  revealFile: (filePath) => ipcRenderer.invoke("reveal-file", filePath),
  backendPort: 8934,
});
