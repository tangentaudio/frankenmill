"""dynfatc.py — DynFATC QML widget wrapper.

A QQuickWidget that renders the FrankenMill ATC mechanism:
  - Top-down view: spindle nose, linear track, carousel ring
  - Side profile: spindle cross-section, Z position, drawbar state

Driven directly by fatc HAL pins — no fragile widget-name lookups or NGC EVAL.
All carousel positioning is absolute (computed from pocket angles), eliminating
the sync drift that plagued DynATC's relative step accumulation.
"""

import os
import json
import socket

from qtpy.QtCore import QUrl, QMetaObject, Q_ARG, Qt
from qtpy.QtQuickWidgets import QQuickWidget

from qtpyvcp.utilities import logger

LOG = logger.getLogger(__name__)

WIDGET_PATH = os.path.dirname(os.path.abspath(__file__))
SOCK_PATH = os.environ.get('FATC_SOCKET', '/tmp/fatc.sock')


class DynFATC(QQuickWidget):
    """QML-based ATC visualization widget.

    Python drives the QML scene by setting QML properties via setProperty().
    HAL pin polling is handled externally (by fatc_atc.py's _poll timer)
    which calls the update_* methods on this widget.
    """

    def __init__(self, parent=None, pocket_count=12, orientation='LEFT'):
        super().__init__(parent)

        self._pocket_count = pocket_count
        self._mirror = orientation.upper() == 'RIGHT'

        # Load QML
        qml_path = os.path.join(WIDGET_PATH, 'dynfatc.qml')
        self.setSource(QUrl.fromLocalFile(qml_path))
        self.setResizeMode(QQuickWidget.SizeRootObjectToView)

        # Wait for QML to load, then initialize
        if self.status() == QQuickWidget.Ready:
            self._init_qml()
        else:
            self.statusChanged.connect(self._on_status_changed)

    def _on_status_changed(self, status):
        if status == QQuickWidget.Ready:
            self._init_qml()
        elif status == QQuickWidget.Error:
            for err in self.errors():
                LOG.error('DynFATC QML error: %s', err.toString())

    def _init_qml(self):
        """Configure QML root object after load."""
        root = self.rootObject()
        if not root:
            LOG.error('DynFATC: no root object')
            return
        root.setProperty('pocketCount', self._pocket_count)
        root.setProperty('mirrorLayout', self._mirror)
        LOG.info('DynFATC initialized: %d pockets, mirror=%s',
                 self._pocket_count, self._mirror)

    # ------------------------------------------------------------------
    # Public API — called by fatc_atc.py poll loop
    # All updates use setProperty() to set QML properties directly.
    # ------------------------------------------------------------------

    def update_carousel_angle(self, angle_deg):
        """Set absolute carousel rotation angle (degrees)."""
        root = self.rootObject()
        if root:
            root.setProperty('carouselAngle', float(angle_deg))

    def update_arm_position(self, extended):
        """Set linear arm extended/retracted."""
        root = self.rootObject()
        if root:
            root.setProperty('armExtended', bool(extended))

    def update_spindle_tool(self, tool_num):
        """Set tool number in spindle (0 = empty)."""
        root = self.rootObject()
        if root:
            root.setProperty('spindleTool', int(tool_num))

    def update_pocket(self, pocket, tool_num):
        """Set tool in a specific pocket (0 = empty).

        Uses invokeMethod because updatePocket() is a JS function that
        modifies Repeater item properties (can't be set as a root property).
        """
        root = self.rootObject()
        if root:
            QMetaObject.invokeMethod(root, "updatePocket",
                                     Qt.DirectConnection,
                                     Q_ARG("QVariant", int(pocket)),
                                     Q_ARG("QVariant", int(tool_num)))

    def update_drawbar(self, clamped, unclamped):
        """Set drawbar sensor state."""
        root = self.rootObject()
        if root:
            root.setProperty('drawbarClamped', bool(clamped))
            root.setProperty('drawbarUnclamped', bool(unclamped))

    def update_z_position(self, z_pos):
        """Set current Z position for side-profile view."""
        root = self.rootObject()
        if root:
            root.setProperty('zPosition', float(z_pos))

    def set_z_heights(self, safe_height, tc_height):
        """Set reference Z heights (called once at init)."""
        root = self.rootObject()
        if root:
            root.setProperty('zSafeHeight', float(safe_height))
            root.setProperty('zTcHeight', float(tc_height))

    def update_homed(self, is_homed):
        """Set homed state (controls UNREFERENCED overlay)."""
        root = self.rootObject()
        if root:
            root.setProperty('isHomed', bool(is_homed))

    def update_state(self, state_int, state_text):
        """Set fatc state for visual feedback."""
        root = self.rootObject()
        if root:
            root.setProperty('stateInt', int(state_int))
            root.setProperty('stateText', str(state_text))

    # ------------------------------------------------------------------
    # Bulk pocket sync from fatc IPC
    # ------------------------------------------------------------------

    def sync_from_fatc(self):
        """Pull pocket_map from fatc via GET_INVENTORY and update all pockets."""
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(2.0)
                s.connect(SOCK_PATH)
                s.sendall(json.dumps({'cmd': 'GET_INVENTORY'}).encode() + b'\n')
                buf = b''
                while b'\n' not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
            resp = json.loads(buf.split(b'\n')[0])
            if not resp.get('ok'):
                LOG.warning('DynFATC sync: GET_INVENTORY failed: %s',
                            resp.get('error'))
                return

            pocket_map = resp.get('pocket_map', {})
            for pocket_str, tool in pocket_map.items():
                try:
                    self.update_pocket(int(pocket_str), int(tool))
                except (ValueError, TypeError):
                    pass
            LOG.info('DynFATC synced: %d pockets from fatc', len(pocket_map))

        except Exception as exc:
            LOG.debug('DynFATC sync failed: %s', exc)
