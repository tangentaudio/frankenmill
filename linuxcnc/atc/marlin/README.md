# FrankenMill ATC — Marlin Firmware Configuration

## Overview

Custom Marlin firmware configuration for the ATC carousel controller.
This directory contains **only the customized configuration files** — the full
Marlin source is pulled in as a git submodule.

## Marlin Version

- **Branch:** `bugfix-2.1.x` (git submodule)
- **Source:** https://github.com/MarlinFirmware/Marlin
- **Previous version:** 2.0.9.10 (upgraded for `AXIS4_ROTATES` support)

## Board

- **Board:** `BOARD_RAMPS_CREALITY` (Creality CR-10 V2 RAMPS derivative)
- **MCU:** ATmega2560
- **PlatformIO env:** `mega2560`

## Axis Mapping

| Marlin Axis | Internal | ATC Function          | Units   |
|-------------|----------|-----------------------|---------|
| X           | X        | Linear extend/retract | mm      |
| C           | I (4th)  | Carousel rotation     | degrees |

> The C axis uses Marlin's I axis slot internally (`I_DRIVER_TYPE`),
> renamed to 'C' via `AXIS4_NAME` and marked rotational with `AXIS4_ROTATES`.

## Directory Structure

```
marlin/
├── Configuration.h          # Custom config (tracked in repo)
├── Configuration_adv.h      # Custom advanced config (tracked)
├── platformio.ini           # PlatformIO config (if customized)
├── build.sh                 # Overlay + build script
├── README.md                # This file
└── Marlin/                  # Git submodule → MarlinFirmware/Marlin
```

## Prerequisites

- [PlatformIO](https://platformio.org/) CLI (`pip install platformio`)
- Git (with submodule support)

## Setup

```bash
# Clone the repo with submodules (first time)
git clone --recursive https://github.com/your/frankenmill.git

# Or, if already cloned, initialize the submodule
git submodule update --init --recursive
```

## Building

```bash
# Build firmware
./build.sh

# Build and upload (adjust port as needed)
./build.sh upload --upload-port /dev/ttyUSB0

# Clean build artifacts
./build.sh clean
```

The build script automatically copies `Configuration.h` and
`Configuration_adv.h` over the Marlin defaults before building.

## Configuration Changes from Stock Marlin

Key customizations in `Configuration.h`:

- **Board:** `BOARD_RAMPS_CREALITY`
- **Extruders:** 0 (not a printer)
- **Temperature sensors:** All disabled
- **Drivers:** TMC2208_STANDALONE (X, I/C axes)
- **Axis 4:** `I_DRIVER_TYPE TMC2208_STANDALONE`, `AXIS4_NAME 'C'`, `AXIS4_ROTATES`
- **Steps/unit:** Tuned for stepper motors and mechanical ratios
- **Endstops:** Homing switches on X (linear) and C (rotation)
- **Feedrates & acceleration:** Tuned for ATC motion requirements
- **Serial:** USB @ 115200 baud

See `../requirements.md` (section Q3) for axis mapping discussion.
