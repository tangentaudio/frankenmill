# FrankenMill ATC — Marlin Firmware Configuration

## Overview

Custom Marlin 2.0.x firmware configuration for the ATC carousel controller.
This directory contains **only the customized configuration files** — the full
Marlin source is not included in this repository.

## Marlin Version

- **Base version:** Marlin 2.0.9.10
- **Source:** https://github.com/MarlinFirmware/Marlin/tree/2.0.9.10

## Board

- **Board:** TBD (document your board here)
- **PlatformIO env:** TBD

## Axis Mapping

| Marlin Axis | ATC Function       | Units   |
|-------------|--------------------|---------|
| X           | Linear extend/retract | mm    |
| C (axis 4)  | Carousel rotation  | degrees |

## Build Instructions

### Prerequisites

- [PlatformIO](https://platformio.org/) (CLI or VS Code extension)
- Git

### Build Steps

```bash
# 1. Clone Marlin at the pinned version
git clone -b 2.0.9.10 --depth 1 \
  https://github.com/MarlinFirmware/Marlin.git /tmp/marlin-atc-build

# 2. Copy our custom configuration files over the defaults
cp Configuration.h /tmp/marlin-atc-build/Marlin/
cp Configuration_adv.h /tmp/marlin-atc-build/Marlin/

# 3. Copy our PlatformIO config
cp platformio.ini /tmp/marlin-atc-build/

# 4. Build
cd /tmp/marlin-atc-build
pio run

# 5. Flash (adjust port as needed)
pio run -t upload --upload-port /dev/ttyUSB0
```

## Configuration Changes from Stock Marlin

Key customizations in `Configuration.h`:

- **Axis setup:** X (linear) + C (rotational) — only two physical axes
- **Steps/unit:** Tuned for the specific stepper motors and mechanical ratios
- **Endstops:** Configured for homing switches on both axes
- **Feedrates & acceleration:** Tuned for ATC motion requirements
- **Serial:** USB-serial communication with LinuxCNC host

See the requirements specification (`../requirements.md`, section Q3) for
detailed axis mapping discussion.
