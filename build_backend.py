"""
build_backend.py — Compiles the Python FastAPI server into a standalone Windows executable directory via PyInstaller.

Output: MonitoringSystem/dist_backend/run_web/run_web.exe
"""
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist_backend")
BUILD_DIR = os.path.join(BASE_DIR, "build_backend")

def build():
    print("==================================================")
    print(" Building Standalone Backend Executable (PyInstaller)")
    print("==================================================")

    # Clean previous builds
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR, ignore_errors=True)
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR, ignore_errors=True)

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name=run_web",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
        "--noconfirm",
        "--onedir",
        "--console",
        f"--paths={BASE_DIR}",
        # Data files
        f"--add-data={os.path.join(BASE_DIR, 'static')}{os.pathsep}static",
        f"--add-data={os.path.join(BASE_DIR, 'system_config.json')}{os.pathsep}.",
        f"--add-data={os.path.join(BASE_DIR, 'line_config.json')}{os.pathsep}.",
        f"--add-data={os.path.join(BASE_DIR, 'register_config.json')}{os.pathsep}.",
        f"--add-data={os.path.join(BASE_DIR, 'app.py')}{os.pathsep}.",
        f"--add-data={os.path.join(BASE_DIR, 'config.py')}{os.pathsep}.",
        f"--add-data={os.path.join(BASE_DIR, 'database.py')}{os.pathsep}.",
        f"--add-data={os.path.join(BASE_DIR, 'plc_poller.py')}{os.pathsep}.",
        f"--add-data={os.path.join(BASE_DIR, 'shift_manager.py')}{os.pathsep}.",
        f"--add-data={os.path.join(BASE_DIR, 'schemas.py')}{os.pathsep}.",
        # Hidden imports
        "--hidden-import=fastapi",
        "--hidden-import=uvicorn",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=sqlalchemy.ext.asyncio",
        "--hidden-import=sqlalchemy.dialects.postgresql.asyncpg",
        "--hidden-import=sqlalchemy.dialects.postgresql.psycopg2",
        "--hidden-import=sqlalchemy.dialects.sqlite.aiosqlite",
        "--hidden-import=asyncpg",
        "--hidden-import=psycopg2",
        "--hidden-import=aiosqlite",
        "--hidden-import=sqlite3",
        "--hidden-import=pymcprotocol",
        "--hidden-import=openpyxl",
        "--hidden-import=websockets",
        "--hidden-import=pydantic",
        "--hidden-import=app",
        "--hidden-import=database",
        "--hidden-import=config",
        "--hidden-import=plc_poller",
        "--hidden-import=shift_manager",
        "--hidden-import=schemas",
        os.path.join(BASE_DIR, "run_web.py")
    ]


    print(f"Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=BASE_DIR)

    if result.returncode == 0:
        exe_path = os.path.join(DIST_DIR, "run_web", "run_web.exe")
        print("\n==================================================")
        print(f" SUCCESS: Standalone backend built at:\n {exe_path}")
        print("==================================================\n")
    else:
        print("\nBUILD FAILED!")
        sys.exit(result.returncode)

if __name__ == "__main__":
    build()
