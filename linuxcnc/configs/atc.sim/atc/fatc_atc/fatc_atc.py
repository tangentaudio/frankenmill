"""
fatc_atc.py — FrankenMill ATC operator tab for Probe Basic.

Loaded by probe_basic.py's load_atc() mechanism.  The class must be named
'Atc' and reside in a file matching the directory name (fatc_atc.py inside
atc/fatc_atc/).

Layout (horizontal):
  Left  — DynATC carousel QML widget
  Right — Status group + operator buttons

All carousel motion goes through LinuxCNC MDI (T#n M6 / T0 M6) so that the
M6 remap and toolchange.ngc sequencer handle Z coordination and fatc IPC.

Non-motion fatc operations (GET_INVENTORY, SET_SPINDLE, RESET) go through the
Unix socket directly in a short-timeout blocking call.  The homing HOME command
runs in a QThread so the Qt event loop stays responsive during homing.
"""

import json
import linuxcnc
import os
import socket
import threading

import hal

from qtpy.QtCore import Qt, QTimer, Signal, QObject, QThread
from qtpy.QtGui import QFont, QColor
from qtpy.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QFrame, QSizePolicy,
    QGroupBox, QSpacerItem,
)

from qtpyvcp.actions.machine_actions import issue_mdi
from qtpyvcp.plugins import getPlugin
from qtpyvcp.utilities import logger

LOG = logger.getLogger(__name__)
STATUS = getPlugin('status')

INIFILE = linuxcnc.ini(os.getenv('INI_FILE_NAME'))
SOCK_PATH = os.environ.get('FATC_SOCKET', '/tmp/fatc.sock')

# ---------------------------------------------------------------------------
# HAL pin → friendly state name
# ---------------------------------------------------------------------------
_STATE_NAMES = {
    0:  'INIT',
    1:  'CONNECTING',
    2:  'IDLE',
    3:  'HOMING',
    10: 'STOW: ROTATE',
    11: 'STOW: EXTEND',
    12: 'STOW: WAIT Z',
    13: 'STOW: UNCLAMP',
    14: 'STOW: WAIT Z CLEAR',
    15: 'STOW: RETRACT',
    20: 'LOAD: ROTATE',
    21: 'LOAD: EXTEND',
    22: 'LOAD: WAIT Z',
    23: 'LOAD: CLAMP',
    24: 'LOAD: WAIT Z CLEAR',
    25: 'LOAD: RETRACT',
    99: 'ERROR',
}

_ERROR_MESSAGES = {
    0: '',
    1: 'Serial connection lost — check USB cable to carousel controller.',
    2: 'Carousel move timed out — check for mechanical obstruction.',
    3: 'Homing timed out — check home sensor wiring and Marlin config.',
    4: 'Drawbar actuator timed out — check air pressure and solenoid.',
    5: 'Drawbar sensor mismatch — check clamped/unclamped sensor wiring.',
    6: 'No empty pocket available to stow current tool.',
    7: 'Requested tool not found in carousel — check tool table.',
    8: 'Operation aborted by operator.',
}

# ---------------------------------------------------------------------------
# Style constants (match Probe Basic dark theme)
# ---------------------------------------------------------------------------
_DARK_BG   = '#2b2b2b'
_PANEL_BG  = '#3c3c3c'
_BTN_STYLE = """
QPushButton {
    color: white;
    background: qlineargradient(spread:pad, x1:0, y1:1, x2:0, y2:0,
        stop:0 rgba(213,218,216,255), stop:0.17 rgba(82,82,83,255),
        stop:0.33 rgba(72,70,73,255), stop:0.49 rgba(78,77,79,255),
        stop:0.70 rgba(72,70,73,255), stop:0.86 rgba(82,82,83,255),
        stop:1   rgba(213,218,216,255));
    border: 2px solid black;
    border-radius: 5px;
    font-family: "Bebas Kai";
    font-size: 16pt;
    min-height: 42px;
}
QPushButton:disabled { border-color: #555; color: #777; }
QPushButton:hover    { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #A19E9E,stop:1 #5C5959); }
QPushButton:pressed  { background: qlineargradient(spread:pad, x1:0,y1:0,x2:0,y2:1,
    stop:0 rgba(85,85,238,255), stop:0.54 rgba(90,91,239,255), stop:1 rgba(126,135,243,255)); }
"""
_BTN_DANGER_STYLE = _BTN_STYLE + """
QPushButton { border-color: #c0392b; color: #ff6b6b; }
QPushButton:enabled { border-color: #e74c3c; }
"""
_LABEL_STYLE = "color: #cccccc; font-size: 10pt;"
_VALUE_STYLE = "color: white; font-size: 12pt; font-weight: bold;"
_ERROR_STYLE = "color: #ff6b6b; font-size: 11pt; font-weight: bold; padding: 4px;"
_STATE_STYLE_NORMAL = "color: #2ecc71; font-size: 13pt; font-weight: bold;"
_STATE_STYLE_ERROR  = "color: #e74c3c; font-size: 13pt; font-weight: bold;"
_STATE_STYLE_BUSY   = "color: #f39c12; font-size: 13pt; font-weight: bold;"


# ---------------------------------------------------------------------------
# IPC helper
# ---------------------------------------------------------------------------
def _fatc_ipc(cmd, timeout=5.0, **kwargs):
    """Send a management command to fatc and return the response dict.
    Raises IOError on socket error, returns {'ok': False, 'error': '...'} on
    fatc-level failure.  Not safe to call from Qt main thread for long-running
    commands (HOME) — use _HomeThread for those.
    """
    msg = {'cmd': cmd, **kwargs}
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(SOCK_PATH)
        s.sendall(json.dumps(msg).encode() + b'\n')
        buf = b''
        while b'\n' not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise IOError('connection closed before response')
            buf += chunk
    return json.loads(buf.split(b'\n')[0])


# ---------------------------------------------------------------------------
# Worker thread for blocking HOME IPC call
# ---------------------------------------------------------------------------
class _HomeWorker(QObject):
    finished = Signal(bool, str)   # ok, error_message

    def run(self):
        try:
            resp = _fatc_ipc('HOME', timeout=120.0)
            self.finished.emit(resp.get('ok', False),
                               resp.get('error', ''))
        except Exception as exc:
            self.finished.emit(False, str(exc))


# ---------------------------------------------------------------------------
# Main ATC widget
# ---------------------------------------------------------------------------
class Atc(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('fatc_atc_widget')
        self.setStyleSheet(f'background-color: {_DARK_BG};')

        self._homing_thread = None
        self._homing_worker = None

        self._num_pockets = int(INIFILE.find('ATC', 'POCKETS') or 12)
        self._pocket_1_pos = float(INIFILE.find('ATC', 'POCKET_1_POSITION') or 0.0)
        self._rotation_full_turn = float(INIFILE.find('ATC', 'ROTATION_FULL_TURN') or 360.0)
        # Last known fatc.current-pocket value; used to detect changes in _poll.
        self._last_pocket = 0
        # Last known fatc.tool-in-spindle value; triggers DynFATC re-sync on change.
        self._last_tool_in_spindle = -1  # -1 = not yet read
        # Whether we've done the one-time startup sync between LC and fatc.
        self._startup_sync_done = False

        self._build_ui()
        # Initial pocket sync from fatc
        if self.dynfatc:
            self.dynfatc.sync_from_fatc()
            # Set Z reference heights
            z_safe = float(INIFILE.find('ATC', 'Z_TOOL_CLEARANCE_HEIGHT') or 0.0)
            z_tc = float(INIFILE.find('ATC', 'TC_HEIGHT') or -1.5)
            self.dynfatc.set_z_heights(z_safe, z_tc)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(12)

        # ---- Left: DynFATC visualization widget ----
        try:
            import importlib.util, sys
            _dynfatc_path = os.path.join(os.path.dirname(__file__), 'dynfatc.py')
            _spec = importlib.util.spec_from_file_location('dynfatc', _dynfatc_path)
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            DynFATC = _mod.DynFATC
            orientation = INIFILE.find('ATC', 'ORIENTATION') or 'LEFT'
            self.dynfatc = DynFATC(self,
                                   pocket_count=self._num_pockets,
                                   orientation=orientation)
            self.dynfatc.setMinimumSize(450, 350)
            self.dynfatc.setObjectName('dynfatc')
            root.addWidget(self.dynfatc, stretch=2)
        except Exception as exc:
            LOG.error('Could not load DynFATC widget: %s', exc)
            self.dynfatc = None
            placeholder = QLabel('DynFATC\nunavailable')
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet('color: #888; font-size: 14pt;')
            placeholder.setFixedSize(300, 300)
            root.addWidget(placeholder, stretch=0)

        # ---- Right: controls panel ----
        right = QVBoxLayout()
        right.setSpacing(10)
        root.addLayout(right, stretch=1)

        # Status group
        right.addWidget(self._build_status_group())

        # Error display (hidden until needed)
        self._error_frame = QFrame()
        self._error_frame.setStyleSheet(
            f'background-color: #4a1010; border: 1px solid #c0392b; border-radius: 4px;')
        ef_lay = QVBoxLayout(self._error_frame)
        ef_lay.setContentsMargins(8, 6, 8, 6)
        self._error_label = QLabel('')
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(_ERROR_STYLE)
        ef_lay.addWidget(self._error_label)
        self._error_frame.setVisible(False)
        right.addWidget(self._error_frame)

        # Operator buttons
        right.addWidget(self._build_buttons_group())

        right.addSpacerItem(QSpacerItem(
            0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def _build_status_group(self):
        grp = QGroupBox('Carousel Status')
        grp.setStyleSheet(
            f'QGroupBox {{ color: #aaa; border: 1px solid #555; border-radius: 4px; '
            f'margin-top: 6px; padding-top: 4px; background-color: {_PANEL_BG}; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}')
        grid = QGridLayout(grp)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(10, 12, 10, 10)

        def row(label_text, row_idx):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(_LABEL_STYLE)
            val = QLabel('—')
            val.setStyleSheet(_VALUE_STYLE)
            grid.addWidget(lbl, row_idx, 0)
            grid.addWidget(val, row_idx, 1)
            return val

        self._lbl_state   = row('State',          0)
        self._lbl_state.setStyleSheet(_STATE_STYLE_NORMAL)
        self._lbl_homed   = row('Homed',          1)
        self._lbl_conn    = row('Controller',     2)
        self._lbl_pocket  = row('Current Pocket', 3)
        self._lbl_tool    = row('Tool in Spindle',4)

        return grp

    def _build_buttons_group(self):
        grp = QGroupBox('ATC Operations')
        grp.setStyleSheet(
            f'QGroupBox {{ color: #aaa; border: 1px solid #555; border-radius: 4px; '
            f'margin-top: 6px; padding-top: 4px; background-color: {_PANEL_BG}; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}')
        lay = QVBoxLayout(grp)
        lay.setSpacing(6)
        lay.setContentsMargins(10, 14, 10, 10)

        # --- REF CAROUSEL ---
        self._btn_home = QPushButton('REF CAROUSEL')
        self._btn_home.setStyleSheet(_BTN_STYLE)
        self._btn_home.setToolTip('Home the carousel rotation axis (G28 C)')
        self._btn_home.clicked.connect(self._on_home)
        lay.addWidget(self._btn_home)

        lay.addWidget(_hline())

        # --- LOAD SPINDLE ---
        load_row = QHBoxLayout()
        load_row.setSpacing(6)
        self._tool_entry = QLineEdit()
        self._tool_entry.setPlaceholderText('Tool #')
        self._tool_entry.setFixedWidth(90)
        self._tool_entry.setAlignment(Qt.AlignCenter)
        self._tool_entry.setStyleSheet(
            'background: #555; color: white; font-size: 14pt; border-radius: 4px; padding: 4px;')
        self._tool_entry.textChanged.connect(self._update_button_states)
        self._tool_entry.returnPressed.connect(self._on_load_spindle)
        load_row.addWidget(self._tool_entry)

        self._btn_load = QPushButton('LOAD SPINDLE')
        self._btn_load.setStyleSheet(_BTN_STYLE)
        self._btn_load.setToolTip('Load the entered tool into the spindle (T# M6)')
        self._btn_load.clicked.connect(self._on_load_spindle)
        load_row.addWidget(self._btn_load, stretch=1)
        lay.addLayout(load_row)

        # --- STORE TOOL ---
        self._btn_store = QPushButton('STORE TOOL IN CAROUSEL')
        self._btn_store.setStyleSheet(_BTN_STYLE)
        self._btn_store.setToolTip('Return current spindle tool to its carousel pocket (T0 M6)')
        self._btn_store.clicked.connect(self._on_store_tool)
        lay.addWidget(self._btn_store)

        # --- UNLOAD SPINDLE ---
        self._btn_unload = QPushButton('UNLOAD SPINDLE')
        self._btn_unload.setStyleSheet(_BTN_STYLE)
        self._btn_unload.setToolTip(
            'Mark spindle as empty without moving carousel '
            '(use after manually removing a tool)')
        self._btn_unload.clicked.connect(self._on_unload_spindle)
        lay.addWidget(self._btn_unload)

        lay.addWidget(_hline())

        # --- RESET ERROR ---
        self._btn_reset = QPushButton('RESET ERROR')
        self._btn_reset.setStyleSheet(_BTN_DANGER_STYLE)
        self._btn_reset.setToolTip('Clear error state and return carousel to IDLE')
        self._btn_reset.clicked.connect(self._on_reset_error)
        lay.addWidget(self._btn_reset)

        return grp

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------
    def _poll(self):
        try:
            state_int  = int(hal.get_value('fatc.state'))
            is_homed   = bool(hal.get_value('fatc.is-homed'))
            connected  = bool(hal.get_value('fatc.marlin-connected'))
            pocket     = int(hal.get_value('fatc.current-pocket'))
            tool       = int(hal.get_value('fatc.tool-in-spindle'))
            in_error   = bool(hal.get_value('fatc.error'))
            error_code = int(hal.get_value('fatc.error-code'))
        except Exception as exc:
            LOG.debug('HAL poll error: %s', exc)
            return

        # Also read drawbar and Z for DynFATC visualization
        try:
            clamped   = bool(hal.get_value('fatc.tool-clamped-sensor'))
            unclamped = bool(hal.get_value('fatc.tool-unclamped-sensor'))
            extended  = bool(hal.get_value('fatc.carousel-extended'))
            z_pos     = float(hal.get_value('fatc.z-position'))
        except Exception:
            clamped = True
            unclamped = False
            extended = False
            z_pos = 0.0

        # State label
        state_name = _STATE_NAMES.get(state_int, f'STATE {state_int}')
        self._lbl_state.setText(state_name)
        if state_int == 99:
            self._lbl_state.setStyleSheet(_STATE_STYLE_ERROR)
        elif state_int in (2,):   # IDLE
            self._lbl_state.setStyleSheet(_STATE_STYLE_NORMAL)
        else:
            self._lbl_state.setStyleSheet(_STATE_STYLE_BUSY)

        self._lbl_homed.setText('YES' if is_homed else 'NO')
        self._lbl_homed.setStyleSheet(
            _VALUE_STYLE + ('color: #2ecc71;' if is_homed else 'color: #e74c3c;'))

        self._lbl_conn.setText('Connected' if connected else 'Disconnected')
        self._lbl_conn.setStyleSheet(
            _VALUE_STYLE + ('color: #2ecc71;' if connected else 'color: #e74c3c;'))

        self._lbl_pocket.setText(str(pocket) if pocket > 0 else '—')
        self._lbl_tool.setText(f'T{tool}' if tool > 0 else 'Empty')

        # --- Drive DynFATC visualization ---
        if self.dynfatc:
            # Carousel rotation (absolute angle from pocket number)
            if pocket > 0 and pocket != self._last_pocket:
                angle = self._pocket_angle(pocket)
                self.dynfatc.update_carousel_angle(angle)
            self._last_pocket = pocket

            # Arm position, drawbar, Z, spindle tool, state, homed
            self.dynfatc.update_arm_position(extended)
            self.dynfatc.update_drawbar(clamped, unclamped)
            self.dynfatc.update_z_position(z_pos)
            self.dynfatc.update_spindle_tool(tool)
            self.dynfatc.update_state(state_int, state_name)
            self.dynfatc.update_homed(is_homed)
        else:
            self._last_pocket = pocket

        # Re-sync DynFATC from fatc's pocket_map when tool-in-spindle changes.
        if tool != self._last_tool_in_spindle:
            if self._last_tool_in_spindle >= 0:  # skip the initial read
                LOG.info('Tool in spindle changed: T%d -> T%d — re-syncing DynFATC',
                         self._last_tool_in_spindle, tool)
                if self.dynfatc:
                    self.dynfatc.sync_from_fatc()
            self._last_tool_in_spindle = tool

        # Startup reconciliation
        if not self._startup_sync_done and state_int == 2:  # IDLE
            self._startup_sync_done = True
            self._reconcile_startup_tool(tool)

        # Error display
        if in_error:
            msg = _ERROR_MESSAGES.get(error_code, f'Unknown error (code {error_code})')
            self._error_label.setText(msg)
            self._error_frame.setVisible(True)
        else:
            self._error_frame.setVisible(False)

        self._update_button_states(state_int=state_int, is_homed=is_homed, in_error=in_error)

    def _pocket_angle(self, pocket):
        """Compute absolute rotation angle for a pocket number."""
        step = self._rotation_full_turn / self._num_pockets
        return self._pocket_1_pos + (pocket - 1) * step

    def _update_button_states(self, *args, state_int=None, is_homed=None, in_error=None):
        """Enable/disable buttons based on current machine + fatc state."""
        try:
            lnc_stat = linuxcnc.stat()
            lnc_stat.poll()
            import linuxcnc as _lc
            machine_on = lnc_stat.task_state == _lc.STATE_ON
            interp_idle = lnc_stat.interp_state == _lc.INTERP_IDLE
        except Exception:
            machine_on = False
            interp_idle = False

        if state_int is None:
            try:
                state_int  = int(hal.get_value('fatc.state'))
                is_homed   = bool(hal.get_value('fatc.is-homed'))
                in_error   = bool(hal.get_value('fatc.error'))
            except Exception:
                return

        ready_for_ops = machine_on and interp_idle
        fatc_idle     = state_int == 2   # IDLE
        homing_active = self._homing_thread is not None and self._homing_thread.isRunning()

        tool_num_valid = self._tool_entry.text().strip().isdigit() and \
                         int(self._tool_entry.text().strip()) > 0

        self._btn_home.setEnabled(
            ready_for_ops and fatc_idle and not homing_active)

        self._btn_load.setEnabled(
            ready_for_ops and fatc_idle and is_homed and tool_num_valid)

        self._btn_store.setEnabled(
            ready_for_ops and fatc_idle and is_homed)

        self._btn_unload.setEnabled(
            ready_for_ops and interp_idle)  # no carousel motion, no homed required

        self._btn_reset.setEnabled(in_error)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------
    def _on_home(self):
        """Send HOME to fatc in a background thread (non-blocking to Qt)."""
        if self._homing_thread and self._homing_thread.isRunning():
            return

        self._btn_home.setEnabled(False)
        self._btn_home.setText('HOMING…')

        if self.dynfatc:
            pass  # DynFATC state is driven by HAL pins

        self._homing_worker = _HomeWorker()
        self._homing_thread = QThread()
        self._homing_worker.moveToThread(self._homing_thread)
        self._homing_thread.started.connect(self._homing_worker.run)
        self._homing_worker.finished.connect(self._on_home_done)
        self._homing_worker.finished.connect(self._homing_thread.quit)
        self._homing_thread.start()

    def _on_home_done(self, ok, error_msg):
        self._btn_home.setText('REF CAROUSEL')
        if ok:
            LOG.info('Carousel homing complete')
            self._last_pocket = 1
            if self.dynfatc:
                self.dynfatc.update_carousel_angle(self._pocket_angle(1))
                self.dynfatc.sync_from_fatc()
        else:
            LOG.error('Carousel homing failed: %s', error_msg)

    def _on_load_spindle(self):
        text = self._tool_entry.text().strip()
        if not text.isdigit() or int(text) <= 0:
            return
        tool_num = int(text)
        LOG.info('LOAD SPINDLE: T%d M6', tool_num)
        # G43 is intentionally omitted here: issuing a second CMD.mdi() call
        # immediately after T# M6 races with the interpreter still finishing
        # change_epilog. G43 is handled by toolchange.ngc or by the operator's
        # program preamble.
        issue_mdi(f'T{tool_num} M6')

    def _on_store_tool(self):
        # Determine what tool is actually in the spindle.
        # Prefer LinuxCNC's own record; fall back to fatc's HAL pin if LC shows 0.
        # If LC shows 0 and we issue T0 M6, toolchange.ngc's early-exit fires
        # (selected_tool==tool_in_spindle == 0) and fatc never receives BEGIN.
        try:
            stat = linuxcnc.stat()
            stat.poll()
            lnc_tool = stat.tool_in_spindle
        except Exception:
            lnc_tool = 0

        if lnc_tool == 0:
            try:
                fatc_tool = int(hal.get_value('fatc.tool-in-spindle'))
            except Exception:
                fatc_tool = 0
            if fatc_tool <= 0:
                LOG.info('STORE TOOL: nothing in spindle (LC=0, fatc=0) — nothing to do')
                return
            # Sync LinuxCNC's tool record from fatc, then stow.
            # M61 Q{n} sets LC current_tool without motion; the semicolon-split
            # in issue_mdi queues both commands sequentially.
            LOG.info('STORE TOOL: LC shows T0, syncing to fatc T%d then T0 M6', fatc_tool)
            issue_mdi(f'M61 Q{fatc_tool}; T0 M6')
        else:
            LOG.info('STORE TOOL: T0 M6 (LC has T%d)', lnc_tool)
            issue_mdi('T0 M6')

    def _on_unload_spindle(self):
        """Mark spindle empty in LinuxCNC and fatc — no carousel motion."""
        LOG.info('UNLOAD SPINDLE: M61 Q0, G49, SET_SPINDLE 0')
        issue_mdi('M61 Q0')
        issue_mdi('G49')
        try:
            _fatc_ipc('SET_SPINDLE', tool=0, timeout=5.0)
        except Exception as exc:
            LOG.error('SET_SPINDLE IPC failed: %s', exc)

    def _on_reset_error(self):
        """Pulse fatc.error-reset HAL pin to clear error state."""
        try:
            # Toggle the HAL pin: fatc watches for rising edge
            hal.set_p('fatc.error-reset', '1')
            QTimer.singleShot(100, lambda: hal.set_p('fatc.error-reset', '0'))
            LOG.info('Error reset requested')
        except Exception as exc:
            LOG.error('Error reset failed: %s', exc)

    # ------------------------------------------------------------------
    # Startup reconciliation
    # ------------------------------------------------------------------
    def _reconcile_startup_tool(self, fatc_tool):
        """Sync LinuxCNC's tool-in-spindle to match fatc on startup.

        fatc's persistent state is the physical truth (which tool is actually
        in the spindle).  LinuxCNC's tool_in_spindle comes from the .var file
        and can be stale after a restart.  If they disagree, issue M61 Q{n}
        to tell LC what's really loaded.
        """
        try:
            stat = linuxcnc.stat()
            stat.poll()
            lnc_tool = stat.tool_in_spindle
        except Exception:
            return

        if lnc_tool == fatc_tool:
            LOG.info('Startup sync: LC and fatc agree — T%d in spindle', lnc_tool)
            return

        LOG.warning('Startup sync: LC has T%d, fatc has T%d — syncing LC via M61 Q%d',
                    lnc_tool, fatc_tool, fatc_tool)

        # Check if we can issue MDI right now
        try:
            import linuxcnc as _lc
            if stat.task_state != _lc.STATE_ON:
                LOG.debug('Startup sync: machine not ON — deferring')
                self._startup_sync_done = False  # retry next poll
                return
            if stat.interp_state != _lc.INTERP_IDLE:
                LOG.debug('Startup sync: interpreter busy — deferring')
                self._startup_sync_done = False  # retry next poll
                return
            # M61 is an MDI command; LC requires all joints homed first.
            if not stat.homed or not all(stat.homed[:stat.joints]):
                LOG.debug('Startup sync: machine not homed — deferring')
                self._startup_sync_done = False  # retry next poll
                return
        except Exception:
            return

        issue_mdi(f'M61 Q{fatc_tool}')
        LOG.info('Startup sync: issued M61 Q%d', fatc_tool)

    # DynATC sync removed — DynFATC handles its own sync via dynfatc.sync_from_fatc()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _hline():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet('color: #555;')
    return line
