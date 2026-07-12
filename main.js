const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn, exec, execSync } = require("child_process");
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
// Distinguishes "we killed the backend on purpose" (quitting) from "it
// died on its own" (crash) — the exit handler below only alerts the user
// for the latter.
let isShuttingDown = false;

function getBackendExecutablePath() {
  const exeName = process.platform === "win32" ? "clippull-backend.exe" : "clippull-backend";
  return path.join(__dirname, "backend", "dist", exeName);
}

// Bundled at build time by scripts/fetch-ffmpeg.ps1 into backend/vendor/,
// the same way clippull-backend.exe lands in backend/dist/ -- so this
// resolves consistently whether running from source or packaged. Only
// non-null if the file is actually there: older installs built before
// bundling was added, or a dev machine that never ran the fetch script,
// fall back to check_ffmpeg_available()'s system-PATH lookup instead.
function getBundledFfmpegPath() {
  const exeName = process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg";
  const ffmpegPath = path.join(__dirname, "backend", "vendor", exeName);
  return fs.existsSync(ffmpegPath) ? ffmpegPath : null;
}

function spawnBackend() {
  isShuttingDown = false;
  const bundledFfmpeg = getBundledFfmpegPath();
  const ffmpegEnv = bundledFfmpeg ? { CLIP_PULL_FFMPEG_PATH: bundledFfmpeg } : {};
  if (app.isPackaged) {
    const dbPath = path.join(app.getPath("userData"), "clip_pull.db");
    backendProcess = spawn(getBackendExecutablePath(), [], {
      stdio: "inherit",
      env: { ...process.env, CLIP_PULL_DB_PATH: dbPath, ...ffmpegEnv },
      windowsHide: true,
    });
  } else {
    backendProcess = spawn("python", ["main.py"], {
      cwd: path.join(__dirname, "backend"),
      stdio: "inherit",
      env: { ...process.env, ...ffmpegEnv },
    });
  }
  backendProcess.on("error", (err) => {
    console.error("Failed to start backend:", err);
  });
  // Without this, a mid-session backend crash (unhandled Python exception,
  // segfault, OOM) left the window open and looking alive while every
  // action silently failed — nothing previously watched for this.
  backendProcess.on("exit", (code, signal) => {
    backendProcess = null;
    if (isShuttingDown) return;
    console.error(`Backend exited unexpectedly (code=${code}, signal=${signal}).`);
    dialog.showErrorBox(
      "CLIP.PULL backend stopped",
      "The download engine stopped unexpectedly and can't continue this " +
        "session. Please restart CLIP.PULL. If this keeps happening, check " +
        "that no antivirus software is blocking the app."
    );
  });
}

// backendProcess.kill() only signals that one PID — on Windows it doesn't
// reliably tear down the process tree, so if a download was mid-flight and
// spawned aria2c/ffmpeg as children, those (and sometimes the backend exe
// itself) can survive after the app window closes. taskkill's /T flag kills
// the whole tree; plain .kill() is fine on macOS/Linux, which use real
// signals. Uses the synchronous exec variant deliberately: the app is about
// to quit right after this runs, so it needs to actually finish the kill
// before quitting, not fire-and-forget it (which previously let app.quit()
// proceed before the tree was actually torn down).
function killBackend() {
  isShuttingDown = true;
  if (!backendProcess) return;
  if (process.platform === "win32") {
    try {
      execSync(`taskkill /pid ${backendProcess.pid} /f /t`);
    } catch (err) {
      // taskkill exits non-zero if the process already ended on its own —
      // not a real failure, nothing left to clean up either way.
      console.error("taskkill failed (process may have already exited):", err.message);
    }
  } else {
    backendProcess.kill();
  }
  backendProcess = null;
}

function waitForBackend(retriesLeft, onReady) {
  if (retriesLeft <= 0) {
    console.error("Backend did not become ready in time.");
    dialog.showErrorBox(
      "CLIP.PULL is having trouble starting",
      "The download engine didn't respond in time. The app will still " +
        "open, but downloads may not work. If this keeps happening, check " +
        "that no antivirus software is blocking the app, then restart CLIP.PULL."
    );
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
  try {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"],
    });
    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }
    return result.filePaths[0];
  } catch (err) {
    console.error("choose-folder failed:", err);
    throw err;
  }
});

ipcMain.handle("reveal-file", (_, filePath) => {
  try {
    shell.showItemInFolder(filePath);
  } catch (err) {
    console.error("reveal-file failed:", err);
    throw err;
  }
});

function execCommand(command) {
  return new Promise((resolve, reject) => {
    // Without a timeout, a hung reg.exe/browser launch would await forever
    // here — hanging the open-chrome-extensions IPC handler (and the
    // button that calls it) permanently, with no way to recover short of
    // restarting the app.
    exec(command, { timeout: 5000 }, (error, stdout) => {
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

// Settings links out to where to get API keys -- allow-listed rather than
// accepting any renderer-supplied URL, since a compromised/buggy renderer
// otherwise gets an arbitrary-URL-open primitive via shell.openExternal.
const ALLOWED_EXTERNAL_URLS = new Set([
  "https://platform.openai.com/api-keys",
  "https://console.anthropic.com/settings/keys",
]);

ipcMain.handle("open-external", async (_, url) => {
  if (!ALLOWED_EXTERNAL_URLS.has(url)) {
    console.error("Blocked attempt to open a non-allow-listed external URL:", url);
    return { ok: false, error: "not_allowed" };
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
  killBackend();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  killBackend();
});
