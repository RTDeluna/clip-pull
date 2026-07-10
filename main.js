const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const http = require("http");

const EXTENSION_DIR = path.join(__dirname, "assets", "extension");

const BACKEND_PORT = 8934;
const BACKEND_HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/health`;

let backendProcess = null;
let mainWindow = null;

function getBackendExecutablePath() {
  const exeName = process.platform === "win32" ? "clippull-backend.exe" : "clippull-backend";
  return path.join(__dirname, "backend", "dist", exeName);
}

function spawnBackend() {
  if (app.isPackaged) {
    const dbPath = path.join(app.getPath("userData"), "clip_pull.db");
    backendProcess = spawn(getBackendExecutablePath(), [], {
      stdio: "inherit",
      env: { ...process.env, CLIP_PULL_DB_PATH: dbPath },
    });
  } else {
    backendProcess = spawn("python", ["main.py"], {
      cwd: path.join(__dirname, "backend"),
      stdio: "inherit",
    });
  }
  backendProcess.on("error", (err) => {
    console.error("Failed to start backend:", err);
  });
}

function waitForBackend(retriesLeft, onReady) {
  if (retriesLeft <= 0) {
    console.error("Backend did not become ready in time.");
    onReady();
    return;
  }
  http
    .get(BACKEND_HEALTH_URL, (res) => {
      if (res.statusCode === 200) {
        onReady();
      } else {
        setTimeout(() => waitForBackend(retriesLeft - 1, onReady), 300);
      }
    })
    .on("error", () => {
      setTimeout(() => waitForBackend(retriesLeft - 1, onReady), 300);
    });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 700,
    icon: path.join(__dirname, "assets", "icon.ico"),
    backgroundColor: "#0a0a10",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, "frontend", "index.html"));
}

ipcMain.handle("choose-folder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory"],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }
  return result.filePaths[0];
});

ipcMain.handle("reveal-file", (_, filePath) => {
  shell.showItemInFolder(filePath);
});

ipcMain.handle("open-chrome-extensions", async () => {
  try {
    await shell.openExternal("chrome://extensions");
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

function findExtensionZip() {
  try {
    return fs.readdirSync(EXTENSION_DIR).find((f) => f.endsWith(".zip")) || null;
  } catch {
    return null;
  }
}

ipcMain.handle("get-extension-package-info", () => {
  const zip = findExtensionZip();
  return zip ? { filename: zip } : null;
});

ipcMain.handle("save-extension-package", async () => {
  const zip = findExtensionZip();
  if (!zip) return { ok: false, error: "not_built" };

  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: zip,
    filters: [{ name: "Zip Archive", extensions: ["zip"] }],
  });
  if (result.canceled || !result.filePath) {
    return { ok: false, error: "cancelled" };
  }
  fs.copyFileSync(path.join(EXTENSION_DIR, zip), result.filePath);
  return { ok: true, path: result.filePath };
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  spawnBackend();
  waitForBackend(20, createWindow);
});

app.on("window-all-closed", () => {
  if (backendProcess) {
    backendProcess.kill();
  }
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});
