"""
ShiftManager — Shift-based CSV buffering engine.

Flow:
  1. PLC data arrives every poll cycle via add_reading()
  2. Each reading is buffered in memory AND appended to the active_shift CSV (crash safety)
  3. When a shift ends (time boundary or force-end), the buffer is:
       a. Written to a final archived CSV
       b. Batch-uploaded to PostgreSQL
       c. Cleared for the next shift

Crash Recovery:
  On startup, if active_shift.csv exists:
    - Parse the shift_start from its header
    - If it belongs to the current shift window → reload buffer and continue
    - If it belongs to a past shift → upload it to DB immediately, then start fresh
"""
import asyncio
import csv
import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config import SHIFT_DURATION_HOURS, SHIFT_START_HOUR

logger = logging.getLogger("shift_manager")

# ── Directory layout ──────────────────────────────────────────────
_BASE_DIR = Path(__file__).parent
SHIFTS_DIR = _BASE_DIR / "shifts"
ARCHIVE_DIR = SHIFTS_DIR / "archive"
ACTIVE_CSV = SHIFTS_DIR / "active_shift.csv"

# Columns will be generated dynamically based on active register configurations.


def _ensure_dirs():
    SHIFTS_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)


# ── Shift boundary math ───────────────────────────────────────────

def compute_shift_window(now: datetime) -> tuple[datetime, datetime]:
    """
    Given the current UTC datetime, return (shift_start, shift_end) for the
    active shift window based on SHIFT_DURATION_HOURS and SHIFT_START_HOUR.

    Example: duration=8, start_hour=6  →  shifts at 06:00, 14:00, 22:00 UTC
    """
    ref_start = now.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    diff_seconds = (now - ref_start).total_seconds()
    shift_duration_seconds = SHIFT_DURATION_HOURS * 3600
    shift_index = int(diff_seconds // shift_duration_seconds)

    shift_start = ref_start + timedelta(seconds=shift_index * shift_duration_seconds)
    shift_end = shift_start + timedelta(hours=SHIFT_DURATION_HOURS)
    return shift_start, shift_end


def _csv_filename(shift_start: datetime, shift_end: datetime) -> Path:
    fmt = "%Y-%m-%d_%H-%M"
    return ARCHIVE_DIR / f"shift_{shift_start.strftime(fmt)}_to_{shift_end.strftime(fmt)}.csv"


# ── Main class ────────────────────────────────────────────────────

class ShiftManager:
    """
    Manages one shift at a time.  Thread-safe via asyncio — all public methods
    are called from the same event loop as the poller.
    """

    def __init__(self):
        _ensure_dirs()
        self.buffer: list[dict] = []          # In-memory rows for current shift
        self.shift_start: Optional[datetime] = None
        self.shift_end: Optional[datetime] = None
        self._wal_file: Optional[io.TextIOWrapper] = None
        self._wal_writer: Optional[csv.DictWriter] = None
        self._lock = asyncio.Lock()
        self._initialized = False

    # ── Init / crash recovery ─────────────────────────────────────

    async def initialize(self):
        """Call once at startup. Handles crash recovery."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            cur_start, cur_end = compute_shift_window(now)

            if ACTIVE_CSV.exists():
                logger.info("Found active_shift.csv — checking for crash recovery…")
                recovered, old_start, old_end = self._load_active_csv()

                if recovered and old_start and old_end:
                    # Is it the same shift window?
                    if old_start == cur_start:
                        logger.info(
                            f"✓ Resuming shift {cur_start.isoformat()} "
                            f"({len(recovered)} rows recovered)"
                        )
                        self.buffer = recovered
                        self.shift_start = cur_start
                        self.shift_end = cur_end
                        self._open_wal(append=True)
                        self._initialized = True
                        return
                    else:
                        logger.warning(
                            f"Stale shift CSV detected (was {old_start.isoformat()}). "
                            f"Uploading to DB and starting fresh…"
                        )
                        await self._commit_buffer(recovered, old_start, old_end, force_filename=True)
                        self._safe_remove_active_csv()
                else:
                    logger.warning("Could not parse active_shift.csv — starting fresh.")
                    self._safe_remove_active_csv()

            # Fresh start
            self.shift_start = cur_start
            self.shift_end = cur_end
            self.buffer = []
            self._open_wal(append=False)
            self._initialized = True
            logger.info(
                f"✓ Shift started: {cur_start.isoformat()} → {cur_end.isoformat()} "
                f"(duration={SHIFT_DURATION_HOURS}h)"
            )

    def _safe_remove_active_csv(self):
        if self._wal_file:
            try:
                self._wal_file.close()
                self._wal_file = None
            except Exception:
                pass
        try:
            if ACTIVE_CSV.exists():
                ACTIVE_CSV.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Could not remove active_shift.csv: {e}")

    def _load_active_csv(self) -> tuple[list[dict], Optional[datetime], Optional[datetime]]:
        """Read active_shift.csv and extract rows + shift metadata from header comment."""
        rows = []
        old_start: Optional[datetime] = None
        old_end: Optional[datetime] = None
        try:
            with open(ACTIVE_CSV, "r", newline="", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("# shift_start="):
                        try:
                            old_start = datetime.fromisoformat(line.split("=", 1)[1])
                        except Exception:
                            pass
                    elif line.startswith("# shift_end="):
                        try:
                            old_end = datetime.fromisoformat(line.split("=", 1)[1])
                        except Exception:
                            pass
                    elif line and not line.startswith("#"):
                        break  # reached CSV header row — stop scanning

            # Now read CSV data rows
            with open(ACTIVE_CSV, "r", newline="", encoding="utf-8") as f:
                # Skip comment lines
                content = "".join(l for l in f if not l.startswith("#"))
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                rows.append(dict(row))
        except Exception as e:
            logger.error(f"Error reading active_shift.csv: {e}")
        return rows, old_start, old_end

    def get_csv_columns(self) -> list[str]:
        cols = ["timestamp", "plc_ip", "plc_port", "status"]
        cols += [f"X{i}" for i in range(18)]
        cols += [f"Y{i}" for i in range(18)]

        registers = []

        # Load register_config.json
        reg_path = _BASE_DIR / "register_config.json"
        if reg_path.exists():
            try:
                with open(reg_path, "r") as f:
                    cfg = json.load(f)
                for item in cfg:
                    addr = item.get("address", "").strip()
                    dtype = item.get("data_type", "int").strip().lower()
                    if "-" in addr:
                        parts = addr.split("-")
                        start_idx = int(parts[0].strip().replace('D', ''))
                        end_idx = int(parts[1].strip().replace('D', ''))
                        step = 2 if dtype in ("float", "float32", "int32") else 1
                        for idx in range(start_idx, end_idx + 1, step):
                            registers.append(f"D{idx}")
                    elif addr:
                        registers.append(addr)
            except Exception as e:
                logger.error(f"Error loading register_config.json in shift_manager: {e}")

        # Load line_config.json
        line_path = _BASE_DIR / "line_config.json"
        if line_path.exists():
            try:
                with open(line_path, "r") as f:
                    lines = json.load(f)
                for line in lines:
                    regs = line.get("registers", {})
                    for key, addr in regs.items():
                        if addr and addr not in registers:
                            registers.append(addr)
                    for machine in line.get("machines", []):
                        m_regs = machine.get("registers", {})
                        for key, addr in m_regs.items():
                            if addr and addr not in registers:
                                registers.append(addr)
            except Exception as e:
                logger.error(f"Error loading line_config.json in shift_manager: {e}")

        def reg_sort_key(r):
            try:
                return int(r.replace('D', ''))
            except ValueError:
                return 99999

        registers = sorted(list(set(registers)), key=reg_sort_key)
        cols += registers
        return cols

    def unflatten_row(self, flat_row: dict, reg_details: dict = None) -> dict:
        """Map flat CSV row back to the JSON format expected by DB batch insert."""
        row = {
            "timestamp": flat_row.get("timestamp"),
            "plc_ip": flat_row.get("plc_ip"),
            "plc_port": flat_row.get("plc_port"),
            "status": flat_row.get("status"),
        }

        # 1. Reconstruct x_inputs
        x_inputs = []
        for i in range(18):
            val = flat_row.get(f"X{i}", "0")
            x_inputs.append(val in ("1", "True", "true", 1, True, "1.0"))
        row["x_inputs"] = json.dumps(x_inputs)

        # 2. Reconstruct y_outputs
        y_outputs = []
        for i in range(18):
            val = flat_row.get(f"Y{i}", "0")
            y_outputs.append(val in ("1", "True", "true", 1, True, "1.0"))
        row["y_outputs"] = json.dumps(y_outputs)

        # 3. Reconstruct d_registers
        if reg_details is None:
            reg_details = {}

            def add_details(cfg_list):
                for item in cfg_list:
                    addr = item.get("address", "").strip()
                    name = item.get("name", addr).strip()
                    dtype = item.get("data_type", "int").strip().lower()
                    if "-" in addr:
                        parts = addr.split("-")
                        try:
                            start_idx = int(parts[0].strip().replace('D', ''))
                            end_idx = int(parts[1].strip().replace('D', ''))
                            step = 2 if dtype in ("float", "float32", "int32") else 1
                            for idx in range(start_idx, end_idx + 1, step):
                                reg_details[f"D{idx}"] = {
                                    "name": f"{name} (D{idx})" if name != addr else f"D{idx}",
                                    "data_type": dtype
                                }
                        except Exception:
                            pass
                    elif addr:
                        reg_details[addr] = {"name": name, "data_type": dtype}

            # Load register_config.json
            reg_path = _BASE_DIR / "register_config.json"
            if reg_path.exists():
                try:
                    with open(reg_path, "r") as f:
                        add_details(json.load(f))
                except Exception:
                    pass

            # Load line_config.json
            line_path = _BASE_DIR / "line_config.json"
            if line_path.exists():
                try:
                    with open(line_path, "r") as f:
                        lines = json.load(f)
                    for line in lines:
                        l_name = line.get("line_name", "")
                        for k, addr in line.get("registers", {}).items():
                            if addr:
                                reg_details[addr] = {
                                    "name": f"{l_name} - {k.replace('_', ' ').title()}",
                                    "data_type": "float" if "accuracy" in k else "int"
                                }
                        for machine in line.get("machines", []):
                            m_name = machine.get("machine_name", "")
                            for k, addr in machine.get("registers", {}).items():
                                if addr:
                                    reg_details[addr] = {
                                        "name": f"{l_name} - {m_name} - {k.replace('_', ' ').title()}",
                                        "data_type": "float" if "accuracy" in k else "int"
                                    }
                except Exception:
                    pass

        # Reconstruct D register values
        d_registers = []
        for key, val in flat_row.items():
            if key.startswith("D"):
                details = reg_details.get(key, {"name": key, "data_type": "int"})

                parsed_val = None
                if val is not None and val != "":
                    try:
                        if details["data_type"] in ("float", "float32"):
                            parsed_val = float(val)
                        else:
                            parsed_val = int(float(val))
                    except Exception:
                        parsed_val = val

                d_registers.append({
                    "address": key,
                    "name": details["name"],
                    "data_type": details["data_type"],
                    "value": parsed_val
                })

        row["d_registers"] = json.dumps(d_registers)
        return row

    def _open_wal(self, append: bool = False):
        """Open (or reopen) the write-ahead log CSV file."""
        if self._wal_file:
            try:
                self._wal_file.close()
            except Exception:
                pass

        mode = "a" if append else "w"
        self._wal_file = open(ACTIVE_CSV, mode, newline="", encoding="utf-8")

        if not append:
            # Write metadata comment header
            self._wal_file.write(f"# shift_start={self.shift_start.isoformat()}\n")
            self._wal_file.write(f"# shift_end={self.shift_end.isoformat()}\n")
            self._wal_file.flush()

        cols = self.get_csv_columns()
        self._wal_writer = csv.DictWriter(
            self._wal_file, fieldnames=cols, extrasaction="ignore"
        )
        if not append:
            self._wal_writer.writeheader()
            self._wal_file.flush()

    # ── Public API ────────────────────────────────────────────────

    async def add_reading(
        self,
        timestamp: datetime,
        plc_ip: str,
        plc_port: int,
        status: str,
        x_inputs: list,
        y_outputs: list,
        d_registers: list,
    ):
        """
        Called by PLCPoller on every poll cycle.
        Buffers the reading and flushes it to the crash-safe CSV immediately.
        Triggers a shift commit if the current shift window has elapsed.
        """
        if not self._initialized:
            return

        row = {
            "timestamp": timestamp.isoformat(),
            "plc_ip": plc_ip,
            "plc_port": plc_port,
            "status": status,
        }
        for i, val in enumerate(x_inputs):
            row[f"X{i}"] = 1 if val else 0
        for i, val in enumerate(y_outputs):
            row[f"Y{i}"] = 1 if val else 0
        for reg in d_registers:
            row[reg["address"]] = reg["value"]

        async with self._lock:
            self.buffer.append(row)

            # Write-ahead log — crash safety
            if self._wal_writer:
                try:
                    self._wal_writer.writerow(row)
                    self._wal_file.flush()
                except Exception as e:
                    logger.error(f"WAL write error: {e}")

            # Check if shift has ended
            now = datetime.now(timezone.utc)
            if now >= self.shift_end:
                logger.info(
                    f"Shift boundary reached. Committing {len(self.buffer)} rows…"
                )
                await self._commit_and_rotate()

    async def force_end_shift(self) -> dict:
        """
        Force-end the current shift early. Commits current buffer to DB and starts a new shift.
        Returns a summary dict.
        """
        async with self._lock:
            row_count = len(self.buffer)
            logger.info(f"Force-ending shift with {row_count} rows…")
            await self._commit_and_rotate()
            return {
                "status": "success",
                "rows_committed": row_count,
                "new_shift_start": self.shift_start.isoformat(),
                "new_shift_end": self.shift_end.isoformat(),
            }

    def get_status(self) -> dict:
        """Return current shift status (non-blocking, no lock needed for reads)."""
        now = datetime.now(timezone.utc)
        if not self._initialized or not self.shift_start or not self.shift_end:
            return {"initialized": False}

        remaining = max(0.0, (self.shift_end - now).total_seconds())
        return {
            "initialized": True,
            "shift_start": self.shift_start.isoformat(),
            "shift_end": self.shift_end.isoformat(),
            "rows_buffered": len(self.buffer),
            "seconds_remaining": round(remaining, 1),
            "shift_duration_hours": SHIFT_DURATION_HOURS,
        }

    async def shutdown(self):
        """Called on graceful shutdown — saves WAL but does NOT commit to DB (shift continues on restart)."""
        async with self._lock:
            if self._wal_file:
                try:
                    self._wal_file.flush()
                    self._wal_file.close()
                    self._wal_file = None
                except Exception:
                    pass
            logger.info(
                f"ShiftManager shutdown. {len(self.buffer)} rows saved to WAL for next startup."
            )

    # ── Internal commit logic ─────────────────────────────────────

    async def _commit_and_rotate(self):
        """
        Commit current buffer, rotate to a new shift.
        Caller MUST hold self._lock.
        """
        old_buffer = list(self.buffer)
        old_start = self.shift_start
        old_end = self.shift_end

        # Rotate shift window
        now = datetime.now(timezone.utc)
        self.shift_start, self.shift_end = compute_shift_window(now)
        self.buffer = []

        # Close old WAL and open fresh one
        self._safe_remove_active_csv()
        self._open_wal(append=False)

        # Upload (runs outside lock scope conceptually but we hold lock for atomicity)
        await self._commit_buffer(old_buffer, old_start, old_end)

        logger.info(
            f"✓ New shift started: {self.shift_start.isoformat()} → {self.shift_end.isoformat()}"
        )

    async def _commit_buffer(
        self,
        rows: list[dict],
        shift_start: datetime,
        shift_end: datetime,
        force_filename: bool = False,
    ):
        """
        1. Write rows to an archived CSV.
        2. Batch-upload to PostgreSQL.
        3. Record shift metadata in ShiftRecord table.
        """
        if not rows:
            logger.info("Shift commit: buffer empty, nothing to upload.")
            return

        # ── 1. Write archived CSV ──
        csv_path = _csv_filename(shift_start, shift_end)
        # Handle duplicate filenames (force-end mid-shift)
        if csv_path.exists():
            stem = csv_path.stem
            csv_path = ARCHIVE_DIR / f"{stem}_partial_{datetime.now(timezone.utc).strftime('%H%M%S')}.csv"

        # Determine dynamic CSV headers
        cols = ["timestamp", "plc_ip", "plc_port", "status"]
        x_cols = [f"X{i}" for i in range(18)]
        y_cols = [f"Y{i}" for i in range(18)]
        d_cols = []
        for r in rows:
            for k in r.keys():
                if k.startswith("D") and k not in d_cols:
                    d_cols.append(k)
        def reg_sort_key(r):
            try: return int(r.replace('D', ''))
            except ValueError: return 99999
        d_cols.sort(key=reg_sort_key)
        all_cols = cols + x_cols + y_cols + d_cols

        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                f.write(f"# shift_start={shift_start.isoformat()}\n")
                f.write(f"# shift_end={shift_end.isoformat()}\n")
                writer = csv.DictWriter(f, fieldnames=all_cols, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"✓ Archived CSV: {csv_path.name} ({len(rows)} rows)")
        except Exception as e:
            logger.error(f"Failed to write archived CSV: {e}")
            csv_path = None

        # ── 2. Batch upload to DB ──
        try:
            from database import batch_insert_readings, record_shift

            # Load register config details once for unflattening
            reg_details = {}
            def add_details(cfg_list):
                for item in cfg_list:
                    addr = item.get("address", "").strip()
                    name = item.get("name", addr).strip()
                    dtype = item.get("data_type", "int").strip().lower()
                    if "-" in addr:
                        parts = addr.split("-")
                        try:
                            start_idx = int(parts[0].strip().replace('D', ''))
                            end_idx = int(parts[1].strip().replace('D', ''))
                            step = 2 if dtype in ("float", "float32", "int32") else 1
                            for idx in range(start_idx, end_idx + 1, step):
                                reg_details[f"D{idx}"] = {
                                    "name": f"{name} (D{idx})" if name != addr else f"D{idx}",
                                    "data_type": dtype
                                }
                        except Exception:
                            pass
                    elif addr:
                        reg_details[addr] = {"name": name, "data_type": dtype}

            reg_path = _BASE_DIR / "register_config.json"
            if reg_path.exists():
                try:
                    with open(reg_path, "r") as f:
                        add_details(json.load(f))
                except Exception:
                    pass

            line_path = _BASE_DIR / "line_config.json"
            if line_path.exists():
                try:
                    with open(line_path, "r") as f:
                        lines = json.load(f)
                    for line in lines:
                        l_name = line.get("line_name", "")
                        for k, addr in line.get("registers", {}).items():
                            if addr:
                                reg_details[addr] = {
                                    "name": f"{l_name} - {k.replace('_', ' ').title()}",
                                    "data_type": "float" if "accuracy" in k else "int"
                                }
                        for machine in line.get("machines", []):
                            m_name = machine.get("machine_name", "")
                            for k, addr in machine.get("registers", {}).items():
                                if addr:
                                    reg_details[addr] = {
                                        "name": f"{l_name} - {m_name} - {k.replace('_', ' ').title()}",
                                        "data_type": "float" if "accuracy" in k else "int"
                                    }
                except Exception:
                    pass

            unflattened_rows = [self.unflatten_row(r, reg_details) for r in rows]
            await batch_insert_readings(unflattened_rows)
            logger.info(f"✓ Batch uploaded {len(rows)} rows to DB")

            # ── 3. Record shift metadata & ShiftSummary ──
            first_row = rows[0] if rows else {}
            plc_ip = first_row.get("plc_ip")
            plc_port = first_row.get("plc_port")
            if plc_port is not None:
                try:
                    plc_port = int(plc_port)
                except ValueError:
                    plc_port = None

            await record_shift(
                shift_start=shift_start,
                shift_end=shift_end,
                row_count=len(rows),
                csv_path=str(csv_path) if csv_path else None,
                plc_ip=plc_ip,
                plc_port=plc_port,
            )

            # ── 4. Create ShiftSummary records for each line ──
            await self._create_shift_summaries(shift_start)
        except Exception as e:
            logger.error(f"DB batch upload failed: {e}")

    async def _create_shift_summaries(self, shift_start: datetime):
        """Create and store ShiftSummary entries in the database for each production line."""
        try:
            from plc_poller import poller
            from database import async_session_factory, ShiftSummary

            hour = shift_start.hour
            if 6 <= hour < 14:
                shift_name = "Shift 1 (06:00 - 14:00)"
            elif 14 <= hour < 22:
                shift_name = "Shift 2 (14:00 - 22:00)"
            else:
                shift_name = "Shift 3 (22:00 - 06:00)"

            date_str = shift_start.strftime("%Y-%m-%d")

            async with async_session_factory() as session:
                async with session.begin():
                    for line in poller.lines_layout:
                        line_id = line.get("line_id", "line1")
                        line_name = line.get("line_name", "Production Line")
                        
                        target = 0
                        actual = 0
                        runtime_sec = 0.0
                        idle_sec = 0.0
                        breakdown_sec = 0.0

                        for machine in line.get("machines", []):
                            m_id = machine["machine_id"]
                            m_state = poller.machine_states.get(m_id, {})
                            target += int(m_state.get("target", 500))
                            actual += int(m_state.get("actual", 0))
                            runtime_sec += float(m_state.get("runtime", 0.0))
                            idle_sec += float(m_state.get("idle", 0.0))
                            breakdown_sec += float(m_state.get("breakdown", 0.0))

                        runtime_min = round(runtime_sec / 60.0, 1)
                        idle_min = round(idle_sec / 60.0, 1)
                        breakdown_min = round(breakdown_sec / 60.0, 1)
                        
                        target_val = target if target > 0 else 500
                        efficiency = round((actual / target_val) * 100.0, 1) if target_val > 0 else 0.0
                        efficiency = min(100.0, max(0.0, efficiency))

                        summary = ShiftSummary(
                            date=date_str,
                            shift_name=shift_name,
                            line_id=line_id,
                            line_name=line_name,
                            plc_ip=poller.plc_ip or "192.168.1.10",
                            target=target_val,
                            production=actual,
                            runtime_minutes=runtime_min,
                            idle_time_minutes=idle_min,
                            breakdown_time_minutes=breakdown_min,
                            efficiency=efficiency,
                        )
                        session.add(summary)
            logger.info(f"✓ Created ShiftSummary records for date={date_str}, shift={shift_name}")
            
            # Reset machine accumulators for next shift
            if hasattr(poller, "reset_machine_shift_accumulators"):
                poller.reset_machine_shift_accumulators()
        except Exception as e:
            logger.error(f"Failed to create ShiftSummary records: {e}")


# ── Singleton ─────────────────────────────────────────────────────
shift_manager = ShiftManager()

