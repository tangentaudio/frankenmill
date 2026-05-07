#!/usr/bin/env python3
"""Quick LC state snapshot. Run any time during a test."""
import linuxcnc, hal, sys

INTERP = {1:'IDLE', 2:'READING', 3:'PAUSED', 4:'WAITING'}
EXEC   = {1:'ERROR', 2:'DONE', 3:'WAIT_MOTION', 4:'WAIT_QUEUE',
          5:'WAIT_IO', 6:'WAIT_MOTION+IO', 7:'WAIT_DELAY',
          8:'WAIT_SYSCMD', 9:'WAIT_SPINDLE'}
TASK   = {1:'MANUAL', 2:'AUTO', 3:'MDI'}

s = linuxcnc.stat()
s.poll()
print(f"interp_state : {s.interp_state} ({INTERP.get(s.interp_state,'?')})")
print(f"exec_state   : {s.exec_state} ({EXEC.get(s.exec_state,'?')})")
print(f"task_mode    : {s.task_mode} ({TASK.get(s.task_mode,'?')})")
print(f"feed_hold    : {s.feed_hold_enabled}")
print(f"paused       : {s.paused}")
print(f"tool_in_spin : {s.tool_in_spindle}")
print(f"pocket_prepped:{s.pocket_prepped}")
print(f"current_line : {s.current_line}")

try:
    for pin in ['fatc.state','fatc.is-homed','fatc.tool-in-spindle',
                'fatc.program-stop','halui.program.is-idle','motion.feed-hold']:
        print(f"hal {pin:35s}: {hal.get_value(pin)}")
except Exception as e:
    print(f"HAL read error: {e}")
