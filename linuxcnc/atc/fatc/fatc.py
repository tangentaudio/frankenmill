"""
fatc.py — FrankenMill ATC (fatc) userspace HAL component.

Loaded by LinuxCNC as:
    loadusr -Wn fatc python3 ../atc/fatc/fatc.py --ini atc_sim.ini --name fatc

This is the Phase 2 skeleton: HAL pins created, serial thread running,
poll loop active.  State machine logic is stubs only (Phase 3).

Pin naming follows requirements.md §5.  All pins are prefixed with the
component name (default 'fatc'), e.g. 'fatc.tool-change'.

HAL pin directions (from this component's perspective):
  HAL_IN  = input  to this component (we read them)
  HAL_OUT = output from this component (we write them)
"""

import argparse
import json
import logging
import os
import signal
import socketserver
import sys
import threading
import time
import queue
from enum import IntEnum

# LinuxCNC HAL bindings — only available inside a running LinuxCNC instance.
# We import conditionally so the module can be syntax-checked standalone.
try:
    import hal
    _HAL_AVAILABLE = True
except ImportError:
    _HAL_AVAILABLE = False

# Locate the fatc package (sibling to this file).
# Python auto-inserts the script's own directory at sys.path[0], which means
# 'fatc.py' would shadow the 'fatc' package on 'from fatc import ...' calls.
# Remove it explicitly, then add the parent so the package is found correctly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _HERE in sys.path:
    sys.path.remove(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from fatc import config as cfg_module
from fatc.persistent_state import PersistentState
from fatc.serial_thread import SerialThread, Command, CmdType, Result

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('fatc')


# ---------------------------------------------------------------------------
# State machine states (Phase 2: stubs only)
# ---------------------------------------------------------------------------

class State(IntEnum):
    INIT              = 0   # Component just started, not yet ready
    CONNECTING        = 1   # Waiting for Marlin serial connection
    IDLE              = 2   # Ready, no tool change in progress
    HOMING            = 3   # Executing G28 home sequence
    # --- Phase 3 tool-change states ---
    # STOW phase (only when a tool is currently in the spindle)
    STOW_ROTATE       = 10  # Rotating carousel to empty pocket
    STOW_EXTEND       = 11  # Extending carousel to spindle
    STOW_WAIT_Z       = 12  # Waiting for remap "Z engaged" signal (stow)
    STOW_UNCLAMP      = 13  # Opening drawbar, waiting for unclamped sensor
    STOW_WAIT_Z_CLEAR = 14  # Waiting for remap "Z clear" signal (stow)
    STOW_RETRACT      = 15  # Retracting carousel after stow
    # LOAD phase (always)
    LOAD_ROTATE       = 20  # Rotating carousel to target tool pocket
    LOAD_EXTEND       = 21  # Extending carousel to spindle
    LOAD_WAIT_Z       = 22  # Waiting for remap "Z engaged" signal (load)
    LOAD_CLAMP        = 23  # Closing drawbar, waiting for clamped sensor
    LOAD_WAIT_Z_CLEAR = 24  # Waiting for remap "Z clear" signal (load)
    LOAD_RETRACT      = 25  # Retracting carousel after load
    ERROR             = 99  # Fault state — operator intervention required


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

class ErrorCode(IntEnum):
    NONE              = 0
    SERIAL_LOST       = 1
    MOVE_TIMEOUT      = 2
    HOME_TIMEOUT      = 3
    DRAWBAR_TIMEOUT   = 4
    SENSOR_MISMATCH   = 5
    NO_EMPTY_POCKET   = 6
    TOOL_NOT_FOUND    = 7
    ABORTED           = 8


# ---------------------------------------------------------------------------
# IPC primitives (Unix socket → state machine)
# ---------------------------------------------------------------------------

class _IpcCommand:
    """A command posted from a Unix socket handler to the state machine."""
    __slots__ = ('cmd', 'tool', 'event', 'result')

    def __init__(self, cmd: str, tool: int = 0):
        self.cmd   = cmd
        self.tool  = tool
        self.event = threading.Event()
        self.result: dict = {}

    def respond(self, ok: bool, error: str = ''):
        self.result = {'ok': ok}
        if error:
            self.result['error'] = error
        self.event.set()


class _SocketHandler(socketserver.StreamRequestHandler):
    """Handles one M-code connection: read JSON command, wait for result, write JSON response."""

    def handle(self):
        try:
            raw = self.rfile.readline()
            if not raw:
                return  # EOF before command sent (connection closed early)
            data = json.loads(raw.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            self._send({'ok': False, 'error': f'bad request: {exc}'})
            return

        cmd = _IpcCommand(
            cmd=str(data.get('cmd', '')),
            tool=int(data.get('tool', 0)),
        )
        # Post to state machine
        self.server._fatc._ipc_queue.put(cmd)

        # Block until state machine responds or timeout
        if not cmd.event.wait(timeout=120.0):
            self._send({'ok': False, 'error': 'timeout waiting for fatc'})
            return

        self._send(cmd.result)

    def _send(self, payload: dict):
        try:
            self.wfile.write(json.dumps(payload).encode() + b'\n')
        except OSError:
            log.debug("IPC: connection closed before response could be sent")


# ---------------------------------------------------------------------------
# FatcComponent
# ---------------------------------------------------------------------------

class FatcComponent:
    """
    Main fatc userspace HAL component.

    Lifecycle:
      __init__  — parse args, load config
      setup     — create HAL component + pins, start serial thread
      run       — main poll loop (blocks until shutdown signal)
      shutdown  — stop serial thread, save state
    """

    # How often to save persistent state (multiples of poll_interval)
    _STATE_SAVE_EVERY = 100

    def __init__(self, ini_path: str, component_name: str = 'fatc'):
        self._name = component_name
        self._ini_path = os.path.abspath(ini_path)
        self._running = False

        log.info("fatc starting — INI: %s", self._ini_path)
        self._cfg = cfg_module.load(self._ini_path)
        log.info(
            "Config: port=%s baud=%d pockets=%d homing_required=%s",
            self._cfg.serial_port, self._cfg.baud_rate,
            self._cfg.pockets, self._cfg.homing_required,
        )

        # Persistent state file lives next to the INI
        state_path = os.path.join(os.path.dirname(self._ini_path), 'fatc_state.json')
        self._state = PersistentState(state_path)
        self._state.load(self._cfg.pockets)

        self._serial = SerialThread(self._cfg)

        # HAL component handle (set in setup)
        self._h = None

        # Internal state tracking
        self._sm_state = State.INIT
        self._error_code = ErrorCode.NONE
        self._poll_count = 0
        self._is_homed = False

        # Pending move result (from serial_thread)
        self._pending_result: 'queue.Queue[Result] | None' = None

        # Tool being loaded in current change cycle
        self._pending_tool: int = 0

        # Track previous values of edge-sensitive input pins
        self._prev_tool_change = False
        self._prev_home_cmd    = False
        self._prev_abort       = False
        self._prev_error_reset = False
        self._first_poll       = True   # used to seed prev values from actual pin state

        # IPC socket server (M101/M102/M103 connect here)
        self._ipc_queue: queue.Queue = queue.Queue()
        self._active_ipc_cmd: '_IpcCommand | None' = None
        self._sock_server: 'socketserver.TCPServer | None' = None

        # Abort is only armed after the machine has been enabled in IDLE once.
        # This prevents startup false-aborts caused by the abort pin being
        # unconnected (False) until postgui wires not.2, then going high.
        self._abort_armed      = False

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self):
        """Create HAL component and all pins."""
        if not _HAL_AVAILABLE:
            log.error("HAL module not available — are we inside LinuxCNC?")
            sys.exit(1)

        log.info("Creating HAL component '%s'", self._name)
        h = hal.component(self._name)

        # --- Input pins (we read) ---
        # Sensors (from Mesa GPIO or sim loopback)
        h.newpin('tool-clamped-sensor',    hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('tool-unclamped-sensor',  hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('spindle-tool-sense',     hal.HAL_BIT,   hal.HAL_IN)

        # Machine interface
        h.newpin('tool-change',            hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('tool-prep-number',       hal.HAL_S32,   hal.HAL_IN)
        h.newpin('tool-number',            hal.HAL_S32,   hal.HAL_IN)   # iocontrol.0.tool-number
        h.newpin('abort',                  hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('error-reset',            hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('retry',                  hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('home-command',           hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('z-position',             hal.HAL_FLOAT, hal.HAL_IN)
        h.newpin('machine-enabled',        hal.HAL_BIT,   hal.HAL_IN)

        # M6 remap handshake via Unix socket IPC (M101/M102/M103).
        # No HAL pins needed — communication is over /tmp/fatc.sock.

        # --- Output pins (we write) ---
        # Actuators
        h.newpin('drawbar-activate',       hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('air-blast-activate',     hal.HAL_BIT,   hal.HAL_OUT)

        # Tool change handshake
        h.newpin('tool-changed',           hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('tool-prepared',          hal.HAL_BIT,   hal.HAL_OUT)

        # Status
        h.newpin('is-homed',               hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('current-pocket',         hal.HAL_S32,   hal.HAL_OUT)
        h.newpin('tool-in-spindle',        hal.HAL_S32,   hal.HAL_OUT)
        h.newpin('state',                  hal.HAL_S32,   hal.HAL_OUT)
        h.newpin('error',                  hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('error-code',             hal.HAL_S32,   hal.HAL_OUT)
        h.newpin('ready',                  hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('busy',                   hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('marlin-connected',       hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('carousel-extended',      hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('carousel-retracted',     hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('pocket-occupied',        hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('spindle-occupied',       hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('inventory-valid',        hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('program-stop',           hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('z-clear-request',        hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('z-engage-request',       hal.HAL_BIT,   hal.HAL_OUT)
        h.newpin('marlin-x-position',      hal.HAL_FLOAT, hal.HAL_OUT)
        h.newpin('marlin-c-position',      hal.HAL_FLOAT, hal.HAL_OUT)

        # Signal LinuxCNC that pins are ready
        h.ready()
        self._h = h

        # Write initial output pin values from persistent state
        self._write_status_pins()

        # Start IPC socket server (M101/M102/M103 connect here)
        self._start_socket_server()

        # Start serial thread
        self._serial.start()
        self._transition(State.CONNECTING)
        log.info("HAL component '%s' ready", self._name)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        """Poll loop.  Blocks until a shutdown signal is received."""
        self._running = True
        signal.signal(signal.SIGINT,  self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        log.info("Entering poll loop (interval=%.3fs)", self._cfg.poll_interval)
        while self._running:
            try:
                self._poll()
            except Exception:
                log.exception("Unhandled exception in poll loop — continuing")
            time.sleep(self._cfg.poll_interval)

    def _on_signal(self, signum, frame):
        log.info("Signal %d received — shutting down", signum)
        self._running = False

    # ------------------------------------------------------------------
    # Poll loop body
    # ------------------------------------------------------------------

    def _poll(self):
        self._poll_count += 1

        # --- Read HAL inputs ---
        h = self._h
        machine_enabled  = bool(h['machine-enabled'])
        abort            = bool(h['abort'])
        error_reset      = bool(h['error-reset'])
        home_cmd         = bool(h['home-command'])
        tool_change      = bool(h['tool-change'])
        tool_prep_num    = int(h['tool-prep-number'])
        tool_number      = int(h['tool-number'])   # LinuxCNC's current tool in spindle
        z_pos            = float(h['z-position'])

        # --- Update Marlin connection status ---
        marlin_ok = self._serial.is_connected
        h['marlin-connected'] = marlin_ok

        # --- Advance state machine ---
        self._sm_update(
            machine_enabled=machine_enabled,
            abort=abort,
            error_reset=error_reset,
            home_cmd=home_cmd,
            tool_change=tool_change,
            tool_prep_num=tool_prep_num,
            tool_number=tool_number,
            z_pos=z_pos,
            marlin_ok=marlin_ok,
        )

        # --- Write all status pins ---
        self._write_status_pins()

        # --- Save state periodically ---
        if self._poll_count % self._STATE_SAVE_EVERY == 0:
            self._state.save_if_dirty()

        # --- Update edge-detection prev values ---
        self._prev_tool_change = tool_change
        self._prev_home_cmd    = home_cmd
        self._prev_abort       = abort
        self._prev_error_reset = error_reset
        self._first_poll       = False

    # ------------------------------------------------------------------
    # State machine (Phase 2 skeleton — stubs for Phases 3+)
    # ------------------------------------------------------------------

    def _sm_update(self, **inputs):
        state = self._sm_state
        marlin_ok = inputs['marlin_ok']
        machine_enabled = inputs['machine_enabled']
        abort = inputs['abort']
        error_reset = inputs['error_reset']

        # --- Global abort (any active state) ---
        # Only armed after the machine has been observed enabled in IDLE at least
        # once.  This prevents false-aborts during startup when the abort pin is
        # unconnected (False) until postgui wires not.2, causing a spurious rising edge.
        if machine_enabled and state == State.IDLE:
            self._abort_armed = True

        if self._abort_armed and abort and not self._prev_abort and state not in (State.INIT, State.CONNECTING):
            log.warning("ABORT received in state %s", state.name)
            self._serial.request_estop()
            self._enter_error(ErrorCode.ABORTED)
            return

        # --- Error reset (from ERROR state only) ---
        if state == State.ERROR and error_reset and not self._prev_error_reset:
            log.info("Error reset requested")
            self._error_code = ErrorCode.NONE
            self._transition(State.CONNECTING if not marlin_ok else State.IDLE)
            return

        # --- CONNECTING: wait for Marlin to come online ---
        if state == State.CONNECTING:
            if marlin_ok:
                log.info("Marlin connected — transitioning to IDLE")
                self._transition(State.IDLE)
            return

        # --- Machine disabled: don't do anything ---
        if not machine_enabled and state not in (State.INIT, State.CONNECTING, State.ERROR):
            # Not an error — just wait
            return

        # --- Marlin lost mid-operation ---
        if not marlin_ok and state not in (State.INIT, State.CONNECTING, State.ERROR):
            log.warning("Marlin connection lost in state %s", state.name)
            self._enter_error(ErrorCode.SERIAL_LOST)
            return

        # --- IDLE: respond to commands ---
        if state == State.IDLE:
            self._sm_idle(**inputs)
            return

        # --- HOMING: wait for result ---
        if state == State.HOMING:
            self._sm_homing()
            return

        # --- STOW phase states ---
        if state == State.STOW_ROTATE:
            self._sm_await_move(state, State.STOW_EXTEND)
            return
        if state == State.STOW_EXTEND:
            self._sm_await_move(state, State.STOW_WAIT_Z)
            return
        if state == State.STOW_WAIT_Z:
            self._sm_stow_wait_z(**inputs)
            return
        if state == State.STOW_UNCLAMP:
            self._sm_stow_unclamp()
            return
        if state == State.STOW_WAIT_Z_CLEAR:
            self._sm_stow_wait_z_clear(**inputs)
            return
        if state == State.STOW_RETRACT:
            self._sm_await_move(state, State.LOAD_ROTATE)
            return

        # --- LOAD phase states ---
        if state == State.LOAD_ROTATE:
            self._sm_await_move(state, State.LOAD_EXTEND)
            return
        if state == State.LOAD_EXTEND:
            self._sm_await_move(state, State.LOAD_WAIT_Z)
            return
        if state == State.LOAD_WAIT_Z:
            self._sm_load_wait_z(**inputs)
            return
        if state == State.LOAD_CLAMP:
            self._sm_load_clamp()
            return
        if state == State.LOAD_WAIT_Z_CLEAR:
            self._sm_load_wait_z_clear(**inputs)
            return
        if state == State.LOAD_RETRACT:
            self._sm_await_move(state, None)   # None → transition to IDLE on complete
            return

    # ------------------------------------------------------------------
    # IPC helpers
    # ------------------------------------------------------------------

    def _ipc_respond(self, ok: bool, error: str = ''):
        """Respond to the currently active IPC command and clear it."""
        if self._active_ipc_cmd is not None:
            self._active_ipc_cmd.respond(ok, error)
            self._active_ipc_cmd = None

    def _ipc_next_cmd(self, expected: str) -> '_IpcCommand | None':
        """Non-blocking: try to pop the next IPC command.  Returns None if queue is empty."""
        try:
            return self._ipc_queue.get_nowait()
        except queue.Empty:
            return None

    def _sm_idle(self, *, home_cmd, tool_change, tool_prep_num, tool_number, **_):
        """IDLE state: handle command edges."""
        # Home command edge
        if home_cmd and not self._prev_home_cmd:
            log.info("Home command received")
            self._pending_result = self._serial.command_home('XC')
            self._transition(State.HOMING)
            return

        # Tool prepare: LinuxCNC sends tool-prep-number first, then tool-change.
        # Acknowledge immediately (no carousel motion at prep time).
        if tool_prep_num > 0 and not self._h['tool-prepared']:
            log.info("Tool prep requested: T%d", tool_prep_num)
            self._h['tool-prepared'] = True

        # --- IPC path: M101 sends BEGIN ---
        cmd = self._ipc_next_cmd('BEGIN')
        if cmd is not None:
            if cmd.cmd == 'BEGIN':
                log.info("IPC BEGIN — T%d requested, spindle has T%d (lc) / T%d (state)",
                         cmd.tool, tool_number, self._state.tool_in_spindle)
                self._pending_tool = cmd.tool
                self._active_ipc_cmd = cmd   # responded when carousel reaches WAIT_Z
                # LinuxCNC's tool-number is authoritative.  Sync persistent state.
                if self._state.tool_in_spindle != tool_number:
                    log.info("Syncing tool_in_spindle: state=%d -> lc=%d",
                             self._state.tool_in_spindle, tool_number)
                    self._state.set_tool_in_spindle(tool_number)
                if tool_number > 0:
                    self._start_stow()
                else:
                    self._start_load()
            else:
                log.warning("Unexpected IPC cmd %r in IDLE", cmd.cmd)
                cmd.respond(False, f"unexpected command {cmd.cmd!r} in IDLE")
            return

        # --- Non-REMAP fallback / iocontrol handshake cleanup ---
        # With REMAP active, iocontrol.0.tool-change pulses high after
        # change_epilog commits.  Ack it silently so iocontrol is satisfied.
        if tool_change and not self._prev_tool_change:
            self._h['tool-changed'] = True

        if not tool_change and self._h['tool-changed']:
            self._h['tool-changed'] = False
            self._h['tool-prepared'] = False

    # ------------------------------------------------------------------
    # Sequence helpers
    # ------------------------------------------------------------------

    def _find_empty_pocket(self) -> int:
        """Return a pocket number with no tool, or 0 if none available."""
        result = self._state.find_empty_pocket()
        return result if result is not None else 0

    def _start_stow(self):
        """Begin stow sequence: rotate to empty pocket."""
        empty = self._find_empty_pocket()
        if empty == 0:
            log.error("No empty pocket available for stow")
            self._enter_error(ErrorCode.NO_EMPTY_POCKET)
            return
        log.info("Stow: rotating to empty pocket %d", empty)
        self._state.set_current_pocket(empty)
        angle = cfg_module.pocket_angle(self._cfg, empty)
        self._pending_result = self._serial.command_move_to(
            axis=self._cfg.rotation_axis,
            position=angle,
            feedrate=self._cfg.rotation_feedrate,
        )
        self._transition(State.STOW_ROTATE)

    def _start_load(self):
        """Begin load sequence: rotate to target tool pocket."""
        target_pocket = self._state.find_pocket_for_tool(self._pending_tool)
        if target_pocket is None:
            # Tool not in inventory — use tool number as pocket number (fallback)
            if self._pending_tool < 1 or self._pending_tool > self._cfg.pockets:
                log.error("T%d not in inventory and tool number exceeds pocket count (%d) — cannot load",
                          self._pending_tool, self._cfg.pockets)
                self._enter_error(ErrorCode.TOOL_NOT_FOUND)
                return
            target_pocket = self._pending_tool
            log.warning("T%d not in inventory map — using pocket %d by number",
                        self._pending_tool, target_pocket)
        log.info("Load: rotating to pocket %d for T%d", target_pocket, self._pending_tool)
        self._state.set_current_pocket(target_pocket)
        angle = cfg_module.pocket_angle(self._cfg, target_pocket)
        self._pending_result = self._serial.command_move_to(
            axis=self._cfg.rotation_axis,
            position=angle,
            feedrate=self._cfg.rotation_feedrate,
        )
        self._transition(State.LOAD_ROTATE)

    # ------------------------------------------------------------------
    # STOW state handlers
    # ------------------------------------------------------------------

    def _sm_stow_wait_z(self, **_):
        """STOW_WAIT_Z: respond READY_FOR_Z to BEGIN, then wait for Z_ENGAGED."""
        # Respond to the pending BEGIN command once carousel is extended
        if self._active_ipc_cmd is not None:
            log.info("Stow: carousel extended — responding READY_FOR_Z")
            self._ipc_respond(True)

        # Wait for Z_ENGAGED (sent by M102)
        cmd = self._ipc_next_cmd('Z_ENGAGED')
        if cmd is None:
            return
        if cmd.cmd == 'Z_ENGAGED':
            log.info("Stow: Z engaged — unclamping drawbar")
            self._active_ipc_cmd = cmd   # responded when drawbar op complete
            self._h['drawbar-activate'] = True
            self._transition(State.STOW_UNCLAMP)
        else:
            log.warning("Stow WAIT_Z: unexpected cmd %r", cmd.cmd)
            cmd.respond(False, f"unexpected command {cmd.cmd!r} in STOW_WAIT_Z")

    def _sm_stow_unclamp(self):
        """STOW_UNCLAMP: wait for unclamped sensor confirmation."""
        if self._h['tool-unclamped-sensor']:
            log.info("Stow: tool unclamped — waiting for Z clear")
            self._transition(State.STOW_WAIT_Z_CLEAR)

    def _sm_stow_wait_z_clear(self, **_):
        """STOW_WAIT_Z_CLEAR: respond DRAWBAR_DONE to Z_ENGAGED, then wait for Z_CLEAR."""
        # Respond to the pending Z_ENGAGED command once drawbar is open
        if self._active_ipc_cmd is not None:
            log.info("Stow: drawbar open — responding DRAWBAR_DONE")
            self._ipc_respond(True)

        # Wait for Z_CLEAR (sent by M103)
        cmd = self._ipc_next_cmd('Z_CLEAR')
        if cmd is None:
            return
        if cmd.cmd == 'Z_CLEAR':
            log.info("Stow: Z clear — retracting carousel")
            self._active_ipc_cmd = cmd   # responded when load carousel reaches WAIT_Z
            self._h['drawbar-activate'] = False
            self._state.set_tool_in_pocket(self._state.current_pocket,
                                           self._state.tool_in_spindle)
            self._state.set_tool_in_spindle(0)
            self._pending_result = self._serial.command_move_to(
                axis=self._cfg.linear_axis,
                position=self._cfg.linear_retract_position,
                feedrate=self._cfg.linear_feedrate,
            )
            self._transition(State.STOW_RETRACT)
        else:
            log.warning("Stow WAIT_Z_CLEAR: unexpected cmd %r", cmd.cmd)
            cmd.respond(False, f"unexpected command {cmd.cmd!r} in STOW_WAIT_Z_CLEAR")

    # ------------------------------------------------------------------
    # LOAD state handlers
    # ------------------------------------------------------------------

    def _sm_load_wait_z(self, **_):
        """LOAD_WAIT_Z: respond READY_FOR_Z (to BEGIN or stow Z_CLEAR), then wait for Z_ENGAGED."""
        # Respond to the pending command once carousel is extended
        if self._active_ipc_cmd is not None:
            log.info("Load: carousel extended — responding READY_FOR_Z")
            self._ipc_respond(True)

        # Wait for Z_ENGAGED (sent by M102)
        cmd = self._ipc_next_cmd('Z_ENGAGED')
        if cmd is None:
            return
        if cmd.cmd == 'Z_ENGAGED':
            log.info("Load: Z engaged — clamping drawbar")
            self._active_ipc_cmd = cmd   # responded when drawbar clamped
            self._h['drawbar-activate'] = False  # deactivate = clamp
            self._transition(State.LOAD_CLAMP)
        else:
            log.warning("Load WAIT_Z: unexpected cmd %r", cmd.cmd)
            cmd.respond(False, f"unexpected command {cmd.cmd!r} in LOAD_WAIT_Z")

    def _sm_load_clamp(self):
        """LOAD_CLAMP: wait for clamped sensor confirmation."""
        if self._h['tool-clamped-sensor']:
            log.info("Load: tool clamped — waiting for Z clear")
            self._transition(State.LOAD_WAIT_Z_CLEAR)

    def _sm_load_wait_z_clear(self, **_):
        """LOAD_WAIT_Z_CLEAR: respond DRAWBAR_DONE to Z_ENGAGED, then wait for Z_CLEAR."""
        # Respond to the pending Z_ENGAGED command once drawbar is clamped
        if self._active_ipc_cmd is not None:
            log.info("Load: tool clamped — responding DRAWBAR_DONE")
            self._ipc_respond(True)

        # Wait for Z_CLEAR (sent by M103)
        cmd = self._ipc_next_cmd('Z_CLEAR')
        if cmd is None:
            return
        if cmd.cmd == 'Z_CLEAR':
            log.info("Load: Z clear — retracting carousel")
            self._active_ipc_cmd = cmd   # responded when retract complete (COMPLETE)
            self._state.set_tool_in_pocket(self._state.current_pocket, 0)
            self._state.set_tool_in_spindle(self._pending_tool)
            self._pending_result = self._serial.command_move_to(
                axis=self._cfg.linear_axis,
                position=self._cfg.linear_retract_position,
                feedrate=self._cfg.linear_feedrate,
            )
            self._transition(State.LOAD_RETRACT)
        else:
            log.warning("Load WAIT_Z_CLEAR: unexpected cmd %r", cmd.cmd)
            cmd.respond(False, f"unexpected command {cmd.cmd!r} in LOAD_WAIT_Z_CLEAR")

    # ------------------------------------------------------------------
    # Homing
    # ------------------------------------------------------------------

    def _sm_homing(self):
        """HOMING state: check for home result."""
        if self._pending_result is None:
            return
        try:
            result = self._pending_result.get_nowait()
        except queue.Empty:
            return

        self._pending_result = None
        if result.ok:
            log.info("Homing complete")
            self._is_homed = True
            self._transition(State.IDLE)
        else:
            log.error("Homing failed: %s", result.error)
            self._enter_error(ErrorCode.HOME_TIMEOUT)

    # ------------------------------------------------------------------
    # Generic move-wait handler
    # ------------------------------------------------------------------

    def _sm_await_move(self, current_state: State, next_state):
        """Wait for a pending Marlin move to complete, then advance."""
        if self._pending_result is None:
            # No move queued yet — nothing to do this poll
            return
        try:
            result = self._pending_result.get_nowait()
        except queue.Empty:
            return

        self._pending_result = None
        if not result.ok:
            log.error("Move failed in state %s: %s", current_state.name, result.error)
            self._enter_error(ErrorCode.MOVE_TIMEOUT)
            return

        log.info("Move complete in state %s", current_state.name)

        if next_state is None:
            # LOAD_RETRACT complete — sequence done
            self._sequence_complete()
        elif next_state == State.STOW_EXTEND:
            # After STOW_ROTATE: extend carousel
            log.info("Stow: pocket aligned — extending carousel")
            self._pending_result = self._serial.command_move_to(
                axis=self._cfg.linear_axis,
                position=self._cfg.linear_extend_position,
                feedrate=self._cfg.linear_feedrate,
            )
            self._transition(next_state)
        elif next_state == State.STOW_WAIT_Z:
            # After STOW_EXTEND: carousel at spindle, wait for Z
            self._transition(next_state)
        elif next_state == State.LOAD_ROTATE:
            # After STOW_RETRACT: start load rotation
            self._transition(next_state)
            self._start_load()
        elif next_state == State.LOAD_EXTEND:
            # After LOAD_ROTATE: extend carousel
            log.info("Load: pocket aligned — extending carousel")
            self._pending_result = self._serial.command_move_to(
                axis=self._cfg.linear_axis,
                position=self._cfg.linear_extend_position,
                feedrate=self._cfg.linear_feedrate,
            )
            self._transition(next_state)
        elif next_state == State.LOAD_WAIT_Z:
            # After LOAD_EXTEND: carousel at spindle, wait for Z
            self._transition(next_state)
        else:
            self._transition(next_state)

    def _sequence_complete(self):
        """Called when LOAD_RETRACT finishes — the full sequence is done."""
        log.info("Tool change complete: T%d loaded", self._pending_tool)
        # Respond to the final Z_CLEAR IPC command (M103 load)
        self._ipc_respond(True)
        # Update HAL pin
        self._h['tool-in-spindle'] = self._pending_tool
        self._pending_tool = 0
        self._transition(State.IDLE)

    # ------------------------------------------------------------------
    # Socket server (IPC for M101/M102/M103)
    # ------------------------------------------------------------------

    def _start_socket_server(self):
        sock_path = getattr(self._cfg, 'socket_path', '/tmp/fatc.sock')
        # Remove stale socket file if present
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        self._sock_server = socketserver.ThreadingUnixStreamServer(
            sock_path, _SocketHandler
        )
        self._sock_server._fatc = self
        t = threading.Thread(
            target=self._sock_server.serve_forever,
            name='fatc-ipc',
            daemon=True,
        )
        t.start()
        log.info("IPC socket server listening on %s", sock_path)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _transition(self, new_state: State):
        if new_state != self._sm_state:
            log.info("State: %s -> %s", self._sm_state.name, new_state.name)
        self._sm_state = new_state

    def _enter_error(self, code: ErrorCode):
        self._error_code = code
        self._h['program-stop'] = True   # stop any running G-code
        # Respond to any blocked M-code with an error so it exits non-zero
        # and LinuxCNC raises a fault, rather than hanging until timeout.
        error_msg = f'fatc error: {code.name}'
        self._ipc_respond(False, error_msg)
        # Drain any queued commands that haven't been processed yet
        while True:
            cmd = self._ipc_next_cmd('')
            if cmd is None:
                break
            cmd.respond(False, error_msg)
        self._transition(State.ERROR)
        log.error("Entered ERROR state: %s (%d)", code.name, int(code))

    # ------------------------------------------------------------------
    # Write all HAL output status pins from current state
    # ------------------------------------------------------------------

    def _write_status_pins(self):
        h = self._h
        state = self._sm_state
        ps = self._state

        h['state']           = int(state)
        h['error']           = (state == State.ERROR)
        h['error-code']      = int(self._error_code)
        h['ready']           = (state == State.IDLE)
        h['busy']            = state not in (State.IDLE, State.INIT,
                                             State.CONNECTING, State.ERROR)

        h['is-homed']        = self._is_homed
        h['current-pocket']  = int(ps.current_pocket)
        h['tool-in-spindle'] = int(ps.tool_in_spindle)
        h['inventory-valid'] = bool(ps.inventory_valid)

        # Carousel position flags
        extended_states = {State.STOW_WAIT_Z, State.STOW_UNCLAMP, State.STOW_WAIT_Z_CLEAR,
                           State.LOAD_WAIT_Z, State.LOAD_CLAMP, State.LOAD_WAIT_Z_CLEAR}
        h['carousel-extended']  = (state in extended_states)
        h['carousel-retracted'] = (state in (State.IDLE, State.HOMING,
                                              State.STOW_ROTATE, State.LOAD_ROTATE))

        # Spindle occupied = spindle pre-sense OR component tracking says tool loaded
        h['spindle-occupied'] = (bool(h['spindle-tool-sense']) or
                                  ps.tool_in_spindle > 0)

        # Clear program-stop unless we're asserting it
        if state != State.ERROR:
            h['program-stop'] = False

        # Live Marlin axis positions from heartbeat M114
        pos = self._serial.last_position
        if pos:
            h['marlin-x-position'] = float(pos.get('X', 0.0))
            h['marlin-c-position'] = float(pos.get('C', 0.0))

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self):
        log.info("Shutting down fatc")
        if self._sock_server is not None:
            self._sock_server.shutdown()
        self._serial.request_estop()
        self._serial.stop()
        self._state.save()
        log.info("fatc shutdown complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='fatc — FrankenMill ATC HAL component'
    )
    parser.add_argument(
        '--ini', required=True,
        help='Path to the LinuxCNC INI file (e.g. atc_sim.ini)',
    )
    parser.add_argument(
        '--name', default='fatc',
        help='HAL component name (default: fatc)',
    )
    args = parser.parse_args()

    component = FatcComponent(ini_path=args.ini, component_name=args.name)

    try:
        component.setup()
        component.run()
    except KeyboardInterrupt:
        pass
    finally:
        component.shutdown()


if __name__ == '__main__':
    main()
