# PLC Tag Monitor — Desktop App

Electron wrapper that spawns the Python FastAPI backend as a subprocess and displays the dashboard in a native window.

## Quick Start

```powershell
# 1. Install the Python dependencies (if not already done)
cd ..
pip install -r requirements.txt

# 2. Install Electron
cd desktop
npm install

# 3. Run in dev mode
npm start
```

## How it works

1. Electron launches `python run_web.py` in the parent directory
2. A splash screen is shown while Python boots up (~2-3 seconds)
3. Once `http://localhost:8000/api/status` responds, the main window opens
4. The main window is just an Electron BrowserWindow loading the local FastAPI

## Tray behavior

Closing the window **does not stop data collection**. The app minimizes to the system tray and keeps polling the PLC every 500ms. Right-click the tray icon → **Quit PLC Monitor** to fully stop.

## Adding a tray icon

Place a `icon.png` (32×32 or 64×64 px) in this `desktop/` folder. The app works without it but the tray icon will be empty.

## Build a Windows installer

```powershell
npm run build:win
# Output: desktop/dist/
```

> **Note**: The packaged build bundles the Python backend via `electron-builder`'s `extraResources`. You still need Python installed on the target machine.
