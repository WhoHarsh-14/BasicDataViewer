"""Centralized configuration for PLC Tag Monitor.

Loads operator-tunable settings from system_config.json at startup.
All values can be overridden by environment variables.
"""
import os
import json
import logging

logger = logging.getLogger("config")

# ─── Load system_config.json ───
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "system_config.json")
_DEFAULT_SYSTEM_CONFIG = {
    "poll_interval_seconds": 0.02,
    "shift_duration_hours": 8,
    "shift_start_hour": 6,
}

def _load_system_config() -> dict:
    """Load system_config.json, falling back to defaults on error."""
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            # Merge with defaults to handle any missing keys
            merged = {**_DEFAULT_SYSTEM_CONFIG, **cfg}
            logger.info(f"✓ Loaded system_config.json: {merged}")
            return merged
        except Exception as e:
            logger.warning(f"Failed to parse system_config.json ({e}), using defaults")
    return dict(_DEFAULT_SYSTEM_CONFIG)

_sys_cfg = _load_system_config()

# ─── Shift / Polling Settings (from system_config.json) ───
PLC_POLL_INTERVAL: float = float(os.getenv("PLC_POLL_INTERVAL", str(_sys_cfg["poll_interval_seconds"])))
SHIFT_DURATION_HOURS: int = int(os.getenv("SHIFT_DURATION_HOURS", str(_sys_cfg["shift_duration_hours"])))
SHIFT_START_HOUR: int = int(os.getenv("SHIFT_START_HOUR", str(_sys_cfg["shift_start_hour"])))

# ─── PLC Configuration ───
PLC_IP = os.getenv("PLC_IP", "192.168.1.10")
PLC_PORT = int(os.getenv("PLC_PORT", "5000"))

# ─── Administration Credentials ───
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")

# ─── PostgreSQL Configuration ───
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "tag_monitor")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

DATABASE_URL = "postgresql+asyncpg://neondb_owner:npg_Ez2geFGf5XuH@ep-square-frost-ao525947.c-2.ap-southeast-1.aws.neon.tech/neondb?ssl=require"
DATABASE_URL_SYNC = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ─── Simulation Mode ───
# When True, generates fake PLC data (no physical PLC required)
SIMULATION_MODE = os.getenv("SIMULATION_MODE", "true").lower() == "true"

# ─── Server Configuration ───
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
