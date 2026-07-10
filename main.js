const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn, exec } = require("child_process");
const http = require("http");

// Without this, Windows notifications/taskbar grouping fall back to a
// generic "electron.app.<name>" identity instead of the app's own name —
// must match electron-builder's "appId" so it lines up with the installed
// shortcut's AppUserModelID.
app.setAppUserModelId("com.clippull.downloader");

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
      windowsHide: true,
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

function execCommand(command) {
  return new Promise((resolve, reject) => {
    exec(command, (error, stdout) => {
      if (error) reject(error);
      else resolve(stdout);
    });
  });
}

// chrome:// isn't a registered OS protocol on Windows (by design — it would
// let any app deep-link into a browser's internal pages), so
// shell.openExternal() can't hand it to "the default browser": Windows just
// shows a "no app can open this link" dialog. To actually reach the browser
// the user picked in Settings > Default apps, look up that browser's own
// registered launch command and invoke it directly with the URL as an
// argument — the browser then interprets its own chrome://-style scheme
// itself, the way it would if the user typed it into the address bar.
async function getWindowsDefaultBrowserLaunchCommand() {
  // Primary source: the explicit choice from Settings > Default apps, if
  // the user ever went through that picker.
  try {
    const progIdOutput = await execCommand(
      'reg query "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Shell\\Associations\\UrlAssociations\\http\\UserChoice" /v ProgId'
    );
    const progIdMatch = progIdOutput.match(/ProgId\s+REG_SZ\s+(\S+)/);
    const progId = progIdMatch && progIdMatch[1];
    // ProgIds are short identifiers like "ChromeHTML" or "MSEdgeHTM" — never
    // contain spaces or shell metacharacters. Reject anything else rather
    // than interpolate it into the next reg query command.
    if (progId && /^[\w.-]+$/.test(progId)) {
      const commandOutput = await execCommand(`reg query "HKCR\\${progId}\\shell\\open\\command" /ve`);
      const commandMatch = commandOutput.match(/REG_SZ\s+(.+)/);
      if (commandMatch) return commandMatch[1].trim();
    }
  } catch {
    // No UserChoice recorded (e.g. never set via the picker) — fall
    // through to the classic association below.
  }

  // Fallback: HKCR\http is the classic protocol association Windows keeps
  // pointed at the effective default browser even without an explicit
  // UserChoice, so this resolves on machines where the picker was never used.
  const commandOutput = await execCommand('reg query "HKCR\\http\\shell\\open\\command" /ve');
  const commandMatch = commandOutput.match(/REG_SZ\s+(.+)/);
  return commandMatch ? commandMatch[1].trim() : null;
}

ipcMain.handle("open-chrome-extensions", async () => {
  const url = "chrome://extensions";
  if (process.platform === "win32") {
    try {
      const launchTemplate = await getWindowsDefaultBrowserLaunchCommand();
      if (launchTemplate) {
        const launchCommand = launchTemplate.includes("%1")
          ? launchTemplate.replace("%1", url)
          : `${launchTemplate} "${url}"`;
        await execCommand(launchCommand);
        return { ok: true };
      }
    } catch (error) {
      console.error("Could not launch the default browser directly:", error);
      // Fall through to the generic attempt below.
    }
  }
  try {
    await shell.openExternal(url);
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
  // The packaged backend is a PyInstaller onefile executable, which
  // self-extracts to a temp directory on every launch — on first run,
  // with antivirus scanning an unsigned exe, this can take much longer
  // than a dev-mode `python main.py` start. 40 retries at 300ms gives it
  // 12s before falling back, instead of the previous 6s.
  waitForBackend(40, createWindow);
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
