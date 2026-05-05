#!/bin/bash
# Wrapper so loadusr can redirect fatc output to a log file.
# HAL cannot handle single-quoted shell commands in loadusr args.
cd "$(dirname "$0")"
exec python3 -u ../../atc/fatc/fatc.py --ini atc_sim.ini --name fatc >> fatc.log 2>&1
