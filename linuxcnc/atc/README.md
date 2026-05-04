# FrankenMill ATC — LinuxCNC Python Userspace Component

Automatic Tool Changer control component for the FrankenMill CNC milling machine.

## Overview

This is a Python userspace HAL component (`loadusr`) for LinuxCNC that manages the
carousel-style automatic tool changer on the FrankenMill. It replaces the current
G-code subroutine-based approach (M10–M13, M21–M24, toolchange.ngc) with a
centralized state machine that directly controls the carousel motor, drawbar
solenoid, and position sensors via HAL pins.

See `requirements.md` for the full requirements specification.

## Project Status

🚧 **In Development** — Requirements phase
