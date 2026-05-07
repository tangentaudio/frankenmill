"""fatc_sync.py — DynATC startup sync plugin.

A minimal QtPyVCP DataPlugin whose sole purpose is to populate the
``dynatc`` carousel widget from ``fatc_state.json`` on GUI startup.

It uses the ``postGuiInitialise`` hook (called after the main window is
shown) and defers the actual socket call via ``QTimer.singleShot`` so
that the fatc daemon has time to start its socket server.

Registered in ``custom_config.yml`` under ``data_plugins``.
"""

import json
import socket
import logging

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QApplication

from qtpyvcp.plugins.base_plugins import DataPlugin

LOG = logging.getLogger(__name__)

FATC_SOCK = '/tmp/fatc.sock'
_SYNC_DELAY_MS = 3000   # 3 s — fatc daemon needs time to bind the socket


def _get_inventory():
    """Connect to fatc socket, send GET_INVENTORY, return pocket_map or {}."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(FATC_SOCK)
            s.sendall(json.dumps({'cmd': 'GET_INVENTORY'}).encode() + b'\n')
            data = b''
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b'\n' in data:
                    break
        resp = json.loads(data.decode().strip())
        if resp.get('ok'):
            return resp.get('pocket_map', {})
    except Exception as exc:
        LOG.debug("fatc_sync: GET_INVENTORY failed: %s", exc)
    return {}


def _find_dynatc():
    """Return the dynatc QQuickWidget by objectName, or None."""
    for widget in QApplication.allWidgets():
        if widget.objectName() == 'dynatc':
            return widget
    return None


def _do_sync():
    """Pull inventory from fatc and push each pocket into dynatc."""
    pocket_map = _get_inventory()
    if not pocket_map:
        LOG.debug("fatc_sync: no inventory data — skipping dynatc populate")
        return

    dynatc = _find_dynatc()
    if dynatc is None:
        LOG.warning("fatc_sync: dynatc widget not found — skipping populate")
        return

    for pocket_str, tool in pocket_map.items():
        try:
            pocket = int(pocket_str)
            if tool > 0:
                dynatc.store_tool(pocket, tool)
                LOG.debug("fatc_sync: pocket %d ← T%d", pocket, tool)
        except (ValueError, AttributeError) as exc:
            LOG.warning("fatc_sync: store_tool(%s, %s) failed: %s",
                        pocket_str, tool, exc)

    try:
        dynatc.load_tools()
        LOG.info("fatc_sync: dynatc populated from fatc_state.json "
                 "(%d pocket(s))", len(pocket_map))
    except AttributeError:
        # load_tools may not exist in all ATC widget versions
        pass


class FatcSyncPlugin(DataPlugin):
    """QtPyVCP plugin that syncs the DynATC widget from fatc state on startup."""

    def postGuiInitialise(self, main_window):
        QTimer.singleShot(_SYNC_DELAY_MS, _do_sync)
        self._postGuiInitialized = True
        LOG.info("fatc_sync: scheduled DynATC sync in %d ms", _SYNC_DELAY_MS)
