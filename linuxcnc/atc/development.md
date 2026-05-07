# FrankenMill ATC — Development Approach

> **Status as of 2026-05-06:** Phases 1–5 are implemented and running in sim.
> Phase 6 (Probe Basic GUI) is partially implemented (`fatc_atc.py` exists,
> is loaded, and is functional for LOAD/STORE). Two open bugs remain before
> the GUI tab can be called complete — see CONTEXT.md §8.

## 1. Overview

The `fatc` component will be developed incrementally using a **hybrid
environment**: a LinuxCNC simulator for the host-side HAL/G-code integration,
combined with real Marlin hardware (RAMPS + dummy motors) for the serial
protocol and motion control layer.

This document describes the development environment, phasing, and testing
strategy.

---

## 2. Development Environment

### 2.1 Host: Dev Workstation

- **LinuxCNC 2.9.8** in simulator mode (no real-time kernel required)
- **Probe Basic / QtPyVCP** — dev install at `/home/steve/dev/probe_basic/` and `/home/steve/dev/qtpyvcp/`
- Sim config: `linuxcnc/configs/atc.sim/` (in this repo — NOT in `pico-cnc-hmi`)
- Python 3.x for `fatc` component development
- Marlin serial connection via USB (`/dev/ttyUSB0`)

### 2.2 Marlin Hardware (Real)

- RAMPS Creality board (ATmega2560) with dummy stepper motors
- Firmware: `bugfix-2.1.x` with ATC config (see `marlin/`)
- X axis: dummy motor (linear motion)
- C axis: dummy motor (carousel rotation)
- Endstop switches: X-min (pin 3), C/I homing (pin 18, Z-min connector)
- USB serial @ 115200 baud

### 2.3 Test Bench Constraints

The Marlin test bench has **no endstop switches wired** and uses dummy motors
with no mechanical load. This is fine for development:

- **Serial protocol** is fully testable — command/response parsing, timeouts,
  error handling, reconnection, `BUSY:processing` behavior
- **Motor motion** is observable (motors spin) but positions are not meaningful
- **Homing (`G28`) will not work** — use relative moves (`G91`) or disable
  software endstops (`M211 S0`) during development
- **Absolute positioning** is not needed — dummy motors don't control anything
- **No Mesa GPIO sensors** — drawbar-clamped, drawbar-unclamped,
  tool-in-spindle, pocket-has-tool are all absent on the test bench
- **No drawbar solenoids** — clamp/unclamp outputs have no physical actuator

All sensor inputs will be **simulated via HAL stubs** (see section 2.5).
The `fatc` component sees HAL pins regardless of whether real hardware or
loopback stubs are behind them — this is the key advantage of HAL-based I/O.

Homing and absolute positioning are deferred to real-machine integration.
The `fatc` component should support a config flag (e.g., `homing_required`)
to allow operation without homing for development.

### 2.4 The Hybrid Model

```
┌──────────────────────────┐     ┌──────────────────────┐
│   LinuxCNC Simulator     │     │  Marlin Hardware      │
│                          │     │                       │
│  ┌────────────────────┐  │     │  RAMPS + dummy motors │
│  │ Probe Basic GUI    │  │     │  X axis (linear)      │
│  │ (sim mode)         │  │     │  C axis (rotation)    │
│  └────────┬───────────┘  │     │  Endstop switches     │
│           │ HAL          │     └───────────┬───────────┘
│  ┌────────┴───────────┐  │         USB     │
│  │ fatc component     │──│─────────────────┘
│  │ (loadusr Python)   │  │     Serial @ 115200
│  └────────┬───────────┘  │
│           │ HAL          │
│  ┌────────┴───────────┐  │
│  │ M6 remap / G-code  │  │
│  │ (thin sequencer)   │  │
│  └────────────────────┘  │
└──────────────────────────┘
```

**What's simulated:**
- LinuxCNC machine axes (X, Y, Z) — no real motion
- Spindle on/off
- Mesa GPIO (drawbar solenoids, clamp sensors) — stubbed via HAL
- All supplemental sensors (tool-in-spindle, pocket-has-tool) — HAL stubs

**What's real:**
- Marlin serial protocol — actual USB connection
- Stepper motion on dummy motors — validates timing, acceleration
- Command/response flow and error handling

> [!IMPORTANT]
> The fatc component talks to Marlin over a real serial port even in sim mode.
> This means serial protocol development is tested against real firmware from
> day one — no mocking needed for the most complex integration boundary.

### 2.5 Simulated I/O (Mesa GPIO Stubs)

On the real machine, drawbar solenoids and sensors are wired to Mesa
7i80 GPIO. In sim mode, these are replaced with HAL signal stubs that
provide immediate "happy path" responses:

```hal
# --- Drawbar ---
# Loopback: clamp command instantly reports clamped
net sim-drawbar-clamp    fatc.drawbar-clamp-cmd   => fatc.drawbar-clamped
net sim-drawbar-unclamp  fatc.drawbar-unclamp-cmd => fatc.drawbar-unclamped

# --- Tool-in-spindle sensor ---
# Directly driven by fatc's own tool-tracking state
# (or tied HIGH to simulate "tool always present")
sets fatc.spindle-has-tool true

# --- Pocket sensor ---
# Tied HIGH to simulate "pocket occupied" for all positions
sets fatc.pocket-has-tool true
```

These stubs let the state machine run its full sequence. To test error
paths (e.g., "sensor disagrees with expected state"), individual stubs
can be toggled manually via `halcmd sets` during development.

> [!NOTE]
> These stubs are deliberately simple (instant loopbacks + manual toggles).
> A full behavioral hardware simulator (with realistic timing, state coupling,
> and failure injection) would approach the complexity of the `fatc` component
> itself. Real sensor integration testing happens on real hardware.

---

## 3. Development Phases

### Phase 1: Serial Foundation

**Goal:** Reliable bidirectional Marlin communication.

- [x] Serial port open/close, baud rate config
- [x] G-code send with `ok` response parsing
- [x] Error response handling (`echo:`, `Error:`)
- [x] Command queue with timeout/retry
- [x] `M115` firmware identification on connect
- [ ] `G28 X C` homing sequence — deferred to real machine (no endstops on test bench)
- [x] Basic `G0 X<pos>` and `G0 C<angle>` motion commands
- [x] `M114` position query and parsing
- [ ] Startup/reconnection logic — basic connect on startup done; auto-reconnect on loss deferred

**Test method:** Direct Python script → Marlin hardware. No LinuxCNC needed
yet. Validate with real serial responses from real firmware.

### Phase 2: HAL Component Skeleton

**Goal:** `fatc` loads as a LinuxCNC `loadusr` component with HAL pins.

- [x] Component entry point (`fatc.py`)
- [x] INI file configuration loading (serial port, pocket count, etc.)
- [x] HAL pin creation (per section 5.4 of requirements)
- [x] Main loop: poll HAL pins → update state → write HAL pins
- [x] Serial connection management (connect on startup)
- [x] Basic status reporting via HAL pins
- [x] Persistent state file load/save (`fatc_state.json`)

**Test method:** `loadusr` in LinuxCNC sim. Monitor pins with `halshow`
/ `halmeter`. Marlin hardware connected.

### Phase 3: State Machine Core

**Goal:** Complete tool-change state machine (manual trigger via HAL pin).

- [x] State machine framework (enum states, transition validation)
- [x] Tool-change sequence: stow + load cycle working end-to-end in sim
- [x] Tool-in-spindle tracking with persistent state
- [x] Error state entry on fault conditions (`TOOL_NOT_FOUND`, timeouts)
- [x] Drawbar timeout detection with `_state_entered_at` timer
- [x] Abort handling via `fatc.abort` pin (rising edge, armed after first IDLE)
- [ ] Carousel pocket sensor integration — deferred to real hardware
- [ ] Configurable homing behavior — deferred to real machine
- [ ] Parallel motion (carousel rotate while Z clears) — deferred

### Phase 4: M6 Remap Integration

**Goal:** LinuxCNC `T<n> M6` triggers the full ATC sequence via User M-codes and Unix socket IPC.

**Approach:** User M-codes (M101–M103) replace the previous M66/M68 HAL pin
handshake. Each M-code is a small executable Python script on `SUBROUTINE_PATH`
that connects to a Unix domain socket served by `fatc.py`, sends a JSON command,
waits for a JSON response, and exits (0=ok, 1=error). The interpreter blocks
for the entire duration of each call, eliminating all timing/edge-detection races.

**Socket server in fatc.py:**
- `socketserver.ThreadingUnixStreamServer` on `/tmp/fatc.sock` (path in INI)
- Each connection handled in its own thread; posts a command to the state machine
  via a `threading.Event` / queue and waits for the reply
- Socket server starts after HAL `h.ready()` so pins exist before any M-code fires

**M-code scripts (one file per code, executable, on `SUBROUTINE_PATH`):**

| File  | Command sent | Fatc blocks until              | P arg         |
|-------|--------------|--------------------------------|---------------|
| `M101`| `BEGIN`      | Carousel extended (READY_FOR_Z)| selected_tool |
| `M102`| `Z_ENGAGED`  | Drawbar op complete            | —             |
| `M103`| `Z_CLEAR`    | Full sequence complete or error| —             |

**`toolchange.ngc` structure:**
```gcode
G53 G0 Z0                          ; raise Z
M101 P#<selected_tool>             ; BEGIN (blocks)
; stow phase (if tool_in_spindle > 0):
;   G53 G0 Z#<_ini[ATC]TC_HEIGHT>
;   M102  (Z_ENGAGED stow — blocks)
;   G53 G0 Z0
;   M103  (Z_CLEAR stow — blocks)
; load phase:
G53 G0 Z#<_ini[ATC]TC_HEIGHT>
M102                               ; Z_ENGAGED load (blocks)
G53 G0 Z0
M103                               ; Z_CLEAR load / COMPLETE (blocks)
o<toolchange> endsub [1]
```

**HAL changes:** Remove `remap-cmd`, `remap-cmd-code`, `remap-tool-num`,
`remap-ack`, `remap-ack-code` pins and all associated `motion.analog-out-*` /
`motion.digital-out-*` wiring. `num_aio` / `num_dio` can revert to defaults.

**Checklist:**
- [x] Unix socket server added to `fatc.py` (starts after `h.ready()`)
- [x] State machine command/response queue wired to socket handler thread
- [x] `M101`, `M102`, `M103` scripts written, `chmod +x`, on `SUBROUTINE_PATH`
- [x] `toolchange.ngc` rewritten using M101–M103
- [x] Remap HAL pins removed from `fatc.py` and `fatc_sim.hal`
- [x] `atc_sim.ini` `num_aio`/`num_dio` reverted to defaults
- [x] Z-axis coordination verified (sim loopback): load-only and stow+load cycles tested
- [ ] Pre-dock parallel motion (carousel rotate while Z travels) — deferred
- [ ] Tool table synchronisation — deferred
- [x] Abort mid-sequence: `_enter_error` unblocks active IPC cmd and drains queue; M-code exits non-zero; LinuxCNC raises fault

**Test method:** Run G-code programs in LinuxCNC sim with `T1 M6`, `T2 M6`,
etc. Verify full sequence including simulated Z motion and real Marlin motion.

### Phase 4.5: Inventory IPC Commands

**Goal:** Operator can declare and query tool/pocket assignments via Unix socket,
without needing direct file access.

- [x] `GET_INVENTORY` — returns pocket_map, tool_in_spindle, inventory_valid flag
- [x] `SET_POCKET pocket=N tool=T` — assign tool T to pocket N (0 = empty)
- [x] `CLEAR_INVENTORY` — zero all pockets, clear tool_in_spindle, mark invalid
- [x] `SET_SPINDLE tool=T` — declare what tool is in the spindle
- [x] `SET_INVENTORY_VALID valid=true/false` — mark inventory as trusted
- [x] Management commands handled synchronously in socket handler thread (no state machine interaction)
- [x] All management commands call `state.save()` immediately for durability

**Test method:**
```bash
# Quick one-liner socket client:
python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX)
s.connect('/tmp/fatc.sock')
s.sendall(json.dumps({'cmd': 'GET_INVENTORY'}).encode() + b'\n')
print(json.loads(s.recv(4096)))
"
# Set pocket 3 = T5:
# {'cmd': 'SET_POCKET', 'pocket': 3, 'tool': 5}
# Clear all:
# {'cmd': 'CLEAR_INVENTORY'}
```

### Phase 5: Safety & Error Recovery

**Goal:** Robust error handling and operator recovery.

- [x] Drawbar timeout detection: `_state_entered_at` recorded on every `_transition()`; `STOW_UNCLAMP` and `LOAD_CLAMP` enter `ERROR(DRAWBAR_TIMEOUT)` if sensor doesn't confirm within `DRAWBAR_UNCLAMP_TIMEOUT` / `DRAWBAR_CLAMP_TIMEOUT`
- [x] De-energise solenoid on unclamp timeout (fail-safe: drawbar stays clamped)
- [x] Drawbar sim delay: `timedelay` HAL component (0.5 s on/off) makes timeout path testable
- [ ] Timeout detection (Marlin response, motion completion) — move timeout exists; sensor/comms timeouts in progress
- [ ] Sensor disagreement detection (expected vs actual pocket state)
- [ ] E-stop integration (Marlin power cut via hardwired relay)
- [ ] Pause-and-recover architecture (operator can fix and resume)
- [x] Error code reporting via HAL pins (`fatc.error-code`)
- [ ] State recovery after power cycle (from persistent state file)
- [ ] Air blast integration

**Test method:** Inject faults (disconnect USB, block endstop, etc.) and
verify graceful error handling and recovery paths.

### Phase 6: GUI Integration & Polish

**Goal:** Visual feedback and operator-friendly interface.

**Status: Partially implemented.**

- [x] `fatc_atc.py` — Probe Basic ATC tab module (`class Atc(QWidget)`)
  - DynATC carousel widget with HAL-driven rotation animation
  - Status labels: fatc state, homed, error, Marlin connection, current pocket, tool in spindle
  - Buttons: REF CAROUSEL, LOAD SPINDLE, STORE TOOL, UNLOAD SPINDLE, RESET ERROR
  - 200ms poll loop updating all status from HAL pins and IPC
  - Button state gating: machine_on + interp_idle + fatc_idle + is_homed
- [x] `probe_basic.py` patched — `load_atc()` supports `ATC_USER_PATH` and `ATC_SKIP_BUILTIN_MODULES` INI keys
- [x] LOAD SPINDLE: `T{n} M6` via `issue_mdi()` — confirmed working in sim
- [ ] **OPEN BUG:** STORE TOOL hangs — `T0 M6` MDI issued but interpreter does not return to IDLE after sequence completes (see CONTEXT.md §8.1)
- [ ] **OPEN BUG:** DynATC pocket not cleared after LOAD — NGC `DEBUG EVAL getWidget` may not reach the correct widget instance (see CONTEXT.md §8.2)
- [ ] Per-pocket calibration wizard — future
- [ ] Error recovery UI beyond current RESET ERROR button — future

---

## 4. File Layout (Actual)

```
linuxcnc/atc/
├── CONTEXT.md               # ★ Read first — full implementation context
├── requirements.md          # Architectural requirements
├── development.md           # This file — phases and status
├── README.md                # Project overview
├── fatc/                    # Python component source
│   ├── fatc.py              # Main entry point: HAL component + state machine + IPC server
│   ├── serial_marlin.py     # Marlin USB-serial protocol layer
│   ├── serial_thread.py     # Background thread: serial commands, M114 heartbeat
│   ├── config.py            # INI config loading
│   └── persistent_state.py  # JSON tool-pocket map (fatc_state.json)
└── marlin/                  # Marlin firmware (built and flashed)
    ├── Configuration.h
    ├── Configuration_adv.h
    ├── platformio.ini
    ├── build.sh
    ├── README.md
    └── Marlin/              # Git submodule (bugfix-2.1.x)

linuxcnc/configs/atc.sim/    # ★ Active sim+dev config
├── atc_sim.ini              # LinuxCNC config (Probe Basic, REMAP, ATC settings)
├── fatc_launch.sh           # loadusr wrapper (logs to fatc.log)
├── fatc_state.json          # Persistent state (written at runtime — reset before tests)
├── tool.tbl                 # LinuxCNC tool table
├── watch_state.py           # Diagnostic: streams LC stat + HAL pin changes
├── lcstat.py                # One-shot state snapshot
├── hallib/
│   ├── core_sim.hal         # Axis sim, iocontrol, tool-change nets
│   ├── fatc_sim.hal         # fatc wiring: tool-change handshake, program-stop
│   ├── spindle_sim.hal      # Simulated spindle encoder
│   └── probe_basic_postgui.hal  # Postgui: cycle timer, drawbar sim, abort
├── subroutines/
│   ├── toolchange.ngc       # M6 REMAP thin sequencer
│   ├── M101                 # BEGIN IPC (Python executable)
│   ├── M102                 # Z_ENGAGED IPC (Python executable)
│   ├── M103                 # Z_CLEAR IPC (Python executable)
│   ├── M104                 # HOME IPC (Python executable, called by m13.ngc)
│   └── m13.ngc              # REF CAROUSEL (calls M104)
├── python/
│   ├── remap.py             # Local remap additions
│   ├── stdglue.py           # change_prolog / change_epilog
│   └── toplevel.py          # Interpreter toplevel
└── atc/
    └── fatc_atc/
        └── fatc_atc.py      # Probe Basic ATC tab module
```


---

## 5. Development Conventions

- **Commits:** Small, focused commits. Each phase milestone = commit.
- **Testing:** Each phase has explicit test criteria before moving on.
- **Config-driven:** All tunable values (serial port, pocket count, feed rates,
  timeouts) in INI file, not hardcoded.
- **Logging:** Structured logging at DEBUG/INFO/WARN/ERROR levels. Critical
  for debugging the serial ↔ HAL ↔ G-code interaction in sim mode.
- **Pattern reference:** Follow `cmdrsk_vfd` driver architecture for the
  `loadusr` daemon pattern, INI config loading, and HAL pin conventions.

---

## 6. Resolved Questions

- **Sim config location:** `linuxcnc/configs/atc.sim/` in this repo. ✅
- **Phase 1 standalone serial:** Yes, done — `serial_marlin.py` tested stand-alone before HAL integration. ✅
- **Marlin BUSY handling:** `serial_thread.py` reads responses in a background thread; `BUSY:processing` lines are consumed but do not block the state machine. `ok` response signals command complete. ✅
- **M400 vs poll:** `ok` after the last queued move is sufficient; no explicit `M400` needed. ✅

