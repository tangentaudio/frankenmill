# FrankenMill ATC Component — Requirements Specification

> **Document Status:** DRAFT v3 — post-review refactor  
> **Date:** 2026-05-06  
> **Author:** Steve / Antigravity  
>
> **Implementation Status (2026-05-06):** Phases 1–5 complete; Phase 6
> (GUI) partially complete. All Q1–Q7 open questions are now resolved.
> For current implementation details, see CONTEXT.md.

---

## 1. Background & Motivation

This is a **new project to build an automatic tool changer from the ground up** —
mechanical design, electrical integration, and software — for the FrankenMill
CNC milling machine. The FrankenMill does not currently have an ATC.

Probe Basic ships with boilerplate G-code subroutines for carousel-style ATC
control (`toolchange.ngc`, `m10-m13.ngc`, `m21-m25.ngc`, etc.). These are **not
in use** on the machine but were reviewed as reference for the tool-change
sequence and LinuxCNC integration patterns. See **Appendix B** for details.

The boilerplate approach (G-code subroutines with `M64`/`M65`/`M66` digital I/O)
has fundamental limitations that motivate this project:

1. **No centralized state machine** — logic spread across ~12 G-code subroutines
2. **Limited error recovery** — failures abort with no retry
3. **No HAL-level status** — GUI relies on `DEBUG EVAL` calls from G-code
4. **Persistent state fragility** — tool-pocket map in LinuxCNC `#` parameters
5. **Hard to extend** — tightly coupled subroutines

---

## 2. New ATC Hardware Architecture

### 2.1 Mechanical Design

The new ATC is a **carousel/umbrella-style** tool changer with two axes of motion:

1. **Rotation axis** — Stepper motor rotates the carousel to position tool pockets
   under the spindle
2. **Linear extend/retract axis** — Stepper motor moves the carousel linearly
   to/from the spindle for tool engagement

**Physical mounting:**
- The carousel mounts to the left or right of the spindle head
- The linear axis brings the carousel to the spindle nose, generally in line
  with the machine's X axis (may be at an angle, e.g. 45° XY, for packaging)
- The carousel itself has **no vertical axis** — the machine's Z axis provides
  the vertical motion needed for tool loading and unloading
- Pocket count is configurable; targeting **12–16 pockets**

### 2.2 Motion Controller

Both stepper motors and their homing switches connect to an **external controller
board running Marlin 2.0.x firmware** (the same firmware widely used in DIY 3D
printers). This provides:

- Stepper motor driving with acceleration/deceleration control
- Endstop/homing switch handling (Marlin's `G28` homing sequence)
- Absolute and relative positioning via standard G-code
- USB-serial communication with the LinuxCNC host PC

### 2.3 Communication Path

```
LinuxCNC HAL ◄──► Python ATC Component ◄──USB-Serial──► Marlin Controller ──► Steppers
                         │
                         ▼
                    HAL Pins (drawbar, sensors, status)
                         │
                         ▼
                    Mesa 7i80 GPIO (drawbar solenoid, clamp sensors)
```

The Python component is the **bridge** between LinuxCNC's HAL world and the
Marlin controller's G-code world.

---

## 3. Proposed Solution

### 3.1 Architecture

A **Python userspace HAL component** (`loadusr`) that:

1. Opens a USB-serial connection to the Marlin controller
2. Creates HAL pins for tool-change commands, status, and any direct I/O (drawbar, sensors)
3. Implements an internal **state machine** for tool change sequences
4. Translates tool-change requests into **G-code command sequences** sent to Marlin
5. Parses Marlin responses (`ok`, position reports, endstop status) for feedback
6. Manages persistent state (tool-pocket mapping) internally
7. Provides rich status output for GUI integration

This follows the same `loadusr` daemon pattern as `cmdrsk_vfd`, but communicates
G-code over serial instead of Modbus.

### 3.2 Design Principles

1. **GUI-agnostic** — The ATC component communicates primarily via HAL pins
   and persistent state files. It has **no dependency on any specific LinuxCNC
   GUI** (Probe Basic, AXIS, gmoccapy, etc.). Any GUI visualization or
   operator interaction should be a **separate, modular layer**. This allows
   switching GUIs without touching the core ATC logic. (Supplementary IPC
   via Unix socket `/tmp/fatc.sock` provides structured communication — see Q7.)

2. **All intelligence in the component** — Tool tracking, state machine,
   motion planning, and error handling live in the Python component, not in
   G-code subroutines or GUI widgets.

3. **Design for recovery** — Error states should be recoverable, not
   dead-ends. The state machine architecture should support pause, retry,
   and resume from the start, even if the recovery UI comes later.

### 3.3 Component Name

`fatc` ("fat-see", for **F**ranken **ATC**) — loaded as:

```hal
loadusr -Wn fatc ./fatc --ini ffatc.ini --name fatc
```

---

## 4. Functional Requirements

### 4.1 Serial Communication with Marlin

- Open and manage USB-serial connection (configurable device path, baud rate)
- Handle Marlin startup sequence (wait for `start` / firmware info)
- Send G-code commands and wait for `ok` acknowledgment
- Parse `ok`, `error`, position reports (`M114`), endstop status (`M119`)
- Handle serial disconnection/reconnection gracefully
- Command queue with flow control (Marlin's serial buffer is limited)

### 4.2 Carousel Rotation (Marlin Axis)

- **Homing**: Send `G28` for the rotation axis — Marlin handles endstop detection
- **Absolute positioning**: `G0`/`G1` to specific pocket positions (angles or step counts)
- **Pocket calculation**: Convert pocket number to axis position based on pocket count
  and geometry (360° / N pockets, or step-based)
- **Shortest path**: Calculate CW vs CCW rotation for minimum travel
- **Calibration offsets**: Per-pocket rotational fine-tuning stored in persistent
  state (see 4.11)

### 4.3 Carousel Extend/Retract (Marlin Axis)

- **Homing**: `G28` for the linear axis
- **Extend**: Move linear axis to tool-change position
- **Retract**: Move linear axis to home/stowed position
- **Positions configurable** via INI
- **Calibration offset**: Linear position fine-tuning stored in persistent
  state (see 4.11)

> [!IMPORTANT]
> **Homing sequence is order-dependent**: The linear axis must **always retract
> and home first** (homing sensor is at the retracted end), then the carousel
> rotation axis homes. This sequence must be enforced by the state machine —
> never attempt to home rotation while the carousel is extended.

### 4.4 Core Tool Change Sequence

1. **Receive tool-change command** via HAL pin
2. **Identify target pocket** — look up requested tool in pocket map (error if not found, see §4.13.4)
3. **Stow current tool** (if spindle loaded):
   a. Look up home pocket for the current spindle tool in pocket map (it must have come from somewhere)
   b. Rotate carousel to stow pocket (Marlin G-code)
   c. Extend carousel (Marlin G-code)
   d. Coordinate Z-axis motion (see Q1)
   e. Activate drawbar (HAL pin or Marlin)
   f. Coordinate Z retract
   g. Retract carousel (Marlin G-code)
4. **Load new tool**:
   a. Rotate carousel to tool pocket (Marlin G-code)
   b. Extend carousel (Marlin G-code)
   c. Coordinate Z-axis motion
   d. Deactivate drawbar, verify clamped
   e. Coordinate Z retract
   f. Retract carousel (Marlin G-code)
5. **Signal completion** via HAL pin

> [!IMPORTANT]
> The Z-axis motion for tool engagement is on the **LinuxCNC** side (mill's Z axis),
> not on the Marlin controller. The Python component cannot directly command LinuxCNC
> axis motion. See Q1 for coordination options.

### 4.5 Motion Coordination Philosophy

The ATC and CNC machine axes do **not** require tightly coordinated interpolated
motion (like synchronized multi-axis cutting). Instead, the coordination model
is a **traffic cop** — the Python component ensures the machine and ATC never
try to occupy the same physical space at the same time, while allowing safe
parallel motion for speed optimization.

**Key principles:**

1. **Zone-based safety**: Define safe zones where ATC and machine can move
   independently. The component tracks which zone each system is in and
   prevents conflicting moves.

2. **Safe parallel motion**: The ATC and machine axes may move simultaneously
   when their paths don't conflict. This enables timing optimizations.

3. **Sequential-only for engagement**: The final approach moves (Z down to
   tool-change height, carousel extend to spindle) must be strictly sequenced
   with interlocks.

**Example — Pre-dock optimization:**

In a naive sequential tool change, the machine goes to G28, then the ATC
rotates and extends, then the machine lowers Z. With parallel coordination:

```
Time ──────────────────────────────────────────────►

Machine Z:  [── moving to G28 home ──]...[── Z down to TC height ──]
ATC rotate: ......[── rotating to pocket ──]........................
ATC linear: ..........[── pre-dock move ──].[── final extend ──]....
```

While the machine is still moving Z to home, the ATC can simultaneously:
- Rotate the carousel to select the correct pocket
- Move to a **pre-dock position** — close to but safely clear of the spindle/head

Once the machine reaches G28 and the ATC is pre-positioned, only the final
short extend move and Z engagement are needed, significantly reducing total
tool-change time.

**Implementation implications:**
- The state machine needs to track both systems concurrently (not purely
  sequential states)
- Safety interlocks must be position-aware (know where Z is before allowing
  ATC extend)
- The component may need to monitor LinuxCNC position feedback (Z axis
  position via HAL pins) to know when it's safe to begin ATC motion

### 4.6 Drawbar Control

- **Unclamp**: Activate drawbar, wait for unclamped sensor confirmation, with timeout
- **Clamp**: Deactivate drawbar, wait for clamped sensor confirmation, with timeout
- **Interlock**: Prevent spindle start while drawbar active (existing VFD interlock)

> [!NOTE]
> The drawbar solenoid and clamp sensors are wired to **Mesa 7i80 GPIO**,
> controlled via HAL pins from this component (see Q2).

### 4.7 State Persistence

The component assumes the carousel **stays physically loaded as it was** between
executions. Persistent state is stored in a local JSON file and survives
component restarts, machine power cycles, and LinuxCNC restarts.

**Persisted data:**
- Tool-to-pocket mapping (which tool is in which pocket). **Tool numbers and
  pocket numbers are fully independent** — any tool number can occupy any
  pocket. Pocket numbers are physical slots (1..N); tool numbers come from
  the LinuxCNC tool table and are assigned by the operator. See §4.13.
- `inventory_valid` flag — false on fresh state, set true once the operator
  has declared all pocket contents. When false, tool changes are refused.
- Current pocket position (last known carousel rotation position)
- Tool-in-spindle (which tool, if any, is loaded) — **see note below**
- Calibration offsets (per-pocket rotation and linear fine-tuning — see 4.12)
- Carousel inventory timestamp (when last verified by sensor scan)

> [!WARNING]
> **Tool-in-spindle requires coordination with LinuxCNC.** LinuxCNC maintains
> its own current-tool state (`iocontrol.0.tool-in-spindle`, interpreter
> variable `#5400`) which **resets to 0 (no tool) on every LinuxCNC restart**.
> This creates a conflict: the component knows a tool is physically in the
> spindle, but LinuxCNC thinks there isn't one.
>
> **Physical constraint:** The spindle will **always** have a tool holder
> installed during normal operation — releasing the drawbar with no tool is
> not recommended. This means "spindle empty" is not a normal state; the
> component can treat it as an error/setup condition rather than a routine one.
>
> The component should persist tool-in-spindle as the **authoritative physical
> truth**, but reconciling it with LinuxCNC's interpreter state on startup is
> an open design question. Options include:
> - The M6 remap prolog/epilog could read the component's state on first use
> - The component could force a "T0 M6" equivalent on startup to sync
> - The spindle pre-sense sensor (4.8) could provide independent verification
>
> This needs careful design to avoid a scenario where LinuxCNC calls for T5
> but doesn't realize it needs to stow T3 first because it thinks the spindle
> is empty.

**Reliability:**
- Atomic writes (write to temp file, then rename) to prevent corruption
- On startup, validate persisted state against sensor readings when possible
- If state file is missing or corrupt, require operator re-initialization
  (home + inventory scan)

### 4.8 Tool Presence Detection

Two optional proximity sensors provide crash-prevention safety:

**Carousel pocket sensor** (connected to Marlin controller):
- A proximity sensor positioned to detect whether the carousel pocket currently
  facing the spindle nose contains a tool
- By rotating through all pockets, the component can perform a **full carousel
  inventory scan** to verify/rebuild the tool-pocket map
- Used during tool change to **confirm pocket state** before loading or stowing:
  - Before stowing: verify target pocket is actually empty
  - Before loading: verify source pocket actually has a tool
- Reported to the Python component via Marlin endstop/input query (`M119` or
  similar)

**Spindle pre-sense sensor** (stretch goal — mechanical viability TBD):
- Detects whether a tool is currently loaded in the spindle, independent of
  the drawbar clamp sensors
- Provides a **pre-check before carousel extend** — if the carousel is about to
  present a tool but the spindle already holds one (unexpected), abort rather
  than crash
- **The system must not depend on this sensor being available.** Mechanical
  feasibility depends on finding a workable mounting approach, which has not
  been determined. The component should work correctly without it, but allow
  it to be enabled via configuration if implemented.
- Connection TBD — Marlin or Mesa GPIO

**Combined safety logic:**
- Before any tool change, cross-check carousel pocket sensor (and spindle
  pre-sense **if available**) against the component's internal state
- If sensor readings contradict expected state → enter error state, do not proceed
- Enables automatic recovery from state corruption (e.g. after power loss mid-change)
- Full carousel scan on homing or on demand to re-sync tool-pocket map

> [!NOTE]
> The carousel pocket sensor alone provides significant safety value even
> without the spindle pre-sense sensor. Combined with the physical constraint
> that the spindle always has a tool holder (4.7), the component's persisted
> state is a reasonable proxy for spindle status.

### 4.9 Error Handling

- Timeout on every Marlin command with configurable durations
- Serial communication error detection and recovery
- Named error states with descriptive HAL status output
- Safe shutdown: retract carousel, stop motors on error
- Reset from error state via HAL pin
- On abort/estop: safe state all outputs, send Marlin emergency stop (`M112` or `M410`)
- **Sensor mismatch errors**: tool presence sensors disagree with expected state

### 4.10 Safety & Interlocking

Safety interlocking must be **bidirectional** — machine safety events must stop
the ATC, and ATC errors must stop the machine.

#### 4.10.1 Machine → ATC Safety (stopping ATC on machine events)

| Machine Event | ATC Response | Mechanism |
|---|---|---|
| **E-stop** | Immediate ATC motor stop | Hardwired: E-stop relay cuts power to Marlin stepper drivers. Software: component sends `M112` to Marlin |
| **Machine power disable** | ATC motors de-energized | Hardwired: machine-enable relay in series with Marlin power |
| **Program stop** (M0/M1) | ATC pauses current sequence | Software: component monitors `halui.program.is-paused` |
| **Feed hold / stop button** | ATC pauses current sequence | Software: component monitors motion control pins (see discussion below) |
| **Abort** | ATC emergency stop, safe state | Software: component monitors abort signal via HAL |

> [!WARNING]
> **Feed hold / stop button**: LinuxCNC's feed-hold and stop button halt the
> motion planner and G-code interpreter, but they have **no direct effect on
> userspace components**. The Python component must actively monitor relevant
> HAL pins to respond, but prior experience with the VFD monitor project has
> shown that `halui` signal naming and behavior can be quirky and unreliable
> (e.g. `halui.program.is-idle` actually means "is-idle" despite being wired
> to a signal named `prog-running`). Software-based pause/stop integration
> should be treated as **best-effort, not safety-critical**.
>
> **E-stop is the primary safety boundary.** The operator will instinctively
> reach for it if the ATC looks wrong. A **hardwired interlock** that cuts
> power to the Marlin stepper drivers is **non-negotiable** — software-only
> E-stop via USB-serial is insufficient.

**Hardwired interlocks (recommended):**
- E-stop relay should cut power to Marlin's stepper driver enable lines
  (or the Marlin board's motor power supply entirely)
- This provides a guaranteed stop regardless of USB connection, software
  state, or Marlin firmware behavior
- The existing E-stop safety relay chain can be extended to include the
  ATC motor power

**Software interlocks:**
- Component monitors `motion.motion-enabled` (machine enable state)
- Component monitors E-stop chain via `iocontrol.0.user-enable-out`
- On any safety event: send `M112` (emergency stop) to Marlin, de-assert
  all HAL output pins, enter error state

#### 4.10.2 ATC → Machine Safety (stopping machine on ATC errors)

| ATC Event | Machine Response | Mechanism |
|---|---|---|
| **ATC error** (timeout, sensor mismatch, serial loss) | Abort running program | Software: component asserts `halui.program.stop` via HAL pin |
| **Tool presence conflict** | Abort + operator notification | Software: abort + error status pins for GUI display |
| **Marlin disconnect** | Abort + operator notification | Software: abort + status pins |
| **ATC in unexpected state** | Prevent program resume | Software: component holds `fatc.error` high, blocks tool-change completion |

**How the component can stop a running G-code program:**
- Assert a HAL output pin connected to `halui.program.stop` (same pattern
  used by VFD fault → program stop in the existing `probe_basic_postgui.hal`)
- The tool-change will be aborted because `iocontrol.0.tool-changed` is never
  asserted, which causes LinuxCNC to remain in the tool-change wait state
- The component sets descriptive error status via HAL pins for the GUI to display

> [!NOTE]
> Since tool changes happen while the machine is **not actively cutting**,
> the risk envelope is significantly smaller than if ATC errors could occur
> mid-cut. The main crash risks are:
> - Carousel colliding with spindle/head during extend
> - Z-axis descending into a carousel full of tools
> - Drawbar operating with misaligned tool
>
> All of these are addressed by the sensor interlocks (4.8) and zone-based
> motion coordination (4.5).

> [!IMPORTANT]
> **HAL pins alone may not provide a rich enough error recovery experience.**
> Communicating error details, recovery options, and calibration data through
> typed HAL pins (bit, s32, float) is tedious at best. A richer integration
> with an ATC tab or error dialog in the GUI is provided via Unix socket IPC
> — see **Q7** (now resolved) for the implemented protocol.

#### 4.10.3 LinuxCNC Override Integration

LinuxCNC provides operator overrides (feed rate override, rapid override,
max velocity limit) that affect machine axis motion. The question is whether
these should also affect ATC Marlin motion.

**Considerations:**

| Override | Apply to ATC? | Rationale |
|---|---|---|
| **Feed rate override** | Probably no | ATC moves are not cutting moves; overriding them adds complexity and slows tool changes unnecessarily |
| **Rapid override** | Maybe | Could be useful for cautious first-time testing; would require reading `halui.max-velocity.value` and scaling Marlin feedrates |
| **Max velocity** | Maybe | Same reasoning as rapid override |
| **Feed hold** | Yes | Operator expects "stop" to stop everything |

**Implementation approach (if desired):**
- The Python component reads `halui.rapid-override.value` (0.0–1.0 float)
  from a HAL pin
- Scales Marlin feedrates accordingly: `marlin_feedrate = base_feedrate * rapid_override`
- This is a best-effort approximation — Marlin moves already in progress
  cannot have their feedrate changed mid-move (Marlin doesn't support that)
- A "next move" approach: override scaling is applied when the next G-code
  command is sent to Marlin

> [!NOTE]
> Override integration is a **nice-to-have**, not a safety requirement.
> The priority is correct E-stop and error propagation. Override scaling
> can be added incrementally.

#### 4.10.4 Operator Recovery

When the ATC enters an error state, the system should help the operator
recover:

- **Clear error indication**: HAL status pins + GUI display showing what went
  wrong (timeout, sensor mismatch, serial loss, etc.)
- **Safe manual control**: ability to jog the carousel (rotate, extend/retract)
  manually via operator controls while in error state, for clearing jams or
  repositioning
- **Inventory re-sync**: ability to trigger a full carousel scan to rebuild
  the tool-pocket map from sensor data
- **Resume or restart**: after manual correction, operator can reset the error
  and either resume the tool change or start fresh

**Pause-and-recover architecture (vs. abort-or-hang):**

The existing Probe Basic / DynATC stack provides **zero error recovery** for ATC
problems. DynATC is a pure visualization widget (4 methods, no error UI). The
boilerplate G-code subroutines can only `(ABORT, "message")` to kill the
running program, or hang waiting on a sensor. There is no retry, no operator
dialog, no guided recovery.

The Python component architecture enables a fundamentally better pattern:
during M6, the G-code interpreter is **blocked waiting** for `tool-changed`
to be asserted. As long as the component doesn't signal completion *or* cause
an abort, the G-code program remains suspended but alive. This creates a
recovery window:

1. ATC component detects error → enters `ERROR` state
2. Component exposes error details via HAL pins (error code, description)
3. GUI panel (future) shows error + recovery options to operator
4. Operator intervenes (manual jog, re-check sensors, fix physical issue)
5. Operator signals "retry" or "resume" via HAL pin
6. Component retries the failed step or resumes the sequence
7. On success, `tool-changed` is asserted → G-code program continues

This avoids program abort entirely for recoverable errors.

> [!TIP]
> **Priority note:** Getting basic tool changes working reliably comes first.
> Error recovery UI and the pause-and-recover flow are future enhancements,
> but the component's state machine should be **designed from the start** to
> support this pattern (error states that can transition back to operational
> states, not just dead-ends). Building the hook points early is cheap;
> retrofitting them later is not.

---

### 4.11 Air Blast (Taper Cleaning)

An optional air blast feature can blow debris from the tool holder taper
before loading into the spindle, reducing contamination that causes TIR
(Total Indicated Runout) problems.

- **Activation**: Solenoid-controlled air valve, activated during the tool
  change sequence (after selecting the pocket, before or during spindle
  engagement)
- **Timing**: Configurable blast duration and timing within the sequence
- **Connection**: Probably Mesa GPIO via HAL pin (consistent with drawbar),
  but Marlin I/O is also an option if wiring is simpler
- **Control pin**: `fatc.air-blast-activate` (bit OUT)

### 4.12 Calibration & Tuning
and linear extend/retract positions for reliable tool changes. Calibration
data is stored in the persistent state file (4.7) alongside the tool-pocket
map.

**Rotational calibration:**
- Per-pocket angular offset from the nominal calculated position
  (compensates for manufacturing tolerances in the carousel)
- Calibration procedure: jog carousel to each pocket position, fine-tune
  alignment to spindle centerline, save offset

**Linear calibration:**
- Fine-tuning of the extend position for proper tool engagement depth
- May need per-pocket linear offsets if pocket geometry varies

**Calibration interface:**
- Preferably driven through the GUI via a calibration wizard or panel
- The component exposes HAL pins for manual jog commands and current
  position readback to support GUI-driven calibration
- Stored offsets are applied automatically during tool changes

> [!NOTE]
> Calibration is a critical prerequisite before the ATC can be used reliably.
> The calibration workflow should be designed early even if the GUI for it
> comes later — command-line or halcmd-based calibration should work as a
> fallback.

---

### 4.13 Tool Inventory Management

**Tool numbers and pocket numbers are independent.** A pocket is a physical
slot in the carousel, identified by number 1..N. A tool is a numbered item
from the LinuxCNC tool table. Any tool can live in any pocket; pocket
assignments change between jobs and over the machine's lifetime.

#### 4.13.1 The Inventory Problem

The fatc component must always know **which tool is in which pocket** to:
- Find the right pocket when `T<n> M6` is requested
- Find an empty pocket when stowing the current spindle tool
- Detect mismatch errors before a crash can occur

When `inventory_valid = false` (fresh install, state file deleted, or unknown
state), the component cannot safely execute a tool change and must refuse,
prompting the operator to declare the inventory.

#### 4.13.2 How Inventory Becomes Valid

Three mechanisms populate the pocket map:

1. **ATC-driven change tracking** — every stow and load updates the map
   automatically. Once the map is accurate, normal tool changes keep it
   accurate indefinitely. This is the steady-state mode.

2. **Manual operator declaration** — before first use, or after manually
   moving tools, the operator declares the contents of each pocket via a
   setup interface (GUI panel, HAL pin + tool number, or IPC command).
   This is how the map is initially populated.

3. **Carousel scan** (future, requires pocket sensor hardware) — the
   component rotates through all pockets and reads the presence sensor.
   This confirms occupied vs empty but does **not** identify which tool is
   in an occupied pocket; it must be combined with operator declaration or
   prior knowledge.

#### 4.13.3 Job Setup Workflow

For each job the operator:

1. Decides which tools are needed and which pockets to place them in
2. Manually loads physical tool holders into carousel pockets
3. Declares the pocket contents to fatc (GUI or command)
4. Declares the spindle contents if a tool is already loaded
5. Runs the job — `T<n> M6` commands resolve tools by the declared map

Between jobs, tools may be swapped. The operator must update the map
whenever tools are **manually** moved in or out of the carousel. The map
is **not** invalidated by ATC-driven changes (those are tracked).

#### 4.13.4 "Tool Not Found" Behaviour

If `T<n> M6` is requested and T<n> is not in the pocket map:

- **Do not guess** (e.g. pocket = tool number). This is only valid as a
  transient development fallback and must not be the production behaviour.
- Enter error state with `TOOL_NOT_FOUND` code
- Message operator: "T<n> not in carousel — load it and declare its pocket"
- Operator loads the tool, declares the pocket via the setup interface,
  resets the error, and re-runs

- **Do not guess** (e.g. pocket = tool number). This is only valid as a
  transient development fallback and must not be the production behaviour.
- Enter error state with `TOOL_NOT_FOUND` code
- Message operator: "T<n> not in carousel — load it and declare its pocket"
- Operator loads the tool, declares the pocket via the setup interface,
  resets the error, and re-runs

#### 4.13.5 Manual Tool Change Fallback

Some tools **cannot** live in the carousel — wrong taper geometry, no
tool fork groove, oversize holders, or operator preference. These tools
must be changed manually. The system needs to handle this gracefully as
a first-class workflow, not an error condition.

**Manual tool change flow:**

1. The tool table (or a fatc-specific annotation) marks T<n> as
   `manual_only` — not a carousel tool
2. When `T<n> M6` is requested for a manual-only tool, fatc detects this
   and does **not** attempt a carousel sequence
3. The GUI prompts the operator: "Please manually change to T<n> and press
   OK" (standard LinuxCNC toolchange dialog pattern)
4. After operator confirms, the sequence completes normally

**Stow-to-carousel offer:**

When the outgoing spindle tool is a carousel tool and an empty pocket is
available, the sequence should optionally offer: "Stow T<current> to
carousel before manual change? [Yes / No]". This avoids a "put the manual
tool in, then discover the old tool is still in your hand" situation and
keeps the carousel inventory tidy.

Conversely, after a successful manual change, the system could offer:
"Store T<n> in an empty carousel slot? [Yes / Pocket N / No]" — useful if
the operator decides mid-job to semi-automate a tool they initially loaded
by hand.

**Interaction with pocket map:**

Manual-only tools never appear in the pocket map. `TOOL_NOT_FOUND` and
`manual_only` are distinct states — `TOOL_NOT_FOUND` means the tool
*could* be in the carousel but isn't declared; `manual_only` means it is
by design never there. The system must not conflate them.

> [!NOTE]
> The exact UX for the manual change prompt (dialog, HAL pin, button on
> ATC tab) and the stow/load offer is an open design question related to
> §4.13.8. The underlying state machine logic in fatc can be designed
> first; the UX wired on top later.

#### 4.13.6 IPC Interface for Inventory Management

The Unix socket server (§Q1) accepts inventory management commands in
addition to the M-code handshake commands:

| Command | Payload | Effect |
|---------|---------|--------|
| `SET_POCKET` | `pocket`, `tool` | Declare tool T in pocket P (0 = empty) |
| `GET_INVENTORY` | — | Return full pocket→tool map |
| `CLEAR_INVENTORY` | — | Mark all pockets empty, inventory_valid=false |
| `SET_SPINDLE` | `tool` | Declare tool currently in spindle |

These commands can be driven from the GUI, a setup script, or directly
from the LinuxCNC MDI via a User M-code (e.g. `M104 P3 Q5` = pocket 3
contains T5).

> [!NOTE]
> The `SET_POCKET` command with `tool=0` is how the operator declares that
> a pocket is empty after manually removing a tool. The ATC cannot know about
> manual carousel changes — the operator is responsible for keeping declarations
> accurate.

#### 4.13.7 Probe Basic / DynATC Integration

The machine runs Probe Basic as its GUI. Probe Basic ships a `DynATC`
QtQuickWidget (`src/widgets/atc_widget/atc.py`) that renders an animated
carousel showing which tool is in which pocket.

**How Probe Basic tracks the pocket map (their approach):**

Probe Basic stores the pocket map in **LinuxCNC persistent NGC parameters**:
`#[4000 + pocket]` = tool number in that pocket (e.g. `#4003` = tool in
pocket 3). These are written to the `.var` parameter file and survive
power cycles. The DynATC widget is a *display-only* layer — it holds an
in-memory copy and is updated by calling its Python methods directly from
NGC via qtpyvcp's debug-eval mechanism:

```ngc
(DEBUG, EVAL[vcp.getWidget{"dynatc"}.store_tool{#<pocket>, #<tool>}])
```

On carousel home (`M13`), the NGC macro iterates all pockets, reads each
`#[4000+n]` value, and calls `store_tool` to sync the widget from the
`.var` file. The widget name `"dynatc"` is the Qt object name set in the
`.ui` file.

**DynATC widget API** (methods callable via qtpyvcp EVAL):

| Method | Args | Effect |
|--------|------|--------|
| `store_tool(pocket, tool)` | int, int | Show/hide tool in pocket slot on widget |
| `load_tools()` | — | Redraw all pockets from `self.pockets` dict |
| `rotate(steps, direction)` | int, "cw"/"ccw" | Animate carousel rotation |
| `atc_message(msg)` | str | Display status message on widget |

**Implications for fatc:**

fatc uses `fatc_state.json` (not the `.var` file) as its authoritative
pocket map, because the map is managed by the fatc daemon process, not
by the NGC interpreter. This is a deliberate divergence from the Probe
Basic approach, chosen because:

- fatc is an autonomous daemon with its own serial state machine; the
  authoritative source should live with the daemon
- Atomic file writes in `fatc_state.json` are safer than `.var` file
  manipulation from multiple contexts
- The IPC `SET_POCKET` command provides an explicit, auditable setup
  interface

**Integration approach** — fatc must update the DynATC widget whenever
the pocket map changes. This is done from `toolchange.ngc` using the
same EVAL pattern, after each IPC response confirms a stow or load
completed. The fatc `GET_INVENTORY` IPC command can also be called from
an NGC macro at carousel home time to sync the widget from the authoritative
state file, matching Probe Basic's `M13` sync pattern.

> [!NOTE]
> Probe Basic's approach has **no explicit "declare pocket" UI or command**
> beyond running tool changes. Initial setup requires the operator to
> directly edit the `.var` parameter file, or to run the carousel home
> (`M13`) after which the widget reflects whatever is already in the
> parameters. This is an ergonomic gap that fatc's `SET_POCKET` IPC command
> addresses explicitly.

#### 4.13.8 Setup UX — Open Design Question

> [!WARNING]
> **The operator-facing UX for loading/unloading the carousel is an open
> design problem and has not been solved yet.**

The core interaction — "I am setting up for a job, here are the tools I
want in the carousel, which pockets should I use and how do I tell the
machine" — requires deliberate UX design. Key open questions:

- Does the operator drag tools into pocket slots on a visual carousel
  display, or fill in a table, or use physical buttons on a panel?
- When the operator declares "pocket 3 = T5", does the machine drive the
  carousel to pocket 3 so the operator can physically load it, or does
  the operator load first and then declare?
- How are empty pockets cleared after a job where tools move around?
- How does the DRO/status panel show the current pocket map at a glance?
- What confirmation/validation flow prevents "I said T5 but loaded T6"?

**Options to evaluate:**

1. **Extend DynATC** — add an edit mode to the existing carousel widget
   where clicking a pocket slot opens a tool-number picker. Requires
   modifying Probe Basic's QML/Qt widget but re-uses the carousel graphic.

2. **Separate setup panel** — a standalone tab or dialog (the Probe Basic
   `user_atc_buttons` plugin point) with a pocket-to-tool assignment table
   and "drive carousel to pocket" buttons. Simpler to implement, potentially
   less intuitive.

3. **From-scratch carousel UI** — replace DynATC entirely with a custom
   widget tailored to this machine's workflow, retaining only the fatc
   IPC protocol as the backend interface.

4. **Combination** — use DynATC for display and animation, but build a
   separate setup panel for initial declaration and job-change workflow.

This needs to be explored before Phase 6 (GUI integration) begins. The
right answer likely depends on how the physical carousel-loading workflow
actually feels in practice on the real machine.

---

### 4.14 Tool Length Probing Integration

> [!NOTE]
> **Open design area — details TBD.** The hooks need to be identified early
> so the tool change sequence leaves the right doors open.

Automatic tool length measurement should be available as an optional step
within the tool change flow, not bolted on as a separate manual operation.
Key scenarios:

- **On every ATC load** — probe immediately after a new tool is clamped,
  before the job resumes. Guarantees `G43` offset is always current.
- **On first use of a tool** — probe only if no length offset is stored yet
  (tool is new to the table). Avoids re-probing tools whose length is known.
- **On manual change** — prompt operator whether to probe after a manual
  load, since manual changes are more likely to involve tools the system
  hasn't seen before.
- **On demand** — operator-triggered probe from the ATC tab, e.g. "re-probe
  T<n>" after a tool is re-sharpened or a collet is changed.

**Sequence integration point:**

The natural place is immediately after the `M103` Z_CLEAR step in
`toolchange.ngc`, before `endsub [1]`. At that point the tool is clamped,
Z is clear, and the interpreter is still inside the remap. An optional
subroutine call to a tool-setter routine can be inserted conditionally
based on a flag or the tool table state.

**Dependencies:**

- Probe Basic includes tool-setter routines (`tool_touch_off.ngc`,
  `toolsetter_wco.ngc`, etc.) that can potentially be called as subroutines
- The tool setter's position must be known and reachable from inside the
  remap (G53 coordinates)
- `G43 H<tool>` must be applied *after* probing, not before — the current
  `toolchange.ngc` must not apply G43 prematurely

**Interaction with manual-only tools (§4.13.5):**

Manual changes are the case where auto-probing is most valuable, since
the operator is more likely to be loading an unfamiliar or recently
modified tool. The manual change prompt (§4.13.5) should include a
"probe after load" option.

---

## 5. HAL Interface

### 5.1 Input Pins (from machine — may be Mesa GPIO or Marlin-reported)

| Pin | Type | Description |
|---|---|---|
| `fatc.tool-clamped-sensor` | bit | Tool clamped in spindle |
| `fatc.tool-unclamped-sensor` | bit | Tool released from spindle |
| `fatc.spindle-tool-sense` | bit | Spindle pre-sense: tool present in spindle (if Mesa GPIO) |

> Additional sensor pins TBD based on what's wired to Mesa vs Marlin.
> Marlin handles its own endstops internally for homing.
> The carousel pocket sensor is read via Marlin (`M119` or input query),
> not as a direct HAL pin.

### 5.2 Output Pins (to machine actuators)

| Pin | Type | Description |
|---|---|---|
| `fatc.drawbar-activate` | bit | Power drawbar solenoid (if Mesa-controlled) |
| `fatc.air-blast-activate` | bit | Air blast solenoid for taper cleaning (if Mesa-controlled) |

> Carousel motor/solenoid outputs are now handled by the Marlin controller,
> not via HAL output pins. The Python component sends G-code instead.

### 5.3 Command/Control Pins

| Pin | Type | Description |
|---|---|---|
| `fatc.tool-change` | bit IN | Tool change request (from iocontrol) |
| `fatc.tool-changed` | bit OUT | Tool change complete (to iocontrol) |
| `fatc.tool-prep-number` | s32 IN | Requested tool number |
| `fatc.tool-prepared` | bit OUT | Tool prep complete |
| `fatc.abort` | bit IN | Abort current operation, safe state |
| `ffatc.error-reset` | bit IN | Reset from error state |
| `fatc.retry` | bit IN | Retry last failed step (recovery) |
| `fatc.home-command` | bit IN | Command carousel to home (both axes) |
| `fatc.z-position` | float IN | Machine Z axis position (for zone-based safety) |
| `fatc.machine-enabled` | bit IN | Machine is enabled (from motion.motion-enabled) |

### 5.4 Status Pins

| Pin | Type | Description |
|---|---|---|
| `fatc.is-homed` | bit OUT | Carousel has been homed (both axes) |
| `fatc.current-pocket` | s32 OUT | Current carousel pocket number |
| `fatc.tool-in-spindle` | s32 OUT | Tool number currently in spindle |
| `fatc.state` | s32 OUT | Current state machine state (numeric) |
| `fatc.error` | bit OUT | Component is in error state |
| `fatc.error-code` | s32 OUT | Numeric error code |
| `fatc.ready` | bit OUT | Component is idle and ready |
| `fatc.busy` | bit OUT | Tool change in progress |
| `fatc.marlin-connected` | bit OUT | Serial connection to Marlin is active |
| `fatc.carousel-extended` | bit OUT | Carousel is at tool-change position |
| `fatc.carousel-retracted` | bit OUT | Carousel is at home/stowed position |
| `fatc.pocket-occupied` | bit OUT | Current pocket has a tool (from carousel sensor) |
| `fatc.spindle-occupied` | bit OUT | Spindle has a tool (from pre-sense sensor) |
| `fatc.inventory-valid` | bit OUT | Tool-pocket map has been verified by scan |
| `fatc.program-stop` | bit OUT | Request program stop (wired to halui.program.stop) |
| `fatc.z-clear-request` | bit OUT | Request Z move to clearance height |
| `fatc.z-engage-request` | bit OUT | Request Z move to tool-change height |

---

## 6. Marlin G-Code Protocol

### 6.1 Commands Used

| G-code | Purpose |
|---|---|
| `G28 C` | Home carousel rotation axis |
| `G28 X` | Home linear extend/retract axis |
| `G28` | Home all axes |
| `G0 Cnnn` | Rapid move rotation to position (degrees) |
| `G0 Xnnn` | Rapid move linear axis to position (mm) |
| `G1 Cnnn Fnnn` | Controlled rotation with feedrate |
| `G1 Xnnn Fnnn` | Controlled linear move with feedrate |
| `G90` | Absolute positioning mode |
| `G91` | Relative positioning mode |
| `M92` | Set steps-per-unit (for axis calibration) |
| `M114` | Report current position |
| `M119` | Report endstop status |
| `M112` | Emergency stop |
| `M201` | Set max acceleration per axis |
| `M203` | Set max feedrate per axis |
| `M204` | Set default acceleration |
| `M410` | Quick stop (decel to stop) |
| `M500` | Save settings to Marlin EEPROM |

The component can send `M92`, `M201`, `M203`, and `M204` on startup to
configure Marlin's motion parameters from the `fatc.ini` file rather than
relying solely on values compiled into the Marlin firmware. This allows
tuning feedrates and acceleration profiles without reflashing Marlin.

### 6.2 Marlin Serial Protocol

- Baud rate: typically 115200 or 250000
- Line-based: send command + `\n`, wait for `ok\n`
- Marlin buffers a limited number of commands (typically 4)
- Temperature reports (`T:...`) may be interleaved — must be filtered/ignored
- Startup: Marlin sends firmware version string then `start`

### 6.3 Axis Mapping (TBD)

The Marlin controller's logical axes are mapped to physical ATC functions:

| Marlin Axis | ATC Function | Units |
|---|---|---|
| X | Linear extend/retract | mm |
| C | Carousel rotation | degrees |

This mapping is configurable in `ffatc.ini`. The C axis requires Marlin to be
built with `AXIS4_NAME 'C'` and `AXIS4_ROTATES` (see Q3 discussion).

---

## 7. Configuration

### 7.1 INI File (`ffatc.ini`)

```ini
[ATC]
# Carousel geometry
POCKETS = 12

# Serial connection to Marlin controller
SERIAL_PORT = /dev/ttyUSB0
BAUD_RATE = 115200

# Marlin axis mapping
LINEAR_AXIS = X
ROTATION_AXIS = C

# Carousel rotation positions (in Marlin axis units)
# Position of pocket 1, pocket spacing calculated from POCKETS count
POCKET_1_POSITION = 0.0
ROTATION_FULL_TURN = 360.0

# Linear positions (in Marlin axis units)
LINEAR_RETRACT_POSITION = 0.0
LINEAR_EXTEND_POSITION = 100.0

# Feedrates (Marlin units/min)
ROTATION_FEEDRATE = 3000
LINEAR_FEEDRATE = 2000

# Timeouts (seconds)
SERIAL_TIMEOUT = 2.0
HOME_TIMEOUT = 30.0
MOVE_TIMEOUT = 15.0
DRAWBAR_CLAMP_TIMEOUT = 2.0
DRAWBAR_UNCLAMP_TIMEOUT = 2.0
STARTUP_TIMEOUT = 10.0

# Persistent state file
STATE_FILE = fatc_state.json

# Polling interval (seconds)
POLL_INTERVAL = 0.01

# Auto-home behavior: startup | first-use | machine-enable | manual
AUTO_HOME = startup

# Debug logging
DEBUG = 0
```

---

## 8. State Machine

### 8.1 Top-Level States

```
INIT ──► CONNECTING ──► HOMING ──► IDLE
                                     │
                          (tool-change request)
                                     │
                                     ▼
                               STOW_TOOL ──► LOAD_TOOL ──► COMPLETE ──► IDLE
                                  │               │
                                  ▼               ▼
                               ERROR ◄────────── ERROR
                                  │
                           (error-reset / retry)
                                  │
                                  ▼
                           IDLE or (retry previous step)
```

> [!NOTE]
> **Parallel motion (see 4.5):** The pre-dock optimization requires tracking
> both machine Z position and ATC state concurrently. The state machine may
> need composite states (e.g. `WAITING_Z_AND_ROTATING`) or a separate
> "readiness" tracker for the machine side. This can start simple (purely
> sequential) and be optimized later.
>
> **Error recovery (see 4.10.4):** ERROR states must be designed as
> recoverable — they can transition back to the failed step via `retry`
> or to IDLE via `error-reset`. Not dead-ends.

### 8.2 INIT / CONNECTING States

1. Open serial port
2. Wait for Marlin startup message
3. Send `M114` to verify communication
4. Transition to HOMING (if auto-home) or IDLE

### 8.3 STOW_TOOL Sub-States

1. `STOW_FIND_POCKET` — Find empty pocket in map
2. `STOW_ROTATE` — Send rotation G-code, wait for `ok`
3. `STOW_EXTEND` — Send linear extend G-code, wait for `ok`
4. `STOW_SIGNAL_Z` — Signal LinuxCNC to lower Z (via HAL pin)
5. `STOW_UNCLAMP` — Activate drawbar, wait for unclamped sensor
6. `STOW_SIGNAL_Z_CLEAR` — Signal LinuxCNC to raise Z
7. `STOW_RETRACT` — Send linear retract G-code, wait for `ok`

### 8.4 LOAD_TOOL Sub-States

1. `LOAD_ROTATE` — Send rotation G-code, wait for `ok`
2. `LOAD_EXTEND` — Send linear extend G-code, wait for `ok`
3. `LOAD_SIGNAL_Z` — Signal LinuxCNC to lower Z
4. `LOAD_CLAMP` — Deactivate drawbar, verify clamped sensor
5. `LOAD_SIGNAL_Z_CLEAR` — Signal LinuxCNC to raise Z
6. `LOAD_RETRACT` — Send linear retract G-code, wait for `ok`

---

## 9. Integration with LinuxCNC

### 9.1 HAL Loading

```hal
loadusr -Wn fatc ./fatc --ini fatc.ini --name fatc
```

### 9.2 HAL Wiring

```hal
# Tool change signals (from iocontrol or via remap)
net tool-change-request  iocontrol.0.tool-change      => fatc.tool-change
net tool-change-confirmed fatc.tool-changed            => iocontrol.0.tool-changed
net tool-prep-number     iocontrol.0.tool-prep-number  => fatc.tool-prep-number
net tool-prepared        fatc.tool-prepared             => iocontrol.0.tool-prepared

# Drawbar (if Mesa GPIO)
net drawbar-sol          fatc.drawbar-activate          => hm2_7i80.0.gpio.NNN.out
net tool-clamped         hm2_7i80.0.gpio.NNN.in        => fatc.tool-clamped-sensor
net tool-unclamped       hm2_7i80.0.gpio.NNN.in        => fatc.tool-unclamped-sensor

# Safety interlocks
net estop-out                                          => fatc.abort
net machine-is-enabled   motion.motion-enabled         => fatc.machine-enabled
net fatc-program-stop    fatc.program-stop              => halui.program.stop

# Z position monitoring (for zone-based safety)
net z-pos-fb             joint.2.pos-fb                => fatc.z-position
```

### 9.3 Tool Change Remap

With this architecture, the G-code remap (`toolchange.ngc`) becomes a thin wrapper:
1. Handle Z-axis motion (raise to clearance, lower to engagement)
2. Signal the ATC component to do its work via User M-codes (blocking IPC)
3. Return success or fault to the interpreter

See Q1 for the chosen IPC mechanism.

---

## 10. Open Questions & Discussion

### Q1: Z-Axis Motion Control — RESOLVED: User M-codes + Unix Socket IPC

The Python component cannot directly command LinuxCNC axis motion. MDI
commands (`linuxcnc.command().mdi()`) are **not available during M6** because
the interpreter is occupied executing the remap subroutine — you cannot
switch to `MODE_MDI` while a program is running.

**Solution: User M-codes (M101–M103) as synchronous RPC over a Unix domain socket.**

When LinuxCNC executes a User M-code (`M101`–`M199`), it runs the corresponding
executable found on `PROGRAM_PREFIX` or `SUBROUTINE_PATH`, passes P and Q
arguments on the command line, and **blocks the interpreter until the process
exits**. A non-zero exit code causes LinuxCNC to raise a fault. This is
exactly a synchronous, blocking RPC — no polling, no timing races.

`fatc.py` runs a `socketserver.ThreadingUnixStreamServer` on `/tmp/fatc.sock`.
The M-code scripts are tiny Python programs (~15 lines each) that open a
connection, send a JSON command, wait for a JSON response, and exit with 0
(success) or 1 (error). The interpreter blocks for the entire duration.

**M-code scripts and their roles:**

| M-code | Script   | Command sent | Blocks until                          |
|--------|----------|--------------|---------------------------------------|
| M101   | `M101`   | `BEGIN`      | Carousel extended, READY_FOR_Z        |
| M102   | `M102`   | `Z_ENGAGED`  | Drawbar op complete, DRAWBAR_DONE     |
| M103   | `M103`   | `Z_CLEAR`    | Full sequence complete                |

`toolchange.ngc` becomes:

```gcode
o<toolchange> sub

o10 if [#<_task> EQ 0]
    o<toolchange> endsub [1]
    M2
o10 endif

o20 if [#<selected_tool> EQ #<tool_in_spindle>]
    o<toolchange> endsub [1]
    M2
o20 endif

G53 G0 Z0                              ; raise Z
M101 P#<selected_tool>                 ; BEGIN — blocks until carousel extended
; stow phase only if tool in spindle:
o100 if [#<tool_in_spindle> GT 0]
    G53 G0 Z#<_ini[ATC]TC_HEIGHT>      ; lower Z
    M102                               ; Z_ENGAGED (stow) — blocks until drawbar done
    G53 G0 Z0                          ; retract Z
    M103                               ; Z_CLEAR (stow) — blocks until load ready
o100 endif
G53 G0 Z#<_ini[ATC]TC_HEIGHT>          ; lower Z
M102                                   ; Z_ENGAGED (load) — blocks until drawbar done
G53 G0 Z0                              ; retract Z
M103                                   ; Z_CLEAR (load) — blocks until complete

o<toolchange> endsub [1]
o<toolchange> endsub
```

**Socket protocol:**

Each M-code script sends one newline-delimited JSON object and reads one
back before exiting:

Request: `{"cmd": "BEGIN", "tool": 3}\n`
Response: `{"ok": true}\n`  or  `{"ok": false, "error": "no empty pocket"}\n`

**Why this is better than M66/M68 HAL pin handshake:**

- `M101`–`M103` block the interpreter completely — zero timing race
- No HAL AIO/DIO pins consumed for inter-process signalling
- Error propagation is native: bad exit code → LinuxCNC fault, with message
- `toolchange.ngc` is ~20 readable lines; no `M64`/`M65`/`M66`/`M68` ceremony
- fatc.py state machine is driven by socket connections, not edge detection
  on a pin polled every 10 ms

**Timeline (parallel motion still supported):**

```
Time ────────────────────────────────────────────────────────────►

NGC:   [M101 blocks─────────────────────────][G53 Z engage][M102 blocks─][G53 Z0][M103]
fatc:  [rotate────────────────][extend──────][READY_FOR_Z ][drawbar op  ][DONE   ][retract]
```

fatc begins carousel rotation immediately on receiving `BEGIN`. The M101
call returns only once the carousel is fully extended (READY_FOR_Z), so
Z engage and carousel extend happen in parallel during M101's block.

> [!NOTE]
> M-code script files (`M101`, `M102`, `M103`) must be placed on
> `PROGRAM_PREFIX` or one of the `SUBROUTINE_PATH` directories and must
> be executable (`chmod +x`). They are plain Python scripts with a
> `#!/usr/bin/env python3` shebang.
> The socket path `/tmp/fatc.sock` is configurable via the INI `[ATC]` section.

---

### Q2: Drawbar Control Path — RESOLVED: Mesa GPIO

The power drawbar solenoid and clamp/unclamp sensors will be wired to
**Mesa 7i80 GPIO**, controlled via HAL pins from the fatc component. The
physical location of the existing drawbar solenoid valves (on the spindle
head) makes Mesa I/O the most sensible wiring path. This is also consistent
with the existing VFD drawbar interlock logic and avoids adding serial
round-trip latency to a safety-relevant operation.

---

### Q3: Marlin Axis Mapping — RESOLVED

**Final configuration (verified on hardware):**

| Marlin Axis | Internal Slot | ATC Function          | Units   | Driver            | Endstop    |
|-------------|---------------|-----------------------|---------|-------------------|------------|
| X           | X (slot 1)    | Linear extend/retract | mm      | TMC2208_STANDALONE | X-min (pin 3)  |
| C           | I (slot 4)    | Carousel rotation     | degrees | TMC2208_STANDALONE | Z-min (pin 18) |
| Y           | Y (slot 2)    | *Placeholder*         | —       | A4988 (no motor)  | —          |
| Z           | Z (slot 3)    | *Placeholder*         | —       | A4988 (no motor)  | —          |

**Firmware:** Marlin `bugfix-2.1.x` (upgraded from 2.0.9.10 for native
`AXIS4_ROTATES` support). Source tracked as git submodule.

**Key configuration in `Configuration.h`:**

```cpp
#define I_DRIVER_TYPE  TMC2208_STANDALONE  // Carousel rotation (C axis)
#define AXIS4_NAME 'C'
#define AXIS4_ROTATES
#define I_STOP_PIN 18         // Homing switch on Z-min connector
#define I_MIN_POS 0
#define I_MAX_POS 360         // Full carousel rotation (degrees)
```

G-code examples: `G0 C90` (rotate to 90°), `G0 C180` (rotate to 180°),
`G28 C` (home rotation axis). Units are native degrees.

> [!NOTE]
> Y and Z driver types must remain defined (even with no physical motor)
> because Marlin requires contiguous axis slots up to the I axis (slot 4).
> Their feedrate/acceleration values are set to safe minimums.

**Build environment:** `linuxcnc/atc/marlin/build.sh` — overlays custom
config onto submodule and builds via PlatformIO. Upload via
`./build.sh upload --upload-port /dev/ttyUSB0`.

**Build stats:** RAM 34.7%, Flash 19.1% (ATmega2560)

---

### Q4: Scope & Development Approach — RESOLVED

See [development.md](development.md) for the full development plan, phasing,
hybrid sim/hardware environment setup, and file layout.

**Summary:** Incremental 6-phase build from serial foundation through GUI
integration. Development uses a hybrid environment: LinuxCNC simulator for
HAL/G-code integration + real Marlin hardware (RAMPS + dummy motors) for
serial protocol validation. The existing Probe Basic sim config (in the
`pico-cnc-hmi` repo) provides the LinuxCNC simulator environment.

---

### Q5: GUI Visualization — GUI-AGNOSTIC VIA HAL

Per design principle 3.2, the ATC component has **no dependency on any specific
GUI**. All state is exposed via HAL pins (section 5.4). Any GUI — Probe Basic
DynATC, AXIS, gmoccapy, or a future custom UI — can read these pins to display
carousel status.

**Probe Basic / DynATC integration (current GUI):**

DynATC is a Probe Basic widget that displays carousel pocket assignments,
current position, and tool-in-spindle status. After reviewing its source,
DynATC is a **pure visualization widget** with only 4 callable methods
(`store_tool`, `rotate`, `atc_message`, `load_tools`) and no error handling,
recovery UI, or HAL pin integration.

The boilerplate subroutines feed DynATC via `DEBUG EVAL` calls, which is a
Probe Basic-specific mechanism. For our GUI-agnostic component, the integration
path would be a **thin adapter** — a separate Probe Basic-specific module that
reads ATC HAL pins and calls DynATC methods. This adapter would be:
- Optional — the ATC component works without it
- Replaceable — swapping GUIs means writing a new adapter, not changing the component
- Low priority — core functionality works with just HAL pin monitoring (halshow/halmeter)

> [!NOTE]
> DynATC also provides **no error recovery UI** — no retry buttons, no operator
> dialogs, no guided recovery. If we build error recovery UI (see 4.10.4), it
> would be a separate panel regardless of which GUI is in use.

---

### Q6: Auto-Home Behavior — CONFIGURABLE

Homing behavior should be configurable via INI. Options to support:
- Home on component startup (after Marlin connection established)
- Home on first tool-change request
- Home on machine-enable signal
- Manual home only (via `fatc.home-command` HAL pin)

---

### Q7: Component-to-GUI Communication — **RESOLVED**

**Decision:** Unix domain socket (`/tmp/fatc.sock`) with JSON newline-delimited
messages. Implemented in Phase 4 as part of the M-code IPC layer.

The socket serves two purposes:
1. **Sequencer handshake**: M101/M102/M103 Python executables connect during
   toolchange.ngc execution to coordinate Z motion with the carousel state machine.
2. **Management commands**: GUI (`fatc_atc.py`) and future tooling issue `HOME`,
   `GET_INVENTORY`, `SET_POCKET`, `SET_SPINDLE`, `SET_INVENTORY_VALID` commands
   at any time to read/modify state without touching the state machine.

See CONTEXT.md §4.5 for the full protocol specification.

---

## 11. Non-Functional Requirements

- **Language**: Python 3 (system Python on LinuxCNC 2.9)
- **Dependencies**: `linuxcnc` Python module (HAL bindings), `pyserial`, standard library
- **Startup**: Must establish Marlin connection within configurable timeout
- **Latency**: State machine cycle time ≤ 10ms (serial I/O is inherently slower)
- **Logging**: Configurable debug logging to stderr (captured by LinuxCNC)
- **Safety**: Emergency stop Marlin (`M112`) on component exit / crash / estop
- **Testing**: Unit-testable state machine with mock HAL and mock serial interfaces

---

## 12. Stretch Goals

These are future enhancements that are out of scope for the initial
implementation but worth designing around. Captured here so the architecture
doesn't preclude them.

### 12.1 Automatic Tool / Tool Holder Identification

Eliminate manual tool-to-pocket mapping by automatically identifying tools
(or tool holders) in the carousel. This would enable:
- Automatic tool-pocket map rebuilding without operator input
- Tool verification before load (confirm the right tool is in the pocket)
- Detection of misplaced or swapped tools
- "Load any instance of tool X" rather than "load from pocket Y"

**Approach A: Visual identification with OpenCV**
- A camera embedded in the ATC enclosure, aimed at the pocket facing
  the spindle (same position as the pocket presence sensor)
- Each tool holder has a laser-etched or printed QR code / Data Matrix
  encoding a tool holder ID
- The Python component captures an image, decodes the tag, and maps it
  to a tool number via a lookup table
- **Architecture fit:** Runs entirely on the LinuxCNC host (Python + OpenCV).
  Camera is a USB webcam. No Marlin changes needed. Identification can be
  done during a carousel sweep (rotate to each pocket, capture, decode).

**Approach B: NFC / RFID**
- Each tool holder has an embedded NFC or RFID tag
- A reader mounted at the sensing position reads the tag as pockets pass by
- **Architecture fit:** The reader could connect to either:
  - The Marlin board (via I2C/SPI to a reader module) — would require 100%
    custom Marlin firmware work to add NFC/RFID support, which is not a
    standard Marlin feature
  - A separate USB reader on the LinuxCNC host — simpler integration, the
    Python component reads it directly
  - The Marlin board could alternatively just report raw serial data from
    the reader, with decoding done in the Python component

**Comparison:**

| Aspect | OpenCV / QR | NFC / RFID |
|---|---|---|
| Hardware cost | Low (USB webcam ~$10) | Medium (reader + tags per holder) |
| Reliability | Depends on lighting, cleanliness, camera quality | Very reliable (no line of sight needed) |
| Read speed | ~100-500ms per decode | ~50-100ms per read |
| Durability | QR code can wear/get dirty | Tags are embedded, very durable |
| Marlin changes | None | Significant (custom firmware) or separate USB |
| Coolant/chip resistance | Poor (camera lens fouling) | Excellent (reader behind enclosure wall) |
| Information capacity | High (QR can encode arbitrary data) | Medium (depends on tag type) |

> [!NOTE]
> Both approaches require a **tool holder ID → tool number** mapping table,
> since the same holder can carry different tools over its lifetime. This
> table would be managed by the Python component alongside the pocket map.
> The identification system answers "which holder is in this pocket" rather
> than "which tool is in this pocket" — the operator still associates tools
> to holders.

### 12.2 Other Potential Stretch Goals

- **Tool change time optimization** — measure and log tool change times,
  identify bottlenecks, tune feedrates and parallel motion
- **Tool life tracking** — integrate with LinuxCNC's tool wear tracking
  to flag tools nearing end of life during tool changes
- **Carousel wear monitoring** — track total rotations, extend/retract
  cycles for maintenance scheduling
- **Remote status** — expose ATC state via a simple HTTP API or MQTT for
  integration with shop monitoring systems

---

## 13. File Structure

```
frankenmill/linuxcnc/atc/
├── README.md              # Project overview
├── requirements.md        # This document
├── fatc.ini                # Default INI configuration
├── atc                    # Main executable (Python, chmod +x)
├── atc_hal.py             # HAL pin definitions and setup
├── atc_state_machine.py   # State machine implementation
├── atc_serial.py          # Marlin serial communication layer
├── atc_config.py          # INI file parsing
├── atc_persistence.py     # Tool-pocket map persistence
└── tests/
    ├── test_state_machine.py
    ├── test_serial.py
    └── test_persistence.py
```

---

## Appendix A: LinuxCNC `carousel.comp` Analysis

### A.1 What It Is

`carousel.comp` is a built-in LinuxCNC **realtime HAL component** (written in C
using LinuxCNC's `.comp` preprocessor) by Andy Pugh. It handles the logic of
rotating a carousel-type tool changer to a specific pocket, with support for
various position feedback encoding schemes.

**Key:** It runs in the **servo thread** (realtime), not as a userspace process.

### A.2 What It Does

The component handles **rotation positioning only** — it does not manage:
- Extend/retract motion
- Drawbar control
- Z-axis coordination
- Tool-to-pocket tracking
- The overall tool-change sequence

What it *does* well:
- **Shortest-path calculation** for bidirectional rotation
- **Multiple feedback encodings**: gray code, binary, BCD, single sensor per
  pocket, index+pulse, edge counting, and **stepper/encoder counts** (`counts` mode)
- **Homing**: rotates forward until index sensor found
- **Deceleration and alignment**: supports overshoot compensation with
  configurable decel-time and slow alignment moves
- **Motor control output**: forward/reverse bits, velocity float, and
  direct position target (counts-target) for stepgen

### A.3 The `counts` Mode — Most Relevant

In `counts` mode (most relevant for stepper-driven carousels), carousel.comp:
- Takes raw step counts from a Mesa stepgen as input
- Calculates which pocket the carousel is at based on `scale` (counts per pocket)
- Outputs `motor-fwd`/`motor-rev` bits and `motor-vel` float to drive a stepgen
- Outputs `counts-target` for position-mode stepgen control
- Handles homing via an index sensor
- Provides `ready` output when in-position, `active` while moving

### A.4 How It Would Work with Mesa Stepgen (Alternative Architecture)

If the carousel stepper were wired directly to a Mesa 7i80 stepgen output
(you have `stepgen.03` available — only 0–2 are used for X/Y/Z), the architecture
would be:

```
LinuxCNC servo thread:
  carousel.comp ──► stepgen.03 ──► Mesa 7i80 GPIO ──► stepper driver ──► motor

  carousel.0.motor-vel ──► stepgen.03.velocity-cmd
  stepgen.03.counts ──► carousel.0.counts
  home-sensor GPIO ──► carousel.0.sense-0
```

The carousel stepper would be controlled at the servo-thread rate (1kHz) with
the same step timing precision as the X/Y/Z axes.

### A.5 Comparison: carousel.comp + Mesa Stepgen vs. Marlin

| Aspect | carousel.comp + Mesa | Marlin Controller |
|---|---|---|
| **Rotation positioning** | ✅ Handled by carousel.comp | ✅ Handled by Marlin firmware |
| **Extend/retract axis** | ❌ Need separate solution (another stepgen + custom logic) | ✅ Native multi-axis in Marlin |
| **Timing precision** | ✅ Realtime servo thread (1kHz) | ⚠️ USB-serial latency (~10-50ms round trip) |
| **Homing** | ✅ Built-in (index sensor mode) | ✅ Built-in (`G28`) |
| **Shortest-path rotation** | ✅ Built-in (bidirectional mode) | ❌ Must implement in Python component |
| **State machine / orchestration** | ❌ Must build separately | ❌ Must build separately |
| **Drawbar control** | ❌ Separate HAL logic needed | ❌ Separate logic needed |
| **Tool tracking** | ❌ Not handled | ❌ Not handled |
| **Extra I/O (sensors)** | Need Mesa GPIO pins | Can use Marlin I/O pins |
| **Wiring complexity** | More wires to Mesa card (steppers, drivers, sensors) | Single USB cable to Marlin board |
| **Additional hardware** | Need stepper drivers (separate or on Mesa breakout) | Self-contained board with drivers |
| **Acceleration control** | Via stepgen maxvel/maxaccel | Native in Marlin |
| **Code we write** | Less rotation logic, more HAL glue | More serial protocol, less HAL glue |
| **Debugging** | HAL scope, halshow | Serial terminal, Marlin console |
| **Failure modes** | Realtime watchdog protection | USB disconnect = lost control |

### A.6 What We Can Learn from carousel.comp

Regardless of the Marlin vs. Mesa decision, carousel.comp's logic is worth studying:

1. **Shortest-path algorithm** (lines 366–410 in source): the bidirectional
   pocket selection math handles wraparound correctly and is directly portable
   to our Python component

2. **State machine structure** (states: WAITING → DIR_CHOOSE → MOVE →
   WAIT_STOP → alignment → WAIT_ENABLE_FALSE): clean pattern for our state
   machine design

3. **Deceleration and alignment**: the overshoot-compensate-and-realign
   sequence is relevant if we need precise pocket positioning

4. **Homing sequence**: rotate forward, watch for index, set position = 1

5. **Debounce handling**: configurable debounce cycles before accepting position

### A.7 The Architecture Question

**Why Marlin might still be the right choice:**

1. **Two axes, one board** — Marlin natively handles two coordinated stepper axes
   with a single controller. With Mesa, you'd need carousel.comp for rotation
   plus a separate mechanism (another component or manual HAL logic) for the
   linear extend/retract axis. There is no built-in LinuxCNC component for a
   linear tool-change slide.

2. **Self-contained hardware** — a Marlin board includes stepper drivers,
   endstop inputs, and auxiliary I/O on a single, inexpensive PCB. Using Mesa
   stepgen requires separate stepper drivers, more wiring, and consumes Mesa
   I/O resources.

3. **Isolation** — keeping the ATC motion on a separate controller provides
   clean fault isolation from the main CNC motion system. A Marlin firmware
   crash doesn't affect LinuxCNC's servo thread.

4. **Flexibility** — Marlin's G-code interface is higher-level and easier to
   iterate on during development. Changing motion parameters doesn't require
   reconfiguring HAL files.

5. **Portability** — the Marlin-based ATC could theoretically be adapted to
   other CNC controllers beyond LinuxCNC.

**Why Mesa/carousel.comp might be better:**

1. **Deterministic timing** — realtime servo thread vs. USB-serial latency.
   For a tool changer (not a cutting operation), this may not matter much.

2. **Fewer moving parts** — no serial protocol layer, no USB disconnect
   handling, no Marlin firmware to maintain. The tool changer is just more
   HAL components.

3. **Mature, tested code** — carousel.comp has been in LinuxCNC for years
   and is well-proven.

4. **Unified system** — everything runs under one controller, one config,
   one monitoring framework.

> [!IMPORTANT]
> **Recommendation:** The Marlin approach is well-suited for this project because:
> - The ATC has **two axes** (rotation + linear), and Marlin handles both natively
> - Tool changing is **not time-critical** at the millisecond level — USB-serial
>   latency is acceptable for move-and-wait sequences
> - The self-contained board significantly simplifies hardware integration
> - carousel.comp's rotation logic is straightforward to reimplement in Python
>   for the shortest-path calculation
> - The linear axis has no LinuxCNC equivalent component at all
>
> However, carousel.comp's **state machine patterns and shortest-path math**
> should be studied and adapted for our Python implementation.

---

## Appendix B: Probe Basic Boilerplate Reference

The following tables document the Probe Basic boilerplate subroutines and I/O
mappings. These are **not in use on the FrankenMill** and are not part of the
new ATC design, but were reviewed as reference material.

### B.1 Boilerplate Subroutines

| Subroutine | Purpose |
|---|---|
| `toolchange.ngc` | Top-level M6 remap — orchestrates the full tool change sequence |
| `m10.ngc` | Move carousel to a specific pocket (shortest path) |
| `m11.ngc` | Rotate carousel CW by P steps |
| `m12.ngc` | Rotate carousel CCW by P steps |
| `m13.ngc` | Home the carousel (rotate CW until home index) |
| `m21.ngc` | Unload tool from spindle into carousel pocket |
| `m22.ngc` | Load tool from carousel pocket into spindle |
| `m24.ngc` | Activate power drawbar (unclamp tool) |
| `m25.ngc` | Deactivate power drawbar (clamp tool) |
| `extendatc.ngc` | Extend carousel to tool-change position |
| `retractatc.ngc` | Retract carousel to home/stowed position |

### B.2 Reference Digital I/O Mapping

| Digital Out (M64/M65) | Function |
|---|---|
| P0 | Carousel extend solenoid |
| P1 | Carousel retract solenoid |
| P2 | Power drawbar solenoid |
| P3 | Carousel motor reverse (CCW) |
| P4 | Carousel motor forward (CW) |

| Digital In (M66 wait) | Function |
|---|---|
| P0 | Carousel in-position (retracted) sensor |
| P1 | Carousel out-position (extended) sensor |
| P2 | Tool unclamped sensor |
| P4 | Carousel rotation index sensor |
| P5 | Tool clamped sensor |

### B.3 Reference Persistent Parameters

| Parameter | Purpose |
|---|---|
| `#3989` | Carousel homed flag (volatile) |
| `#3990` | Current pocket position (persistent) |
| `#3991` | Tool in spindle (persistent) |
| `#4001–#4024` | Tool-to-pocket map (persistent) |
