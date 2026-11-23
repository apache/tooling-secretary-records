#!/bin/bash
# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
set -eu

# This script is used by the service in Puppet

# shellcheck source=/dev/null
source .venv/bin/activate

BIND=$(python3 app/config.py --bind)
ACCESS_LOG=$(python3 app/config.py --access-log)
PID_FILE=$(python3 app/config.py --pid-file)

# One process, and explicitly so: the app keeps in-memory singletons, the websocket broker among
# them, that only make sense with exactly one worker.
#
# workers=0 rather than hypercorn's default of 1: under workers=1 hypercorn spawns that one worker
# as a daemonic `multiprocessing.Process`, and a daemonic process may not have children - which
# rules out a process pool for CPU-bound work, and fails with "AssertionError: daemonic processes
# are not allowed to have children". workers=0 runs the worker function in this process instead:
# the same one process and the same singletons, with no daemon restriction.
exec hypercorn server:application -b "${BIND}" --access-logfile "${ACCESS_LOG}" --pid "${PID_FILE}" --worker-class uvloop --workers 0
