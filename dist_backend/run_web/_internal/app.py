"""
FastAPI Backend — REST API + WebSocket for PLC Tag Monitor.

Serves the web dashboard and provides endpoints for:
- Live data streaming (WebSocket)
- Historical readings (REST)
- Drawing pad commands (REST)
- Shift management (force-end, status)
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import Optional
from pydantic import BaseModel

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, desc, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
import json

from config import (
    SIMULATION_MODE, PLC_IP, PLC_PORT, ADMIN_USER, ADMIN_PASS,
    load_system_config, save_system_config, SYSTEM_CONFIG_PATH, get_config_file_path
)
from database import (
    init_db, get_session, async_session_factory, Base,
    PLCReading, BitInput, BitOutput, WordRegister, DrawCommand, Company, ShiftRecord, ShiftSummary
)
from schemas import (
    PLCReadingSchema, PLCReadingSummary, RegisterHistoryPoint,
    DrawCommandCreate, DrawCommandSchema
)
from plc_poller import poller
from shift_manager import shift_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("app")


# ══════════════════════════════════════════════════════════════
# LIFESPAN — Start/stop the PLC poller and ShiftManager
# ══════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("✓ Database tables created")

    # Initialize shift manager (handles crash recovery)
    await shift_manager.initialize()
    logger.info("✓ ShiftManager initialized")

    poller_task = asyncio.create_task(poller.run())
    logger.info("✓ PLC Poller launched")

    yield

    # Graceful shutdown — save WAL without committing (shift resumes on next start)
    poller.stop()
    poller_task.cancel()
    await shift_manager.shutdown()
    logger.info("✓ Shutdown complete")


app = FastAPI(
    title="PLC Tag Monitor",
    description="Mitsubishi PLC monitoring dashboard with shift-based CSV buffering",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Serve static files (web dashboard)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# ══════════════════════════════════════════════════════════════
# PAGES — Serve the SPA
# ══════════════════════════════════════════════════════════════
@app.get("/", include_in_schema=False)
async def serve_dashboard():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


# ══════════════════════════════════════════════════════════════
# SYSTEM STATUS & CONNECTION
# ══════════════════════════════════════════════════════════════
@app.get("/api/status")
async def get_status():
    """Current system status including PLC connection and config."""
    cfg = load_system_config()
    return {
        "plc_connected": poller.connected,
        "simulation_mode": poller.simulation_mode,
        "plc_ip": poller.plc_ip,
        "plc_port": poller.plc_port,
        "websocket_clients": len(poller.websocket_clients),
        "config_path": SYSTEM_CONFIG_PATH,
        "database": cfg.get("database", {}),
    }


@app.get("/api/config")
async def get_system_config():
    """Get complete system_config.json configuration."""
    return {
        "config_path": SYSTEM_CONFIG_PATH,
        "config": load_system_config(),
    }


@app.post("/api/config")
async def update_system_config(new_config: dict):
    """Update system_config.json dynamically and reapply configuration settings."""
    try:
        updated = save_system_config(new_config)
        # Apply updated PLC target to poller if present
        plc_cfg = updated.get("plc", {})
        poller.set_target(
            plc_cfg.get("ip", poller.plc_ip),
            int(plc_cfg.get("port", poller.plc_port)),
            bool(plc_cfg.get("simulation_mode", poller.simulation_mode))
        )
        return {
            "status": "success",
            "message": f"Saved system_config.json to {SYSTEM_CONFIG_PATH}",
            "config": updated
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")



class PLCConnectRequest(BaseModel):
    ip: str
    port: int
    simulation: bool


@app.post("/api/plc/connect")
async def connect_plc(req: PLCConnectRequest):
    """Dynamically set the polling target PLC."""
    poller.set_target(req.ip, req.port, req.simulation)
    return {
        "status": "success",
        "plc_ip": req.ip,
        "plc_port": req.port,
        "simulation_mode": req.simulation
    }


@app.post("/api/plc/disconnect")
async def disconnect_plc():
    """Reset the poller target to clear connections."""
    poller.set_target(None, None, True)
    return {"status": "success"}


# ══════════════════════════════════════════════════════════════
# MULTI-PLC CONNECTION PRESETS & SWITCHING
# ══════════════════════════════════════════════════════════════
PLC_PRESETS = [
    {"id": "plc1", "name": "Line 1 PLC", "ip": "192.168.1.10", "port": 5000, "simulation": True},
    {"id": "plc2", "name": "Line 2 PLC", "ip": "192.168.1.11", "port": 5000, "simulation": True},
    {"id": "plc_sim", "name": "Simulation Sandbox", "ip": "127.0.0.1", "port": 5000, "simulation": True},
]


@app.get("/api/plc/presets")
async def get_plc_presets():
    """List configured PLC connection presets."""
    return PLC_PRESETS


class PLCSwitchRequest(BaseModel):
    preset_id: str


@app.post("/api/plc/switch")
async def switch_plc_preset(req: PLCSwitchRequest):
    """Quick switch between PLC connections via button/dropdown."""
    preset = next((p for p in PLC_PRESETS if p["id"] == req.preset_id), None)
    if not preset:
        raise HTTPException(status_code=404, detail="PLC preset not found")
    poller.set_target(preset["ip"], preset["port"], preset["simulation"])
    return {"status": "success", "active": preset}



# ══════════════════════════════════════════════════════════════
# WEBSOCKET — Live data stream
# ══════════════════════════════════════════════════════════════
@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    await ws.accept()
    poller.websocket_clients.add(ws)
    logger.info(f"WebSocket client connected ({len(poller.websocket_clients)} total)")
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        poller.websocket_clients.discard(ws)
        logger.info(f"WebSocket client disconnected ({len(poller.websocket_clients)} total)")


# ══════════════════════════════════════════════════════════════
# SHIFT MANAGEMENT
# ══════════════════════════════════════════════════════════════
@app.get("/api/shift/status")
async def get_shift_status():
    """Current shift status: start/end times, buffered rows, time remaining."""
    return shift_manager.get_status()


@app.post("/api/shift/force-end")
async def force_end_shift():
    """
    Force-end the current shift early.
    Commits buffered data to DB, writes CSV, and starts a new shift window.
    """
    try:
        result = await shift_manager.force_end_shift()
        return result
    except Exception as e:
        logger.error(f"Force-end shift error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/shift/history")
async def get_shift_history(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List past committed shifts with metadata."""
    result = await session.execute(
        select(ShiftRecord).order_by(desc(ShiftRecord.shift_start)).limit(limit)
    )
    records = result.scalars().all()
    return [
        {
            "id": r.id,
            "shift_start": r.shift_start.isoformat() if r.shift_start else None,
            "shift_end": r.shift_end.isoformat() if r.shift_end else None,
            "row_count": r.row_count,
            "csv_path": r.csv_path,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        }
        for r in records
    ]


# ══════════════════════════════════════════════════════════════
# SHIFT SUMMARIES & REPORTS (Day / Month / Year)
# ══════════════════════════════════════════════════════════════
@app.get("/api/shifts/summaries")
async def get_shift_summaries(
    date: Optional[str] = None,       # YYYY-MM-DD
    month: Optional[str] = None,      # YYYY-MM
    year: Optional[str] = None,       # YYYY
    shift_name: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Fetch shift summary records with date/month/year and shift filtering strictly from DB."""
    query = select(ShiftSummary).order_by(desc(ShiftSummary.date), desc(ShiftSummary.id))

    if date:
        query = query.where(ShiftSummary.date == date)
    elif month:
        query = query.where(ShiftSummary.date.like(f"{month}%"))
    elif year:
        query = query.where(ShiftSummary.date.like(f"{year}%"))

    if shift_name and shift_name != "all":
        query = query.where(ShiftSummary.shift_name.like(f"%{shift_name}%"))

    result = await session.execute(query)
    records = result.scalars().all()

    return [
        {
            "id": r.id,
            "date": r.date,
            "shift_name": r.shift_name,
            "line_id": r.line_id,
            "line_name": r.line_name,
            "plc_ip": r.plc_ip,
            "target": r.target,
            "production": r.production,
            "runtime_minutes": r.runtime_minutes,
            "idle_time_minutes": r.idle_time_minutes,
            "breakdown_time_minutes": r.breakdown_time_minutes,
            "efficiency": r.efficiency,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@app.delete("/api/shifts/summaries")
async def clear_shift_summaries(session: AsyncSession = Depends(get_session)):
    """Clear all shift summary records from database for a fresh reset."""
    try:
        await session.execute(delete(ShiftSummary))
        await session.commit()
        return {"status": "success", "message": "Cleared all shift summary history"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/shifts/monthly-report")
async def get_monthly_report(
    month: str = Query(..., description="Format YYYY-MM"),
    session: AsyncSession = Depends(get_session),
):
    """
    Generate combined monthly report aggregating all shifts of the specified month from DB.
    Includes totals, date-wise breakdowns, and metrics for pie charts.
    """
    query = select(ShiftSummary).where(ShiftSummary.date.like(f"{month}%")).order_by(ShiftSummary.date)
    result = await session.execute(query)
    records = result.scalars().all()

    total_target = sum(r.target for r in records)
    total_production = sum(r.production for r in records)
    total_runtime = sum(r.runtime_minutes for r in records)
    total_idle = sum(r.idle_time_minutes for r in records)
    total_breakdown = sum(r.breakdown_time_minutes for r in records)
    avg_efficiency = round(total_production / total_target * 100.0, 1) if total_target > 0 else 0.0

    daily_aggregated = {}
    for r in records:
        if r.date not in daily_aggregated:
            daily_aggregated[r.date] = {
                "date": r.date,
                "target": 0,
                "production": 0,
                "runtime_minutes": 0.0,
                "idle_time_minutes": 0.0,
                "breakdown_time_minutes": 0.0,
                "shifts_count": 0
            }
        d = daily_aggregated[r.date]
        d["target"] += r.target
        d["production"] += r.production
        d["runtime_minutes"] += r.runtime_minutes
        d["idle_time_minutes"] += r.idle_time_minutes
        d["breakdown_time_minutes"] += r.breakdown_time_minutes
        d["shifts_count"] += 1

    return {
        "month": month,
        "total_shifts": len(records),
        "total_target": total_target,
        "total_production": total_production,
        "total_runtime_hours": round(total_runtime / 60.0, 1),
        "total_idle_hours": round(total_idle / 60.0, 1),
        "total_breakdown_hours": round(total_breakdown / 60.0, 1),
        "overall_efficiency": avg_efficiency,
        "pie_chart_time_distribution": {
            "runtime_hours": round(total_runtime / 60.0, 1),
            "idle_hours": round(total_idle / 60.0, 1),
            "breakdown_hours": round(total_breakdown / 60.0, 1),
        },
        "pie_chart_production": {
            "achieved": total_production,
            "shortfall": max(0, total_target - total_production),
        },
        "daily_breakdown": list(daily_aggregated.values()),
        "shift_records": [
            {
                "id": r.id,
                "date": r.date,
                "shift_name": r.shift_name,
                "line_name": r.line_name,
                "target": r.target,
                "production": r.production,
                "runtime_minutes": r.runtime_minutes,
                "idle_time_minutes": r.idle_time_minutes,
                "breakdown_time_minutes": r.breakdown_time_minutes,
                "efficiency": r.efficiency
            }
            for r in records
        ]
    }



# ══════════════════════════════════════════════════════════════
# READINGS — Historical data
# ══════════════════════════════════════════════════════════════
@app.get("/api/readings", response_model=list[PLCReadingSummary])
async def get_readings(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    plc_ip: Optional[str] = None,
    plc_port: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    session: AsyncSession = Depends(get_session),
):
    """Paginated historical readings with optional filters."""
    query = select(PLCReading).order_by(desc(PLCReading.timestamp))

    ip = plc_ip or poller.plc_ip
    port = plc_port or poller.plc_port

    if ip:
        query = query.where(PLCReading.plc_ip == ip)
    if port:
        query = query.where(PLCReading.plc_port == port)
    if status:
        query = query.where(PLCReading.status == status)
    if start:
        query = query.where(PLCReading.timestamp >= start)
    if end:
        query = query.where(PLCReading.timestamp <= end)

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    readings = result.scalars().all()

    summaries = []
    for r in readings:
        regs = {}
        for wr in r.word_registers:
            regs[wr.address] = wr.value
        summaries.append(PLCReadingSummary(
            id=r.id,
            timestamp=r.timestamp,
            plc_ip=r.plc_ip,
            plc_port=r.plc_port,
            status=r.status,
            registers=regs,
        ))
    return summaries


@app.get("/api/readings/latest", response_model=Optional[PLCReadingSchema])
async def get_latest_reading(
    plc_ip: Optional[str] = None,
    plc_port: Optional[int] = None,
    session: AsyncSession = Depends(get_session)
):
    """Latest reading with all child data for the specified or active PLC."""
    ip = plc_ip or poller.plc_ip
    port = plc_port or poller.plc_port
    if not ip or not port:
        return None
    result = await session.execute(
        select(PLCReading)
        .where(PLCReading.plc_ip == ip)
        .where(PLCReading.plc_port == port)
        .order_by(desc(PLCReading.timestamp))
        .limit(1)
    )
    return result.scalar_one_or_none()


@app.get("/api/readings/count")
async def get_readings_count(
    plc_ip: Optional[str] = None,
    plc_port: Optional[int] = None,
    session: AsyncSession = Depends(get_session)
):
    """Total number of readings in the database."""
    query = select(func.count(PLCReading.id))
    ip = plc_ip or poller.plc_ip
    port = plc_port or poller.plc_port
    if ip:
        query = query.where(PLCReading.plc_ip == ip)
    if port:
        query = query.where(PLCReading.plc_port == port)
    result = await session.execute(query)
    return {"count": result.scalar()}


# ══════════════════════════════════════════════════════════════
# REGISTER HISTORY — Time-series for charting
# ══════════════════════════════════════════════════════════════
@app.get("/api/registers/history")
async def get_register_history(
    minutes: Optional[int] = Query(None, ge=1, le=1440),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    plc_ip: Optional[str] = None,
    plc_port: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
):
    """Register time-series data for charting."""
    ip = plc_ip or poller.plc_ip
    port = plc_port or poller.plc_port

    if not ip or not port:
        return {}

    if minutes is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        start_dt, end_dt = cutoff, datetime.now(timezone.utc)
    elif start is not None:
        start_dt = start
        end_dt = end if end is not None else datetime.now(timezone.utc)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        start_dt, end_dt = cutoff, datetime.now(timezone.utc)

    result = await session.execute(
        select(WordRegister)
        .join(PLCReading)
        .where(PLCReading.timestamp >= start_dt)
        .where(PLCReading.timestamp <= end_dt)
        .where(PLCReading.plc_ip == ip)
        .where(PLCReading.plc_port == port)
        .order_by(PLCReading.timestamp)
    )
    registers = result.scalars().all()

    series = {}
    for r in registers:
        if r.address not in series:
            series[r.address] = []
        if r.data_type != "string":
            val = r.value_float if r.value_float is not None else r.value_int
            if val is not None:
                series[r.address].append({
                    "timestamp": r.reading.timestamp.isoformat(),
                    "value": val,
                })
    return series


# ══════════════════════════════════════════════════════════════
# REGISTER CONFIGURATION — Add / modify registers to poll
# ══════════════════════════════════════════════════════════════
@app.get("/api/registers/config")
async def get_registers_config():
    """Get the current registers configuration."""
    return poller.registers_config


class RegisterConfigSave(BaseModel):
    config: list[dict]


@app.post("/api/registers/config")
async def save_registers_config(req: RegisterConfigSave):
    """Save new registers configuration and reload the poller."""
    try:
        with open(poller.config_path, 'w') as f:
            json.dump(req.config, f, indent=2)
        poller.load_registers_config()
        return {"status": "success", "monitored_count": len(poller.registers_config)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# SYSTEM CONFIG — Read/write system_config.json via API
# ══════════════════════════════════════════════════════════════
@app.get("/api/system/config")
async def get_system_config():
    """Get current system_config.json values."""
    try:
        return load_system_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SystemConfigUpdate(BaseModel):
    poll_interval_seconds: Optional[float] = None
    shift_duration_hours: Optional[int] = None
    shift_start_hour: Optional[int] = None


@app.post("/api/system/config")
async def update_system_config(req: SystemConfigUpdate):
    """
    Update system_config.json using writable %APPDATA% path.
    """
    try:
        current = load_system_config()
        if req.poll_interval_seconds is not None:
            current.setdefault("plc", {})["poll_interval_seconds"] = req.poll_interval_seconds
        if req.shift_duration_hours is not None:
            current.setdefault("shift", {})["shift_duration_hours"] = req.shift_duration_hours
        if req.shift_start_hour is not None:
            current.setdefault("shift", {})["shift_start_hour"] = req.shift_start_hour
        
        updated = save_system_config(current)

        import config
        from shift_manager import shift_manager, compute_shift_window
        if req.shift_duration_hours is not None:
            config.SHIFT_DURATION_HOURS = req.shift_duration_hours
        if req.shift_start_hour is not None:
            config.SHIFT_START_HOUR = req.shift_start_hour
        
        # Re-align current shift window
        now = datetime.now(timezone.utc)
        shift_manager.shift_start, shift_manager.shift_end = compute_shift_window(now)

        return {"status": "success", "config": updated, "note": "Updated shift configuration live"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# PRODUCTION LINES CONFIG — Read/write line_config.json via API
# ══════════════════════════════════════════════════════════════
@app.get("/api/lines/config")
async def get_lines_config():
    """Get current line_config.json values."""
    cfg_path = get_config_file_path("line_config.json")
    try:
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LinesConfigSave(BaseModel):
    config: list[dict]


@app.post("/api/lines/config")
async def save_lines_config(req: LinesConfigSave):
    """Save new production lines config and reload the poller."""
    cfg_path = get_config_file_path("line_config.json")
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(req.config, f, indent=2)
        poller.load_registers_config()
        poller.machine_states = {}
        return {"status": "success", "lines_count": len(req.config)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# DESKTOP BACKEND OVERVIEW API — For Desktop App Monitor UI
# ══════════════════════════════════════════════════════════════
@app.get("/api/backend/overview")
async def get_backend_overview(session: AsyncSession = Depends(get_session)):
    """Summary of backend state: PLC connection, DB info, shift status, and summary count."""
    import config
    shift_info = shift_manager.get_status()
    
    # Query total shift summaries recorded in DB
    try:
        count_res = await session.execute(select(func.count(ShiftSummary.id)))
        total_summaries = count_res.scalar() or 0
    except Exception:
        total_summaries = 0

    return {
        "status": "online",
        "plc": {
            "connected": poller.connected,
            "simulation_mode": poller.simulation_mode,
            "ip": poller.plc_ip,
            "port": poller.plc_port,
            "poll_interval": config.PLC_POLL_INTERVAL,
        },
        "database": {
            "type": config.DB_TYPE,
            "url": config.DATABASE_URL,
            "total_shift_summaries_pushed": total_summaries,
        },
        "shift": shift_info,
        "config_path": SYSTEM_CONFIG_PATH,
        "system_config": load_system_config(),
    }



# ══════════════════════════════════════════════════════════════
# PORTAL AUTHENTICATION & COMPANY MANAGEMENT
# ══════════════════════════════════════════════════════════════
class CompanyCreateSchema(BaseModel):
    name: str
    password: str
    plc_ip: str
    plc_port: int


class PortalLoginRequest(BaseModel):
    username: str
    password: str
    is_admin: bool


@app.get("/api/portal/companies")
async def get_portal_companies(session: AsyncSession = Depends(get_session)):
    """Get list of companies for the login dropdown."""
    result = await session.execute(select(Company).order_by(Company.name))
    companies = result.scalars().all()
    return [{"id": c.id, "name": c.name} for c in companies]


@app.post("/api/portal/login")
async def portal_login(req: PortalLoginRequest, session: AsyncSession = Depends(get_session)):
    """Login endpoint for portal."""
    if req.is_admin:
        if req.username == ADMIN_USER and req.password == ADMIN_PASS:
            return {"status": "success", "role": "admin"}
        return {"status": "error", "message": "Invalid admin credentials"}
    else:
        result = await session.execute(select(Company).where(Company.name == req.username))
        company = result.scalar_one_or_none()
        if company and company.password == req.password:
            return {
                "status": "success",
                "role": "company",
                "company_name": company.name,
                "plc_ip": company.plc_ip,
                "plc_port": company.plc_port
            }
        return {"status": "error", "message": "Invalid company credentials"}


@app.get("/api/portal/companies/manage")
async def manage_get_companies(session: AsyncSession = Depends(get_session)):
    """Admin CRUD: list all companies."""
    result = await session.execute(select(Company).order_by(Company.name))
    return result.scalars().all()


@app.post("/api/portal/companies/manage")
async def manage_create_company(req: CompanyCreateSchema, session: AsyncSession = Depends(get_session)):
    """Admin CRUD: create a company."""
    result = await session.execute(select(Company).where(Company.name == req.name))
    if result.scalar_one_or_none():
        return {"status": "error", "message": "Company already exists"}
    session.add(Company(
        name=req.name, password=req.password,
        plc_ip=req.plc_ip, plc_port=req.plc_port
    ))
    await session.commit()
    return {"status": "success"}


@app.delete("/api/portal/companies/manage/{company_id}")
async def manage_delete_company(company_id: int, session: AsyncSession = Depends(get_session)):
    """Admin CRUD: delete a company."""
    result = await session.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if company:
        await session.delete(company)
        await session.commit()
        return {"status": "success"}
    return {"status": "error", "message": "Company not found"}


# ══════════════════════════════════════════════════════════════
# DRAWING PAD — Send coordinates to PLC
# ══════════════════════════════════════════════════════════════
@app.post("/api/draw", response_model=DrawCommandSchema)
async def send_draw_command(
    cmd: DrawCommandCreate,
    session: AsyncSession = Depends(get_session),
):
    """Send X,Y coordinate to PLC and store in database."""
    if (not poller.simulation_mode
            and poller.connected
            and poller.plc
            and poller.plc_ip == cmd.plc_ip
            and poller.plc_port == cmd.plc_port):
        try:
            poller.plc.batchwrite_wordunits(cmd.register, [cmd.x, cmd.y])
        except Exception as e:
            logger.error(f"Failed to write draw command to PLC register {cmd.register}: {e}")

    draw = DrawCommand(
        timestamp=datetime.now(timezone.utc),
        plc_ip=cmd.plc_ip,
        plc_port=cmd.plc_port,
        register=cmd.register,
        x=cmd.x, y=cmd.y,
    )
    session.add(draw)
    await session.commit()
    await session.refresh(draw)
    return draw


@app.get("/api/draw/history", response_model=list[DrawCommandSchema])
async def get_draw_history(
    limit: int = Query(100, ge=1, le=1000),
    plc_ip: Optional[str] = None,
    plc_port: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
):
    """Recent drawing commands."""
    query = select(DrawCommand).order_by(desc(DrawCommand.timestamp))
    if plc_ip:
        query = query.where(DrawCommand.plc_ip == plc_ip)
    if plc_port:
        query = query.where(DrawCommand.plc_port == plc_port)
    query = query.limit(limit)
    result = await session.execute(query)
    return result.scalars().all()
