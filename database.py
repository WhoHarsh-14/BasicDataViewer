"""SQLAlchemy models — PLC data serialized with timestamp as primary/foreign key.

Includes ShiftRecord for shift metadata and batch insert helpers for
the shift-based buffering system.
"""
import json
import logging
import struct
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float,
    ForeignKey, Index, Text, text
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from config import DATABASE_URL

logger = logging.getLogger("database")
Base = declarative_base()


# ══════════════════════════════════════════════════════════════
# CORE TABLE — One row per poll cycle, keyed by timestamp
# ══════════════════════════════════════════════════════════════
class PLCReading(Base):
    """
    Each poll cycle produces one PLCReading row.
    In shift mode these are written in bulk at end-of-shift.
    """
    __tablename__ = "plc_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=func.now(), index=True)
    plc_ip = Column(String(50), nullable=False, index=True)
    plc_port = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="OK")  # OK | DISCONNECTED

    bit_inputs = relationship(
        "BitInput", back_populates="reading",
        cascade="all, delete-orphan", lazy="selectin"
    )
    bit_outputs = relationship(
        "BitOutput", back_populates="reading",
        cascade="all, delete-orphan", lazy="selectin"
    )
    word_registers = relationship(
        "WordRegister", back_populates="reading",
        cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self):
        return f"<PLCReading id={self.id} ts={self.timestamp} plc={self.plc_ip}:{self.plc_port} status={self.status}>"


# ══════════════════════════════════════════════════════════════
# BIT INPUTS — X0 through X17
# ══════════════════════════════════════════════════════════════
class BitInput(Base):
    """Digital input bits (X0-X17). Foreign-keyed to PLCReading.id."""
    __tablename__ = "bit_inputs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plc_reading_id = Column(
        Integer, ForeignKey("plc_readings.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    address = Column(String(10), nullable=False)
    value = Column(Boolean, nullable=False, default=False)

    reading = relationship("PLCReading", back_populates="bit_inputs")

    __table_args__ = (
        Index("ix_bit_inputs_reading_addr", "plc_reading_id", "address"),
    )

    def __repr__(self):
        return f"<BitInput {self.address}={'ON' if self.value else 'OFF'}>"


# ══════════════════════════════════════════════════════════════
# BIT OUTPUTS — Y0 through Y17
# ══════════════════════════════════════════════════════════════
class BitOutput(Base):
    """Digital output bits (Y0-Y17). Foreign-keyed to PLCReading.id."""
    __tablename__ = "bit_outputs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plc_reading_id = Column(
        Integer, ForeignKey("plc_readings.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    address = Column(String(10), nullable=False)
    value = Column(Boolean, nullable=False, default=False)

    reading = relationship("PLCReading", back_populates="bit_outputs")

    __table_args__ = (
        Index("ix_bit_outputs_reading_addr", "plc_reading_id", "address"),
    )

    def __repr__(self):
        return f"<BitOutput {self.address}={'ON' if self.value else 'OFF'}>"


# ══════════════════════════════════════════════════════════════
# WORD REGISTERS — Dynamic registers configuration
# ══════════════════════════════════════════════════════════════
class WordRegister(Base):
    """Word registers with dynamic data types. Foreign-keyed to PLCReading.id."""
    __tablename__ = "word_registers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plc_reading_id = Column(
        Integer, ForeignKey("plc_readings.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    address = Column(String(50), nullable=False)
    value = Column(String(255), nullable=True)
    data_type = Column(String(20), nullable=False, default="int")
    value_int = Column(Integer, nullable=True)
    value_float = Column(Float, nullable=True)
    value_str = Column(String(255), nullable=True)

    reading = relationship("PLCReading", back_populates="word_registers")

    __table_args__ = (
        Index("ix_word_registers_reading_addr", "plc_reading_id", "address"),
    )

    def __repr__(self):
        return f"<WordRegister {self.address}={self.value} ({self.data_type})>"


# ══════════════════════════════════════════════════════════════
# SHIFT RECORD — Metadata for each committed shift
# ══════════════════════════════════════════════════════════════
class ShiftRecord(Base):
    """
    One row per committed shift.  Written when the shift buffer is flushed
    to the database (either at shift boundary or via force-end).
    """
    __tablename__ = "shift_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shift_start = Column(DateTime(timezone=True), nullable=False, index=True)
    shift_end = Column(DateTime(timezone=True), nullable=False)
    plc_ip = Column(String(50), nullable=True)
    plc_port = Column(Integer, nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    csv_path = Column(Text, nullable=True)       # Absolute path to archived CSV
    uploaded_at = Column(DateTime(timezone=True), default=func.now())

    def __repr__(self):
        return (
            f"<ShiftRecord id={self.id} "
            f"start={self.shift_start} rows={self.row_count}>"
        )


# ══════════════════════════════════════════════════════════════
# SHIFT SUMMARY — Store shift performance indicators (3 rows/day)
# ══════════════════════════════════════════════════════════════
class ShiftSummary(Base):
    """
    Stored after each shift completes (3 times a day per line).
    Contains target, actual production, runtime, idle time, breakdown time,
    and calculated efficiency per shift per production line.
    """
    __tablename__ = "shift_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, index=True)          # YYYY-MM-DD
    shift_name = Column(String(50), nullable=False, index=True)    # e.g., Shift 1 (06:00 - 14:00)
    line_id = Column(String(50), nullable=False, index=True)       # e.g., line1
    line_name = Column(String(100), nullable=False)                # e.g., Assembly Line 1
    plc_ip = Column(String(50), nullable=True)
    target = Column(Integer, nullable=False, default=0)
    production = Column(Integer, nullable=False, default=0)
    runtime_minutes = Column(Float, nullable=False, default=0.0)
    idle_time_minutes = Column(Float, nullable=False, default=0.0)
    breakdown_time_minutes = Column(Float, nullable=False, default=0.0)
    efficiency = Column(Float, nullable=False, default=0.0)        # Percentage (0 - 100%)
    created_at = Column(DateTime(timezone=True), default=func.now())

    def __repr__(self):
        return (
            f"<ShiftSummary date={self.date} shift={self.shift_name} "
            f"line={self.line_name} prod={self.production}/{self.target} eff={self.efficiency:.1f}%>"
        )


# ══════════════════════════════════════════════════════════════
# COMPANIES — Portal tenants
# ══════════════════════════════════════════════════════════════
class Company(Base):
    """Companies participating in the portal."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    password = Column(String(100), nullable=False)
    plc_ip = Column(String(50), nullable=False)
    plc_port = Column(Integer, nullable=False)

    def __repr__(self):
        return f"<Company {self.name} plc={self.plc_ip}:{self.plc_port}>"


# ══════════════════════════════════════════════════════════════
# DRAW COMMANDS — Sketch pad coordinates sent to PLC
# ══════════════════════════════════════════════════════════════
class DrawCommand(Base):
    """Each mouse stroke on the drawing pad sends (x, y) to a PLC register."""
    __tablename__ = "draw_commands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=func.now(), index=True)
    plc_ip = Column(String(50), nullable=False, index=True)
    plc_port = Column(Integer, nullable=False)
    register = Column(String(20), nullable=False, default="D200")
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)

    def __repr__(self):
        return f"<DrawCommand ({self.x}, {self.y}) at {self.timestamp} register={self.register}>"


# ══════════════════════════════════════════════════════════════
# ENGINE + SESSION FACTORY
# ══════════════════════════════════════════════════════════════
async_engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Create all tables if they don't already exist. Data is preserved across restarts."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Schema migrations for existing deployments
        migrations = [
            "ALTER TABLE word_registers ALTER COLUMN address TYPE VARCHAR(50);",
            "ALTER TABLE word_registers ALTER COLUMN value TYPE VARCHAR(255);",
            "ALTER TABLE word_registers ADD COLUMN IF NOT EXISTS data_type VARCHAR(20) DEFAULT 'int';",
            "ALTER TABLE word_registers ADD COLUMN IF NOT EXISTS value_int INTEGER;",
            "ALTER TABLE word_registers ADD COLUMN IF NOT EXISTS value_float DOUBLE PRECISION;",
            "ALTER TABLE word_registers ADD COLUMN IF NOT EXISTS value_str VARCHAR(255);",
            "ALTER TABLE draw_commands ADD COLUMN IF NOT EXISTS register VARCHAR(20) DEFAULT 'D200';",
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS password VARCHAR(100) DEFAULT 'demo123';",
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS plc_ip VARCHAR(50) DEFAULT '192.168.1.10';",
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS plc_port INTEGER DEFAULT 5000;",
        ]
        for stmt in migrations:
            try:
                await conn.execute(text(stmt))
            except Exception as e:
                logger.debug(f"Migration notice: {e}")

    # Seed default company if empty
    async with async_session_factory() as session:
        from sqlalchemy import select
        try:
            result = await session.execute(select(Company).limit(1))
            if result.scalar_one_or_none() is None:
                session.add(Company(
                    name="DemoCorp", password="demo123",
                    plc_ip="192.168.1.10", plc_port=5000
                ))
                await session.commit()
                logger.info("✓ Seeded default company DemoCorp")
        except Exception as e:
            logger.warning(f"Seeding warning: {e}")


async def get_session() -> AsyncSession:
    """Dependency for FastAPI — yields an async session."""
    async with async_session_factory() as session:
        yield session


# ══════════════════════════════════════════════════════════════
# BATCH INSERT — Used by ShiftManager at shift commit
# ══════════════════════════════════════════════════════════════

def _parse_bit_list(json_str: str) -> list[bool]:
    """Safely parse a JSON list of booleans from CSV column."""
    try:
        return [bool(v) for v in json.loads(json_str)]
    except Exception:
        return []


def _parse_registers(json_str: str) -> list[dict]:
    """Safely parse a JSON list of register dicts from CSV column."""
    try:
        return json.loads(json_str)
    except Exception:
        return []


def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


async def batch_insert_readings(rows: list[dict]):
    """
    Bulk-insert a list of row dicts (from CSV buffer) into plc_readings and
    all child tables in a single transaction.

    Each row dict has keys: timestamp, plc_ip, plc_port, status,
                            x_inputs (JSON), y_outputs (JSON), d_registers (JSON)
    """
    if not rows:
        return

    async with async_session_factory() as session:
        async with session.begin():
            for row in rows:
                try:
                    ts_raw = row.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_raw)
                    except Exception:
                        ts = datetime.now()

                    reading = PLCReading(
                        timestamp=ts,
                        plc_ip=row.get("plc_ip", "unknown"),
                        plc_port=int(row.get("plc_port", 0)),
                        status=row.get("status", "OK"),
                    )

                    # Bit inputs
                    x_list = _parse_bit_list(row.get("x_inputs", "[]"))
                    for i, val in enumerate(x_list):
                        reading.bit_inputs.append(BitInput(address=f"X{i}", value=bool(val)))

                    # Bit outputs
                    y_list = _parse_bit_list(row.get("y_outputs", "[]"))
                    for i, val in enumerate(y_list):
                        reading.bit_outputs.append(BitOutput(address=f"Y{i}", value=bool(val)))

                    # Word registers
                    d_list = _parse_registers(row.get("d_registers", "[]"))
                    for reg in d_list:
                        addr = reg.get("address", "")
                        dtype = reg.get("data_type", "int")
                        raw_val = reg.get("value")

                        val_int = None
                        val_float = None
                        val_str = None

                        if dtype in ("int", "int16", "int32"):
                            val_int = _safe_int(raw_val)
                        elif dtype in ("float", "float32"):
                            val_float = _safe_float(raw_val)
                        elif dtype == "string":
                            val_str = str(raw_val) if raw_val is not None else None

                        reading.word_registers.append(WordRegister(
                            address=addr,
                            data_type=dtype,
                            value=str(raw_val) if raw_val is not None else None,
                            value_int=val_int,
                            value_float=val_float,
                            value_str=val_str,
                        ))

                    session.add(reading)
                except Exception as e:
                    logger.error(f"Skipping malformed row during batch insert: {e}")

        logger.info(f"✓ Batch committed {len(rows)} readings to DB")


async def record_shift(
    shift_start: datetime,
    shift_end: datetime,
    row_count: int,
    csv_path: Optional[str],
    plc_ip: Optional[str] = None,
    plc_port: Optional[int] = None,
):
    """Insert a ShiftRecord row after a successful shift commit."""
    async with async_session_factory() as session:
        async with session.begin():
            session.add(ShiftRecord(
                shift_start=shift_start,
                shift_end=shift_end,
                plc_ip=plc_ip,
                plc_port=plc_port,
                row_count=row_count,
                csv_path=csv_path,
            ))
    logger.info(f"✓ ShiftRecord saved for {shift_start.isoformat()}")
