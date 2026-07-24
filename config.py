"""Centralized configuration for PLC Tag Monitor.

Loads all system settings (Database, PLC, Shift Management, Admin, Server)
from system_config.json stored in %APPDATA%/PLC Tag Monitor/ or local app directory.
All values can be overridden by environment variables.
"""
import os
import json
import logging
from typing import Any, Dict

logger = logging.getLogger("config")


def get_config_dir() -> str:
    """Get writable directory for system configuration and WAL storage."""
    env_dir = os.getenv("CONFIG_DIR")
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return env_dir

    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        if appdata:
            path = os.path.join(appdata, "PLC Tag Monitor")
            os.makedirs(path, exist_ok=True)
            return path

    # Fallback to local script directory
    local_dir = os.path.dirname(os.path.abspath(__file__))
    return local_dir


CONFIG_DIR = get_config_dir()
SYSTEM_CONFIG_PATH = os.path.join(CONFIG_DIR, "system_config.json")


def get_config_file_path(filename: str) -> str:
    """Resolve file path inside CONFIG_DIR, initializing from local template if needed."""
    target_path = os.path.join(CONFIG_DIR, filename)
    if not os.path.exists(target_path):
        local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        if os.path.exists(local_path) and local_path != target_path:
            try:
                with open(local_path, "r", encoding="utf-8") as src, open(target_path, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
            except Exception as e:
                logger.warning(f"Could not copy {filename} to {target_path}: {e}")
    return target_path


# Default comprehensive configuration structure
DEFAULT_SYSTEM_CONFIG: Dict[str, Any] = {
    "plc": {
        "ip": "192.168.1.10",
        "port": 5000,
        "poll_interval_seconds": 0.5,
        "simulation_mode": True
    },
    "database": {
        "url": "postgresql+asyncpg://neondb_owner:npg_Ez2geFGf5XuH@ep-square-frost-ao525947.c-2.ap-southeast-1.aws.neon.tech/neondb?ssl=require",
        "url_sync": "postgresql+psycopg2://neondb_owner:npg_Ez2geFGf5XuH@ep-square-frost-ao525947.c-2.ap-southeast-1.aws.neon.tech/neondb?ssl=require",
        "type": "postgresql",
        "host": "localhost",
        "port": 5432,
        "name": "tag_monitor",
        "user": "postgres",
        "password": "postgres"
    },
    "shift": {
        "shift_duration_hours": 8,
        "shift_start_hour": 6
    },
    "admin": {
        "user": "admin",
        "pass": "admin123"
    },
    "server": {
        "web_host": "0.0.0.0",
        "web_port": 8000
    }
}


def _deep_merge(default: dict, override: dict) -> dict:
    """Recursively merge configuration dictionaries."""
    res = dict(default)
    for k, v in override.items():
        if k in res and isinstance(res[k], dict) and isinstance(v, dict):
            res[k] = _deep_merge(res[k], v)
        else:
            res[k] = v
    return res


def load_system_config() -> dict:
    """Load system_config.json from CONFIG_DIR, falling back to defaults."""
    cfg_file = SYSTEM_CONFIG_PATH
    loaded_cfg = {}

    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                loaded_cfg = json.load(f)
            logger.info(f"✓ Loaded system_config.json from {cfg_file}")
        except Exception as e:
            logger.warning(f"Failed to parse system_config.json from {cfg_file} ({e}), using defaults")

    # Handle legacy flat JSON config compatibility
    flattened_defaults = dict(DEFAULT_SYSTEM_CONFIG)
    if "poll_interval_seconds" in loaded_cfg and "plc" not in loaded_cfg:
        loaded_cfg = {
            "plc": {
                "poll_interval_seconds": loaded_cfg.get("poll_interval_seconds", 0.5),
                "ip": loaded_cfg.get("plc_ip", "192.168.1.10"),
                "port": loaded_cfg.get("plc_port", 5000),
                "simulation_mode": loaded_cfg.get("simulation_mode", True),
            },
            "shift": {
                "shift_duration_hours": loaded_cfg.get("shift_duration_hours", 8),
                "shift_start_hour": loaded_cfg.get("shift_start_hour", 6),
            }
        }

    merged = _deep_merge(DEFAULT_SYSTEM_CONFIG, loaded_cfg)

    # Write merged configuration back if missing or updated
    try:
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save merged system_config.json: {e}")

    return merged


_sys_cfg = load_system_config()


def _apply_globals(cfg: dict):
    """Extract and assign global config variables from loaded config dict."""
    global PLC_IP, PLC_PORT, PLC_POLL_INTERVAL, SIMULATION_MODE
    global DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS, DB_TYPE, DATABASE_URL, DATABASE_URL_SYNC
    global SHIFT_DURATION_HOURS, SHIFT_START_HOUR
    global ADMIN_USER, ADMIN_PASS
    global WEB_HOST, WEB_PORT

    plc = cfg.get("plc", {})
    db = cfg.get("database", {})
    shift = cfg.get("shift", {})
    admin = cfg.get("admin", {})
    server = cfg.get("server", {})

    # PLC
    PLC_IP = os.getenv("PLC_IP", str(plc.get("ip", "192.168.1.10")))
    PLC_PORT = int(os.getenv("PLC_PORT", str(plc.get("port", 5000))))
    PLC_POLL_INTERVAL = float(os.getenv("PLC_POLL_INTERVAL", str(plc.get("poll_interval_seconds", 0.5))))
    sim_env = os.getenv("SIMULATION_MODE")
    if sim_env is not None:
        SIMULATION_MODE = sim_env.lower() == "true"
    else:
        SIMULATION_MODE = bool(plc.get("simulation_mode", True))

    # DB
    DB_TYPE = os.getenv("DB_TYPE", str(db.get("type", "postgresql")))
    DB_HOST = os.getenv("DB_HOST", str(db.get("host", "localhost")))
    DB_PORT = os.getenv("DB_PORT", str(db.get("port", 5432)))
    DB_NAME = os.getenv("DB_NAME", str(db.get("name", "tag_monitor")))
    DB_USER = os.getenv("DB_USER", str(db.get("user", "postgres")))
    DB_PASS = os.getenv("DB_PASS", str(db.get("password", "postgres")))

    default_async_url = f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    default_sync_url = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    DATABASE_URL = os.getenv("DATABASE_URL", db.get("url") or default_async_url)
    DATABASE_URL_SYNC = os.getenv("DATABASE_URL_SYNC", db.get("url_sync") or default_sync_url)

    # Shift
    SHIFT_DURATION_HOURS = int(os.getenv("SHIFT_DURATION_HOURS", str(shift.get("shift_duration_hours", 8))))
    SHIFT_START_HOUR = int(os.getenv("SHIFT_START_HOUR", str(shift.get("shift_start_hour", 6))))

    # Admin
    ADMIN_USER = os.getenv("ADMIN_USER", str(admin.get("user", "admin")))
    ADMIN_PASS = os.getenv("ADMIN_PASS", str(admin.get("pass", "admin123")))

    # Server
    WEB_HOST = os.getenv("WEB_HOST", str(server.get("web_host", "0.0.0.0")))
    WEB_PORT = int(os.getenv("WEB_PORT", str(server.get("web_port", 8000))))


_apply_globals(_sys_cfg)


def save_system_config(new_config: dict) -> dict:
    """Save updated system configuration to system_config.json and reload globals."""
    global _sys_cfg
    merged = _deep_merge(_sys_cfg, new_config)
    with open(SYSTEM_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    _sys_cfg = merged
    _apply_globals(_sys_cfg)
    logger.info(f"✓ Saved updated system_config.json to {SYSTEM_CONFIG_PATH}")
    return _sys_cfg

