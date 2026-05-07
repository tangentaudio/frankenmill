"""
config.py — INI file configuration loader for the fatc ATC component.

Reads the [ATC] section from the LinuxCNC INI file and exposes all
tuneable parameters as a typed, validated Config dataclass.

All physical parameters (positions, feedrates, timeouts) are centrally
defined here with sensible defaults.  Nothing is hardcoded in fatc.py or
serial_thread.py.
"""

import configparser
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # --- Serial ---
    serial_port: str = '/dev/ttyUSB0'
    baud_rate: int = 115200
    serial_timeout: float = 2.0        # command_timeout for MarlinSerial
    home_timeout: float = 30.0         # timeout for G28 homing
    move_timeout: float = 15.0         # timeout for a single carousel move
    connect_timeout: float = 10.0      # wait for Marlin 'start' on connect

    # --- Carousel geometry ---
    pockets: int = 12
    pocket_1_position: float = 0.0     # rotation axis position (deg) for pocket 1
    rotation_full_turn: float = 360.0  # degrees per full carousel revolution

    # --- Axis configuration ---
    linear_axis: str = 'X'
    rotation_axis: str = 'C'

    # --- Marlin motion parameters ---
    linear_extend_position: float = 100.0   # mm, extended (at spindle nose)
    linear_retract_position: float = 0.0    # mm, retracted (home)
    rotation_feedrate: int = 3000           # deg/min
    linear_feedrate: int = 2000             # mm/min

    # --- Z heights (machine coords, inch) ---
    z_tool_change_height: float = -13.5     # Z at which drawbar engages tool
    z_tool_clearance_height: float = 0.0    # Z safe height above all tools

    # --- Drawbar timeouts ---
    drawbar_clamp_timeout: float = 2.0
    drawbar_unclamp_timeout: float = 2.0

    # --- Component behaviour ---
    poll_interval: float = 0.01            # seconds between HAL poll loop cycles
    position_poll_interval: float = 0.05   # seconds between M114 polls during motion

    # --- Homing ---
    homing_required: int = 0   # 0=skip all, 1=home both axes, 2=rotation-only (no linear sensor)

    # --- IPC ---
    socket_path: str = '/tmp/fatc.sock'    # Unix socket for M101/M102/M103

    # --- Simulation / debug ---
    sim_stage_delay: float = 0.0  # seconds to pause after each state transition (0 = off)


def load(ini_path: str, section: str = 'ATC') -> Config:
    """
    Load configuration from an INI file.

    Reads ``section`` (default 'ATC') from ``ini_path``.  Keys that are
    absent in the file retain their dataclass default values.

    Raises FileNotFoundError if ``ini_path`` does not exist.
    Raises ValueError if a value cannot be converted to the expected type.
    """
    if not os.path.isfile(ini_path):
        raise FileNotFoundError(f"INI file not found: {ini_path!r}")

    parser = configparser.ConfigParser(strict=False)
    parser.read(ini_path)

    cfg = Config()

    if not parser.has_section(section):
        return cfg          # all defaults

    def get(key, converter, default):
        try:
            raw = parser.get(section, key)
            return converter(raw)
        except configparser.NoOptionError:
            return default
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"[{section}] {key}: cannot convert {parser.get(section, key)!r}: {exc}"
            ) from exc

    cfg.serial_port           = get('SERIAL_PORT',             str,   cfg.serial_port)
    cfg.baud_rate             = get('BAUD_RATE',               int,   cfg.baud_rate)
    cfg.serial_timeout        = get('SERIAL_TIMEOUT',          float, cfg.serial_timeout)
    cfg.home_timeout          = get('HOME_TIMEOUT',            float, cfg.home_timeout)
    cfg.move_timeout          = get('MOVE_TIMEOUT',            float, cfg.move_timeout)
    cfg.connect_timeout       = get('CONNECT_TIMEOUT',         float, cfg.connect_timeout)

    cfg.pockets               = get('POCKETS',                 int,   cfg.pockets)
    cfg.pocket_1_position     = get('POCKET_1_POSITION',       float, cfg.pocket_1_position)
    cfg.rotation_full_turn    = get('ROTATION_FULL_TURN',      float, cfg.rotation_full_turn)

    cfg.linear_axis           = get('LINEAR_AXIS',             str,   cfg.linear_axis).upper()
    cfg.rotation_axis         = get('ROTATION_AXIS',           str,   cfg.rotation_axis).upper()

    cfg.linear_extend_position  = get('LINEAR_EXTEND_POSITION',  float, cfg.linear_extend_position)
    cfg.linear_retract_position = get('LINEAR_RETRACT_POSITION', float, cfg.linear_retract_position)
    cfg.rotation_feedrate     = get('ROTATION_FEEDRATE',       int,   cfg.rotation_feedrate)
    cfg.linear_feedrate       = get('LINEAR_FEEDRATE',         int,   cfg.linear_feedrate)

    cfg.z_tool_change_height    = get('TC_HEIGHT',               float, cfg.z_tool_change_height)
    cfg.z_tool_clearance_height = get('Z_TOOL_CLEARANCE_HEIGHT', float, cfg.z_tool_clearance_height)

    cfg.drawbar_clamp_timeout   = get('DRAWBAR_CLAMP_TIMEOUT',   float, cfg.drawbar_clamp_timeout)
    cfg.drawbar_unclamp_timeout = get('DRAWBAR_UNCLAMP_TIMEOUT', float, cfg.drawbar_unclamp_timeout)

    cfg.homing_required         = get('HOMING_REQUIRED', int,   cfg.homing_required)
    cfg.socket_path             = get('SOCKET_PATH',      str,   cfg.socket_path)
    cfg.sim_stage_delay         = get('SIM_STAGE_DELAY',  float, cfg.sim_stage_delay)

    return cfg


def _bool(value: str) -> bool:
    """Parse INI boolean strings: 1/0, true/false, yes/no, on/off."""
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def pocket_angle(cfg: Config, pocket: int) -> float:
    """
    Return the absolute rotation axis position (degrees) for a pocket number.

    Pocket numbering is 1-based.  Pocket 1 is at ``cfg.pocket_1_position``.
    """
    if pocket < 1 or pocket > cfg.pockets:
        raise ValueError(f"Pocket {pocket} out of range 1..{cfg.pockets}")
    step = cfg.rotation_full_turn / cfg.pockets
    return cfg.pocket_1_position + (pocket - 1) * step
