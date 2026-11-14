#!/bin/sh
# Run the Playwright end-to-end suite entirely in containers.
#
# This is what `make e2e-test` runs. Nothing is installed on the host: the app
# runs in the project's own dev container and Playwright in the official image,
# on a shared network. (`make e2e-test-host` runs the suite directly on the host
# instead, for debugging this harness.)
#
# The browser reaches the app by container ip over plain http, which is
# deliberately NOT a secure context. That is a stricter environment than
# 127.0.0.1: APIs gated on a secure context (crypto.randomUUID, clipboard,
# service workers) are absent, so a feature depending on one fails here rather
# than in front of a user running the dev server on a LAN address.
#
# Usage:
#   scripts/e2e-container.sh                 # whole suite
#   scripts/e2e-container.sh home            # only specs matching "home"
#
# The server container, its network and its volumes are named after the checkout
# this runs in, so a second session working in another worktree gets a second
# server rather than a fight over one (see scripts/checkout-id.sh).
#
# Environment:
#   SR_RUNTIME          container runtime to use (default: podman, else docker)
#   SR_KEEP             leave the app container running afterwards, to re-run
#                        quickly
#   SR_E2E_SERVER       name the application container outright, rather than
#                        taking the name derived from this checkout
#   SR_E2E_NETWORK      likewise for the network the two containers share
#   SR_E2E_NODE_VOLUME  likewise for Playwright's own node_modules volume
#   SR_IMAGE            the application image, built from the Dockerfile when absent
#   SR_PLAYWRIGHT_IMAGE the image the browser and the suite run in
#   SR_VENV_VOLUME      as in scripts/container.sh: the volumes the application
#   SR_NODE_VOLUME      container runs from

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
# shellcheck source=scripts/checkout-id.sh
. "$script_dir/checkout-id.sh"
# shellcheck source=scripts/checkout-resources.sh
. "$script_dir/checkout-resources.sh"
# shellcheck source=scripts/seed-volumes.sh
. "$script_dir/seed-volumes.sh"

# Per checkout, so that two sessions in two worktrees each get a server of their
# own. This one matters more than the volumes do: the cleanup below is
# `rm -f $SERVER`, which on a shared name killed the suite another session was
# halfway through - and a suite that dies at test 130 of 243 looks like a broken
# branch rather than like someone else's tidying up.
suffix=$(checkout_suffix "$repo_root")

IMAGE_NAME="${SR_IMAGE:-secretary-records-dev}"
PLAYWRIGHT_IMAGE="${SR_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v1.61.1-noble}"
NETWORK="${SR_E2E_NETWORK:-secretary-records-e2e-net$suffix}"
SERVER="${SR_E2E_SERVER:-secretary-records-e2e-server$suffix}"
VENV_VOLUME="${SR_VENV_VOLUME:-secretary-records-venv$suffix}"
NODE_VOLUME="${SR_NODE_VOLUME:-secretary-records-node$suffix}"
E2E_NODE_VOLUME="${SR_E2E_NODE_VOLUME:-secretary-records-e2e-node$suffix}"
PORT=8099

# Already inside a disposable environment (SR_ISOLATED=1): the suite runs on it directly,
# as `make e2e-test-host` does, rather than in containers nested inside. The arguments
# are Playwright's, as they are below, and are not put through make, which would split
# `-g 'two words'` and run what follows a `;` in one.
if [ "${SR_ISOLATED:-}" = 1 ]; then
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    cd "$repo_root"
    # What the container path does: node_modules matched to the lockfile, and the assets built
    # every time, since a bundle left from the last run is one the suite would run against
    # while the source has moved on. Not `make e2e-test-host`, which builds them only when
    # there is no manifest, and whose recipe cannot take arguments safely. All of it inside
    # the status below, so that a failed install ends in the line a pipe can read.
    status=0
    (
        sh "$script_dir/ensure-node-modules.sh" "$repo_root/assets" &&
            (cd assets && yarn build) &&
            rm -rf state-e2e &&
            cd e2e &&
            npm install --no-audit --no-fund &&
            npx playwright install chromium &&
            npx playwright test "$@"
    ) || status=$?
    # The verdict on stdout as well: an exit status does not survive a pipe.
    if [ "$status" -eq 0 ]; then
        echo "e2e: PASSED"
    else
        echo "e2e: FAILED - exited $status, and the suite above did not pass"
    fi
    exit "$status"
fi

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

# Invoked by the trap below rather than by name, which shellcheck cannot see - and only
# notices at all now the script ends in an explicit `exit`.
# shellcheck disable=SC2329
cleanup() {
    if [ -z "${SR_KEEP:-}" ]; then
        "$runtime" rm -f "$SERVER" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM HUP

if ! "$runtime" image exists "$IMAGE_NAME" 2>/dev/null &&
    ! "$runtime" image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo "building $IMAGE_NAME from Dockerfile..." >&2
    "$runtime" build -t "$IMAGE_NAME" "$repo_root"
fi

# labelled only under the name this checkout derives: SR_E2E_NETWORK can name one that is shared
if [ "$NETWORK" = "secretary-records-e2e-net$suffix" ] && [ -n "$suffix" ]; then
    "$runtime" network create --label "$PATH_LABEL=$repo_root" "$NETWORK" >/dev/null 2>&1 || true
else
    "$runtime" network create "$NETWORK" >/dev/null 2>&1 || true
fi

# The application container runs from the same two volumes container.sh uses, so
# it seeds them the same way. Unconditionally, not only on the path that starts a
# server: a reused one is already running from them, and doing it here keeps the
# two scripts interchangeable as the first thing run in a new worktree.
seed_volume "$VENV_VOLUME" secretary-records-venv
seed_volume "$NODE_VOLUME" secretary-records-node
own_volume "$E2E_NODE_VOLUME" "secretary-records-e2e-node$suffix"

# SR_KEEP leaves the container up afterwards; this is the half that makes that worth
# anything. Starting one costs about seventy seconds - copying the tree in and building
# the frontend - which is most of the wall clock of a narrow run. The e2e directory is
# bind-mounted into the Playwright container further down, so an already-running server
# picks up spec edits with no restart at all; only a change under app/ or assets/ needs
# a fresh one.
#
# The network is checked as well as the running state: SR_E2E_NETWORK can differ between
# runs, and a server left on the previous one is unreachable from the Playwright container
# attached to this one - which would show up as a silent five-minute wait rather than as
# anything to do with reuse.
reuse=""
# $name and $_ are Go template variables, so the single quotes are deliberate
# shellcheck disable=SC2016
if [ -n "${SR_KEEP:-}" ] &&
    "$runtime" inspect "$SERVER" --format '{{.State.Running}}' 2>/dev/null | grep -q true &&
    "$runtime" inspect "$SERVER" \
        --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' 2>/dev/null |
    grep -qx "$NETWORK"; then
    reuse=1
fi

if [ -n "$reuse" ]; then
    echo "reusing the running application container: it serves app/ and assets/ as they" >&2
    echo "were when it started. Drop SR_KEEP, or remove $SERVER, after changing either." >&2
else
    "$runtime" rm -f "$SERVER" >/dev/null 2>&1 || true
    echo "starting the application container..." >&2
# The inner script runs inside the container, so its variables must reach it
# unexpanded - the single quotes around it are deliberate.
# shellcheck disable=SC2016
# The tree is copied in rather than bind-mounted: the run rewrites e2e-config.yaml
# and builds the frontend assets, neither of which belongs in the developer's
# checkout. .venv and node_modules stay on their volumes.
"$runtime" run -d --name "$SERVER" --network "$NETWORK" \
    --label "$PATH_LABEL=$repo_root" \
    -v "$repo_root:/mnt:ro" \
    -v "$VENV_VOLUME:/run/secretary-records/.venv" \
    -v "$NODE_VOLUME:/run/secretary-records/assets/node_modules" \
    "$IMAGE_NAME" \
    sh -euc '
        PATH="/root/.local/bin:$PATH"
        export PATH
        corepack enable --install-directory /root/.local/bin >/dev/null 2>&1 || true

        tar -C /mnt \
            --exclude=./.venv \
            --exclude=./assets/node_modules \
            --exclude=./.git \
            --exclude=./state \
            --exclude=./state-e2e \
            --exclude=./state-preview \
            -cf - . | tar -C /run/secretary-records -xf -
        cd /run/secretary-records

        # The same call container.sh makes, for the same reason: yarn installs when
        # asked and not otherwise. Without it the build dies on "Couldn'"'"'t find the
        # node_modules state file", which reaches the developer as an application
        # container that exited during startup. Note that .git is excluded from the
        # tar above - ensure-node-modules.sh asks git only to hash file contents,
        # which it does without needing a repository to be in.
        sh scripts/ensure-node-modules.sh assets

        (cd assets && yarn build)

        # bind on every interface so the Playwright container can reach us
        sed -i "s/host: 127.0.0.1/host: 0.0.0.0/" e2e/e2e-config.yaml

        # serve.py sets SR_E2E itself, which is what enables the test-login route.
        # One server for the whole run: every spec shares it, so a spec that changes
        # state leaves it changed for the next one. BAT grew a supervisor that gives
        # each spec file a fresh server (its e2e/supervisor.py); port it when a second
        # spec needs that isolation.
        exec uv run python e2e/serve.py
    ' >/dev/null
fi

# This network's address specifically. Ranging over every network concatenates their
# addresses into one unusable string as soon as the container is attached to more than
# one, which a reused container can be.
server_ip=$("$runtime" inspect "$SERVER" \
    --format "{{(index .NetworkSettings.Networks \"$NETWORK\").IPAddress}}")
if [ -z "$server_ip" ]; then
    echo "could not determine the application container's ip address" >&2
    exit 1
fi
base_url="http://${server_ip}:${PORT}"
host="http://${server_ip}"

ready_url="$base_url"

echo "waiting for $ready_url ..." >&2
waited=0
until "$runtime" run --rm --network "$NETWORK" "$IMAGE_NAME" \
    sh -c "wget -q -O /dev/null $ready_url/ 2>/dev/null"; do
    if ! "$runtime" inspect "$SERVER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
        echo "the application container exited during startup:" >&2
        "$runtime" logs "$SERVER" >&2 || true
        exit 1
    fi
    waited=$((waited + 5))
    if [ "$waited" -ge 300 ]; then
        echo "the application did not become ready within 300s:" >&2
        "$runtime" logs --tail 50 "$SERVER" >&2 || true
        exit 1
    fi
    sleep 5
done

echo "running the end-to-end suite against $host (not a secure context)" >&2
status=0
"$runtime" run --rm --network "$NETWORK" \
    -v "$repo_root/e2e:/e2e:z" \
    -v "$E2E_NODE_VOLUME:/e2e/node_modules" \
    -w /e2e \
    -e "SR_E2E_URL=$base_url" \
    -e "SR_E2E_HOST=$host" \
    "$PLAYWRIGHT_IMAGE" \
    sh -euc 'npm install --no-audit --no-fund >/dev/null 2>&1; exec npx playwright test "$@"' sh "$@" ||
    status=$?

# The last line says which it was, in words, on stdout - where Playwright's own summary
# goes, so a reader who piped this into `tail` still has it. An exit status does not
# survive a pipe: `scripts/e2e-container.sh | tail -25` reports tail's, which is 0
# whatever the suite did, so a run that failed one test of 336 reads as one that passed.
if [ "$status" -eq 0 ]; then
    echo "e2e: PASSED"
else
    echo "e2e: FAILED - playwright exited $status, and the suite above did not pass"
fi
exit "$status"
