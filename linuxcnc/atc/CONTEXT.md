# FrankenMill ATC — Session Context Summary

> **Purpose:** This document captures the full context of the `fatc` (Franken-ATC)
> project so development can be continued by a different agent/platform. Read this
> first, then the referenced documents for details.
>
> **Date:** 2026-05-05
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
    ├── configs/fmx3.probebasic/     # Production LinuxCNC config (Mesa 7i80)
    │   ├── fmx3.ini
    │   ├── fmx3.hal
    │   └── subroutines/toolchange.ngc  # Legacy ATC G-code (reference only)
    └── atc/                          # ★ ATC subproject (active development)
        ├── README.md                 # Project overview
        ├── requirements.md           # Architectural requirements (57KB, comprehensive)
        ├── development.md            # Development approach & phasing
        └── marlin/                   # Marlin firmware
            ├── Configuration.h       # Custom config (tracked)
            ├── Configuration_adv.h   # Custom advanced config (tracked)
            ├── platformio.ini        # PlatformIO config
            ├── build.sh              # Overlay + build script
            ├── README.md             # Build instructions
            └── Marlin/               # Git submodule → MarlinFirmware/Marlin
```

**No `fatc` Python code exists yet.** Only docs and Marlin firmware config.

---

## 3. Key Documents (Read These)

| Document | Path | What It Contains |
|----------|------|------------------|
| **requirements.md** | `linuxcnc/atc/requirements.md` | Full architectural spec: component design, HAL pins, state machine, safety, M6 remap, error handling, Marlin protocol, all resolved design questions |
| **development.md** | `linuxcnc/atc/development.md` | Dev environment setup, hybrid sim+hardware approach, 6-phase build plan, planned file layout, test bench constraints |
| **marlin/README.md** | `linuxcnc/atc/marlin/README.md` | Marlin build instructions, axis mapping table |

---

## 4. Resolved Design Decisions (Q1–Q7)

### Q1: Z-Axis Coordination — RESOLVED
**"Thin Sequencer" M6 remap.** The LinuxCNC remap G-code handles only Z-axis
motion (safe height, return) and uses non-blocking `M68` (analog out) /
blocking `M66` (wait on input) to handshake with the `fatc` component.
Supports parallel motion (carousel rotating while Z travels to clearance).

### Q2: Drawbar Control — RESOLVED
**Mesa 7i80 GPIO.** Drawbar solenoid valves and clamp sensors wired to Mesa
I/O (HAL-controlled). Not routed through Marlin — avoids serial latency on
safety-critical I/O.

### Q3: Marlin Axis Mapping — RESOLVED & VERIFIED
**Marlin `bugfix-2.1.x`** with native `AXIS4_ROTATES` support:

| Marlin Axis | Internal | Function          | Units   | Driver            | Endstop Pin |
|-------------|----------|-------------------|---------|-------------------|-------------|
| X           | X (1)    | Linear extend     | mm      | TMC2208_STANDALONE | 3 (X-min)   |
| C           | I (4)    | Carousel rotation | degrees | TMC2208_STANDALONE | 18 (Z-min)  |
| Y           | Y (2)    | *Placeholder*     | —       | A4988 (no motor)  | —           |
| Z           | Z (3)    | *Placeholder*     | —       | A4988 (no motor)  | —           |

Verified on hardware: `G0 C180` correctly rotates to 180°.
Y/Z must remain defined as placeholders — Marlin requires contiguous axis slots.

### Q4: Development Approach — RESOLVED
See `development.md`. Hybrid LinuxCNC sim + real Marlin hardware. 6 phases.

### Q5: GUI Integration — RESOLVED
GUI-agnostic via HAL pins. Optional thin adapter for Probe Basic/DynATC.
HAL-only may be insufficient for rich error recovery UX — Unix socket/D-Bus
flagged for future consideration (Q7).

### Q6: Auto-Home — RESOLVED
Configurable via INI: on startup, on first tool change, on machine-enable,
or manual only via HAL pin.

### Q7: Component-to-GUI IPC — OPEN
HAL pins handle status well but can't pass structured data (error messages,
tool maps, calibration arrays). Options: (A) HAL-only with error code lookup,
(B) Unix domain socket, (C) D-Bus. Deferred to Phase 6.

---

## 5. Marlin Firmware Status

- **Branch:** `bugfix-2.1.x` (git submodule at commit `25a0a88`)
- **Board:** `BOARD_RAMPS_CREALITY` (ATmega2560)
- **Build:** SUCCESS — RAM 34.7%, Flash 19.1%
- **Build command:** `cd linuxcnc/atc/marlin && ./build.sh`
- **Upload:** `./build.sh upload --upload-port /dev/ttyUSB0`
- **PlatformIO:** Installed standalone at `~/.platformio/penv/bin/pio`
  (build.sh auto-detects this path)

Key config changes from stock Marlin:
- Zero extruders, zero temp sensors, thermal protection disabled
- `CUSTOM_MACHINE_NAME "FMX3-ATC"`
- Baud 115200 (not 250000)
- `I_DRIVER_TYPE TMC2208_STANDALONE`, `AXIS4_NAME 'C'`, `AXIS4_ROTATES`
- `I_STOP_PIN 18` (Z-min connector repurposed for C axis homing)
- Classic jerk + S-curve acceleration enabled
- Steps: `{ 80, 80, 400, 160 }` — 4th value is steps/degree for C axis
- Max feedrate: `{ 750, 5, 5, 750 }` — Y/Z set low (unused)
- Max accel: `{ 2000, 100, 100, 2000 }`

---

## 6. Test Bench Status

- **Marlin hardware:** Connected, firmware flashed, C axis verified
- **Endstops:** NOT wired — use relative moves (`G91`) for dev
- **Sensors:** NOT present — drawbar, tool-in-spindle, pocket sensors
  will be simulated via HAL stubs (simple loopbacks)
- **LinuxCNC sim:** Available on this host (configs in `pico-cnc-hmi` repo)

---

## 7. Architecture Summary

```
┌─────────────────────┐
│   LinuxCNC          │
│                     │
│  M6 Remap G-code    │  ← "thin sequencer": Z motion + M68/M66 handshake
│       ↕ HAL         │
│  fatc component     │  ← Python loadusr: state machine, tool tracking
│    ↕ HAL    ↕ Serial│
│  Mesa I/O   Marlin  │  ← Mesa: drawbar solenoids/sensors
│             (USB)   │  ← Marlin: carousel X (linear) + C (rotation)
└─────────────────────┘
```

**Key patterns:**
- `fatc` follows the `cmdrsk_vfd` driver pattern (loadusr daemon, INI config)
- State machine is the "traffic cop" — owns all motion sequencing
- Persistent state: JSON file with tool-pocket map, tool-in-spindle
- "Spindle always occupied" constraint simplifies recovery logic
- Safety: hardwired E-stop cuts Marlin power + software interlocks in fatc

---

## 8. Current Status and What's Next

### Completed Phases

- ✅ **Phase 1**: `serial_marlin.py` — serial protocol, 6/6 tests on hardware
- ✅ **Phase 2**: `config.py`, `serial_thread.py`, `persistent_state.py`, `fatc.py` — HAL component skeleton, sim launches with Probe Basic
- ✅ **Heartbeat**: 1-second M114 idle poll in `serial_thread.py`; position on `fatc.marlin-x-position` / `fatc.marlin-c-position` HAL pins

### Current: Pre-Phase-3 Experiments

Before implementing the Phase 3 state machine, three assumptions need validation.
Test files are in `linuxcnc/configs/atc.sim/subroutines/`:

---

#### Experiment 1 — M66/M68 analog handshake

**Question:** Does `M66 E2 L1 Qn` actually block and return the pin value?

**Files:** `subroutines/test_m66_m68.ngc` (already created)

**Steps:**
1. Launch the sim: `linuxcnc atc_sim.ini`
2. Home all axes
3. Load `test_m66_m68.ngc` and run it (or MDI: `O<test_m66_m68> CALL`)
4. While blocked, open a terminal:
   ```bash
   halcmd setp motion.analog-in-02 99.0
   ```
5. Watch the LinuxCNC terminal / log for DEBUG output

**Expected:** `DEBUG: RESULT: SUCCESS -- received value 99.0`

**HAL slots used (both free):**
- `M68 E2` → `motion.analog-out-02`
- `M66 E2` ← `motion.analog-in-02`

**Note:** `E0` is used by `program_coolant.ngc`. `E1` / `analog-in-01` is used for TLO. Use **E2** for fatc handshake.

---

#### Experiment 2 — M66 timeout and abort behavior

**Question:** Does M66 return -1 cleanly on timeout? Does abort unblock it without hanging the interpreter?

**Steps (timeout):**
1. Run `test_m66_m68.ngc` but do NOT set the pin
2. Wait 8 seconds for timeout
3. Expected: `DEBUG: RESULT: TIMED OUT (_value is -1)`

**Steps (abort):**
1. Run `test_m66_m68.ngc`
2. While blocked, hit Abort in the GUI
3. Expected: interpreter aborts cleanly, `on_abort.ngc` runs, no hang

---

#### Experiment 3 — M6 REMAP mechanism

**Question:** Does the REMAP prolog/epilog set `#<selected_tool>` and `#<tool_in_spindle>` correctly?

**Files:** `subroutines/test_remap_m6.ngc` (already created)

**Activate:** In `atc_sim.ini` `[RS274NGC]`, uncomment:
```ini
REMAP = M6 modalgroup=6 prolog=change_prolog ngc=test_remap_m6 epilog=change_epilog
```

**Steps:**
1. Restart LinuxCNC (REMAP requires restart)
2. Home axes
3. MDI: `T3 M6` (request tool 3)
4. Watch DEBUG output for correct values

**Expected:**
- `selected_tool = 3.0`
- `tool_in_spindle = 0.0` (or previous tool if one was loaded)
- `fatc.tool-change` still pulses (change_epilog fires)

**Deactivate:** Comment out the REMAP line again and restart.

---

### Next: Phase 3 — State Machine Core

Once experiments pass, implement the full tool-change sequence in `fatc.py` + write `toolchange.ngc` (the thin remap sequencer). The M6 flow is:

**UNLOAD current tool (if spindle occupied):**
1. Rotate to empty pocket → `ROTATING`
2. Extend → `EXTENDING`
3. Open drawbar, wait for `tool-unclamped-sensor` → `DRAWBAR_OP`
4. Wait for remap to raise Z (`M66` from remap) → `Z_MOVING`
5. Signal remap Z-is-clear (`M68` back to remap)
6. Retract → `RETRACTING`

**LOAD new tool:**
7. Rotate to target pocket → `ROTATING`
8. Extend → `EXTENDING`
9. Wait for remap to lower Z to tool-change height (`M66`) → `Z_MOVING`
10. Close drawbar, wait for `tool-clamped-sensor` → `DRAWBAR_OP`
11. Signal remap drawbar-done
12. Wait for remap to raise Z, signal done
13. Retract → `RETRACTING`
14. Update persistent state, ACK tool-change → `IDLE`

**Key HAL analog slots for remap↔fatc handshake (E2 confirmed free):**
- `E2`: remap signals fatc (pre-dock start, Z-engaged, Z-clear)
- `E3` (if needed): fatc signals remap (pre-dock ready, drawbar done, all done)
  — check `num_aio` in `core_sim.hal` first; currently set to 3 (slots 0–2 only)
  — may need to increase to 4

---

## 9. Commit History

```
b44c44f atc: add development approach doc, resolve Q4
55e7791 atc: resolve Q3 — Marlin C axis mapping verified on hardware
70460df atc/marlin: migrate to bugfix-2.1.x with native C axis support
d4af031 atc/marlin: add Marlin submodule (bugfix-2.1.x) and build script
cf8bd47 bench test atc marlin config
966122d atc: initial requirements spec and Marlin build scaffolding
```

---

## 10. Important Notes for Continuing Agent

1. **Read `requirements.md` thoroughly** — it's the definitive spec (57KB).
   Sections 4–7 are the most critical (state machine, safety, HAL pins, Marlin).

2. **The Marlin submodule** must be initialized: `git submodule update --init`

3. **No endstops on test bench** — homing won't work. Use `G91` for relative
   moves. The fatc component needs a `homing_required` config option.

4. **Sim configs are in another repo** (`pico-cnc-hmi`), not in this repo.

5. **HAL pin names** are specced in `requirements.md` section 5.4 but not
   finalized — they're a design intent, not a contract yet.

6. **The user** is an experienced embedded/controls engineer familiar with
   LinuxCNC, HAL, Marlin, Python, and CNC machining. Technical discussions
   can be at a high level.

7. **Pattern reference:** The `cmdrsk_vfd` driver in this same repo
   (`linuxcnc/components/cmdrsk_vfd/`) is the architectural template for
   loadusr component structure.
