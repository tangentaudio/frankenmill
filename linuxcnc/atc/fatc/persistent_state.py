"""
persistent_state.py — JSON-backed tool-pocket map for the fatc ATC component.

Survives component restarts, machine power cycles, and LinuxCNC restarts.
The carousel stays physically loaded; this file is the authoritative record.

File format (example, 12-pocket carousel):
{
    "version": 1,
    "tool_in_spindle": 3,
    "current_pocket": 7,
    "inventory_valid": false,
    "pocket_map": {
        "1":  0,
        "2":  2,
        "3":  0,
        "4":  5,
        ...
    },
    "calibration": {
        "linear_offset": 0.0,
        "pocket_offsets": {"1": 0.0, "2": -0.5, ...}
    }
}

pocket_map values: 0 = empty, positive integer = tool number

Writes are atomic: data is written to a .tmp file, then renamed over the
real file.  A partial write (e.g. power loss mid-write) never corrupts the
saved state.
"""

import json
import logging
import os
from typing import Dict, Optional

log = logging.getLogger(__name__)

_FORMAT_VERSION = 1


class PersistentState:
    """
    Manages the on-disk ATC state file.

    All mutating methods dirty-mark the state and schedule a write.
    Call save() explicitly, or use the context manager for auto-save.
    """

    def __init__(self, path: str):
        self._path = path
        self._dirty = False

        # In-memory state
        self.tool_in_spindle: int = 0        # 0 = no tool
        self.current_pocket: int = 0         # 0 = unknown
        self.inventory_valid: bool = False
        self.pocket_map: Dict[int, int] = {} # pocket_number -> tool_number (0=empty)
        self.linear_offset: float = 0.0
        self.pocket_offsets: Dict[int, float] = {}

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load(self, num_pockets: int) -> bool:
        """
        Load state from disk.

        Initialises pocket_map to empty for any pockets absent in the file.
        Returns True if a valid file was loaded, False if starting fresh.
        """
        if not os.path.isfile(self._path):
            log.info("No persistent state file at %s — starting fresh", self._path)
            self._init_empty(num_pockets)
            return False

        try:
            with open(self._path, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Failed to load state file %s: %s — starting fresh", self._path, exc)
            self._init_empty(num_pockets)
            return False

        if data.get('version') != _FORMAT_VERSION:
            log.warning(
                "State file version mismatch (got %s, expected %s) — starting fresh",
                data.get('version'), _FORMAT_VERSION,
            )
            self._init_empty(num_pockets)
            return False

        self.tool_in_spindle  = int(data.get('tool_in_spindle', 0))
        self.current_pocket   = int(data.get('current_pocket', 0))
        self.inventory_valid  = bool(data.get('inventory_valid', False))

        raw_map = data.get('pocket_map', {})
        self.pocket_map = {int(k): int(v) for k, v in raw_map.items()}

        # Fill in any pockets absent from the file (e.g. pocket count changed)
        for p in range(1, num_pockets + 1):
            if p not in self.pocket_map:
                self.pocket_map[p] = 0

        cal = data.get('calibration', {})
        self.linear_offset   = float(cal.get('linear_offset', 0.0))
        raw_offsets = cal.get('pocket_offsets', {})
        self.pocket_offsets  = {int(k): float(v) for k, v in raw_offsets.items()}

        self._dirty = False
        log.info(
            "Loaded state: tool_in_spindle=%d current_pocket=%d inventory_valid=%s",
            self.tool_in_spindle, self.current_pocket, self.inventory_valid,
        )
        return True

    def save(self) -> bool:
        """
        Write state to disk atomically.

        Returns True on success, False on I/O error.
        """
        data = {
            'version': _FORMAT_VERSION,
            'tool_in_spindle': self.tool_in_spindle,
            'current_pocket': self.current_pocket,
            'inventory_valid': self.inventory_valid,
            'pocket_map': {str(k): v for k, v in sorted(self.pocket_map.items())},
            'calibration': {
                'linear_offset': self.linear_offset,
                'pocket_offsets': {str(k): v for k, v in sorted(self.pocket_offsets.items())},
            },
        }

        tmp_path = self._path + '.tmp'
        try:
            with open(tmp_path, 'w') as f:
                json.dump(data, f, indent=2)
                f.write('\n')
            os.replace(tmp_path, self._path)
            self._dirty = False
            log.debug("State saved to %s", self._path)
            return True
        except OSError as exc:
            log.error("Failed to save state to %s: %s", self._path, exc)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return False

    def save_if_dirty(self) -> bool:
        """Save only if state has been modified since last save."""
        if self._dirty:
            return self.save()
        return True

    # ------------------------------------------------------------------
    # Pocket map accessors
    # ------------------------------------------------------------------

    def get_tool_in_pocket(self, pocket: int) -> int:
        """Return tool number in pocket (0 = empty)."""
        return self.pocket_map.get(pocket, 0)

    def set_tool_in_pocket(self, pocket: int, tool: int):
        """Record that pocket now contains tool (0 = empty)."""
        self.pocket_map[pocket] = tool
        self._dirty = True

    def find_pocket_for_tool(self, tool: int) -> Optional[int]:
        """Return pocket number containing tool, or None if not found."""
        for pocket, t in self.pocket_map.items():
            if t == tool:
                return pocket
        return None

    def find_empty_pocket(self) -> Optional[int]:
        """Return the first empty pocket number, or None if carousel is full."""
        for pocket, t in sorted(self.pocket_map.items()):
            if t == 0:
                return pocket
        return None

    def set_tool_in_spindle(self, tool: int):
        self.tool_in_spindle = tool
        self._dirty = True

    def set_current_pocket(self, pocket: int):
        self.current_pocket = pocket
        self._dirty = True

    def set_inventory_valid(self, valid: bool):
        self.inventory_valid = valid
        self._dirty = True

    def set_pocket_offset(self, pocket: int, offset: float):
        self.pocket_offsets[pocket] = offset
        self._dirty = True

    def set_linear_offset(self, offset: float):
        self.linear_offset = offset
        self._dirty = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _init_empty(self, num_pockets: int):
        self.tool_in_spindle = 0
        self.current_pocket  = 0
        self.inventory_valid = False
        self.pocket_map      = {p: 0 for p in range(1, num_pockets + 1)}
        self.linear_offset   = 0.0
        self.pocket_offsets  = {}
        self._dirty          = True
