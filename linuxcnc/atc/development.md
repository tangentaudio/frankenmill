# FrankenMill ATC — Development Approach

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

- **LinuxCNC 2.9.x** in simulator mode (no real-time kernel required)
- **Probe Basic / QtPyVCP** simulator config (from `pico-cnc-hmi` repo)
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

- [ ] Serial port open/close, baud rate config
- [ ] G-code send with `ok` response parsing
- [ ] Error response handling (`echo:`, `Error:`)
- [ ] Command queue with timeout/retry
- [ ] `M115` firmware identification on connect
- [ ] `G28 X C` homing sequence
- [ ] Basic `G0 X<pos>` and `G0 C<angle>` motion commands
- [ ] `M114` position query and parsing
- [ ] Startup/reconnection logic

**Test method:** Direct Python script → Marlin hardware. No LinuxCNC needed
yet. Validate with real serial responses from real firmware.

### Phase 2: HAL Component Skeleton

**Goal:** `fatc` loads as a LinuxCNC `loadusr` component with HAL pins.

- [ ] Component entry point (`fatc.py` or compiled `.comp`)
- [ ] INI file configuration loading (serial port, pocket count, etc.)
- [ ] HAL pin creation (per section 5.4 of requirements)
- [ ] Main loop: poll HAL pins → update state → write HAL pins
- [ ] Serial connection management (connect on startup, reconnect on loss)
- [ ] Basic status reporting via HAL pins
- [ ] Persistent state file load/save (JSON tool-pocket map)

**Test method:** `loadusr` in LinuxCNC sim. Monitor pins with `halshow`
/ `halmeter`. Marlin hardware connected.

### Phase 3: State Machine Core

**Goal:** Complete tool-change state machine (manual trigger via HAL pin).

- [ ] State machine framework (enum states, transition validation)
- [ ] Zone-based motion coordination ("traffic cop" model)
- [ ] Tool-change sequence: full put-away → rotate → pick-up cycle
- [ ] Parallel motion (carousel rotate while Z clears)
- [ ] Tool-in-spindle tracking with persistent state
- [ ] Carousel pocket sensor integration (HAL pin, simulated)
- [ ] Error state entry on fault conditions
- [ ] Configurable homing behavior (per Q6)

**Test method:** Trigger tool changes via HAL pin (`fatc.tool-change`).
Observe Marlin dummy motors moving through sequence. Verify state
transitions with `halshow`.

### Phase 4: M6 Remap Integration

**Goal:** LinuxCNC `T<n> M6` triggers the full ATC sequence.

- [ ] M6 remap G-code file (thin sequencer)
- [ ] M68/M66 handshaking between remap and fatc component
- [ ] Z-axis coordination (safe height before/after tool change)
- [ ] Pre-dock parallel motion (carousel rotating during Z travel)
- [ ] Tool table synchronization
- [ ] Abort/cancel handling (M2, program stop)

**Test method:** Run G-code programs in LinuxCNC sim with `T1 M6`, `T2 M6`,
etc. Verify full sequence including simulated Z motion and real Marlin motion.

### Phase 5: Safety & Error Recovery

**Goal:** Robust error handling and operator recovery.

- [ ] Timeout detection (Marlin response, motion completion)
- [ ] Sensor disagreement detection (expected vs actual pocket state)
- [ ] E-stop integration (Marlin power cut via hardwired relay)
- [ ] Pause-and-recover architecture (operator can fix and resume)
- [ ] Error code reporting via HAL pins
- [ ] State recovery after power cycle (from persistent state file)
- [ ] Air blast integration

**Test method:** Inject faults (disconnect USB, block endstop, etc.) and
verify graceful error handling and recovery paths.

### Phase 6: GUI Integration & Polish

**Goal:** Visual feedback and operator-friendly interface.

- [ ] Probe Basic / DynATC adapter (HAL pins → widget calls)
- [ ] Error recovery UI (if needed beyond HAL error codes)
- [ ] Per-pocket calibration wizard (future)
- [ ] IPC mechanism evaluation (Unix socket / D-Bus) if HAL proves insufficient
- [ ] Documentation and operator manual

**Test method:** Full integration testing with Probe Basic GUI in sim mode.

---

## 4. File Layout (Planned)

```
linuxcnc/atc/
├── requirements.md          # Architectural requirements
├── development.md           # This file
├── README.md                # Project overview
├── fatc/                    # Python component source
│   ├── __init__.py
│   ├── fatc.py              # Main component entry point
│   ├── serial_marlin.py     # Marlin serial protocol layer
│   ├── state_machine.py     # ATC state machine
│   ├── config.py            # INI file config loading
│   └── persistent_state.py  # JSON tool-pocket map
├── hal/                     # HAL configuration files
│   ├── fatc.hal             # Component loading and pin wiring
│   └── fatc_sim.hal         # Simulated I/O loopbacks for dev
├── remap/                   # M6 remap G-code
│   └── m6remap.ngc          # Thin sequencer
├── marlin/                  # Marlin firmware (exists)
│   ├── Configuration.h
│   ├── Configuration_adv.h
│   ├── build.sh
│   ├── README.md
│   └── Marlin/              # Git submodule
└── tests/                   # Test scripts
    ├── test_serial.py       # Standalone serial protocol tests
    └── test_state_machine.py
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

## 6. Open Questions

### Dev Environment

- **Q:** Where should the LinuxCNC sim config for ATC development live?
  Currently sim configs are in the `pico-cnc-hmi` repo. Should a dedicated
  ATC sim config be created in this repo, or added to the existing sim setup?

- **Q:** Should we start Phase 1 with a standalone serial test script (no
  LinuxCNC dependency) to validate the Marlin protocol layer in isolation?

### Marlin Protocol

- **Q:** Does the fatc component need to handle Marlin's `BUSY:processing`
  messages during long moves, or is polling `M114` sufficient?

- **Q:** Should we use Marlin's `M400` (wait for moves to finish) for
  synchronization, or track `ok` responses to know when queued moves complete?
