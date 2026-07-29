"""
run_website.py — Independent Web Reporting Dashboard & Analytics Server

Runs the web reporting frontend and database reporting API.
This is separate from the Desktop Backend Data Collector app.
"""
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import uvicorn
from app import app

WEB_HOST = os.getenv("WEBSITE_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEBSITE_PORT", "8080"))

if __name__ == "__main__":
    print(f"""
    ===================================================
     PLC TAG MONITOR — Web Reporting & Analytics Server
    ---------------------------------------------------
     Fetching data & generating reports from Database.
     Open: http://localhost:{WEB_PORT}
    ===================================================
    """)
    uvicorn.run(
        app,
        host=WEB_HOST,
        port=WEB_PORT,
        reload=False,
        log_level="info",
    )
