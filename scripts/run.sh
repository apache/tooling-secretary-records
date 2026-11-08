#!/bin/bash
set -eu

# This script is used by the secretary-records service in Puppet

# shellcheck source=/dev/null
source .venv/bin/activate

BIND=$(python3 app/config.py --bind)
ACCESS_LOG=$(python3 app/config.py --access-log)
PID_FILE=$(python3 app/config.py --pid-file)

# One process, and explicitly so: the app keeps in-memory singletons (the database engine,
# and the websocket broker once there is one) that only make sense with exactly one worker.
#
# workers=0 rather than hypercorn's default of 1: under workers=1 hypercorn spawns that one
# worker as a daemonic `multiprocessing.Process`, and a daemonic process may not have
# children - which rules out a process pool for CPU-bound work, such as the OCR and PDF
# conversion this tool will do. workers=0 runs the worker function in this process instead.
exec hypercorn server:application -b "${BIND}" --access-logfile "${ACCESS_LOG}" --pid "${PID_FILE}" --worker-class uvloop --workers 0
