"""Pydantic schemas for API serialization."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ─── Bit I/O ───
class BitIOSchema(BaseModel):
    address: str
    value: bool

    class Config:
        from_attributes = True


# ─── Word Register ───
class WordRegisterSchema(BaseModel):
    address: str
    value: int

    class Config:
        from_attributes = True


# ─── Full PLC Reading (response) ───
class PLCReadingSchema(BaseModel):
    id: int
    timestamp: datetime
    plc_ip: str
    plc_port: int
    status: str
    bit_inputs: list[BitIOSchema]
    bit_outputs: list[BitIOSchema]
    word_registers: list[WordRegisterSchema]

    class Config:
        from_attributes = True


# ─── Compact reading for list views ───
class PLCReadingSummary(BaseModel):
    id: int
    timestamp: datetime
    plc_ip: str
    plc_port: int
    status: str
    registers: dict[str, Optional[str]] = {}

    class Config:
        from_attributes = True


# ─── Register history point (for charting) ───
class RegisterHistoryPoint(BaseModel):
    timestamp: datetime
    address: str
    value: int

    class Config:
        from_attributes = True


# ─── Draw command ───
class DrawCommandCreate(BaseModel):
    x: int
    y: int
    plc_ip: str
    plc_port: int
    register: str = "D200"


class DrawCommandSchema(BaseModel):
    id: int
    timestamp: datetime
    plc_ip: str
    plc_port: int
    register: str
    x: int
    y: int

    class Config:
        from_attributes = True


# ─── Model introspection ───
class ColumnInfo(BaseModel):
    name: str
    type: str
    primary_key: bool
    nullable: bool
    foreign_key: Optional[str] = None
    index: bool = False


class RelationshipInfo(BaseModel):
    name: str
    target_table: str
    type: str  # "one-to-many" / "many-to-one"


class TableSchema(BaseModel):
    table_name: str
    description: str
    columns: list[ColumnInfo]
    relationships: list[RelationshipInfo]


# ─── WebSocket live data payload ───
class LiveDataPayload(BaseModel):
    timestamp: str
    status: str
    x_inputs: list[dict]    # [{address, value}, ...]
    y_outputs: list[dict]
    d_registers: list[dict]


# ─── Shift Summary Schemas ───
class ShiftSummarySchema(BaseModel):
    id: int
    date: str
    shift_name: str
    line_id: str
    line_name: str
    plc_ip: Optional[str] = None
    target: int
    production: int
    runtime_minutes: float
    idle_time_minutes: float
    breakdown_time_minutes: float
    efficiency: float
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ShiftSummaryCreate(BaseModel):
    date: str
    shift_name: str
    line_id: str
    line_name: str
    plc_ip: Optional[str] = None
    target: int = 0
    production: int = 0
    runtime_minutes: float = 0.0
    idle_time_minutes: float = 0.0
    breakdown_time_minutes: float = 0.0
    efficiency: float = 0.0


class PLCConnectionPreset(BaseModel):
    id: str
    name: str
    ip: str
    port: int
    simulation: bool = False

