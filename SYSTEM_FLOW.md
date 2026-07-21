# PLC Tag Monitor — System Flow & Architecture

> **Version 2.0** · Shift-Based CSV Buffering System

---

## Table of Contents

1. [Overview](#overview)
2. [Core Architecture](#core-architecture)
3. [Shift System](#shift-system)
4. [Data Flow](#data-flow)
5. [Crash Safety & Recovery](#crash-safety--recovery)
6. [Configuration Reference](#configuration-reference)
7. [API Endpoints](#api-endpoints)
8. [File Structure](#file-structure)

---

## Overview

The PLC Tag Monitor is a **FastAPI + PostgreSQL** web application that:

- Polls a Mitsubishi PLC (or simulates one) at a configurable rate
- Buffers all readings in-memory and writes a crash-safe CSV on every poll
- At the end of each **shift window**, flushes the buffer to an archived CSV and batch-uploads to PostgreSQL
- Provides a real-time web dashboard with live WebSocket streaming
- Supports **force-ending a shift** from the dashboard UI for early commits

---

## Core Architecture

```
+-------------------------------------------------------------+
|                      FastAPI Server                         |
|                                                             |
|  +--------------+   +----------------+   +--------------+  |
|  |  PLCPoller   |-->|  ShiftManager  |-->|  PostgreSQL  |  |
|  |  (async loop)|   |  (buffer/WAL)  |   |  (batch at   |  |
|  +------+-------+   +--------+-------+   |  shift end)  |  |
|         |                   |            +--------------+  |
|         | WebSocket          | CSV WAL                      |
|         v                   v                              |
|  +-------------+    +------------------+                   |
|  |  Browser    |    |  shifts/          |                   |
|  |  Dashboard  |    |  +- active_shift  |                   |
|  |  (live view)|    |  +- archive/*.csv |                   |
|  +-------------+    +------------------+                   |
+-------------------------------------------------------------+
```

### Components

| File | Role |
|------|------|
| `run_web.py` | Entry point — launches uvicorn |
| `app.py` | FastAPI routes, lifespan hooks, shift endpoints |
| `config.py` | Loads `system_config.json` + env vars |
| `plc_poller.py` | PLC read loop, WebSocket broadcast |
| `shift_manager.py` | Buffer, crash-safe WAL, shift boundary, DB batch commit |
| `database.py` | SQLAlchemy models, session factory, batch insert helpers |
| `schemas.py` | Pydantic I/O schemas |
| `system_config.json` | **Operator-editable**: poll rate, shift duration |
| `register_config.json` | **Operator-editable**: which D-registers to monitor |

---

## Shift System

### What Is a Shift?

A **shift** is a fixed-length time window. Data is **collected** throughout the shift, then **committed** (CSV + DB) at the end.

```
Day timeline with shift_duration_hours=8, shift_start_hour=6:

00:00      06:00      14:00      22:00     00:00
  |          |          |          |          |
  <--------->|<--------->|<--------->|<-------->
  (prev day) | Shift 1  |  Shift 2  |  Shift 3 |
             | 06->14   |  14->22   |  22->06  |
             |          |           |           |
             v commit   v commit    v commit    v commit
           CSV+DB     CSV+DB     CSV+DB     CSV+DB
```

### Shift Boundary Calculation

`shift_manager.py :: compute_shift_window(now)`

```python
anchor_secs      = shift_start_hour * 3600          # e.g. 6*3600 = 21600
elapsed          = seconds since midnight
offset           = elapsed - anchor_secs             # seconds past the anchor
shift_index      = floor(offset / shift_duration_secs)
shift_start      = midnight + anchor + shift_index * duration
shift_end        = shift_start + duration
```

---

## Data Flow

### Normal Operation

```
+-----------------------------------------------------------+
| PLCPoller.run()  [every poll_interval_seconds]            |
|                                                           |
|  1. _read_plc()  ----------------------------------------+|
|     (real or simulated PLC data)                         ||
|                                                          v|
|  2. _parse_registers(raw)                   x, y, d_regs ||
|                                                          ||
|  3. shift_manager.add_reading(...)  <--------------------+|
|         |                                                 |
|         +---> buffer.append(row)         [in-memory]      |
|         |                                                 |
|         +---> active_shift.csv << row   [crash-safe WAL]  |
|         |                                                 |
|         +---> check: now >= shift_end?                    |
|                   YES --> _commit_and_rotate()            |
|                   NO  --> continue                        |
|                                                           |
|  4. _broadcast(ws_clients, data)       [WebSocket / live] |
+-----------------------------------------------------------+
```

### Shift Commit (automatic or forced)

```
ShiftManager._commit_and_rotate()
  |
  +--1--> Rotate shift window
  |         self.shift_start, self.shift_end = compute_shift_window(now)
  |         self.buffer = []
  |
  +--2--> Re-open active_shift.csv (fresh WAL for new shift)
  |
  +--3--> _commit_buffer(old_rows, old_start, old_end)
            |
            +--> Write  shifts/archive/shift_YYYY-MM-DD_HH-MM_to_HH-MM.csv
            |
            +--> database.batch_insert_readings(rows)
            |      - one PLCReading + children per row, single transaction
            |
            +--> database.record_shift(...)
                   - inserts one ShiftRecord row with metadata
```

### Force End Shift (from UI)

```
Browser --POST /api/shift/force-end--> app.py
                                         |
                                         v
                                  shift_manager.force_end_shift()
                                         |
                                         v
                                  _commit_and_rotate()   (same as above)
                                         |
                                         v
                                  return { status, rows_committed,
                                           new_shift_start, new_shift_end }
                                         |
                                         v
Browser <-- JSON response ---------- dashboard updates
```

---

## Crash Safety & Recovery

The system is designed to **never lose data** even if the server crashes mid-shift.

```
CRASH SCENARIO
--------------
Server running, collecting shift data...
    buffer = [row1, row2, ... rowN]   <- in memory
    shifts/active_shift.csv = same    <- on disk (written after each row)

  [CRASH]

RESTART
-------
app.py lifespan -> shift_manager.initialize()
  |
  +- active_shift.csv EXISTS?
  |      YES
  |       |
  |       +- read # shift_start from CSV header
  |       |
  |       +- compute current shift window
  |       |
  |       +- same shift?  --YES--> reload buffer from CSV
  |       |                         open WAL in append mode
  |       |                         continue collecting  [OK]
  |       |
  |       +- old shift?   --YES--> upload old rows to DB
  |                                archive old CSV
  |                                start fresh for new shift [OK]
  |
  +- active_shift.csv NOT FOUND -> start fresh [OK]
```

### On Graceful Shutdown

```
SIGINT / uvicorn stop
  |
  v
app.py lifespan teardown
  +-- poller.stop()
  +-- shift_manager.shutdown()
        +-- flush & close active_shift.csv WAL
            (does NOT commit to DB - shift continues on next restart)
```

---

## Configuration Reference

### `system_config.json`

```json
{
  "poll_interval_seconds": 0.02,
  "shift_duration_hours": 8,
  "shift_start_hour": 6
}
```

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `poll_interval_seconds` | float | How often the PLC is polled | `0.02` = 50 polls/sec |
| `shift_duration_hours` | int | Length of one shift | `8` = 3 shifts/day |
| `shift_start_hour` | int | Hour (UTC) of first shift | `6` = shifts at 06, 14, 22 |

> **Note:** Changes to `system_config.json` take effect on the next server restart.
> You can also update it live via `POST /api/system/config`.

### `register_config.json`

```json
[
  { "name": "Temperature", "address": "D100",      "data_type": "int"    },
  { "name": "Pressure",    "address": "D101",      "data_type": "string" },
  { "name": "Flow Rate",   "address": "D102",      "data_type": "int32"  },
  { "name": "Sensor Band", "address": "D200-D205", "data_type": "float"  }
]
```

Supported `data_type` values: `int`, `int16`, `int32`, `float`, `float32`, `string`

Range notation (`D100-D105`) is supported for all types.

---

## API Endpoints

### Shift Management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/shift/status` | Current shift status |
| `POST` | `/api/shift/force-end` | Force-end current shift, commit to DB |
| `GET` | `/api/shift/history` | List of past committed shifts |

#### `GET /api/shift/status` response
```json
{
  "initialized": true,
  "shift_start": "2026-07-15T06:00:00+00:00",
  "shift_end":   "2026-07-15T14:00:00+00:00",
  "rows_buffered": 142857,
  "seconds_remaining": 14400.0,
  "shift_duration_hours": 8
}
```

#### `POST /api/shift/force-end` response
```json
{
  "status": "success",
  "rows_committed": 142857,
  "new_shift_start": "2026-07-15T21:04:12+00:00",
  "new_shift_end":   "2026-07-15T22:00:00+00:00"
}
```

### System Configuration

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/system/config` | Read current `system_config.json` |
| `POST` | `/api/system/config` | Update config values (restart to apply) |

### PLC & Data

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/status` | PLC connection state |
| `POST` | `/api/plc/connect` | Set PLC target |
| `POST` | `/api/plc/disconnect` | Disconnect |
| `WS` | `/ws/live` | Live WebSocket stream |
| `GET` | `/api/readings` | Historical readings (paginated) |
| `GET` | `/api/readings/latest` | Latest reading |
| `GET` | `/api/registers/history` | Register time-series for charting |
| `GET` | `/api/registers/config` | Active register config |
| `POST` | `/api/registers/config` | Update register config |

---

## File Structure

```
MonitoringSystem/
+-- app.py                  # FastAPI application + all REST/WS routes
+-- config.py               # Config loader (system_config.json + env vars)
+-- database.py             # SQLAlchemy models + batch insert helpers
+-- plc_poller.py           # PLC polling loop (routes to ShiftManager)
+-- schemas.py              # Pydantic I/O schemas
+-- shift_manager.py        # Shift buffer, WAL, crash recovery, DB commit  [NEW]
+-- run_web.py              # Entry point (uvicorn launch)
|
+-- system_config.json      # <- OPERATOR CONFIG: poll rate, shift settings  [NEW]
+-- register_config.json    # <- OPERATOR CONFIG: which registers to monitor
+-- requirements.txt
|
+-- static/                 # Web dashboard (served by FastAPI)
|   +-- index.html
|   +-- css/
|   |   +-- style.css
|   +-- js/
|       +-- app.js          # Core SPA navigation, WS connection
|       +-- dashboard.js    # Live I/O display, charts
|       +-- history.js      # Historical data browser
|       +-- drawpad.js      # Drawing pad feature
|       +-- shift.js        # Shift Control panel             [NEW]
|
+-- shifts/                 # Auto-created at runtime         [NEW]
    +-- active_shift.csv    # Crash-safe write-ahead log
    +-- archive/            # Completed shift files
        +-- shift_2026-07-15_06-00_to_14-00.csv
```

### Database Tables

| Table | Purpose |
|-------|---------|
| `plc_readings` | One row per poll cycle (written at shift commit) |
| `bit_inputs` | X0-X17 values, FK -> plc_readings |
| `bit_outputs` | Y0-Y17 values, FK -> plc_readings |
| `word_registers` | D-register values, FK -> plc_readings |
| `shift_records` | Metadata per committed shift (start, end, row count, CSV path) |
| `companies` | Portal tenant credentials |
| `draw_commands` | Drawing pad X,Y coordinates |

---

## Starting the Server

```bash
cd MonitoringSystem
pip install -r requirements.txt
python run_web.py
# Open http://localhost:8000
```

### Environment Overrides

| Variable | Default | Description |
|----------|---------|-------------|
| `PLC_POLL_INTERVAL` | from system_config.json | Poll interval in seconds |
| `SHIFT_DURATION_HOURS` | from system_config.json | Shift length in hours |
| `SHIFT_START_HOUR` | from system_config.json | Anchor hour (UTC) |
| `SIMULATION_MODE` | `true` | Set `false` for real PLC |
| `WEB_HOST` | `0.0.0.0` | Listen address |
| `WEB_PORT` | `8000` | Listen port |
