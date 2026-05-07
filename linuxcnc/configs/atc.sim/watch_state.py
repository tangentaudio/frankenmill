#!/usr/bin/env python3
"""
watch_state.py — ATC diagnostic monitor.
Run in a terminal alongside LinuxCNC:
    python3 watch_state.py | tee /tmp/watch_state.log

Samples LinuxCNC stat + key HAL pins every 100ms and prints any change.
"""
import sys
import time
import subprocess
import linuxcnc

# Force line-buffered output so tee/pipes see output immediately
sys.stdout.reconfigure(line_buffering=True)

INTERP = {1: 'IDLE', 2: 'READING', 3: 'PAUSED', 4: 'WAITING'}
MODE   = {1: 'MANUAL', 2: 'AUTO', 3: 'MDI'}
STATE  = {1: 'ESTOP', 2: 'ESTOP_RESET', 3: 'ON'}  # (actually 1=ESTOP,2=ON for task_state)

HAL_PINS = [
    'motion.feed-hold',
    'fatc.program-stop',
    'fatc.state',
    'fatc.is-homed',
    'fatc.error',
    'fatc.error-code',
    'halui.program.stop',
    'halui.program.is-paused',
    'halui.program.is-idle',
    'iocontrol.0.tool-change',
    'iocontrol.0.tool-changed',
    'iocontrol.0.tool-prep-number',
    'fatc.tool-change',
    'fatc.tool-changed',
]

def halget(pin):
    try:
        r = subprocess.run(['halcmd', 'getp', pin],
                           capture_output=True, text=True, timeout=0.5)
        return r.stdout.strip()
    except Exception:
        return '?'

def main():
    s = linuxcnc.stat()
    prev = None   # None forces a print on the very first sample

    print(f"{'TIME':12} {'INTERP':10} {'MODE':8} {'FH':3} {'PAUSED':7} {'TASK':3} | HAL pins")
    print('-' * 90)

    # Wait for LinuxCNC to come up
    while True:
        try:
            s.poll()
            break
        except Exception:
            time.sleep(0.5)
    print(f"LinuxCNC connected.")

    while True:
        try:
            s.poll()
        except Exception as e:
            print(f"stat poll error: {e}")
            time.sleep(0.5)
            continue

        hal = {p: halget(p) for p in HAL_PINS}

        cur = {
            'interp': s.interp_state,
            'exec':   s.exec_state,
            'line':   s.current_line,
            'mode':   s.task_mode,
            'fh':     s.feed_hold_enabled,
            'paused': s.paused,
            'state':  s.task_state,
            **hal,
        }

        if cur != prev:
            ts = time.strftime('%H:%M:%S') + f'.{int(time.time()*10)%10}'
            interp_s = INTERP.get(s.interp_state, str(s.interp_state))
            mode_s   = MODE.get(s.task_mode, str(s.task_mode))
            fh_s     = 'YES' if s.feed_hold_enabled else 'no'
            pau_s    = 'YES' if s.paused else 'no'
            state_s  = str(s.task_state)

            # Print what changed vs previous (or everything on first sample)
            if prev is None:
                changes = [f'{k}={v}' for k, v in cur.items()]
            else:
                changes = [f'{k}={v}' for k, v in cur.items() if prev.get(k) != v]

            print(f"{ts:12} {interp_s:10} {mode_s:8} {fh_s:3} {pau_s:7} {state_s:3} | {', '.join(changes)}")
            prev = cur

        time.sleep(0.1)

if __name__ == '__main__':
    main()
