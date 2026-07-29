"""Entry point — Launch the PLC Tag Monitor web dashboard."""
import sys
import os

# Ensure PyInstaller frozen bundle path is in sys.path
if getattr(sys, 'frozen', False):
    bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

import uvicorn
from config import WEB_HOST, WEB_PORT
from app import app


if __name__ == "__main__":
    print(f"""
    +----------------------------------------------+
    |       PLC TAG MONITOR - Web Dashboard        |
    |  ------------------------------------------- |
    |  Open http://localhost:{WEB_PORT} in your browser  |
    +----------------------------------------------+
    """)
    uvicorn.run(
        app,
        host=WEB_HOST,
        port=WEB_PORT,
        reload=False,
        log_level="info",
    )

