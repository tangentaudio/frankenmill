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
import logging
import os
import signal
import sys
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
    INIT         = 0   # Component just started, not yet ready
    CONNECTING   = 1   # Waiting for Marlin serial connection
    IDLE         = 2   # Ready, no tool change in progress
    HOMING       = 3   # Executing G28 home sequence
    ROTATING     = 4   # Carousel rotating to target pocket
    EXTENDING    = 5   # Carousel extending toward spindle
    RETRACTING   = 6   # Carousel retracting to home
    DRAWBAR_OP   = 7   # Drawbar clamp/unclamp in progress
    Z_MOVING     = 8   # Waiting for Z axis to reach target height
    ERROR        = 99  # Fault state — operator intervention required


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

        # Track previous values of edge-sensitive input pins
        self._prev_tool_change = False
        self._prev_home_cmd    = False
        self._prev_abort       = False
        self._prev_error_reset = False
        self._first_poll       = True   # used to seed prev values from actual pin state

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
        h.newpin('abort',                  hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('error-reset',            hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('retry',                  hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('home-command',           hal.HAL_BIT,   hal.HAL_IN)
        h.newpin('z-position',             hal.HAL_FLOAT, hal.HAL_IN)
        h.newpin('machine-enabled',        hal.HAL_BIT,   hal.HAL_IN)

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

        # --- ROTATING / EXTENDING / RETRACTING: wait for move result ---
        if state in (State.ROTATING, State.EXTENDING, State.RETRACTING):
            self._sm_await_move(state)
            return

    def _sm_idle(self, *, home_cmd, tool_change, tool_prep_num, **_):
        """IDLE state: handle command edges."""
        # Home command edge
        if home_cmd and not self._prev_home_cmd:
            log.info("Home command received")
            self._pending_result = self._serial.command_home('XC')
            self._transition(State.HOMING)
            return

        # Tool prepare: LinuxCNC sends tool-prep-number first, then tool-change
        # For Phase 2 we immediately acknowledge tool-prep (no carousel motion yet)
        if tool_prep_num > 0 and not self._h['tool-prepared']:
            log.info("Tool prep requested: T%d", tool_prep_num)
            self._h['tool-prepared'] = True

        # Tool change edge
        if tool_change and not self._prev_tool_change:
            log.info("Tool change requested: T%d", tool_prep_num)
            # Phase 2 stub: acknowledge immediately without doing anything
            # Phase 3 will implement the full sequence here
            self._state.set_tool_in_spindle(tool_prep_num)
            self._h['tool-changed'] = True
            log.info("Tool change acknowledged (stub — no carousel motion)")

        # Clear tool-changed when tool-change goes low
        if not tool_change and self._h['tool-changed']:
            self._h['tool-changed'] = False
            self._h['tool-prepared'] = False

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

    def _sm_await_move(self, current_state: State):
        """ROTATING/EXTENDING/RETRACTING: check pending move result."""
        if self._pending_result is None:
            return
        try:
            result = self._pending_result.get_nowait()
        except queue.Empty:
            return

        self._pending_result = None
        if result.ok:
            log.info("Move complete in state %s", current_state.name)
            # Phase 3 will handle the next step in the sequence here
            self._transition(State.IDLE)
        else:
            log.error("Move failed in state %s: %s", current_state.name, result.error)
            self._enter_error(ErrorCode.MOVE_TIMEOUT)

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
