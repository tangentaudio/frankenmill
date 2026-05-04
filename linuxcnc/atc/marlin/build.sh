#!/bin/bash
#
# FrankenMill ATC — Marlin Firmware Build Script
#
# Overlays custom configuration files onto the Marlin submodule
# and builds the firmware using PlatformIO.
#
# Usage:
#   ./build.sh              # Build only
#   ./build.sh upload       # Build and upload (pass any pio args)
#   ./build.sh clean        # Clean build artifacts
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARLIN_DIR="${SCRIPT_DIR}/Marlin"
MARLIN_SRC="${MARLIN_DIR}/Marlin"

# Find PlatformIO CLI — check PATH first, then VS Code extension location
if ! command -v pio &>/dev/null; then
    if [ -x "${HOME}/.platformio/penv/bin/pio" ]; then
        export PATH="${HOME}/.platformio/penv/bin:${PATH}"
        echo "Using PlatformIO from VS Code extension: $(which pio)"
    else
        echo "ERROR: PlatformIO CLI (pio) not found."
        echo "Install via: pip install platformio"
        echo "  or install the PlatformIO IDE extension in VS Code"
        exit 1
    fi
fi

# Verify submodule is initialized
if [ ! -f "${MARLIN_SRC}/Configuration.h" ]; then
    echo "ERROR: Marlin submodule not initialized."
    echo "Run: git submodule update --init --recursive"
    exit 1
fi

# Overlay custom configuration files
echo "=== Overlaying custom configuration ==="
for f in Configuration.h Configuration_adv.h; do
    if [ -f "${SCRIPT_DIR}/${f}" ]; then
        cp -v "${SCRIPT_DIR}/${f}" "${MARLIN_SRC}/${f}"
    else
        echo "WARNING: ${f} not found in ${SCRIPT_DIR}, using Marlin defaults"
    fi
done

# Handle PlatformIO config if we have a custom one
if [ -f "${SCRIPT_DIR}/platformio.ini" ]; then
    # Only overlay if it's not our placeholder
    if ! grep -q "PLACEHOLDER" "${SCRIPT_DIR}/platformio.ini"; then
        cp -v "${SCRIPT_DIR}/platformio.ini" "${MARLIN_DIR}/platformio.ini"
    fi
fi

# Build
echo ""
echo "=== Building Marlin firmware ==="
echo "Working directory: ${MARLIN_DIR}"
echo ""

if [ "${1:-}" = "clean" ]; then
    cd "${MARLIN_DIR}" && pio run -t clean
elif [ "${1:-}" = "upload" ]; then
    shift
    cd "${MARLIN_DIR}" && pio run -t upload "$@"
else
    cd "${MARLIN_DIR}" && pio run "$@"
fi
