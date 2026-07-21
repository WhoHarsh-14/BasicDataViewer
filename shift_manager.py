"""
ShiftManager — Shift Management & Performance Summary Engine.

Flow:
  1. Displays live values as-is without storing every poll row to CSV or DB.
  2. Tracks shift window boundaries (shift_duration_hours, shift_start_hour).
  3. At shift end (auto boundary or manual force-end):
     - Calculates shift performance summary per line: target, actual production,
       runtime, idle time, breakdown time, and efficiency.
     - Pushes shift summary metrics to PostgreSQL database.
     - Resets live machine accumulators for the new shift.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import SHIFT_DURATION_HOURS, SHIFT_START_HOUR

logger = logging.getLogger("shift_manager")


def compute_shift_window(now: datetime) -> tuple[datetime, datetime]:
    """
    Given current UTC datetime, return (shift_start, shift_end) for active shift.
    Example: duration=8, start_hour=6 -> shifts at 06:00, 14:00, 22:00 UTC
    """
    ref_start = now.replace(hour=SHIFT_START_HOUR, minute=0, second=0, microsecond=0)
    diff_seconds = (now - ref_start).total_seconds()
    shift_duration_seconds = SHIFT_DURATION_HOURS * 3600
    shift_index = int(diff_seconds // shift_duration_seconds)

    shift_start = ref_start + timedelta(seconds=shift_index * shift_duration_seconds)
    shift_end = shift_start + timedelta(hours=SHIFT_DURATION_HOURS)
    return shift_start, shift_end


class ShiftManager:
    def __init__(self):
        self.shift_start: Optional[datetime] = None
        self.shift_end: Optional[datetime] = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self):
        """Initialize active shift window."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            self.shift_start, self.shift_end = compute_shift_window(now)
            self._initialized = True
            logger.info(
                f"✓ ShiftManager initialized: {self.shift_start.isoformat()} → {self.shift_end.isoformat()} "
                f"(duration={SHIFT_DURATION_HOURS}h)"
            )

    async def check_shift_boundary(self, now: datetime = None):
        """Called during poller tick to check if shift window has completed."""
        if not self._initialized or not self.shift_end:
            return
        now = now or datetime.now(timezone.utc)
        if now >= self.shift_end:
            logger.info("Shift duration reached. Pushing shift summary report to DB...")
            await self.commit_shift_summary()

    async def add_reading(self, timestamp: datetime = None, **kwargs):
        """Alias for poll tick check — live values are displayed as-is without storing every poll to CSV."""
        await self.check_shift_boundary(timestamp)

    async def force_end_shift(self) -> dict:
        """Force end current shift early, push report to PostgreSQL, and start a new shift."""
        async with self._lock:
            logger.info("Force-ending current shift early...")
            count = await self._create_shift_summaries(self.shift_start)
            now = datetime.now(timezone.utc)
            self.shift_start, self.shift_end = compute_shift_window(now)
            return {
                "status": "success",
                "rows_committed": count,
                "new_shift_start": self.shift_start.isoformat(),
                "new_shift_end": self.shift_end.isoformat(),
            }

    async def commit_shift_summary(self):
        """Push end-of-shift report metrics to PostgreSQL and rotate shift."""
        async with self._lock:
            count = await self._create_shift_summaries(self.shift_start)
            now = datetime.now(timezone.utc)
            self.shift_start, self.shift_end = compute_shift_window(now)
            logger.info(f"✓ Shift summary report pushed to DB ({count} lines). New shift started.")

    async def _create_shift_summaries(self, shift_start: datetime) -> int:
        """Calculate and store ShiftSummary entries in PostgreSQL database for each line."""
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
            inserted_count = 0

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
                        inserted_count += 1

            logger.info(f"✓ Pushed {inserted_count} ShiftSummary report records to PostgreSQL for {date_str} ({shift_name})")

            # Reset live machine accumulators for next shift
            if hasattr(poller, "reset_machine_shift_accumulators"):
                poller.reset_machine_shift_accumulators()

            return inserted_count
        except Exception as e:
            logger.error(f"Failed to create ShiftSummary records: {e}")
            return 0

    def get_status(self) -> dict:
        """Return current shift status."""
        now = datetime.now(timezone.utc)
        if not self._initialized or not self.shift_start or not self.shift_end:
            return {"initialized": False}

        remaining = max(0.0, (self.shift_end - now).total_seconds())
        return {
            "initialized": True,
            "shift_start": self.shift_start.isoformat(),
            "shift_end": self.shift_end.isoformat(),
            "seconds_remaining": round(remaining, 1),
            "shift_duration_hours": SHIFT_DURATION_HOURS,
        }

    async def shutdown(self):
        logger.info("ShiftManager shutdown complete.")


shift_manager = ShiftManager()
