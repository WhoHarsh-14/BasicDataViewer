"""
PLC Poller — Background service that reads PLC data every N seconds
(configurable via system_config.json), buffers it via ShiftManager,
and broadcasts live data via WebSocket.

Data is NO LONGER written to DB per-poll. Instead, each reading is handed
to ShiftManager which writes a crash-safe WAL CSV and batch-uploads to DB
at the end of each shift (or on force-end).
"""
import asyncio
import random
import math
import json
import logging
import os
import struct
from datetime import datetime, timezone

from config import PLC_IP, PLC_PORT, PLC_POLL_INTERVAL, SIMULATION_MODE
from shift_manager import shift_manager

logger = logging.getLogger("plc_poller")


class PLCPoller:
    """Polls the Mitsubishi PLC and feeds data to ShiftManager + WebSocket clients."""

    def __init__(self):
        self.plc_ip = None
        self.plc_port = None
        self.simulation_mode = True
        self.connected = False
        self.plc = None
        self.running = False
        self.websocket_clients: set = set()
        self._sim_tick = 0
        self.machine_states = {}
        self.lines_layout = []

        # Dynamic register configuration path
        self.config_path = os.path.join(os.path.dirname(__file__), "register_config.json")
        self.registers_config = []
        self.expanded_config = []
        self.load_registers_config()

    def load_registers_config(self):
        """Load and expand registers configuration from JSON file."""
        try:
            if not os.path.exists(self.config_path):
                default_config = [
                    {"address": "D100", "data_type": "int", "name": "Temperature"},
                    {"address": "D101", "data_type": "int", "name": "Pressure"},
                    {"address": "D102", "data_type": "int", "name": "Flow Rate"}
                ]
                with open(self.config_path, 'w') as f:
                    json.dump(default_config, f, indent=2)

            with open(self.config_path, 'r') as f:
                self.registers_config = json.load(f)

            # Load line_config.json and append registers
            line_cfg_path = os.path.join(os.path.dirname(__file__), "line_config.json")
            if os.path.exists(line_cfg_path):
                with open(line_cfg_path, 'r') as f:
                    self.lines_layout = json.load(f)
                for line in self.lines_layout:
                    line_name = line.get("line_name", "")
                    regs = line.get("registers", {})
                    for key, addr in regs.items():
                        if addr:
                            self.registers_config.append({
                                "address": addr,
                                "data_type": "float" if "accuracy" in key else "int",
                                "name": f"{line_name} - {key.replace('_', ' ').title()}"
                            })
                    for machine in line.get("machines", []):
                        m_name = machine.get("machine_name", "")
                        m_regs = machine.get("registers", {})
                        for key, addr in m_regs.items():
                            if addr:
                                self.registers_config.append({
                                    "address": addr,
                                    "data_type": "float" if "accuracy" in key else "int",
                                    "name": f"{line_name} - {m_name} - {key.replace('_', ' ').title()}"
                                })

            self._expand_registers_config()
            logger.info(
                f"✓ Loaded register configuration. "
                f"Monitored: {len(self.registers_config)} items, "
                f"Expanded: {len(self.expanded_config)} registers."
            )
        except Exception as e:
            logger.error(f"Failed to load register configuration: {e}")
            self.registers_config = [
                {"address": "D100", "data_type": "int", "name": "Temperature"},
                {"address": "D101", "data_type": "int", "name": "Pressure"},
                {"address": "D102", "data_type": "int", "name": "Flow Rate"}
            ]
            self._expand_registers_config()

    def _expand_registers_config(self):
        """Expand range configs like 'D100-D105' into individual items."""
        expanded = []
        for item in self.registers_config:
            addr = item.get("address", "").strip()
            dtype = item.get("data_type", "int").strip().lower()
            name = item.get("name", addr).strip()

            if "-" in addr:
                parts = addr.split("-")
                try:
                    start_idx = int(parts[0].strip().replace('D', ''))
                    end_idx = int(parts[1].strip().replace('D', ''))

                    if dtype in ("int", "int16"):
                        for idx in range(start_idx, end_idx + 1):
                            expanded.append({
                                "address": f"D{idx}",
                                "data_type": dtype,
                                "name": f"{name} (D{idx})" if name != addr else f"D{idx}"
                            })
                    elif dtype in ("float", "float32", "int32"):
                        for idx in range(start_idx, end_idx + 1, 2):
                            if idx + 1 <= end_idx:
                                expanded.append({
                                    "address": f"D{idx}",
                                    "data_type": dtype,
                                    "name": f"{name} (D{idx})" if name != addr else f"D{idx}"
                                })
                    elif dtype == "string":
                        expanded.append({
                            "address": addr,
                            "data_type": dtype,
                            "name": name,
                            "start_idx": start_idx,
                            "count": end_idx - start_idx + 1
                        })
                except ValueError:
                    expanded.append(item)
            else:
                expanded.append(item)
        self.expanded_config = expanded

    def set_target(self, ip, port, simulation_mode):
        """Configure the target PLC dynamically."""
        if ip != self.plc_ip or port != self.plc_port or simulation_mode != self.simulation_mode:
            logger.info(f"Updating PLC target: {ip}:{port} (sim={simulation_mode})")
            self.plc_ip = ip
            self.plc_port = port
            self.simulation_mode = simulation_mode
            self.connected = False
            self.plc = None

    # ─── Connection ───
    def _connect_plc(self):
        """Connect to the physical PLC via MC Protocol Type3E."""
        if not self.plc_ip or not self.plc_port:
            self.connected = False
            return

        if self.simulation_mode:
            self.connected = True
            logger.info(f"▶ Simulation mode for PLC at {self.plc_ip}:{self.plc_port}")
            return

        try:
            from pymcprotocol import Type3E
            self.plc = Type3E()
            self.plc.connect(self.plc_ip, self.plc_port)
            self.connected = True
            logger.info(f"✓ Connected to physical PLC at {self.plc_ip}:{self.plc_port}")
        except Exception as e:
            self.connected = False
            logger.warning(f"✗ PLC connection failed to {self.plc_ip}:{self.plc_port}: {e}")

    # ─── Simulation Data ───
    def _simulate_data(self):
        """Generate realistic-looking fake PLC data matching configured datatypes."""
        self._sim_tick += 1
        t = self._sim_tick * PLC_POLL_INTERVAL
        dt = PLC_POLL_INTERVAL

        x_data = [random.random() > 0.7 for _ in range(18)]
        x_data[0] = int(t) % 4 < 2
        x_data[1] = int(t) % 6 < 3

        y_data = [random.random() > 0.8 for _ in range(18)]
        y_data[0] = x_data[0]
        y_data[1] = not x_data[1]

        raw_values = {}

        # 1. Simulate base registers from register_config.json
        for item in self.expanded_config:
            addr = item.get("address", "")
            dtype = item.get("data_type", "int")

            # Skip line/machine specific registers to avoid double setting
            is_line_machine_reg = False
            for line in self.lines_layout:
                if addr in line.get("registers", {}).values():
                    is_line_machine_reg = True
                    break
                for m in line.get("machines", []):
                    if addr in m.get("registers", {}).values():
                        is_line_machine_reg = True
                        break
            if is_line_machine_reg:
                continue

            if "-" in addr:
                start_idx = item.get("start_idx")
                count = item.get("count", 1)
            else:
                start_idx = int(addr.replace('D', ''))
                count = 2 if dtype in ("float", "float32", "int32") else 1

            if dtype in ("int", "int16"):
                val = int(500 + 300 * math.sin(t * 0.5 + start_idx * 0.1) + random.randint(-5, 5))
                val = max(-32768, min(32767, val))
                w = val if val >= 0 else val + 65536
                raw_values[start_idx] = w
            elif dtype == "int32":
                val = int(100000 + 50000 * math.cos(t * 0.3 + start_idx * 0.1) + random.randint(-50, 50))
                raw_values[start_idx] = val & 0xFFFF
                raw_values[start_idx + 1] = (val >> 16) & 0xFFFF
            elif dtype in ("float", "float32"):
                val = 50.0 + 20.0 * math.sin(t * 0.4 + start_idx * 0.1) + random.random()
                packed = struct.pack('<f', val)
                w1, w2 = struct.unpack('<HH', packed)
                raw_values[start_idx] = w1
                raw_values[start_idx + 1] = w2
            elif dtype == "string":
                states = ["RUNNING", "IDLE", "PAUSED", "WARNING", "STOPPED", "ERROR"]
                state = states[int(t // 2) % len(states)]
                padded = state.ljust(count * 2, '\x00')
                for k in range(count):
                    c1 = ord(padded[k * 2])
                    c2 = ord(padded[k * 2 + 1])
                    raw_values[start_idx + k] = (c2 << 8) | c1

        # 2. Simulate Production Lines & Machines
        if not self.machine_states:
            for line in self.lines_layout:
                for machine in line.get("machines", []):
                    m_id = machine["machine_id"]
                    self.machine_states[m_id] = {
                        "target": 500,
                        "actual": 120,
                        "runtime": 3600.0,
                        "idle": 600.0,
                        "breakdown": 120.0,
                        "state": "running",
                        "state_timer": 0.0,
                        "state_duration": random.uniform(30.0, 120.0),
                        "ok_count": 115,
                        "ng_count": 5,
                        "last_part_tick": 0.0
                    }

        # Update machine state machines
        for line in self.lines_layout:
            for machine in line.get("machines", []):
                m_id = machine["machine_id"]
                m_state = self.machine_states[m_id]

                m_state["state_timer"] += dt
                m_state["last_part_tick"] += dt

                # Switch state?
                if m_state["state_timer"] >= m_state["state_duration"]:
                    m_state["state_timer"] = 0.0
                    m_state["state_duration"] = random.uniform(30.0, 150.0)
                    r = random.random()
                    if r < 0.70:
                        m_state["state"] = "running"
                    elif r < 0.90:
                        m_state["state"] = "idle"
                    else:
                        m_state["state"] = "breakdown"

                # Accumulate
                if m_state["state"] == "running":
                    m_state["runtime"] += dt
                    # Produce a part every 4 to 8 seconds
                    if m_state["last_part_tick"] >= random.uniform(4.0, 8.0):
                        m_state["last_part_tick"] = 0.0
                        if m_state["actual"] < m_state["target"]:
                            m_state["actual"] += 1
                            if random.random() < 0.95:
                                m_state["ok_count"] += 1
                            else:
                                m_state["ng_count"] += 1
                elif m_state["state"] == "idle":
                    m_state["idle"] += dt
                elif m_state["state"] == "breakdown":
                    m_state["breakdown"] += dt

        def set_reg_val(addr, val, dtype):
            if not addr: return
            idx = int(addr.replace('D', ''))
            if dtype in ("int", "int16"):
                val = int(val)
                val = max(-32768, min(32767, val))
                w = val if val >= 0 else val + 65536
                raw_values[idx] = w
            elif dtype == "int32":
                val = int(val)
                raw_values[idx] = val & 0xFFFF
                raw_values[idx + 1] = (val >> 16) & 0xFFFF
            elif dtype in ("float", "float32"):
                val = float(val)
                packed = struct.pack('<f', val)
                w1, w2 = struct.unpack('<HH', packed)
                raw_values[idx] = w1
                raw_values[idx + 1] = w2

        # Populate register raw_values
        for line in self.lines_layout:
            line_target = 0
            line_actual = 0
            line_time = 0

            for machine in line.get("machines", []):
                m_id = machine["machine_id"]
                m_state = self.machine_states[m_id]

                line_target += m_state["target"]
                line_actual += m_state["actual"]

                m_time = int((m_state["target"] - m_state["actual"]) * 6.0)
                m_time = max(0, m_time)
                line_time = max(line_time, m_time)

                m_acc = (m_state["actual"] / m_state["target"] * 100.0) if m_state["target"] > 0 else 0.0
                m_acc = min(100.0, m_acc)

                prod_type_val = 2 if m_state["state"] == "breakdown" else 1

                m_regs = machine.get("registers", {})
                set_reg_val(m_regs.get("target"), m_state["target"], "int")
                set_reg_val(m_regs.get("actual"), m_state["actual"], "int")
                set_reg_val(m_regs.get("accuracy"), m_acc, "float")
                set_reg_val(m_regs.get("time_to_complete"), m_time, "int")
                set_reg_val(m_regs.get("breakdown_time"), int(m_state["breakdown"]), "int")
                set_reg_val(m_regs.get("idle_time"), int(m_state["idle"]), "int")
                set_reg_val(m_regs.get("runtime"), int(m_state["runtime"]), "int")
                set_reg_val(m_regs.get("product_type"), prod_type_val, "int")

            line_acc = (line_actual / line_target * 100.0) if line_target > 0 else 0.0
            line_acc = min(100.0, line_acc)

            line_regs = line.get("registers", {})
            set_reg_val(line_regs.get("target"), line_target, "int")
            set_reg_val(line_regs.get("actual"), line_actual, "int")
            set_reg_val(line_regs.get("accuracy"), line_acc, "float")
            set_reg_val(line_regs.get("time_to_complete"), line_time, "int")

        return x_data, y_data, raw_values

    # ─── Read from PLC ───
    def _read_plc(self):
        """Read all data from physical/simulated PLC."""
        if self.simulation_mode:
            return self._simulate_data()

        x_data = self.plc.batchread_bitunits("X0", 18)
        y_data = self.plc.batchread_bitunits("Y0", 18)

        if not self.expanded_config:
            return x_data, y_data, {}

        indices = set()
        for item in self.expanded_config:
            addr = item.get("address", "")
            dtype = item.get("data_type", "int")
            if "-" in addr:
                start_idx = item.get("start_idx")
                count = item.get("count", 1)
                for k in range(count):
                    indices.add(start_idx + k)
            else:
                start_idx = int(addr.replace('D', ''))
                indices.add(start_idx)
                if dtype in ("float", "float32", "int32"):
                    indices.add(start_idx + 1)

        sorted_indices = sorted(list(indices))
        min_idx = sorted_indices[0]
        max_idx = sorted_indices[-1]
        span = max_idx - min_idx + 1

        raw_words = self.plc.batchread_wordunits(f"D{min_idx}", span)
        raw_values = {min_idx + k: raw_words[k] for k in range(span)}

        return x_data, y_data, raw_values

    # ─── Parse registers ───
    def _parse_registers(self, raw_values: dict) -> list[dict]:
        """Convert raw word values into typed register dicts for buffering and broadcast."""
        parsed = []
        for item in self.expanded_config:
            addr = item.get("address", "")
            dtype = item.get("data_type", "int")
            name = item.get("name", addr)

            if "-" in addr:
                start_idx = item.get("start_idx")
                count = item.get("count", 1)
            else:
                start_idx = int(addr.replace('D', ''))
                count = 2 if dtype in ("float", "float32", "int32") else 1

            words = [raw_values.get(start_idx + k, 0) for k in range(count)]
            val = None

            if dtype in ("int", "int16") or (dtype == "string" and count == 1):
                w = words[0]
                if dtype in ("int", "int16"):
                    val = w if w < 32768 else w - 65536
                else:
                    b1 = w & 0xFF
                    b2 = (w >> 8) & 0xFF
                    val = (chr(b1) if b1 != 0 else "") + (chr(b2) if b2 != 0 else "")
                    val = val.strip()
            elif dtype == "int32":
                w1, w2 = words[0], words[1]
                unsigned = (w2 << 16) | w1
                val = unsigned if unsigned < 2147483648 else unsigned - 4294967296
            elif dtype in ("float", "float32"):
                packed = struct.pack('<HH', words[0], words[1])
                val = round(struct.unpack('<f', packed)[0], 3)
            elif dtype == "string":
                chars = []
                for w in words:
                    b1 = w & 0xFF
                    b2 = (w >> 8) & 0xFF
                    if b1 != 0: chars.append(chr(b1))
                    if b2 != 0: chars.append(chr(b2))
                val = "".join(chars).strip('\x00').strip()

            parsed.append({"address": addr, "name": name, "data_type": dtype, "value": val})
        return parsed

    # ─── Broadcast via WebSocket ───
    async def _broadcast(self, timestamp, x_data, y_data, parsed_registers, status):
        """Push live data to all connected WebSocket clients."""
        if not self.websocket_clients:
            return

        payload = json.dumps({
            "timestamp": timestamp.isoformat(),
            "status": status,
            "plc_ip": self.plc_ip,
            "plc_port": self.plc_port,
            "x_inputs": [{"address": f"X{i}", "value": bool(v)} for i, v in enumerate(x_data)],
            "y_outputs": [{"address": f"Y{i}", "value": bool(v)} for i, v in enumerate(y_data)],
            "d_registers": parsed_registers,
        })

        dead = set()
        for ws in self.websocket_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self.websocket_clients -= dead

    async def _broadcast_disconnected(self):
        """Broadcast disconnected state to web clients when idle."""
        if not self.websocket_clients:
            return

        payload = json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "DISCONNECTED",
            "plc_ip": self.plc_ip,
            "plc_port": self.plc_port,
            "x_inputs": [],
            "y_outputs": [],
            "d_registers": [],
        })

        dead = set()
        for ws in self.websocket_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self.websocket_clients -= dead

    # ─── Main Loop ───
    async def run(self):
        """Main polling loop — runs as an asyncio background task."""
        self.running = True
        logger.info(f"PLC Poller starting (interval={PLC_POLL_INTERVAL}s)")

        while self.running:
            if not self.plc_ip or not self.plc_port:
                await self._broadcast_disconnected()
                await asyncio.sleep(1.0)
                continue

            if not self.connected:
                self._connect_plc()
                if not self.connected:
                    await self._broadcast_disconnected()
                    await asyncio.sleep(2)
                    continue

            try:
                x_data, y_data, raw_values = self._read_plc()
                parsed_registers = self._parse_registers(raw_values)
                now = datetime.now(timezone.utc)

                # ── Hand off to ShiftManager (replaces direct DB write) ──
                await shift_manager.add_reading(
                    timestamp=now,
                    plc_ip=self.plc_ip,
                    plc_port=self.plc_port,
                    status="OK",
                    x_inputs=[bool(v) for v in x_data],
                    y_outputs=[bool(v) for v in y_data],
                    d_registers=parsed_registers,
                )

                # ── WebSocket broadcast (live, unchanged) ──
                await self._broadcast(now, x_data, y_data, parsed_registers, "OK")

            except Exception as e:
                logger.error(f"Poll error: {e}")
                self.connected = False
                await self._broadcast_disconnected()

            await asyncio.sleep(PLC_POLL_INTERVAL)

    def reset_machine_shift_accumulators(self):
        """Reset machine counters and accumulators for a new shift."""
        for m_id, state in self.machine_states.items():
            state["actual"] = 0
            state["runtime"] = 0.0
            state["idle"] = 0.0
            state["breakdown"] = 0.0
            state["ok_count"] = 0
            state["ng_count"] = 0
            state["last_part_tick"] = 0.0
        logger.info("✓ Reset machine shift accumulators for new shift")

    def stop(self):
        """Signal the polling loop to stop."""
        self.running = False
        logger.info("PLC Poller stopping…")


# Singleton instance
poller = PLCPoller()
