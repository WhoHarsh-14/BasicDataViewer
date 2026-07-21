"""Entry point — Launch the PLC Tag Monitor web dashboard."""
import uvicorn
from config import WEB_HOST, WEB_PORT

if __name__ == "__main__":
    print(f"""
    +----------------------------------------------+
    |       PLC TAG MONITOR - Web Dashboard        |
    |  ------------------------------------------- |
    |  Open http://localhost:{WEB_PORT} in your browser  |
    +----------------------------------------------+
    """)
    uvicorn.run(
        "app:app",
        host=WEB_HOST,
        port=WEB_PORT,
        reload=False,
        log_level="info",
    )
