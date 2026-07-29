/**
 * main.js — Electron Main Process
 *
 * Responsibilities:
 *  1. Spawn the Python FastAPI backend as a child process
 *  2. Wait for the backend to be ready (poll /api/status)
 *  3. Show a splash screen while the backend boots
 *  4. Create the main BrowserWindow and load http://localhost:8000
 *  5. Set up system tray so the app keeps running (and collecting) when minimized
 *  6. Kill the Python process cleanly on quit
 */

const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');

// ─── Config ───────────────────────────────────────────────────
const BACKEND_PORT = process.env.WEB_PORT || 8000;
const BACKEND_URL  = `http://localhost:${BACKEND_PORT}`;
const MAX_RETRIES  = 40;   // 40 × 1s = 40 seconds max wait
const RETRY_DELAY  = 1000; // ms

// ─── State ────────────────────────────────────────────────────
let mainWindow    = null;
let splashWindow  = null;
let tray          = null;
let pythonProcess = null;
app.isQuitting    = false;

// ══════════════════════════════════════════════════════════════
// BACKEND — Spawn Python FastAPI (Executable or Script)
// ══════════════════════════════════════════════════════════════
function startBackend() {
  // When packaged: backend is in process.resourcesPath/backend/
  // When in dev:   backend is one directory up from desktop/
  const backendDir = app.isPackaged
    ? path.join(process.resourcesPath, 'backend')
    : path.join(__dirname, '..');

  // Check for standalone PyInstaller compiled executable
  const exeCandidates = [
    path.join(backendDir, 'run_web', 'run_web.exe'),
    path.join(backendDir, 'run_web.exe'),
    path.join(__dirname, '..', 'dist_backend', 'run_web', 'run_web.exe'),
  ];

  let binaryCmd = null;
  for (const candidate of exeCandidates) {
    if (fs.existsSync(candidate)) {
      binaryCmd = candidate;
      console.log(`[Electron] Found standalone backend executable: ${binaryCmd}`);
      break;
    }
  }

  if (binaryCmd) {
    console.log(`[Electron] Starting compiled backend on port ${BACKEND_PORT}`);
    pythonProcess = spawn(binaryCmd, [], {
      cwd: path.dirname(binaryCmd),
      stdio: 'pipe',
      windowsHide: true,
      env: { ...process.env, WEB_PORT: String(BACKEND_PORT) },
    });
  } else {
    // Fallback to python script execution
    const scriptPath = path.join(backendDir, 'run_web.py');
    const venvCandidates = [
      path.join(backendDir, '.venv', 'Scripts', 'python.exe'),
      path.join(backendDir, 'venv',  'Scripts', 'python.exe'),
      path.join(backendDir, '.venv', 'bin', 'python'),
      path.join(backendDir, 'venv',  'bin', 'python'),
    ];

    let pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
    for (const candidate of venvCandidates) {
      if (fs.existsSync(candidate)) {
        pythonCmd = candidate;
        console.log(`[Electron] Using venv Python: ${pythonCmd}`);
        break;
      }
    }

    console.log(`[Electron] Starting backend via Python: ${pythonCmd} ${scriptPath}`);
    pythonProcess = spawn(pythonCmd, [scriptPath], {
      cwd: backendDir,
      stdio: 'pipe',
      windowsHide: true,
      env: { ...process.env, WEB_PORT: String(BACKEND_PORT) },
    });
  }

  let backendLogs = [];
  let backendExitedEarly = false;
  let backendExitMessage = '';

  pythonProcess.stdout.on('data', (data) => {
    const text = data.toString();
    backendLogs.push(text);
    if (backendLogs.length > 50) backendLogs.shift();
    process.stdout.write(`[Backend] ${text}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    const text = data.toString();
    backendLogs.push(text);
    if (backendLogs.length > 50) backendLogs.shift();
    process.stderr.write(`[Backend] ${text}`);
  });

  pythonProcess.on('exit', (code, signal) => {
    console.log(`[Electron] Backend process exited — code=${code}, signal=${signal}`);
    backendExitedEarly = true;
    backendExitMessage = `Backend process exited with code ${code}. ${backendLogs.slice(-3).join(' ')}`;
    if (!app.isQuitting && mainWindow) {
      mainWindow.webContents.executeJavaScript(
        `document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;background:#080c14;color:#e8edf5;font-family:Inter,sans-serif;flex-direction:column;gap:16px"><h2>Backend process stopped</h2><p style="color:#576070">Restart the application to reconnect.</p></div>'`
      ).catch(() => {});
    }
  });

  pythonProcess.on('error', (err) => {
    console.error(`[Electron] Failed to spawn backend process: ${err.message}`);
    backendExitedEarly = true;
    backendExitMessage = `Failed to spawn backend process: ${err.message}`;
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.webContents.executeJavaScript(
        `document.getElementById('status').textContent = 'Error: Could not start backend process.'`
      ).catch(() => {});
    }
  });
}

// ══════════════════════════════════════════════════════════════
// BACKEND — Wait until /api/status responds 200
// ══════════════════════════════════════════════════════════════
function waitForBackend() {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    let resolved  = false;

    const tryConnect = () => {
      if (pythonProcess && pythonProcess.exitCode !== null) {
        reject(new Error(`Backend exited early: ${pythonProcess.exitCode}`));
        return;
      }

      const req = http.get(`${BACKEND_URL}/api/status`, (res) => {
        if (resolved) return;
        if (res.statusCode === 200) {
          resolved = true;
          console.log(`[Electron] Backend ready after ${attempts} attempts`);
          resolve();
        } else {
          scheduleRetry();
        }
        res.resume(); // drain the response
      });

      req.on('error', scheduleRetry);
      req.setTimeout(800, () => { req.destroy(); scheduleRetry(); });
    };

    const scheduleRetry = () => {
      if (resolved) return;
      attempts++;
      if (attempts >= MAX_RETRIES) {
        reject(new Error(`Backend did not respond after ${MAX_RETRIES} seconds`));
        return;
      }

      // Update splash with attempt count
      if (splashWindow && !splashWindow.isDestroyed()) {
        splashWindow.webContents.executeJavaScript(
          `document.getElementById('status').textContent = 'Starting backend… (${attempts}s)'`
        ).catch(() => {});
      }

      setTimeout(tryConnect, RETRY_DELAY);
    };

    tryConnect();
  });
}



// ══════════════════════════════════════════════════════════════
// WINDOWS — Splash & Main
// ══════════════════════════════════════════════════════════════
function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 440,
    height: 260,
    frame: false,
    transparent: false,
    resizable: false,
    center: true,
    alwaysOnTop: true,
    backgroundColor: '#080c14',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 880,
    minWidth: 1000,
    minHeight: 620,
    show: false,
    title: 'PLC Tag Monitor',
    backgroundColor: '#080c14',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadURL(`${BACKEND_URL}?mode=desktop`);

  // Remove default menu bar (keep keyboard shortcuts via preload)
  mainWindow.setMenuBarVisibility(false);

  mainWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
    }
    mainWindow.show();
    mainWindow.focus();
  });

  // Minimize to tray instead of closing
  mainWindow.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      if (tray) {
        tray.displayBalloon({
          iconType: 'info',
          title: 'PLC Tag Monitor',
          content: 'Still collecting data in the background. Right-click the tray icon to quit.',
        });
      }
    }
  });

  // Open external links in browser, not Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

// ══════════════════════════════════════════════════════════════
// TRAY
// ══════════════════════════════════════════════════════════════
function createTray() {
  let icon;
  const iconPath = path.join(__dirname, 'icon.png');
  if (fs.existsSync(iconPath)) {
    icon = nativeImage.createFromPath(iconPath);
  } else {
    icon = nativeImage.createEmpty();
  }

  tray = new Tray(icon);

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show Dashboard',
      click: () => {
        if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
      },
    },
    { type: 'separator' },
    {
      label: 'Open Web Dashboard',
      click: () => shell.openExternal(BACKEND_URL),
    },
    {
      label: 'Open Config Directory (%APPDATA%)',
      click: () => {
        const appData = process.env.APPDATA || (process.platform === 'darwin' ? path.join(process.env.HOME, 'Library', 'Preferences') : path.join(process.env.HOME, '.config'));
        const configPath = path.join(appData, 'PLC Tag Monitor');
        if (!fs.existsSync(configPath)) {
          fs.mkdirSync(configPath, { recursive: true });
        }
        shell.openPath(configPath);
      },
    },
    { type: 'separator' },
    {
      label: 'Quit PLC Monitor',
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setToolTip('PLC Tag Monitor — Collecting Data');
  tray.setContextMenu(contextMenu);

  tray.on('double-click', () => {
    if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
  });
}

// ══════════════════════════════════════════════════════════════
// IPC HANDLERS
// ══════════════════════════════════════════════════════════════
ipcMain.handle('app:version', () => app.getVersion());
ipcMain.handle('app:quit', () => {
  app.isQuitting = true;
  app.quit();
});

function checkBackendAlreadyRunning() {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/api/status`, (res) => {
      if (res.statusCode === 200) {
        resolve(true);
      } else {
        resolve(false);
      }
      res.resume();
    });
    req.on('error', () => resolve(false));
    req.setTimeout(600, () => { req.destroy(); resolve(false); });
  });
}

// ══════════════════════════════════════════════════════════════
// APP LIFECYCLE
// ══════════════════════════════════════════════════════════════
app.whenReady().then(async () => {
  createSplashWindow();

  const isRunning = await checkBackendAlreadyRunning();
  if (isRunning) {
    console.log(`[Electron] Backend is already running on ${BACKEND_URL}`);
  } else {
    startBackend();
    try {
      await waitForBackend();
    } catch (err) {
      console.error('[Electron] Backend timeout:', err.message);
    }
  }

  createMainWindow();
  createTray();
});

// Don't quit when all windows are closed — keep collecting in tray
app.on('window-all-closed', (event) => {
  if (!app.isQuitting) event.preventDefault();
});

app.on('will-quit', () => {
  app.isQuitting = true;
  if (pythonProcess) {
    console.log('[Electron] Killing Python backend…');
    pythonProcess.kill('SIGTERM');
    // Force kill after 3s if it doesn't respond
    setTimeout(() => {
      if (pythonProcess) pythonProcess.kill('SIGKILL');
    }, 3000);
  }
  if (tray) tray.destroy();
});

app.on('activate', () => {
  if (mainWindow) mainWindow.show();
});
