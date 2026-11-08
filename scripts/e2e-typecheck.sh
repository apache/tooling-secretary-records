#!/bin/sh
# Type-check the Playwright specs, in the Playwright image, without starting the app.
#
# This is what `make e2e-typecheck` runs, and it is the only thing that checks these
# files: Playwright transpiles a spec and runs it, so a spec whose types are wrong runs
# anyway and passes for as long as the wrongness does not happen to throw.
#
# It does not catch a deliberate `as unknown as` - a double assertion beats any checker,
# which is why the one on #729 needed a reviewer rather than a tool. It catches the
# commoner ways: a name misspelt in a typed position (TS2820), an untyped parameter that
# silently unchecks every call made on it (TS7006 - there were nine), an index that might
# be `undefined` (TS18048), and a binding nothing reads (TS6133).
#
# Seconds rather than minutes: no application container, no browser, nothing served. The
# dependencies come from the same volume the suite installs into (see
# scripts/e2e-container.sh), so the first run in a checkout pays an `npm install` and the
# rest do not.
#
# Usage:
#   scripts/e2e-typecheck.sh
#
# Environment:
#   SR_RUNTIME             container runtime to use (default: podman, else docker)
#   SR_PLAYWRIGHT_IMAGE    the image to run in, which carries the node the compiler runs
#                           on - pin it to reproduce a version-specific result
#   SR_E2E_NODE_VOLUME     name Playwright's node_modules volume outright, rather than
#                           taking the name derived from this checkout

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
# shellcheck source=scripts/checkout-id.sh
. "$script_dir/checkout-id.sh"
# shellcheck source=scripts/checkout-resources.sh
. "$script_dir/checkout-resources.sh"

suffix=$(checkout_suffix "$repo_root")

PLAYWRIGHT_IMAGE="${SR_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v1.61.1-noble}"
NODE_VOLUME="${SR_E2E_NODE_VOLUME:-secretary-records-e2e-node$suffix}"

if [ -n "${SR_RUNTIME:-}" ]; then
    runtime="$SR_RUNTIME"
elif command -v podman >/dev/null 2>&1; then
    runtime=podman
elif command -v docker >/dev/null 2>&1; then
    runtime=docker
else
    echo "no container runtime found: install podman or docker" >&2
    exit 1
fi

echo "type-checking the end-to-end specs" >&2
own_volume "$NODE_VOLUME" "secretary-records-e2e-node$suffix"
# The inner script runs inside the container, so the single quotes around it are
# deliberate - as in e2e-container.sh, which quotes its own the same way.
status=0
# shellcheck disable=SC2016
"$runtime" run --rm \
    -v "$repo_root/e2e:/e2e:z" \
    -v "$NODE_VOLUME:/e2e/node_modules" \
    -w /e2e \
    "$PLAYWRIGHT_IMAGE" \
    sh -euc '
        # stdout only: an install that fails has its reason on stderr, and dropping that
        # leaves the failure to surface as a compiler that is not there - which reads as a
        # broken checkout rather than as a bad install. `-e` stops here rather than going on
        # to a typecheck that cannot run.
        npm install --no-audit --no-fund --loglevel=error >/dev/null
        exec npm run --silent typecheck
    ' || status=$?

# On stdout, where tsc puts the errors, so that a reader who piped this into `tail` has
# the verdict too - an exit status does not survive a pipe.
if [ "$status" -eq 0 ]; then
    echo "e2e-typecheck: PASSED"
else
    echo "e2e-typecheck: FAILED - tsc exited $status"
fi
exit "$status"
