"""
build_windows_installer.py — One-click Master Build Script for PLC Tag Monitor Windows Installer.

Process:
  1. Compiles Python backend into a standalone Windows binary (dist_backend/run_web/run_web.exe)
  2. Runs Electron Builder to generate NSIS Installer (desktop/dist/PLC-Tag-Monitor-Setup-1.0.0.exe)
"""
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DESKTOP_DIR = os.path.join(BASE_DIR, "desktop")

def main():
    print("==================================================")
    print(" STEP 1: Building Standalone Backend (PyInstaller)")
    print("==================================================")

    backend_script = os.path.join(BASE_DIR, "build_backend.py")
    res1 = subprocess.run([sys.executable, backend_script], cwd=BASE_DIR)
    if res1.returncode != 0:
        print("Backend build failed! Aborting installer build.")
        sys.exit(res1.returncode)

    print("\n==================================================")
    print(" STEP 2: Building Electron NSIS Windows Installer")
    print("==================================================")

    # Check npx electron-builder
    npx_cmd = "npx.cmd" if os.name == "nt" else "npx"
    builder_cmd = [npx_cmd, "electron-builder", "--win"]

    print(f"Running command in desktop/: {' '.join(builder_cmd)}")
    res2 = subprocess.run(builder_cmd, cwd=DESKTOP_DIR, shell=True)

    if res2.returncode == 0:
        dist_dir = os.path.join(DESKTOP_DIR, "dist")
        print("\n==================================================")
        print(" BUILD COMPLETE!")
        print(f" Windows Installer created in: {dist_dir}")
        print("==================================================\n")
    else:
        print("\nElectron Builder failed!")
        sys.exit(res2.returncode)

if __name__ == "__main__":
    main()
