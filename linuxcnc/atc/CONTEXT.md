# FrankenMill ATC — Session Context Summary

> **Purpose:** This document captures the full context of the `fatc` (Franken-ATC)
> project so development can be continued by a different agent/platform. Read this
> first, then the referenced documents for details.
>
> **Date:** 2026-05-06
> **Repo:** `tangentaudio/frankenmill` — `/home/steve/devel/frankenmill`

---

## 1. What Is This Project?

An **automatic tool changer (ATC)** system for a converted Grizzly G0463 milling
machine ("FrankenMill"). The ATC uses an **umbrella/carousel-style** mechanism
(12–16 pockets) controlled by a dedicated **Marlin 3D printer controller board**
(RAMPS Creality / ATmega2560) that handles the carousel's two axes of motion.
The host-side CNC controller is **LinuxCNC 2.9.x** with a Mesa 7i80HD for
main machine I/O.

The `fatc` component is a **Python `loadusr` HAL component** that bridges
LinuxCNC and Marlin over USB-serial, implementing a state machine for tool
change orchestration.

---

## 2. Repository Layout

```
frankenmill/
└── linuxcnc/
    ├── configs/fmx3.probebasic/     # Production LinuxCNC config (Mesa 7i80) — NOT YET using fatc
    │   ├── fmx3.ini
    │   ├── fmx3.hal
    │   └── subroutines/toolchange.ngc  # Legacy ATC G-code (reference only)
    └── atc/                          # ★ ATC subproject (active development)
        ├── README.md                 # Project overview
        ├── CONTEXT.md                # This file — read first
        ├── requirements.md           # Architectural requirements
        ├── development.md            # Development approach, phasing, phase status
        └── fatc/                     # Python component source (implemented)
            ├── fatc.py               # Main entry point — HAL component + state machine
            ├── serial_marlin.py      # Marlin USB-serial protocol layer
            ├── serial_thread.py      # Background serial thread + heartbeat M114
            ├── config.py             # INI config loader
            ├── persistent_state.py   # JSON tool-pocket map persistence
            └── socket_server.py      # Unix socket IPC server (M-codes ↔ fatc)
                                      # (or may be integrated directly in fatc.py)
    └── configs/atc.sim/              # ★ Active sim/dev LinuxCNC config
        ├── atc_sim.ini               # LinuxCNC config — Probe Basic GUI, sim mode
        ├── fatc_launch.sh            # loadusr wrapper: runs fatc.py, redirects to fatc.log
        ├── fatc_state.json           # Persistent carousel state (written at runtime)
        ├── tool.tbl                  # LinuxCNC tool table (T1-T105, real machine tools)
        ├── watch_state.py            # Diagnostic monitor: polls LC stat + HAL pins, prints diffs
        ├── lcstat.py                 # One-shot diagnostic snapshot
        ├── hallib/
        │   ├── core_sim.hal          # Sim axis, iocontrol wiring, tool-change net definitions
        │   ├── fatc_sim.hal          # fatc component loading, pin wiring, program-stop net
        │   ├── spindle_sim.hal       # Simulated spindle encoder
        │   └── probe_basic_postgui.hal # Postgui: cycle timer, drawbar sim, fatc abort
        ├── subroutines/
        │   ├── toolchange.ngc        # M6 REMAP thin sequencer (uses M101-M103)
        │   ├── M101                  # BEGIN: blocks until carousel READY_FOR_Z
        │   ├── M102                  # Z_ENGAGED: blocks until drawbar op done
        │   ├── M103                  # Z_CLEAR: blocks until COMPLETE
        │   └── m13.ngc               # REF CAROUSEL (homes fatc carousel via M104)
        ├── python/
        │   ├── remap.py              # M6 remap prolog/epilog (local additions)
        │   ├── stdglue.py            # Standard change_prolog / change_epilog functions
        │   └── toplevel.py           # Interpreter top-level (imports remap)
        └── atc/
            └── fatc_atc/
                └── fatc_atc.py       # ★ Probe Basic ATC tab module (class Atc(QWidget))
```

**Probe Basic** is installed at `/home/steve/dev/probe_basic/` (development install).
**QtPyVCP** is at `/home/steve/dev/qtpyvcp/`.
Both are `pip install -e` installs; edits to source take effect immediately.

---

## 3. Key Documents

| Document | Path | What It Contains |
|----------|------|------------------|
| **requirements.md** | `linuxcnc/atc/requirements.md` | Full architectural spec: HAL pins, state machine, safety, M6 remap, error handling, Marlin protocol |
| **development.md** | `linuxcnc/atc/development.md` | Dev environment, 6-phase build plan, current phase status per checklist |
| **marlin/README.md** | `linuxcnc/atc/marlin/README.md` | Marlin build instructions, axis mapping |

---

## 4. Implemented Architecture

### 4.1 Overview

```
┌──────────────────────────────────────────────────────────┐
│   LinuxCNC (sim or real)                                 │
│                                                          │
│   Probe Basic GUI (QtPyVCP)                              │
│     └── ATC tab: fatc_atc.py  ──IPC──► fatc.sock        │
│                                                          │
│   M6 REMAP: toolchange.ngc                               │
│     M101/M102/M103 (Python executables) ──► fatc.sock    │
│                                                          │
│   HAL layer                                              │
│     fatc component (loadusr) ◄── HAL pins                │
│       └── Unix socket server (fatc.sock)                 │
│       └── USB serial ──────────────────► Marlin          │
│                                          (carousel axes) │
│     Mesa 7i80 (real) / timedelay sim                     │
│       └── drawbar solenoid + clamped/unclamped sensors   │
└──────────────────────────────────────────────────────────┘
```

### 4.2 fatc HAL Component (`linuxcnc/atc/fatc/fatc.py`)

Loaded via `loadusr` in `fatc_sim.hal`:
```hal
loadusr -Wn fatc bash fatc_launch.sh
```

**HAL Pins (all prefixed `fatc.`):**

| Pin | Dir | Type | Description |
|-----|-----|------|-------------|
| `state` | OUT | S32 | Current state enum (see §4.3) |
| `is-homed` | OUT | BIT | Carousel has been homed |
| `current-pocket` | OUT | S32 | Currently active pocket number |
| `tool-in-spindle` | OUT | S32 | Tool number fatc believes is in spindle |
| `error` | OUT | BIT | Error active |
| `error-code` | OUT | S32 | Error code enum (see §4.4) |
| `ready` | OUT | BIT | IDLE and no error |
| `busy` | OUT | BIT | Tool change in progress |
| `marlin-connected` | OUT | BIT | Serial link to Marlin is up |
| `marlin-x-position` | OUT | FLOAT | Last known Marlin X position (mm) |
| `marlin-c-position` | OUT | FLOAT | Last known Marlin C position (degrees) |
| `program-stop` | OUT | BIT | Asserted on error → wired to `halui.program.stop` |
| `spindle-occupied` | OUT | BIT | Tool present in spindle (sense or tracking) |
| `carousel-retracted` | OUT | BIT | Carousel is in stowed/retracted position |
| `carousel-extended` | OUT | BIT | Carousel is in extended position |
| `error-reset` | IN | BIT | Pulse to clear ERROR state |
| `home-command` | IN | BIT | Rising edge triggers homing sequence |
| `machine-enabled` | IN | BIT | Machine is powered on (from `machine-is-enabled` net) |
| `abort` | IN | BIT | Rising edge aborts current sequence (from NOT of machine-enabled) |
| `tool-change` | IN | BIT | From `iocontrol.0.tool-change` (REMAP ack only) |
| `tool-changed` | OUT | BIT | To `iocontrol.0.tool-changed` (REMAP ack) |
| `tool-prep-number` | IN | S32 | From `iocontrol.0.tool-prep-number` |
| `tool-number` | IN | S32 | From `iocontrol.0.tool-in-spindle` (LC's current tool) |
| `tool-prepared` | OUT | BIT | To `iocontrol.0.tool-prepared` |
| `drawbar-activate` | OUT | BIT | Energize drawbar solenoid (unclamp) |
| `tool-clamped-sensor` | IN | BIT | Drawbar clamped sense (from real Mesa or sim) |
| `tool-unclamped-sensor` | IN | BIT | Drawbar unclamped sense |
| `spindle-tool-sense` | IN | BIT | Optional spindle presence sensor |
| `z-position` | IN | FLOAT | Z axis position feedback |
| `z-engage-request` | OUT | BIT | (unused in current IPC design) |
| `z-clear-request` | OUT | BIT | (unused in current IPC design) |

### 4.3 State Machine

```
INIT(0) → CONNECTING(1) → IDLE(2)
                               ↓ BEGIN cmd (IPC)
                          STOW_ROTATE(10) → STOW_EXTEND(11) → STOW_WAIT_Z(12)
                               → STOW_UNCLAMP(13) → STOW_WAIT_Z_CLEAR(14) → STOW_RETRACT(15)
                               → LOAD_ROTATE(20) → LOAD_EXTEND(21) → LOAD_WAIT_Z(22)
                               → LOAD_CLAMP(23) → LOAD_WAIT_Z_CLEAR(24) → LOAD_RETRACT(25)
                               → IDLE(2)
                          HOMING(3) → IDLE(2)
                          any state → ERROR(99) on fault
```

For **stow-only (T0 M6)**: STOW_RETRACT → `_sequence_complete()` → IDLE (skips load phase).
For **load-only (spindle empty, T>0 M6)**: IDLE → LOAD_ROTATE (skips stow phase).

State machine processes in the `loadusr` thread at ~50 Hz poll rate.

### 4.4 Error Codes

| Code | Value | Meaning |
|------|-------|---------|
| NONE | 0 | No error |
| SERIAL_LOST | 1 | Marlin USB connection dropped |
| MOVE_TIMEOUT | 2 | Marlin move did not complete |
| HOME_TIMEOUT | 3 | Homing sequence timed out |
| DRAWBAR_TIMEOUT | 4 | Drawbar sensor did not confirm in time |
| SENSOR_MISMATCH | 5 | Sensor state contradicts expected state |
| NO_EMPTY_POCKET | 6 | Cannot stow: no empty pocket found |
| TOOL_NOT_FOUND | 7 | Requested tool not in carousel inventory |
| ABORTED | 8 | Machine disabled/e-stopped during sequence |

On ERROR: `fatc.error=TRUE`, `fatc.program-stop` pulsed HIGH (→ `halui.program.stop`).
Recovery: pulse `fatc.error-reset` HIGH.

### 4.5 IPC Protocol (Unix Socket `/tmp/fatc.sock`)

JSON newline-delimited messages. Two categories:

**Sequencer commands** (called only from M101/M102/M103 during toolchange.ngc):

| Command | Sent by | fatc blocks until | Timeout |
|---------|---------|-------------------|---------|
| `{"cmd":"BEGIN","tool":N}` | M101 | Carousel extended (READY_FOR_Z) | 120 s |
| `{"cmd":"Z_ENGAGED"}` | M102 | Drawbar op complete (DRAWBAR_DONE) | 60 s |
| `{"cmd":"Z_CLEAR"}` | M103 | Sequence complete (COMPLETE) | 120 s |

**Management commands** (called from GUI at any time; handled synchronously, do not interact with state machine):

| Command | Description |
|---------|-------------|
| `{"cmd":"HOME"}` | Home the carousel (blocks until done, 120 s timeout) |
| `{"cmd":"GET_INVENTORY"}` | Returns `pocket_map`, `tool_in_spindle`, `inventory_valid` |
| `{"cmd":"SET_POCKET","pocket":N,"tool":T}` | Assign tool T to pocket N (0=empty) |
| `{"cmd":"SET_SPINDLE","tool":T}` | Declare tool T is in spindle |
| `{"cmd":"SET_INVENTORY_VALID","valid":true/false}` | Mark inventory as trusted |

All responses: `{"ok":true}` or `{"ok":false,"error":"message"}`.

### 4.6 M6 Remap — `toolchange.ngc`

The M6 remap uses `change_prolog` / `change_epilog` from `python/stdglue.py`.
The NGC subroutine is the "thin sequencer" — it only handles Z motion and
IPC handshaking. All carousel logic is in `fatc.py`.

```ngc
o<toolchange> sub
; bail if task=0 (preview) or selected_tool==tool_in_spindle
G53 G0 Z0                          ; raise Z to machine zero
M101 P#<selected_tool>             ; BEGIN (blocks until carousel READY_FOR_Z)
                                   ; fatc decides stow vs load based on tool_in_spindle

o100 if [#<tool_in_spindle> GT 0]  ; STOW PHASE
    G53 G0 Z#<_ini[ATC]TC_HEIGHT>  ; lower Z to tool-change height (-1.5 in sim)
    M102                           ; Z_ENGAGED stow (blocks until DRAWBAR_DONE)
    ; DEBUG EVAL: dynatc.store_tool(stow_pocket, tool_in_spindle)
    G53 G0 Z0                      ; retract Z
    M103                           ; Z_CLEAR stow (blocks until READY_FOR_Z for load, or COMPLETE for T0)
o100 endif

o200 if [#<selected_tool> GT 0]    ; LOAD PHASE
    G53 G0 Z#<_ini[ATC]TC_HEIGHT>
    M102                           ; Z_ENGAGED load (blocks until DRAWBAR_DONE)
    ; DEBUG EVAL: dynatc.store_tool(load_pocket, 0) — clear pocket in widget
    G53 G0 Z0
    M103                           ; Z_CLEAR load (blocks until COMPLETE)
o200 endif
o<toolchange> endsub [1]
```

`TC_HEIGHT = -1.5` (G53 Z) and `Z_TOOL_CLEARANCE_HEIGHT = 0.0` are set in `[ATC]` section of `atc_sim.ini`.

### 4.7 User M-codes (M101, M102, M103)

Python executables in `subroutines/`. Each opens a new Unix socket connection to
`/tmp/fatc.sock`, sends the appropriate JSON command, waits for a JSON response,
then exits 0 (ok) or 1 (error). LinuxCNC's interpreter blocks for the entire
duration of each M-code call. Timeouts: M101=120s, M102=60s, M103=120s.

### 4.8 Sim Config HAL Wiring (`atc.sim/hallib/`)

**`fatc_sim.hal`** (key nets):
```hal
net tool-change-request    iocontrol.0.tool-change  =>  fatc.tool-change
net tool-change-confirmed  fatc.tool-changed         =>  iocontrol.0.tool-changed
net tool-number            iocontrol.0.tool-prep-number => fatc.tool-prep-number
net tool-in-spindle-lc     iocontrol.0.tool-in-spindle  => fatc.tool-number
net fatc-program-stop      fatc.program-stop         =>  halui.program.stop
```

**`probe_basic_postgui.hal`** (key):
```hal
net machine-is-enabled  =>  not.2.in
net fatc-abort-signal   not.2.out  =>  fatc.abort
# Drawbar simulation (timedelay):
setp drawbar-delay.on-delay  1.5   # unclamp time
setp drawbar-delay.off-delay 5.0   # clamp time
net sim-drawbar-out     fatc.drawbar-activate  =>  drawbar-delay.in
net sim-drawbar-uncl    drawbar-delay.out      =>  fatc.tool-unclamped-sensor
net sim-drawbar-clamped not.1.out              =>  fatc.tool-clamped-sensor
```

`motion.feed-hold` is **not connected** to anything.

### 4.9 Probe Basic ATC Tab (`atc/fatc_atc/fatc_atc.py`)

`class Atc(QWidget)` loaded by Probe Basic's `load_atc()`. Requires patched
`probe_basic.py` (see §4.10).

**UI layout:**
- Left column: DynATC carousel widget (visual tool inventory)
- Right column: status labels (fatc state, homed, error, Marlin connection, current pocket, tool in spindle) + error frame + buttons

**Buttons and their actions:**

| Button | Label | Action |
|--------|-------|--------|
| REF CAROUSEL | HOME | Sends `HOME` IPC in a QThread (non-blocking to Qt); enables during `fatc_idle and machine_on and interp_idle` |
| LOAD SPINDLE | text field + button | Issues `T{n} M6` via `issue_mdi()`; enables when homed + tool_num_valid |
| STORE TOOL IN CAROUSEL | STORE | Issues `T0 M6` via `issue_mdi()` (with M61 sync if LC tool≠fatc tool); enables when homed |
| UNLOAD SPINDLE | UNLOAD | Issues `M61 Q0`, `G49` via `issue_mdi()`, then `SET_SPINDLE tool=0` IPC |
| RESET ERROR | RESET | Pulses `fatc.error-reset` HAL pin |

**Poll loop** (200ms QTimer):
- Reads `fatc.state`, `fatc.is-homed`, `fatc.error`, `fatc.error-code`, `fatc.current-pocket`, `fatc.tool-in-spindle`, `fatc.marlin-connected` via `hal.get_value()`
- If `fatc.current-pocket` changed, calls `_rotate_dynatc_to_pocket()` to animate carousel
- Calls `_update_button_states()` (gates on `machine_on and interp_idle and fatc_idle and is_homed`)

**`_on_store_tool()` logic:**
1. Read `stat.tool_in_spindle` from LinuxCNC
2. If LC shows T0: fall back to `hal.get_value('fatc.tool-in-spindle')`
3. If still 0: nothing to do, return
4. If LC shows T0 but fatc shows T_n: issue `M61 Q{n}; T0 M6` (sync LC first to bypass toolchange.ngc early-exit guard)
5. If LC shows T_n: issue `T0 M6`

### 4.10 Probe Basic Patch (`/home/steve/dev/probe_basic/src/probe_basic/probe_basic.py`)

`load_atc()` was patched to support config-local ATC modules:

```python
# Reads [DISPLAY] ATC_USER_PATH (relative to INI dir or absolute)
# Reads [DISPLAY] ATC_SKIP_BUILTIN_MODULES = 1 (suppresses template_atc)
atc_paths = [os.path.join(VCP_DIR, "atc")]
user_atc_path = INIFILE.find("DISPLAY", "ATC_USER_PATH")
if user_atc_path:
    if not os.path.isabs(user_atc_path):
        ini_dir = os.path.dirname(os.path.abspath(os.environ.get("INI_FILE_NAME", "")))
        user_atc_path = os.path.join(ini_dir, user_atc_path)
    atc_paths.append(user_atc_path)
skip_builtin = int(INIFILE.find("DISPLAY", "ATC_SKIP_BUILTIN_MODULES") or 0)
if skip_builtin:
    atc_paths = atc_paths[1:]
```

In `atc_sim.ini` `[DISPLAY]` section:
```ini
ATC_USER_PATH = atc
ATC_SKIP_BUILTIN_MODULES = 1
```

---

## 5. Persistent State (`fatc_state.json`)

Written to the INI directory at runtime. Format:

```json
{
  "version": 1,
  "tool_in_spindle": 0,
  "current_pocket": 1,
  "inventory_valid": false,
  "pocket_map": {"1":1,"2":2,"3":3,"4":0,...,"12":0},
  "calibration": {"linear_offset":0.0,"pocket_offsets":{}}
}
```

`pocket_map` keys are pocket numbers (strings); values are tool numbers (0=empty).

> **IMPORTANT:** fatc writes this file continuously during operation. The file
> is always overwritten with the live state when LinuxCNC/fatc runs. Always
> reset it to a known-clean state before a test run — it will not be
> automatically reset on startup.

**Reset command:**
```bash
cat > /home/steve/devel/frankenmill/linuxcnc/configs/atc.sim/fatc_state.json << 'EOF'
{"version":1,"tool_in_spindle":0,"current_pocket":1,"inventory_valid":false,
 "pocket_map":{"1":1,"2":2,"3":3,"4":0,"5":0,"6":0,"7":0,"8":0,"9":0,"10":0,"11":0,"12":0},
 "calibration":{"linear_offset":0.0,"pocket_offsets":{}}}
EOF
```

---

## 6. `atc_sim.ini` Key Settings

```ini
[ATC]
POCKETS       = 12
STEP_TIME     = 500
TC_HEIGHT     = -1.5        ; G53 Z for drawbar engagement
Z_TOOL_CLEARANCE_HEIGHT = 0.0

[DISPLAY]
DISPLAY       = probe_basic
ATC_USER_PATH = atc
ATC_SKIP_BUILTIN_MODULES = 1

[RS274NGC]
REMAP = M6 modalgroup=6 prolog=change_prolog ngc=toolchange epilog=change_epilog
REMAP = M13 modalgroup=10 ngc=m13
USER_M_PATH = subroutines
SUBROUTINE_PATH = subroutines
PYTHON_PATH = python
PYTHON_STARTUP = toplevel
```

---

## 7. Marlin Hardware / Firmware

- **Board:** RAMPS Creality (ATmega2560), running `bugfix-2.1.x`
- **X axis:** Linear extend/retract (mm)
- **C axis:** Carousel rotation (degrees), `AXIS4_ROTATES`
- **Homing:** `G28 XC` — X homes to X-min (pin 3), C homes to Z-min (pin 18)
- **Build:** `cd linuxcnc/atc/marlin && ./build.sh`
- **Connection:** `/dev/ttyUSB0` @ 115200 baud
- **Sim:** In sim config, real Marlin hardware connects over USB — serial layer is tested against real firmware

---

## 8. Known Open Issues (as of 2026-05-06)

### 8.1 STORE TOOL IN CAROUSEL hangs

**Symptom:** Clicking STORE TOOL issues `T0 M6` (or `M61 Q{n}; T0 M6`). The LinuxCNC
interpreter enters MDI→READING mode and then does not return to IDLE. fatc completes
its STOW sequence (state returns to IDLE, log shows "Tool change complete"), but the
interpreter stays in exec_state=WAIT_SYSCMD (8) or WAIT_DELAY (7) indefinitely.

**Observed facts (from watch_state.py and lcstat.py during a successful LOAD T2):**
- LOAD T2 completes cleanly: `iocontrol.0.tool-change` pulses TRUE, `fatc.tool-changed`
  acks immediately, both return FALSE, LC goes IDLE — entire handshake in < 200ms
- STORE TOOL watch trace not yet captured (watch_state was not running during the hang)
- After a hang: `interp_state=2 (READING)`, `exec_state=7`, `current_line=101`,
  `feed_hold_enabled=True`, `tool_in_spindle=1`, `task_mode=3 (MDI)`
- Line 101 of `toolchange.ngc` is `G53 G0 Z0` (the Z retract after load)
- fatc.log shows fatc completed and returned to IDLE at 16:08:01 — the interpreter
  was still stuck 13 minutes later
- `fatc.program-stop=FALSE`, `motion.feed-hold=FALSE` — no HAL signal is asserting a hold
- `feed_hold_enabled=True` is the default LinuxCNC startup state (M53 P1) and does
  not cause motion holds unless `motion.feed-hold` is also TRUE — this is a red herring

**Working hypothesis:** The hang may be occurring inside `change_epilog` (the M6 remap
epilog in `python/stdglue.py`), which runs after `toolchange.ngc` returns. The epilog
calls `emccanon.CHANGE_TOOL()` and `yield INTERP_EXECUTE_FINISH`. In LinuxCNC 2.9.8,
this is known to sometimes stall waiting for motion queue flush if there is a
preceding motion command still in the queue. The `G53 G0 Z0` at line 101 followed
immediately by M103 (a blocking system call) may leave the motion queue in an
unexpected state.

**Not yet confirmed:** Whether the hang occurs specifically on STORE TOOL (T0 M6) or
also on any repeated tool change. A fresh watch_state trace covering the entire
STORE TOOL sequence is needed.

### 8.2 DynATC pocket not cleared after LOAD SPINDLE

**Symptom:** After loading a tool, the DynATC widget in the fatc_atc.py tab still
shows the tool in its pocket. The `(DEBUG, EVAL[...])` call in `toolchange.ngc`
is supposed to call `dynatc.store_tool(pocket, 0)` to clear it.

**Probable cause:** The NGC `(DEBUG, EVAL[vcp.getWidget{"dynatc"}.store_tool{...}])` 
uses the object name `"dynatc"` to look up the widget via `app.getWidget("dynatc")`.
The DynATC instance in `fatc_atc.py` is accessed as `self.dynatc` but may not be
registered in QtPyVCP's widget registry under that name, or may be a different
instance from the one registered.

**Not yet confirmed:** Whether `getWidget("dynatc")` returns the fatc_atc DynATC
instance or a different one (or None). Needs a debug `PRINT` or log check.

### 8.3 LOAD sometimes short-circuits (no ATC motion)

**Symptom:** After a LC restart, clicking LOAD SPINDLE returns immediately
(< 500ms) with no fatc state changes and no carousel motion.

**Probable cause:** `toolchange.ngc` has an early-exit guard:
```ngc
o20 if [#<selected_tool> EQ #<tool_in_spindle>]
    o<toolchange> endsub [1]
o20 endif
```
If LinuxCNC's `#<tool_in_spindle>` already matches the requested tool (e.g., because
a previous session left a `linuxcnc.var` file with that tool recorded), the guard fires
and fatc never receives a BEGIN command.

**Workaround:** Reset `fatc_state.json` before each test session AND verify that
`lcstat.py` shows `tool_in_spindle: 0` before issuing LOAD. If LC starts with a
non-zero tool (from var file), the early-exit will fire for that tool number.

### 8.4 Z/ATC motion overlap

**Symptom:** User observed Z motion and ATC motion appearing to overlap during
LOAD SPINDLE.

**Status:** Not yet investigated. The toolchange.ngc design requires strict sequencing
(Z must reach TC_HEIGHT before M102 is called), so this may be a visual artifact
rather than actual concurrent motion.

---

## 9. Testing Infrastructure

### `watch_state.py`

Polls LinuxCNC stat + HAL pins every 100ms, prints only changed values.
Run: `python3 watch_state.py 2>&1 | tee /tmp/ws.log`

Monitored HAL pins:
- `motion.feed-hold`, `fatc.program-stop`, `fatc.state`, `fatc.is-homed`
- `fatc.error`, `fatc.error-code`
- `halui.program.stop`, `halui.program.is-paused`, `halui.program.is-idle`
- `iocontrol.0.tool-change`, `iocontrol.0.tool-changed`, `iocontrol.0.tool-prep-number`
- `fatc.tool-change`, `fatc.tool-changed`

Also monitors: `interp_state`, `exec_state`, `current_line`, `task_mode`, `feed_hold_enabled`, `paused`, `task_state`.

### `lcstat.py`

One-shot snapshot of LC stat + HAL pins. Run any time while LC is running.

### `fatc.log`

fatc daemon stdout/stderr, appended by `fatc_launch.sh`.
DEBUG level by default. Timestamped. Shows all IPC traffic and state transitions.

---

## 10. Resolved Design Decisions

### Q1: Z-Axis Coordination
**"Thin Sequencer" M6 remap using User M-codes.** Implemented via M101/M102/M103
Python executables that block the LinuxCNC interpreter via Unix socket IPC.

### Q2: Drawbar Control
**Mesa 7i80 GPIO.** `fatc.drawbar-activate` OUT pin drives solenoid. Sensor inputs
`fatc.tool-clamped-sensor` / `fatc.tool-unclamped-sensor`.

### Q3: Marlin Axis Mapping
X = linear extend/retract (mm). C = carousel rotation (degrees, AXIS4_ROTATES).

### Q4: Development Approach
Hybrid: real Marlin hardware over USB-serial + LinuxCNC simulator.

### Q5: GUI Integration
Probe Basic ATC tab via `fatc_atc.py` module. HAL pins + IPC for status.

### Q6: Auto-Home
Manual only via REF CAROUSEL button (or `fatc.home-command` HAL pin).

### Q7: Component-to-GUI IPC
Unix domain socket `/tmp/fatc.sock` with JSON messages. Implemented.

---

## 11. Marlin Firmware Status

- **Branch:** `bugfix-2.1.x` (git submodule at commit `25a0a88`)
- **Board:** `BOARD_RAMPS_CREALITY` (ATmega2560)
- **Build:** SUCCESS — RAM 34.7%, Flash 19.1%
- **Build command:** `cd linuxcnc/atc/marlin && ./build.sh`
- **Upload:** `./build.sh upload --upload-port /dev/ttyUSB0`

Key config:
- Zero extruders, zero temp sensors, thermal protection disabled
- `CUSTOM_MACHINE_NAME "FMX3-ATC"`, baud 115200
- `I_DRIVER_TYPE TMC2208_STANDALONE`, `AXIS4_NAME 'C'`, `AXIS4_ROTATES`
- `I_STOP_PIN 18` (Z-min connector for C homing)
- Steps: `{ 80, 80, 400, 160 }` (4th = steps/degree for C)
- Max feedrate: `{ 750, 5, 5, 750 }`

