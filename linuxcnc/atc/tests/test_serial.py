#!/usr/bin/env python3
"""
test_serial.py — Phase 1 interactive test script for serial_marlin.py

Tests the Marlin serial protocol layer directly against real hardware.
No LinuxCNC dependency required.

Usage:
    python3 test_serial.py [--port /dev/ttyUSB0] [--baud 115200]

Runs a series of tests then drops into an interactive G-code prompt.
The Marlin test bench has no endstops wired; software endstops are
disabled (M211 S0) before any moves so relative moves work freely.
"""

import argparse
import logging
import sys
import os

# Allow running from the tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fatc.serial_marlin import (
    MarlinSerial,
    MarlinError,
    MarlinTimeout,
    MarlinNotConnected,
)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-7s %(name)s | %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('test_serial')


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def section(title: str):
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print(f'{"=" * 60}')


def ok(msg: str):
    print(f'  [PASS] {msg}')


def fail(msg: str):
    print(f'  [FAIL] {msg}')


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

def test_connect(m: MarlinSerial) -> bool:
    section('TEST 1: Connect and M115 firmware identification')
    try:
        info = m.connect()
        ok(f'Connected. Firmware: {info}')
        return True
    except Exception as exc:
        fail(f'Connect failed: {exc}')
        return False


def test_m114_position(m: MarlinSerial) -> bool:
    section('TEST 2: M114 position query')
    try:
        pos = m.get_position()
        if pos:
            ok(f'Position: {pos}')
            return True
        else:
            fail('M114 returned empty position dict')
            return False
    except Exception as exc:
        fail(f'M114 failed: {exc}')
        return False


def test_disable_endstops(m: MarlinSerial) -> bool:
    section('TEST 3: Disable software endstops (M211 S0) — required for unhomed bench')
    try:
        m.disable_software_endstops()
        ok('Software endstops disabled')
        return True
    except Exception as exc:
        fail(f'M211 S0 failed: {exc}')
        return False


def test_relative_mode(m: MarlinSerial) -> bool:
    section('TEST 4: Switch to relative positioning (G91)')
    try:
        m.set_relative()
        ok('Relative mode set (G91)')
        return True
    except Exception as exc:
        fail(f'G91 failed: {exc}')
        return False


def test_rotation_move(m: MarlinSerial) -> bool:
    section('TEST 5: Relative rotation move G0 C30 (30 degrees CW)')
    try:
        pos_before = m.get_position()
        log.info("Position before move: %s", pos_before)

        m.send_gcode('G0 C30')
        m.wait_for_moves()

        pos_after = m.get_position()
        log.info("Position after move: %s", pos_after)

        ok(f'C move complete. Before={pos_before.get("C", "?"):.2f}  '
           f'After={pos_after.get("C", "?"):.2f}')
        return True
    except Exception as exc:
        fail(f'Rotation move failed: {exc}')
        return False


def test_rotation_back(m: MarlinSerial) -> bool:
    section('TEST 6: Relative rotation move G0 C-30 (return)')
    try:
        m.send_gcode('G0 C-30')
        m.wait_for_moves()
        pos = m.get_position()
        ok(f'Return move complete. C={pos.get("C", "?"):.2f}')
        return True
    except Exception as exc:
        fail(f'Return move failed: {exc}')
        return False


def test_linear_move(m: MarlinSerial) -> bool:
    section('TEST 7: Relative linear move G0 X10 (10mm extend)')
    try:
        m.send_gcode('G0 X10')
        m.wait_for_moves()
        pos = m.get_position()
        ok(f'Linear extend complete. X={pos.get("X", "?"):.2f}')
        return True
    except Exception as exc:
        fail(f'Linear move failed: {exc}')
        return False


def test_linear_back(m: MarlinSerial) -> bool:
    section('TEST 8: Relative linear move G0 X-10 (retract)')
    try:
        m.send_gcode('G0 X-10')
        m.wait_for_moves()
        pos = m.get_position()
        ok(f'Linear retract complete. X={pos.get("X", "?"):.2f}')
        return True
    except Exception as exc:
        fail(f'Retract failed: {exc}')
        return False


def test_endstop_status(m: MarlinSerial) -> bool:
    section('TEST 9: M119 endstop status')
    try:
        status = m.get_endstop_status()
        if status:
            ok(f'Endstop status: {status}')
        else:
            ok('M119 returned no data (expected on this bench config)')
        return True
    except Exception as exc:
        fail(f'M119 failed: {exc}')
        return False


def test_timeout_handling(m: MarlinSerial) -> bool:
    section('TEST 10: Timeout handling (send garbage — expect Error or timeout)')
    try:
        # Send an unknown command; Marlin should respond with echo/error + ok
        lines = m.send_gcode('M9999', timeout=3.0)
        ok(f'Got response (no crash): {lines}')
        return True
    except MarlinError as exc:
        ok(f'Got expected MarlinError: {exc}')
        return True
    except MarlinTimeout as exc:
        ok(f'Got MarlinTimeout (acceptable for unknown cmd): {exc}')
        return True
    except Exception as exc:
        fail(f'Unexpected exception: {exc}')
        return False


# ---------------------------------------------------------------------------
# Interactive prompt
# ---------------------------------------------------------------------------

def interactive_prompt(m: MarlinSerial):
    section('Interactive G-code prompt (type "quit" to exit)')
    print('  Commands are sent directly to Marlin.')
    print('  Special: "pos" = M114, "endstops" = M119, "wait" = M400\n')

    while True:
        try:
            cmd = input('  gcode> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue
        if cmd.lower() in ('quit', 'exit', 'q'):
            break
        if cmd.lower() == 'pos':
            cmd = 'M114'
        elif cmd.lower() == 'endstops':
            cmd = 'M119'
        elif cmd.lower() == 'wait':
            cmd = 'M400'

        try:
            lines = m.send_gcode(cmd)
            if lines:
                for line in lines:
                    print(f'  < {line}')
        except MarlinError as exc:
            print(f'  Error: {exc}')
        except MarlinTimeout as exc:
            print(f'  Timeout: {exc}')
        except MarlinNotConnected as exc:
            print(f'  Not connected: {exc}')
            break
        except Exception as exc:
            print(f'  Exception: {exc}')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='FrankenMill fatc serial layer test')
    parser.add_argument('--port', default='/dev/ttyUSB0', help='Serial port')
    parser.add_argument('--baud', type=int, default=115200, help='Baud rate')
    parser.add_argument('--connect-timeout', type=float, default=10.0)
    parser.add_argument('--command-timeout', type=float, default=15.0)
    parser.add_argument('--move-timeout', type=float, default=30.0)
    parser.add_argument('--no-moves', action='store_true',
                        help='Skip motor move tests (protocol tests only)')
    parser.add_argument('--interactive', action='store_true',
                        help='Drop into interactive G-code prompt after tests')
    args = parser.parse_args()

    print(f'\nFrankenMill fatc — Phase 1 Serial Test')
    print(f'Port: {args.port}  Baud: {args.baud}')

    m = MarlinSerial(
        port=args.port,
        baud=args.baud,
        connect_timeout=args.connect_timeout,
        command_timeout=args.command_timeout,
        move_timeout=args.move_timeout,
    )

    results = []

    # Always run connection test first
    if not test_connect(m):
        print('\nCannot continue — connection failed.')
        sys.exit(1)
    results.append(True)

    # Protocol tests (no motion)
    results.append(test_m114_position(m))
    results.append(test_endstop_status(m))
    results.append(test_timeout_handling(m))
    results.append(test_disable_endstops(m))
    results.append(test_relative_mode(m))

    # Motion tests (skip with --no-moves)
    if not args.no_moves:
        results.append(test_rotation_move(m))
        results.append(test_rotation_back(m))
        results.append(test_linear_move(m))
        results.append(test_linear_back(m))
    else:
        print('\n  (Motor move tests skipped — --no-moves)')

    # Summary
    section('Test Summary')
    passed = sum(results)
    total = len(results)
    print(f'  {passed}/{total} tests passed')
    if passed < total:
        print(f'  {total - passed} FAILED')

    # Interactive prompt
    if args.interactive or input('\n  Drop into interactive prompt? [y/N] ').lower() == 'y':
        interactive_prompt(m)

    m.disconnect()
    print('\nDone.\n')
    sys.exit(0 if passed == total else 1)


if __name__ == '__main__':
    main()
