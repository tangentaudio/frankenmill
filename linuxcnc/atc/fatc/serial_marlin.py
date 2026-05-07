"""
serial_marlin.py — Marlin serial protocol layer for the fatc ATC component.

Handles bidirectional communication with Marlin firmware over USB-serial.
No LinuxCNC dependency — usable and testable as a standalone module.

Design:
  - Synchronous send/receive: send_gcode() blocks until 'ok' is received.
  - BUSY:processing messages extend the timeout rather than causing failure.
  - Temperature/debug noise is filtered silently.
  - M400 synchronisation: use wait_for_moves() after motion commands to
    ensure Marlin's move queue is drained before proceeding.
  - Thread-safe write path (single lock around serial.write).
  - Reconnection: call connect() again after a MarlinNotConnected exception.

Phase 1 of FrankenMill ATC (fatc) development.
See: linuxcnc/atc/requirements.md §6, development.md §3 Phase 1
"""

import logging
import re
import threading
import time
from typing import Optional

import serial

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response line classifiers
# ---------------------------------------------------------------------------
_RE_OK = re.compile(r'^ok\b')
_RE_ERROR = re.compile(r'^Error:', re.IGNORECASE)
_RE_ECHO = re.compile(r'^echo:', re.IGNORECASE)
_RE_TEMP = re.compile(r'^T\d*:\s*[\d.]+')          # T:nn.nn or T0:nn.nn
# Marlin sends keepalive as 'echo:busy: processing' (HOST_KEEPALIVE_FEATURE);
# some other firmwares send 'BUSY:processing'. Match both.
_RE_BUSY = re.compile(r'^(BUSY:|echo:busy:)', re.IGNORECASE)
_RE_START = re.compile(r'^start\s*$', re.IGNORECASE)

# M114 position report — covers both 3-axis and 3+C axis responses.
# Marlin prints e.g.: "X:0.00 Y:0.00 Z:0.00 C:0.00 E:0.00 ..."
# The C axis uses Marlin's internal 'I' slot but is reported as 'C' when
# AXIS4_NAME 'C' is set in Configuration.h.
_RE_POS = re.compile(
    r'X:\s*([-\d.]+)\s+Y:\s*([-\d.]+)\s+Z:\s*([-\d.]+)'
    r'(?:.*?(?P<caxis>[CI]):\s*([-\d.]+))?',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MarlinError(Exception):
    """Marlin responded with an Error: line."""


class MarlinTimeout(Exception):
    """No 'ok' received within the allowed timeout."""


class MarlinNotConnected(Exception):
    """Attempted send while the serial port is closed."""


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class MarlinSerial:
    """
    Synchronous serial communication layer for Marlin firmware.

    Typical usage::

        m = MarlinSerial('/dev/ttyUSB0', baud=115200)
        m.connect()
        print(m.get_firmware_info())   # M115
        print(m.get_position())        # M114
        m.send_gcode('G91')            # relative mode
        m.send_gcode('G0 C30')         # rotate 30 degrees
        m.wait_for_moves()             # M400 — block until done
        m.disconnect()
    """

    def __init__(
        self,
        port: str = '/dev/ttyUSB0',
        baud: int = 115200,
        connect_timeout: float = 10.0,
        command_timeout: float = 15.0,
        move_timeout: float = 60.0,
    ):
        self.port = port
        self.baud = baud
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.move_timeout = move_timeout

        self._serial: Optional[serial.Serial] = None
        self._connected = False
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> str:
        """
        Open the serial port and wait for the Marlin startup sequence.

        Returns the firmware identification string from M115.
        Raises serial.SerialException if the port cannot be opened.
        """
        if self._connected:
            log.warning("connect() called while already connected — reconnecting")
            self.disconnect()

        log.info("Connecting to Marlin on %s @ %d baud", self.port, self.baud)
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            timeout=1.0,
            write_timeout=5.0,
        )
        self._connected = True

        try:
            self._wait_for_start()
            info = self.get_firmware_info()
        except Exception:
            self.disconnect()
            raise

        log.info("Connected: %s", info)
        return info

    def disconnect(self):
        """Close the serial port cleanly."""
        self._connected = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        log.info("Disconnected from Marlin")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._serial is not None and self._serial.is_open

    def _wait_for_start(self):
        """
        Drain lines until Marlin sends 'start' (post-boot) or timeout.

        If the board is already running (e.g. reconnect after USB reset),
        'start' will not appear; we fall through after timeout and proceed.
        """
        deadline = time.monotonic() + self.connect_timeout
        log.debug("Waiting for Marlin 'start' (%.1fs timeout)", self.connect_timeout)
        while time.monotonic() < deadline:
            line = self._readline_raw(timeout=1.0)
            if line is None:
                continue
            log.debug("BOOT | %s", line)
            if _RE_START.match(line):
                log.debug("Received 'start' from Marlin")
                return
        log.debug("Startup timeout — board already running or very slow boot")

    # ------------------------------------------------------------------
    # Core send / receive
    # ------------------------------------------------------------------

    def send_gcode(self, cmd: str, timeout: Optional[float] = None) -> list:
        """
        Send a single G-code command and block until Marlin responds with 'ok'.

        Args:
            cmd:     G-code string (e.g. 'G0 C30', 'M114').  No newline needed.
            timeout: Seconds to wait for 'ok'.  Defaults to self.command_timeout.

        Returns:
            List of non-ok response lines collected before the 'ok'
            (position reports, echo output, firmware strings, etc.).

        Raises:
            MarlinNotConnected  if the serial port is not open.
            MarlinError         if Marlin responds with 'Error:'.
            MarlinTimeout       if no 'ok' is received within timeout.
        """
        if not self.is_connected:
            raise MarlinNotConnected("Not connected to Marlin")

        if timeout is None:
            timeout = self.command_timeout

        cmd = cmd.strip()
        log.log(5, "TX | %s", cmd) if cmd.upper().startswith('M114') else log.debug("TX | %s", cmd)

        with self._write_lock:
            try:
                self._serial.write((cmd + '\n').encode('ascii'))
                self._serial.flush()
            except serial.SerialException as exc:
                self._connected = False
                raise MarlinNotConnected(f"Serial write failed: {exc}") from exc

        return self._wait_for_ok(cmd, timeout)

    def _wait_for_ok(self, cmd: str, timeout: float) -> list:
        """
        Read response lines until 'ok'.

        BUSY:processing lines extend the deadline rather than expiring it,
        because Marlin sends them to indicate it is still working.
        """
        deadline = time.monotonic() + timeout
        response_lines = []

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MarlinTimeout(f"Timeout ({timeout:.1f}s) waiting for 'ok' after: {cmd!r}")

            line = self._readline_raw(timeout=min(remaining, 1.0))
            if line is None:
                continue

            _m114 = cmd.upper().startswith('M114')
            if 'Count' in line or _m114:
                log.log(5, "RX | %s", line)
            else:
                log.debug("RX | %s", line)

            if _RE_OK.match(line):
                return response_lines

            if _RE_ERROR.match(line):
                raise MarlinError(f"Marlin error for {cmd!r}: {line}")

            if _RE_BUSY.match(line):
                # Still processing — reset deadline
                deadline = time.monotonic() + timeout
                log.debug("BUSY — timeout reset")
                continue

            if _RE_TEMP.match(line):
                # Thermal noise — discard silently
                continue

            # Collect everything else (position data, firmware info, echo lines)
            response_lines.append(line)

    def _readline_raw(self, timeout: float = 1.0) -> Optional[str]:
        """Read one line from the port.  Returns None on timeout or read error."""
        try:
            self._serial.timeout = timeout
            raw = self._serial.readline()
            if not raw:
                return None
            return raw.decode('ascii', errors='replace').strip()
        except serial.SerialException as exc:
            log.warning("Serial read error: %s", exc)
            self._connected = False
            return None

    # ------------------------------------------------------------------
    # Query commands
    # ------------------------------------------------------------------

    def get_firmware_info(self) -> str:
        """
        Send M115 and return the FIRMWARE_NAME line.
        Falls back to joining all response lines if pattern not found.
        """
        lines = self.send_gcode('M115', timeout=self.connect_timeout)
        for line in lines:
            if 'FIRMWARE_NAME' in line or 'Marlin' in line:
                return line
        return ' | '.join(lines) if lines else '(no M115 response)'

    def get_position(self) -> dict:
        """
        Send M114 and return a dict of axis positions.

        Returns e.g. {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'C': 0.0}
        Keys present depend on what Marlin reports; C is omitted if absent.
        """
        lines = self.send_gcode('M114')
        for line in lines:
            m = _RE_POS.search(line)
            if m:
                pos = {
                    'X': float(m.group(1)),
                    'Y': float(m.group(2)),
                    'Z': float(m.group(3)),
                }
                if m.group('caxis') and m.group(5):
                    pos['C'] = float(m.group(5))
                return pos
        log.warning("Could not parse M114 response: %s", lines)
        return {}

    def get_endstop_status(self) -> dict:
        """
        Send M119 and return a dict of endstop states.
        Returns e.g. {'x_min': 'open', 'z_min': 'TRIGGERED'}
        """
        lines = self.send_gcode('M119')
        status = {}
        for line in lines:
            # Marlin format: "x_min: open" or "x_min: TRIGGERED"
            parts = line.split(':', 1)
            if len(parts) == 2:
                status[parts[0].strip().lower()] = parts[1].strip()
        return status

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------

    def wait_for_moves(self, timeout: Optional[float] = None) -> None:
        """
        Send M400 (wait for all queued moves to complete).

        Marlin will not respond 'ok' until its move queue is empty.
        Use after any G0/G1 motion commands when you need to know the
        physical move is done before proceeding.
        """
        t = timeout if timeout is not None else self.move_timeout
        self.send_gcode('M400', timeout=t)

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------

    def emergency_stop(self):
        """
        Send M112 (hard emergency stop).  Does not wait for 'ok'.
        Should be called on E-stop; Marlin halts all motion immediately.
        """
        if not self.is_connected:
            return
        try:
            log.warning("Sending M112 EMERGENCY STOP to Marlin")
            with self._write_lock:
                self._serial.write(b'M112\n')
                self._serial.flush()
        except Exception as exc:
            log.error("Failed to send M112: %s", exc)

    def quick_stop(self):
        """Send M410 (decelerate to stop, preserving position)."""
        self.send_gcode('M410')

    # ------------------------------------------------------------------
    # Positioning mode
    # ------------------------------------------------------------------

    def set_relative(self):
        """G91 — relative positioning mode."""
        self.send_gcode('G91')

    def set_absolute(self):
        """G90 — absolute positioning mode."""
        self.send_gcode('G90')

    def disable_software_endstops(self):
        """
        M211 S0 — disable software endstop checking.
        Use during development when axes are not homed.
        """
        self.send_gcode('M211 S0')
        log.info("Software endstops disabled (M211 S0)")

    # ------------------------------------------------------------------
    # Motion helpers
    # ------------------------------------------------------------------

    def move(self, axis: str, pos: float, feedrate: Optional[int] = None,
             wait: bool = True):
        """
        Move a single axis to pos (in that axis's native units).

        Args:
            axis:      Axis letter, e.g. 'X' or 'C'.
            pos:       Target position (mm for X, degrees for C).
            feedrate:  If given, use G1 with this feedrate (units/min).
                       If omitted, use G0 (rapid).
            wait:      If True, call wait_for_moves() after the command.
        """
        if feedrate:
            cmd = f'G1 {axis.upper()}{pos:.3f} F{feedrate}'
        else:
            cmd = f'G0 {axis.upper()}{pos:.3f}'
        self.send_gcode(cmd)
        if wait:
            self.wait_for_moves()

    def move_linear(self, pos_mm: float, feedrate: Optional[int] = None,
                    wait: bool = True):
        """Move the linear (X) axis to pos_mm."""
        self.move('X', pos_mm, feedrate=feedrate, wait=wait)

    def move_rotation(self, angle_deg: float, feedrate: Optional[int] = None,
                      wait: bool = True):
        """Move the rotation (C) axis to angle_deg."""
        self.move('C', angle_deg, feedrate=feedrate, wait=wait)

    def home_axes(self, axes: str = 'XC', timeout: Optional[float] = None):
        """
        Home the specified axes.

        IMPORTANT: Linear (X) must always home before rotation (C).
        The homing sensor for X is at the retracted end; attempting to home
        C while extended risks a collision.

        Args:
            axes:    String containing axis letters to home, e.g. 'XC', 'X', 'C'.
            timeout: Per-axis homing timeout.  Defaults to self.move_timeout.
        """
        t = timeout if timeout is not None else self.move_timeout
        axes = axes.upper()
        if 'X' in axes:
            log.info("Homing X (linear) axis")
            self.send_gcode('G28 X', timeout=t)
            self.wait_for_moves(timeout=t)
        if 'C' in axes:
            log.info("Homing C (rotation) axis")
            self.send_gcode('G28 C', timeout=t)
            self.wait_for_moves(timeout=t)
