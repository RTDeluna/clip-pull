const { app, BrowserWindow, Menu, dialog, ipcMain, shell, clipboard, session } = require("electron");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");
const { spawn, execSync, execFile } = require("child_process");
const http = require("http");

// This UI is plain DOM/CSS (no video preview, canvas, or WebGL content), so
// the GPU process Electron otherwise spawns by default buys nothing here but
// costs 50-100MB+ of RAM on its own. Must be called before the app is ready --
// as early as possible, hence right at the top of the file. Trades slightly
// less smooth CSS transitions/animations (ripple.js, charts.js) for a
// meaningfully smaller baseline memory footprint on a mostly-static UI.
app.disableHardwareAcceleration();

// Without this, Windows notifications/taskbar grouping fall back to a
// generic "electron.app.<name>" identity instead of the app's own name —
// must match electron-builder's "appId" so it lines up with the installed
// shortcut's AppUserModelID.
app.setAppUserModelId("com.clippull.downloader");

const EXTENSION_DIR = path.join(__dirname, "assets", "extension");

const BACKEND_PORT = 8934;
const BACKEND_HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/health`;

// How many 300ms polls of BACKEND_HEALTH_URL to attempt before giving up and
// showing the "having trouble starting" dialog (see waitForBackend). This
// only bounds the worst case -- a successful launch never waits this long,
// since waitForBackend returns the instant /health responds. A first-ever
// launch gets a much longer budget: a fresh install's real-time antivirus
// scan of a batch of freshly-written, unsigned files can plausibly take
// longer than a normal 12s launch, where the files are already on disk and
// most AV products cache a clean verdict per file hash+mtime instead of
// re-scanning on every run.
const NORMAL_RETRY_COUNT = 40; // 12s
const FIRST_RUN_RETRY_COUNT = 100; // 30s

// A fresh random secret every launch, required by the backend on every
// request except /health (see backend/main.py's require_api_token). The
// backend binds to 127.0.0.1, but that alone doesn't stop a webpage open in
// the user's own browser from fetch()-ing it too -- this token is what
// actually gates that off, since only this app's own renderer (via the
// header injection below and the token handed to preload for the WS query
// param) ever learns it.
const API_TOKEN = crypto.randomBytes(32).toString("hex");

let backendProcess = null;
let mainWindow = null;
// Distinguishes "we killed the backend on purpose" (quitting) from "it
// died on its own" (crash) — the exit handler below only alerts the user
// for the latter.
let isShuttingDown = false;

// The backend is built with PyInstaller's --onedir (not --onefile): the exe
// sits inside its own "clippull-backend" folder alongside its dependency
// DLLs, rather than as a single self-extracting file. --onefile has to
// unpack that entire payload to a fresh %TEMP% directory on every single
// launch -- slow on its own, and exactly the "unpacks itself to disk at
// runtime" behavior antivirus heuristics are quick to flag on an unsigned
// exe, which is what previously caused the backend health-check in
// waitForBackend() below to time out on some machines. --onedir removes the
// re-extraction step entirely: the files just sit on disk from install time
// onward, so startup is both faster and reads less suspicious.
function getBackendExecutablePath() {
  const exeName = process.platform === "win32" ? "clippull-backend.exe" : "clippull-backend";
  return path.join(__dirname, "backend", "dist", "clippull-backend", exeName);
}

// Bundled at build time by scripts/fetch-ffmpeg.ps1 into backend/vendor/,
// the same way clippull-backend.exe lands in backend/dist/clippull-backend/
// -- so this resolves consistently whether running from source or packaged.
// Only non-null if the file is actually there: older installs built before
// bundling was added, or a dev machine that never ran the fetch script,
// fall back to check_ffmpeg_available()'s system-PATH lookup instead.
function getBundledFfmpegPath() {
  const exeName = process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg";
  const ffmpegPath = path.join(__dirname, "backend", "vendor", exeName);
  return fs.existsSync(ffmpegPath) ? ffmpegPath : null;
}

// The backend creates this file on its first successful start -- used as a
// proxy for "is this a first-ever launch" (see FIRST_RUN_RETRY_COUNT above),
// checked before spawnBackend() runs (which is what would create it).
function getDbPath() {
  return path.join(app.getPath("userData"), "clip_pull.db");
}

function spawnBackend() {
  isShuttingDown = false;
  const bundledFfmpeg = getBundledFfmpegPath();
  const ffmpegEnv = bundledFfmpeg ? { CLIP_PULL_FFMPEG_PATH: bundledFfmpeg } : {};
  if (app.isPackaged) {
    const dbPath = getDbPath();
    backendProcess = spawn(getBackendExecutablePath(), [], {
      stdio: "inherit",
      env: { ...process.env, CLIP_PULL_DB_PATH: dbPath, CLIP_PULL_API_TOKEN: API_TOKEN, ...ffmpegEnv },
      windowsHide: true,
    });
  } else {
    backendProcess = spawn("python", ["main.py"], {
      cwd: path.join(__dirname, "backend"),
      stdio: "inherit",
      env: { ...process.env, CLIP_PULL_API_TOKEN: API_TOKEN, ...ffmpegEnv },
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

let splashWindow = null;

// Shown the instant the app launches, before spawnBackend()/waitForBackend()
// even start -- without this, nothing appears on screen at all for up to
// FIRST_RUN_RETRY_COUNT * 300ms, which reads as a hang even when the app is
// about to start up fine on its own. Purely decorative: no preload, no IPC,
// no backend calls (see frontend/splash.js) -- it can't interact with the
// main window's own startup sequence at all, just sits on top of it.
function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 320,
    height: 280,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    backgroundColor: "#0a0a10",
    icon: path.join(__dirname, "assets", "icon.ico"),
  });
  splashWindow.loadFile(path.join(__dirname, "frontend", "splash.html"));
}

function closeSplashWindow() {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.close();
  }
  splashWindow = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 700,
    // Starts hidden and is shown only once frontend/index.html has actually
    // painted (see the ready-to-show handler below) -- otherwise Electron
    // shows a blank window the instant it's constructed, before any content
    // has loaded, which the splash window above is specifically here to
    // avoid ever exposing to begin with.
    show: false,
    icon: path.join(__dirname, "assets", "icon.ico"),
    backgroundColor: "#0a0a10",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      // The officially-supported way to hand startup data to a preload
      // script under contextIsolation -- preload.js reads this back off
      // process.argv. Used only for the WebSocket connection (see
      // ws-client.js): browsers can't set custom headers on `new
      // WebSocket()`, so that one path needs the token in hand explicitly
      // rather than relying on the header injection below.
      additionalArguments: [`--clip-pull-token=${API_TOKEN}`],
    },
  });
  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    closeSplashWindow();
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
  // filePath always originates from a completed download's own recorded
  // output_path, never free-typed by the user -- but it crosses the IPC
  // boundary as a plain string, so a defensive type/existence check here
  // costs nothing and rules out a malformed or stale call ever reaching
  // shell.showItemInFolder with garbage.
  if (typeof filePath !== "string" || !filePath || !fs.existsSync(filePath)) {
    return;
  }
  try {
    shell.showItemInFolder(filePath);
  } catch (err) {
    console.error("reveal-file failed:", err);
    throw err;
  }
});

// Routed through the main process instead of the renderer's own
// navigator.clipboard.writeText: the web Clipboard API requires the
// document to be focused and rejects with "Document is not focused"
// whenever DevTools or another window has focus instead — which happens
// often enough in normal use to make copy buttons unreliable. Electron's
// native clipboard module has no such requirement.
ipcMain.handle("copy-text", (_, text) => {
  clipboard.writeText(text);
});

function queryRegistry(args) {
  return new Promise((resolve, reject) => {
    // Without a timeout, a hung reg.exe would await forever here, hanging
    // the open-chrome-extensions IPC handler (and the button that calls it)
    // permanently with no way to recover short of restarting the app.
    // execFile (no shell) rather than exec: the returned value ends up in a
    // launch command we later spawn, so this avoids any shell-quoting risk
    // at the lookup stage too.
    execFile("reg.exe", args, { timeout: 5000 }, (error, stdout) => {
      if (error) reject(error);
      else resolve(stdout);
    });
  });
}

// Splits a registry "shell\open\command" value -- typically
// `"C:\Program Files\Browser\browser.exe" --single-argument %1` -- into an
// executable path and argument list, so the browser can be launched via
// spawn(exe, args) instead of a shell string. That sidesteps any quoting
// ambiguity in the registry value and matches how Windows itself would
// invoke it for a URL click.
function parseShellCommand(command) {
  const quotedMatch = command.match(/^"([^"]+)"\s*(.*)$/);
  let exe;
  let rest;
  if (quotedMatch) {
    [, exe, rest] = quotedMatch;
  } else {
    const spaceIdx = command.indexOf(" ");
    exe = spaceIdx === -1 ? command : command.slice(0, spaceIdx);
    rest = spaceIdx === -1 ? "" : command.slice(spaceIdx + 1);
  }
  const args = rest.trim().length ? rest.trim().split(/\s+/) : [];
  return { exe, args };
}

// Tried before ever falling back to "whatever the default browser is": the
// extension is explicitly a Chrome extension and the UI's own copy says
// "Install in Chrome", so if Chrome is actually installed, that's what
// should open -- regardless of whether Edge (Windows' out-of-box default)
// is the system default browser. Standard installer locations only; if
// Chrome was installed somewhere nonstandard this simply falls through to
// getDefaultBrowserLaunch below, same as if Chrome weren't installed.
function getChromeExecutablePath() {
  const candidates = [
    process.env["PROGRAMFILES"] && path.join(process.env["PROGRAMFILES"], "Google\\Chrome\\Application\\chrome.exe"),
    process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Google\\Chrome\\Application\\chrome.exe"),
    process.env["LOCALAPPDATA"] && path.join(process.env["LOCALAPPDATA"], "Google\\Chrome\\Application\\chrome.exe"),
  ];
  return candidates.find((candidate) => candidate && fs.existsSync(candidate)) || null;
}

// On a cold start (Chrome not already running) with multiple profiles
// configured -- or "Ask which profile to use" enabled -- launching chrome.exe
// with just a URL argument shows the "Who's using Chrome?" picker instead of
// navigating there. The picker is a separate, short-lived process; the URL
// from the original command line doesn't reliably carry over to whichever
// profile the user then picks, so the browser opens to a blank/default tab
// instead. Chrome records which profile was last active in its own Local
// State file -- passing that back via --profile-directory tells Chrome
// exactly which profile to open, skipping the picker (and the URL loss)
// entirely. Returns null (silently) on any read/parse failure, including a
// brand new Chrome install with no Local State file yet.
function getChromeLastProfileDirectory() {
  if (!process.env["LOCALAPPDATA"]) return null;
  const localStatePath = path.join(
    process.env["LOCALAPPDATA"], "Google\\Chrome\\User Data\\Local State"
  );
  try {
    const parsed = JSON.parse(fs.readFileSync(localStatePath, "utf8"));
    const lastUsed = parsed?.profile?.last_used;
    return typeof lastUsed === "string" && lastUsed ? lastUsed : null;
  } catch {
    return null;
  }
}

// Catches Chrome installs the standard-path guesses above miss (custom
// install directory, some enterprise/portable deployments) -- App Paths is
// how Windows itself resolves a bare "chrome.exe" regardless of where it
// was actually installed, so it's a more reliable second attempt than
// guessing more folder paths.
async function getChromeExecutablePathFromAppPaths() {
  try {
    const output = await queryRegistry([
      "query",
      "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\chrome.exe",
      "/ve",
    ]);
    const match = output.match(/REG_SZ\s+(.+)/);
    const exePath = match ? match[1].trim() : null;
    return exePath && fs.existsSync(exePath) ? exePath : null;
  } catch {
    return null;
  }
}

// chrome:// isn't a registered OS protocol on Windows (by design — it would
// let any app deep-link into a browser's internal pages), so
// shell.openExternal() can't hand it to "the default browser": Windows just
// shows a "no app can open this link" dialog. To actually reach the browser
// the user picked in Settings > Default apps -- Chrome, Brave, Edge,
// whatever -- look up that browser's own registered launch command and
// invoke it directly with the URL as an argument; the browser then
// interprets its own chrome://-style scheme itself, the way it would if the
// user typed it into the address bar.
async function getDefaultBrowserLaunch() {
  // Primary source: the explicit choice from Settings > Default apps, if
  // the user ever went through that picker.
  let progId = null;
  try {
    const progIdOutput = await queryRegistry([
      "query",
      "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Shell\\Associations\\UrlAssociations\\http\\UserChoice",
      "/v",
      "ProgId",
    ]);
    const progIdMatch = progIdOutput.match(/ProgId\s+REG_SZ\s+(\S+)/);
    progId = progIdMatch && progIdMatch[1];
  } catch {
    // No UserChoice recorded (e.g. never set via the picker) — fall
    // through to the classic association below.
  }

  // ProgIds are short identifiers like "ChromeHTML" or "BraveHTML" — never
  // contain spaces or registry-path metacharacters. Reject anything else
  // rather than interpolate it into the next reg query's key path.
  const key =
    progId && /^[\w.-]+$/.test(progId)
      ? `HKCR\\${progId}\\shell\\open\\command`
      // Fallback: HKCR\http is the classic protocol association Windows
      // keeps pointed at the effective default browser even without an
      // explicit UserChoice, so this resolves on machines where the picker
      // was never used.
      : "HKCR\\http\\shell\\open\\command";
  const commandOutput = await queryRegistry(["query", key, "/ve"]);
  const commandMatch = commandOutput.match(/REG_SZ\s+(.+)/);
  return commandMatch ? parseShellCommand(commandMatch[1].trim()) : null;
}

// Whether a process with this image name (e.g. "chrome.exe") is currently
// running, via tasklist -- used by launchChromiumUrl below to decide whether
// its cold-start workaround is actually needed. tasklist always exits 0
// regardless of a match; a non-match prints "INFO: No tasks are running..."
// instead, so this checks the output text, not the exit code. Any failure to
// even ask (tasklist missing/timeout) resolves false -- the safer default,
// since it just costs one extra warm-up launch rather than a silently
// dropped URL.
function isProcessRunning(imageName) {
  return new Promise((resolve) => {
    execFile(
      "tasklist.exe",
      ["/FI", `IMAGENAME eq ${imageName}`, "/NH"],
      { timeout: 5000 },
      (error, stdout) => {
        if (error) return resolve(false);
        resolve(stdout.toLowerCase().includes(imageName.toLowerCase()));
      }
    );
  });
}

// Chromium-based browsers (Chrome, Edge, Brave, ...) refuse to navigate
// straight to an internal chrome://-style URL passed on the command line at
// a genuine cold start -- a deliberate security mitigation against local
// scripts silently opening sensitive internal pages -- and just open the
// normal new-tab page instead, dropping the URL entirely. The exact same
// command-line URL DOES work once the browser is already running: a second
// launch gets forwarded via IPC to the existing instance instead of being
// parsed as a fresh command line, and that forwarded navigation isn't
// subject to the cold-start restriction. So: only when the browser isn't
// already running, launch it once with no URL to get it up, give it a
// moment to become "the running instance," then launch again with the real
// URL -- which now lands instead of being silently swallowed.
// Chrome's own path is fs.existsSync-verified before use, but a resolved
// default-browser path comes straight from the registry and could be stale
// (uninstalled/moved app). spawn() reports that asynchronously as an
// unhandled 'error' event, which -- with no listener -- crashes the main
// process; a no-op listener here just lets the launch attempt fail quietly
// (the caller already has no way to react to a detached, unref'd process
// anyway) instead of taking the whole app down over a browser that won't open.
function spawnDetached(exe, args) {
  const child = spawn(exe, args, { detached: true, stdio: "ignore" });
  child.on("error", () => {});
  child.unref();
}

async function launchChromiumUrl(exe, baseArgs, url) {
  const alreadyRunning = await isProcessRunning(path.basename(exe));
  if (!alreadyRunning) {
    spawnDetached(exe, baseArgs);
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  spawnDetached(exe, [...baseArgs, url]);
}

ipcMain.handle("open-chrome-extensions", async () => {
  const url = "chrome://extensions";
  if (process.platform === "win32") {
    const chromePath = getChromeExecutablePath() || (await getChromeExecutablePathFromAppPaths());
    if (chromePath) {
      try {
        const lastProfile = getChromeLastProfileDirectory();
        const baseArgs = lastProfile ? [`--profile-directory=${lastProfile}`] : [];
        await launchChromiumUrl(chromePath, baseArgs, url);
        return { ok: true };
      } catch (error) {
        console.error("Could not launch Chrome directly:", error);
        // Fall through to the default-browser resolution below.
      }
    }
    try {
      const browser = await getDefaultBrowserLaunch();
      if (browser?.exe) {
        if (browser.args.some((arg) => arg.includes("%1"))) {
          // A %1-style registry command already dictates exactly where the
          // URL is substituted, so there's no clean "base args without the
          // URL" to warm the browser up with first -- the cold-start
          // double-launch trick doesn't cleanly apply here. This form is
          // uncommon enough (most browsers just take a trailing URL arg,
          // handled by launchChromiumUrl above) that falling back to a
          // single direct launch is an acceptable simplification.
          const args = browser.args.map((arg) => arg.replace("%1", url));
          spawnDetached(browser.exe, args);
        } else {
          await launchChromiumUrl(browser.exe, browser.args, url);
        }
        return { ok: true };
      }
    } catch (error) {
      console.error("Could not launch the default browser directly:", error);
    }
    // Both explicit resolution paths failed. shell.openExternal() is
    // deliberately NOT tried as a last resort here: per the comment above
    // getDefaultBrowserLaunch, chrome:// isn't a registered OS protocol on
    // Windows, so ShellExecute resolves "successfully" while opening
    // nothing visible -- reporting ok:true here would silently lie to the
    // caller and skip the renderer's copy-to-clipboard fallback that's
    // supposed to catch exactly this case.
    return { ok: false, error: "no_browser_found" };
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
  "https://aistudio.google.com/app/apikey",
  "https://console.anthropic.com/settings/keys",
  "https://platform.openai.com/api-keys",
  "https://console.groq.com/keys",
  "https://openrouter.ai/settings/keys",
  // Placeholder Gumroad Pro product URL (matches CLIP_PULL_GUMROAD_PERMALINK's
  // placeholder default) -- update to the real product URL once it exists.
  "https://gumroad.com/l/clippull-pro-placeholder",
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

// Generic "save this text content to a file the user picks" -- used by the
// Insights CSV export (and available for any future plain-text export)
// rather than relying on the browser's own download mechanics, which this
// app doesn't otherwise use anywhere (every existing export writes via the
// backend or, like save-extension-package above, a native save dialog).
ipcMain.handle("save-text-file", async (_, { content, defaultFilename, filters }) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: defaultFilename,
    filters: filters || [{ name: "All Files", extensions: ["*"] }],
  });
  if (result.canceled || !result.filePath) {
    return { ok: false, error: "cancelled" };
  }
  fs.writeFileSync(result.filePath, content, "utf-8");
  return { ok: true, path: result.filePath };
});

app.whenReady().then(() => {
  // First thing, before any backend work even starts -- see
  // createSplashWindow's comment for why.
  createSplashWindow();
  Menu.setApplicationMenu(null);
  // Transparently attaches the auth token to every request the renderer's
  // fetch() calls send to the backend, so frontend/*.js never needs to know
  // about it -- this only touches requests originating from THIS Electron
  // session, an external browser tab has no way to trigger it or learn the
  // token. Registered before spawnBackend/createWindow so it's in place
  // before the renderer can possibly issue its first request.
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: [`http://127.0.0.1:${BACKEND_PORT}/*`] },
    (details, callback) => {
      details.requestHeaders["X-CLIP-PULL-Token"] = API_TOKEN;
      callback({ requestHeaders: details.requestHeaders });
    }
  );
  // Checked before spawnBackend() runs, since that's what creates this file
  // -- see FIRST_RUN_RETRY_COUNT's comment for why a first-ever launch gets
  // a longer budget before falling back to the "having trouble starting"
  // dialog.
  const isFirstRun = app.isPackaged && !fs.existsSync(getDbPath());
  spawnBackend();
  waitForBackend(isFirstRun ? FIRST_RUN_RETRY_COUNT : NORMAL_RETRY_COUNT, createWindow);
});

// Defense-in-depth, not a fix for anything reachable today: the app never
// navigates or opens a new window on its own, but if a future regression
// ever did (or a dependency tried to), these deny it outright rather than
// silently letting a BrowserWindow full of window.api's bridged APIs open
// against arbitrary/remote content.
app.on("web-contents-created", (_event, contents) => {
  // Tab switching (Queue/History/Settings/...) is DOM-based, not real page
  // navigation, and the window never loads anything but its own initial
  // frontend/index.html -- so there is no legitimate will-navigate to allow.
  contents.on("will-navigate", (navigationEvent) => navigationEvent.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
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
