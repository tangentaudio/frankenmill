"""
serial_thread.py — Asynchronous Marlin serial worker for the fatc ATC component.

Architecture
------------
The HAL poll loop (10 ms) must never block.  Marlin serial operations take
10–50 ms for simple queries and can take seconds for motion commands.
Putting the serial work on a dedicated background thread decouples the two.

The serial thread owns the MarlinSerial object exclusively.  All interaction
goes through thread-safe queues and Events.

        HAL thread (10 ms poll)            Serial thread
        ─────────────────────────          ──────────────────────────────
        post(cmd) ─► command_q            get cmd from command_q
                                          send to Marlin  (blocks)
        result_q ◄─ post result           put result on result_q

Motion completion is tracked by polling M114 at ``position_poll_interval``
rather than blocking on M400.  This keeps the serial thread interruptible and
allows M112 to be sent at any time during a move.

Emergency stop path
-------------------
``request_estop()`` writes M112 directly to the serial port, bypassing the
command queue and the current send_gcode() call, then sets the ``abort``
event.  The serial thread's inner read loop checks the event between lines
and exits early if it fires.

Note: Marlin on this build has EMERGENCY_PARSER disabled (Cap:EMERGENCY_PARSER:0).
M112 sent during a blocking serial read will only be processed after the
current command's 'ok' arrives.  To avoid this race, we do NOT use M400; all
motion is followed by M114 polls so the serial thread is never inside a
multi-second send_gcode() call.

Thread lifecycle
----------------
1. Instantiate SerialThread(cfg)
2. Call start()  — spawns background thread, returns immediately
3. Call stop()   — sets stop event, joins thread
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

from .serial_marlin import MarlinSerial, MarlinError, MarlinTimeout, MarlinNotConnected
from . import config as cfg_module

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command / Result protocol
# ---------------------------------------------------------------------------

class CmdType(Enum):
    GCODE      = auto()   # send a G-code string, return response lines
    GET_POS    = auto()   # M114, return position dict
    GET_ESTOP  = auto()   # M119, return endstop dict
    MOVE_TO    = auto()   # send G0/G1 then poll M114 until settled
    HOME       = auto()   # G28 axes, poll until settled
    DISCONNECT = auto()   # internal: close port
    ESTOP      = auto()   # internal: M112 fire-and-forget


@dataclass
class Command:
    kind: CmdType
    args: dict = field(default_factory=dict)
    # Caller optionally provides a result slot; if None result is discarded
    result_slot: Optional[queue.Queue] = None


@dataclass
class Result:
    ok: bool
    value: Any = None       # depends on command kind
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# SerialThread
# ---------------------------------------------------------------------------

class SerialThread:
    """
    Background thread that owns the Marlin serial port.

    All methods are safe to call from any thread.
    """

    RECONNECT_DELAY = 5.0   # seconds between reconnect attempts
    HEARTBEAT_INTERVAL = 1.0  # seconds between idle M114 polls

    def __init__(self, cfg: cfg_module.Config):
        self._cfg = cfg
        self._marlin = MarlinSerial(
            port=cfg.serial_port,
            baud=cfg.baud_rate,
            connect_timeout=cfg.connect_timeout,
            command_timeout=cfg.serial_timeout,
            move_timeout=cfg.move_timeout,
        )
        self._cmd_q: queue.Queue[Command] = queue.Queue(maxsize=16)
        self._stop_event = threading.Event()
        self._abort_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._connected_lock = threading.Lock()
        self._last_position: dict = {}
        self._position_lock = threading.Lock()
        self._last_heartbeat: float = 0.0

    # ------------------------------------------------------------------
    # Public thread control
    # ------------------------------------------------------------------

    def start(self):
        """Spawn the background serial worker thread."""
        self._thread = threading.Thread(
            target=self._run,
            name='fatc-serial',
            daemon=True,
        )
        self._thread.start()
        log.info("Serial thread started")

    def stop(self, timeout: float = 5.0):
        """Signal the thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            # Unblock the queue.get() if thread is waiting
            self._cmd_q.put(Command(kind=CmdType.DISCONNECT))
            self._thread.join(timeout=timeout)
        log.info("Serial thread stopped")

    # ------------------------------------------------------------------
    # Public status properties (thread-safe reads)
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        with self._connected_lock:
            return self._connected

    @property
    def last_position(self) -> dict:
        """Most recently received M114 position dict.  Empty until first heartbeat."""
        with self._position_lock:
            return dict(self._last_position)

    # ------------------------------------------------------------------
    # Async fire-and-forget: emergency stop
    # ------------------------------------------------------------------

    def request_estop(self):
        """
        Send M112 immediately, bypassing the command queue.

        Safe to call from any thread at any time.  Sets the abort event so
        any in-progress send_gcode() read loop can exit early.
        """
        self._abort_event.set()
        try:
            if self._marlin.is_connected:
                log.warning("ESTOP: sending M112 directly to Marlin")
                self._marlin.emergency_stop()
        except Exception as exc:
            log.error("ESTOP M112 send failed: %s", exc)

    # ------------------------------------------------------------------
    # Async command submission (non-blocking, best-effort)
    # ------------------------------------------------------------------

    def post(self, cmd: Command, block: bool = False, timeout: float = 2.0) -> bool:
        """
        Enqueue a command for the serial thread to execute.

        Returns True if queued, False if the queue is full (dropped).
        If block=True, waits up to timeout seconds for a queue slot.
        """
        try:
            self._cmd_q.put(cmd, block=block, timeout=timeout)
            return True
        except queue.Full:
            log.warning("Serial command queue full — dropped %s", cmd.kind)
            return False

    def post_gcode(self, gcode: str) -> None:
        """Convenience: enqueue a raw G-code string, no result slot."""
        self.post(Command(kind=CmdType.GCODE, args={'cmd': gcode}))

    def query_position(self) -> 'queue.Queue[Result]':
        """
        Request an M114 position query.

        Returns a queue.Queue; caller should call .get(timeout=...) on it
        to receive the Result when the serial thread completes the query.
        """
        slot: queue.Queue[Result] = queue.Queue(maxsize=1)
        self.post(Command(kind=CmdType.GET_POS, result_slot=slot))
        return slot

    def command_move_to(
        self,
        axis: str,
        position: float,
        feedrate: int,
        settle_tolerance: float = 0.5,
    ) -> 'queue.Queue[Result]':
        """
        Command a move and poll M114 until the axis reaches position.

        Returns a result queue; Result.ok is True when move completes,
        False on timeout or abort.
        """
        slot: queue.Queue[Result] = queue.Queue(maxsize=1)
        self.post(Command(
            kind=CmdType.MOVE_TO,
            args={
                'axis': axis.upper(),
                'position': position,
                'feedrate': feedrate,
                'settle_tolerance': settle_tolerance,
            },
            result_slot=slot,
        ))
        return slot

    def command_home(self, axes: str = 'XC') -> 'queue.Queue[Result]':
        """Home the specified axes (linear first, then rotation)."""
        slot: queue.Queue[Result] = queue.Queue(maxsize=1)
        self.post(Command(
            kind=CmdType.HOME,
            args={'axes': axes.upper()},
            result_slot=slot,
        ))
        return slot

    # ------------------------------------------------------------------
    # Background thread main loop
    # ------------------------------------------------------------------

    def _run(self):
        log.debug("Serial thread entering run loop")
        while not self._stop_event.is_set():
            # (Re)connect if needed
            if not self._marlin.is_connected:
                self._try_connect()
                if not self._marlin.is_connected:
                    # Wait before retrying; bail if stop requested
                    self._stop_event.wait(timeout=self.RECONNECT_DELAY)
                    continue

            # Process queued commands; use heartbeat interval as queue timeout
            # so the idle heartbeat fires even when no commands are pending.
            try:
                cmd = self._cmd_q.get(timeout=0.5)
            except queue.Empty:
                self._maybe_heartbeat()
                continue

            if self._stop_event.is_set():
                break

            self._dispatch(cmd)
            # Reset heartbeat timer after any successful command so we don't
            # immediately follow a move with an extra M114.
            self._last_heartbeat = time.monotonic()

        # Clean shutdown
        if self._marlin.is_connected:
            self._marlin.disconnect()
        with self._connected_lock:
            self._connected = False
        log.debug("Serial thread exiting")

    def _maybe_heartbeat(self):
        """Send M114 if the heartbeat interval has elapsed.  Marks disconnected on failure."""
        if time.monotonic() - self._last_heartbeat < self.HEARTBEAT_INTERVAL:
            return
        try:
            pos = self._marlin.get_position()
            if pos:
                with self._position_lock:
                    self._last_position = pos
                log.log(5, "Heartbeat: %s", pos)  # level 5 = below DEBUG, effectively silent
            else:
                log.warning("Heartbeat: empty M114 response")
        except Exception as exc:
            log.warning("Heartbeat failed — marking disconnected: %s", exc)
            with self._connected_lock:
                self._connected = False
            try:
                self._marlin.disconnect()
            except Exception:
                pass
        finally:
            self._last_heartbeat = time.monotonic()

    def _try_connect(self):
        log.info("Attempting to connect to Marlin on %s", self._cfg.serial_port)
        try:
            self._marlin.connect()
            # On connect: set absolute mode, disable software endstops for dev
            self._marlin.send_gcode('G90')
            if self._cfg.homing_required == 0:
                self._marlin.disable_software_endstops()
            with self._connected_lock:
                self._connected = True
            self._abort_event.clear()
            # Seed heartbeat timer so we don't poll immediately after connecting
            self._last_heartbeat = time.monotonic()
            log.info("Marlin connected and ready")
        except Exception as exc:
            log.warning("Marlin connect failed: %s", exc)
            with self._connected_lock:
                self._connected = False

    def _dispatch(self, cmd: Command):
        """Execute a single command and put the result in cmd.result_slot."""
        if not self._marlin.is_connected and cmd.kind not in (CmdType.DISCONNECT, CmdType.ESTOP):
            self._deliver(cmd, Result(ok=False, error="Not connected"))
            return

        try:
            result = self._execute(cmd)
        except MarlinNotConnected as exc:
            log.warning("Marlin disconnected during command: %s", exc)
            with self._connected_lock:
                self._connected = False
            result = Result(ok=False, error=str(exc))
        except MarlinTimeout as exc:
            log.warning("Marlin timeout: %s", exc)
            result = Result(ok=False, error=str(exc))
        except MarlinError as exc:
            log.error("Marlin error: %s", exc)
            result = Result(ok=False, error=str(exc))
        except Exception as exc:
            log.exception("Unexpected error in serial thread")
            result = Result(ok=False, error=str(exc))

        self._deliver(cmd, result)

    def _deliver(self, cmd: Command, result: Result):
        if cmd.result_slot is not None:
            try:
                cmd.result_slot.put_nowait(result)
            except queue.Full:
                log.warning("Result slot full for %s — discarded", cmd.kind)

    # ------------------------------------------------------------------
    # Command executors
    # ------------------------------------------------------------------

    def _execute(self, cmd: Command) -> Result:
        kind = cmd.kind
        args = cmd.args

        if kind == CmdType.GCODE:
            lines = self._marlin.send_gcode(args['cmd'])
            return Result(ok=True, value=lines)

        if kind == CmdType.GET_POS:
            pos = self._marlin.get_position()
            return Result(ok=bool(pos), value=pos)

        if kind == CmdType.GET_ESTOP:
            status = self._marlin.get_endstop_status()
            return Result(ok=True, value=status)

        if kind == CmdType.MOVE_TO:
            return self._execute_move_to(
                axis=args['axis'],
                position=args['position'],
                feedrate=args['feedrate'],
                settle_tolerance=args.get('settle_tolerance', 0.5),
            )

        if kind == CmdType.HOME:
            return self._execute_home(args.get('axes', 'XC'))

        if kind == CmdType.DISCONNECT:
            self._marlin.disconnect()
            with self._connected_lock:
                self._connected = False
            return Result(ok=True)

        if kind == CmdType.ESTOP:
            self._marlin.emergency_stop()
            return Result(ok=True)

        return Result(ok=False, error=f"Unknown command kind: {kind}")

    def _execute_move_to(
        self,
        axis: str,
        position: float,
        feedrate: int,
        settle_tolerance: float,
    ) -> Result:
        """
        Issue a G1 move command then poll M114 until settled.

        Polls at cfg.position_poll_interval.  Timeout is cfg.move_timeout.
        Returns ok=True when within settle_tolerance of target, ok=False on timeout.
        """
        cmd_str = f'G1 {axis}{position:.3f} F{feedrate}'
        log.debug("MOVE_TO: %s (settle tol=%.2f)", cmd_str, settle_tolerance)
        self._marlin.send_gcode(cmd_str)

        deadline = time.monotonic() + self._cfg.move_timeout
        last_pos = None
        stable_since = None
        stable_needed = 0.2   # seconds position must be stable

        while time.monotonic() < deadline:
            if self._abort_event.is_set() or self._stop_event.is_set():
                log.warning("MOVE_TO aborted by event")
                return Result(ok=False, error="Aborted")

            time.sleep(self._cfg.position_poll_interval)

            pos = self._marlin.get_position()
            if not pos or axis not in pos:
                continue

            current = pos[axis]
            at_target = abs(current - position) <= settle_tolerance

            if at_target:
                if last_pos is not None and abs(current - last_pos) < 0.01:
                    # Position stable and at target
                    if stable_since is None:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= stable_needed:
                        log.debug("MOVE_TO complete: %s=%.3f", axis, current)
                        return Result(ok=True, value=pos)
                else:
                    stable_since = None
            else:
                stable_since = None

            last_pos = current

        return Result(ok=False, error=f"Move timeout: {axis} target={position:.3f}")

    def _execute_home(self, axes: str) -> Result:
        """
        Home the requested axes.  Linear axis (X) must home first.

        Sends G28 X, waits for settle near 0, then G28 C.

        HOMING_REQUIRED modes:
          0 - skip all homing, return instant success (sim / bench)
          1 - home both linear (X) and rotation (C) axes
          2 - home rotation axis only (C), skip linear (testing with partial hardware)
        """
        cfg = self._cfg

        if cfg.homing_required == 0:
            log.info("HOMING_REQUIRED=0: skipping G28, returning instant home success")
            return Result(ok=True)

        linear = cfg.linear_axis
        rotation = cfg.rotation_axis

        if linear in axes and cfg.homing_required != 2:
            log.info("Homing linear axis (%s)", linear)
            self._marlin.send_gcode(f'G28 {linear}', timeout=cfg.home_timeout)
            # Wait for linear to reach home (0 mm)
            deadline = time.monotonic() + cfg.home_timeout
            while time.monotonic() < deadline:
                if self._abort_event.is_set() or self._stop_event.is_set():
                    return Result(ok=False, error="Home aborted")
                time.sleep(cfg.position_poll_interval)
                pos = self._marlin.get_position()
                if pos and linear in pos and abs(pos[linear]) < 1.0:
                    log.info("Linear axis homed: %s=%.3f", linear, pos[linear])
                    break
            else:
                return Result(ok=False, error="Linear home timeout")

        if rotation in axes:
            log.info("Homing rotation axis (%s)", rotation)
            self._marlin.send_gcode(f'G28 {rotation}', timeout=cfg.home_timeout)
            deadline = time.monotonic() + cfg.home_timeout
            while time.monotonic() < deadline:
                if self._abort_event.is_set() or self._stop_event.is_set():
                    return Result(ok=False, error="Home aborted")
                time.sleep(cfg.position_poll_interval)
                pos = self._marlin.get_position()
                if pos and rotation in pos and abs(pos[rotation]) < 1.0:
                    log.info("Rotation axis homed: %s=%.3f", rotation, pos[rotation])
                    break
            else:
                return Result(ok=False, error="Rotation home timeout")

        return Result(ok=True)
