#!/bin/sh
# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
# Run a command against the working tree inside the project's dev container.
#
# Everything in this repository builds and tests inside a container (see
# AGENTS.md); this is the entry point for doing that from a plain shell, without
# the VS Code devcontainer. The image is the repository Dockerfile, and the
# working tree is bind-mounted read-write at /run/<service> (devkit.conf), so edits are live.
#
# The virtualenv and node_modules live in named volumes rather than the bind
# mount: they hold platform-specific binaries built for the container's musl
# libc, and sharing them with the host would break both.
#
# Those volumes are per checkout: in a linked git worktree they take its name, so
# two sessions working in two worktrees do not sync one another's virtualenv out
# from under a running test. See scripts/checkout-id.sh.
#
# Usage:
#   scripts/container.sh make test
#   scripts/container.sh sh -c 'uv run pytest tests/api -q'
#   DEVKIT_PYTHON=3.14 scripts/container.sh make test
#
# A run of the whole suite - `make test`, `test-python`, `verify`, `verify-fresh` or
# `test-coverage`, or `uv run scripts/ci/verify.py` - is sized to the machine and takes one of its
# gate slots, whether it comes through a container-* target or straight here: see
# scripts/container-size.sh. A command wrapped in `sh -c` is not recognised as one.
#
# Environment:
#   DEVKIT_PYTHON        run against this Python version instead of the image
#                     default, using a throwaway venv (this is how the CI matrix
#                     versions are reproduced locally)
#   DEVKIT_ISOLATED      set to `1` where the whole environment is disposable (an isx
#                     box): the command runs in this checkout directly, with no
#                     container. DEVKIT_TIMEOUT applies; DEVKIT_PYTHON, DEVKIT_DETACH and
#                     DEVKIT_PORT are refused, since each needs a container of its own
#   DEVKIT_RUNTIME       container runtime to use (default: podman, else docker)
#   DEVKIT_VENV_VOLUME   name the virtualenv volume outright, rather than taking the
#                     one derived from this checkout
#   DEVKIT_NODE_VOLUME   likewise for the node_modules volume
#   DEVKIT_NODE_INSTALL  set to `skip` to run without checking that node_modules
#                     matches the lockfile. The way back in when package.json and
#                     yarn.lock disagree, since the check refuses that and this
#                     runs before every command - see ensure-node-modules.sh
#   DEVKIT_TIMEOUT       seconds before the command inside the container is killed
#                     (default 3600; up to 7200 for a sized run given fewer
#                     workers than its command can use). `0` removes the deadline
#   DEVKIT_MEMORY        how much memory the container may have (default 6g, or
#                     what the sizing chooses). `0` removes the cap
#   DEVKIT_SIZE_WORKERS  the most pytest workers the command can use, set by the
#                     Makefile's whole-suite targets: the workers and the memory
#                     cap are then sized to the machine, and the run holds one of
#                     its gate slots, which every worktree shares, waiting for
#                     one where all are held. `0` or unset leaves the fixed
#                     settings, as does CI. See scripts/container-size.sh
#   PYTEST_WORKERS    given outright, the worker count a sized run uses
#   DEVKIT_CONTAINER_NAME  name the container outright, rather than taking the one
#                     derived from this checkout and this process
#   DEVKIT_DETACH        start the container and return, leaving it running. Implies
#                     no deadline - the point of it is to outlive this shell - and
#                     prints the name to reach it by
#   DEVKIT_PORT          a port to publish: `8080:8080`
#
# A container you mean to keep - a server to browse, a shell to work in - wants the
# deadline off and a name of its own. `make container-keep` is that, spelled once
# so that neither a person nor an agent has to assemble it; `make container-shell`
# likewise for a shell. Underneath, for what they do not cover:
#
#   DEVKIT_TIMEOUT=0 DEVKIT_CONTAINER_NAME=<service>-preview \
#       scripts/container.sh sh -c 'uv run python -m app'
#
# The name, the cap and the deadline are here because none of them were, and a
# machine accumulated eleven of these, two spinning a core apiece for a day, under
# names that said nothing about whose they were. docs/troubleshooting.md has the
# whole account, and how to tell a stuck container from a working one.

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
# shellcheck source=scripts/checkout-id.sh
. "$script_dir/checkout-id.sh"
# shellcheck source=scripts/checkout-resources.sh
. "$script_dir/checkout-resources.sh"
# shellcheck source=scripts/seed-volumes.sh
. "$script_dir/seed-volumes.sh"
# shellcheck source=scripts/container-size.sh
. "$script_dir/container-size.sh"

suffix=$(checkout_suffix "$repo_root")
git_dir=$(worktree_git_dir "$repo_root")

IMAGE_NAME="${DEVKIT_IMAGE:-$DEVKIT_SERVICE-dev}"
VENV_VOLUME="${DEVKIT_VENV_VOLUME:-$DEVKIT_SERVICE-venv$suffix}"
NODE_VOLUME="${DEVKIT_NODE_VOLUME:-$DEVKIT_SERVICE-node$suffix}"

# The pid, because two commands can be in flight in one checkout - a suite and the
# e2e typecheck, say - and a name they shared would be a name one of them stole.
CONTAINER_NAME="${DEVKIT_CONTAINER_NAME:-$DEVKIT_SERVICE-run$suffix-$$}"
# The label does not have to be unique, and is what a sweep can select on.
CHECKOUT_LABEL="${suffix#-}"
CHECKOUT_LABEL="${CHECKOUT_LABEL:-primary}"

# `0` and unset both mean off, and are the only two spellings of it: a third would
# be one more thing for these to agree about than they need.
off() {
    case "$1" in 0 | '') return 0 ;; *) return 1 ;; esac
}

TIMEOUT="${DEVKIT_TIMEOUT:-3600}"
MEMORY="${DEVKIT_MEMORY:-6g}"
off "$TIMEOUT" && TIMEOUT=''
off "$MEMORY" && MEMORY=''

# Anything but `1` and `0` is refused rather than read as true. `DEVKIT_DETACH=false` taken
# for "detached" would start a container, print that it had started, and return 0 - with
# the command's output and its status never reaching the caller.
DETACH="${DEVKIT_DETACH:-}"
case "$DETACH" in
    1) ;;
    0 | '') DETACH='' ;;
    *) echo "DEVKIT_DETACH: expected 1 or 0, got '$DETACH'" >&2; exit 2 ;;
esac

# A deadline on a container whose whole purpose is to outlive this shell would be a
# deadline on the thing it was started for.
if [ -n "$DETACH" ]; then
    TIMEOUT=''
fi

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <command> [args...]" >&2
    exit 2
fi

# Already inside a disposable environment (DEVKIT_ISOLATED=1, see AGENTS.md): there is nothing
# left to isolate from, and a container in it would only rebuild the image. The command runs
# where it stands. What is for a container of its own has no meaning here and is refused,
# where running it as something else would answer a different question.
if [ "${DEVKIT_ISOLATED:-}" = 1 ]; then
    if [ -n "${DEVKIT_PYTHON:-}" ] || [ -n "$DETACH" ] || [ -n "${DEVKIT_PORT:-}" ]; then
        echo "DEVKIT_PYTHON, DEVKIT_DETACH and DEVKIT_PORT need a container of their own: not with DEVKIT_ISOLATED=1" >&2
        exit 2
    fi
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    cd "$repo_root"
    sh "$script_dir/ensure-node-modules.sh" "$repo_root/assets"
    if [ -z "$TIMEOUT" ]; then
        exec "$@"
    fi
    # In the background with `wait`, as the container path below does, so that a signal sent
    # to this script is acted on and not held until the command ends - and passed on, since
    # `timeout` is a child and outlives a parent that is killed.
    exec 3<&0
    timeout -s TERM -k 30 "$TIMEOUT" "$@" <&3 &
    child=$!
    # `|| true`: under `set -e` the child's own status would end the trap at the wait, and
    # the status this exits with would be whichever that was and not the one named here.
    trap 'kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null || true; exit 143' TERM
    trap 'kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null || true; exit 130' INT
    status=0
    wait "$child" || status=$?
    exit "$status"
fi

if [ -n "${DEVKIT_RUNTIME:-}" ]; then
    runtime="$DEVKIT_RUNTIME"
elif command -v podman >/dev/null 2>&1; then
    runtime=podman
elif command -v docker >/dev/null 2>&1; then
    runtime=docker
else
    echo "no container runtime found: install podman or docker" >&2
    exit 1
fi

# Build once, then reuse. The layer cache makes a rebuild cheap when the
# Dockerfile has not changed, so this stays a no-op on a warm machine.
if ! "$runtime" image exists "$IMAGE_NAME" 2>/dev/null &&
    ! "$runtime" image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo "building $IMAGE_NAME from Dockerfile..." >&2
    "$runtime" build -t "$IMAGE_NAME" "$repo_root"
fi

seed_volume "$VENV_VOLUME" "$DEVKIT_SERVICE-venv"
seed_volume "$NODE_VOLUME" "$DEVKIT_SERVICE-node"

# A run of the whole suite is sized to the machine and takes one of its gate slots, on descriptor 8,
# which the `podman run` below inherits - and, under podman, conmon and the network helper with it:
# the slot is held until the container ends, and let go by the kernel however it ends. Last before
# the run, so that an image build or a volume copy holds no slot.
#
# The command itself says whether it is such a run, where no target said so (gate_suite_workers);
# and a PYTEST_WORKERS on its make line beats the environment's in there, so it is the count here.
[ -n "${DEVKIT_SIZE_WORKERS+set}" ] || DEVKIT_SIZE_WORKERS=$(gate_suite_workers "$@")
make_workers=$(gate_make_workers "$@")
[ -z "$make_workers" ] || PYTEST_WORKERS=$make_workers
WORKERS=${PYTEST_WORKERS:-}
if [ -z "$DETACH" ] && gate_wanted; then
    gate_size
fi

# The inner script runs inside the container, so its variables must reach it
# unexpanded - the single quotes around it are deliberate.
# shellcheck disable=SC2016
inner='
        PATH="/root/.local/bin:$PATH"
        export PATH
        # corepack provides yarn; the Makefile expects it on PATH
        corepack enable --install-directory /root/.local/bin >/dev/null 2>&1 || true

        # uv re-reads its lockfile on every `uv run`; yarn installs when asked and
        # not otherwise, so this asks. See scripts/ensure-node-modules.sh for what
        # it compares and why an existence check was not enough.
        sh scripts/ensure-node-modules.sh assets

        if [ -n "${DEVKIT_PYTHON:-}" ]; then
            # a venv of its own, so the pinned one in the volume is left alone
            UV_PROJECT_ENVIRONMENT="/tmp/venv-${DEVKIT_PYTHON}"
            # UV_PYTHON as well as --python: the later `uv run` must find the venv built here
            UV_PYTHON="$DEVKIT_PYTHON"
            export UV_PROJECT_ENVIRONMENT UV_PYTHON
            uv python install "$DEVKIT_PYTHON" >&2
            uv sync --frozen --python "$DEVKIT_PYTHON" >&2
        fi

        if [ -n "${DEVKIT_TIMEOUT:-}" ]; then
            # SIGTERM first so a command that can stop tidily does, SIGKILL half a
            # minute later for one that will not.
            #
            # The short options, because the image is Alpine and this is busybox
            # timeout: it has no --signal or --kill-after, and rejects them rather
            # than ignoring them. GNU timeout takes this spelling too, so the line
            # survives the image changing. The status differs - busybox reports the
            # signal, 143, where GNU reports 124 - so neither is worth testing for.
            #
            # Not `exec`, and the `exit` after it is load-bearing rather than tidy:
            # busybox ash tail-execs the last command of a `-c` script, so without
            # something after it this becomes pid 1 - and timeout signals its parent,
            # which pid 1 ignores. Measured both ways: with the `exit`, a three second
            # deadline killed a thirty second sleep at 6s (exit 143); as the last
            # command of the script, a three second deadline let a twenty second sleep
            # run to completion and exit 0.
            timeout -s TERM -k 30 "$DEVKIT_TIMEOUT" "$@"
            exit $?
        else
            exec "$@"
        fi
'

# Built from the tail forward, since the git mount is there only in a worktree.
# :z relabels the bind mount for SELinux; harmless where SELinux is not enforcing.
set -- "$IMAGE_NAME" sh -euc "$inner" sh "$@"
if [ -n "$git_dir" ]; then
    # at the same absolute path it has on the host: that is what the worktree's
    # .git file points at, and what `make check` needs to see a repository
    set -- -v "$git_dir:$git_dir:z" "$@"
fi
set -- -v "$repo_root:$DEVKIT_RUN_DIR:z" \
    -v "$VENV_VOLUME:$DEVKIT_RUN_DIR/.venv" \
    -v "$NODE_VOLUME:$DEVKIT_RUN_DIR/assets/node_modules" \
    -w "$DEVKIT_RUN_DIR" \
    -e "HOME=/root" \
    -e "DEVKIT_PYTHON=${DEVKIT_PYTHON:-}" \
    -e "DEVKIT_TIMEOUT=$TIMEOUT" \
    -e "DEVKIT_NODE_INSTALL=${DEVKIT_NODE_INSTALL:-}" \
    "$@"

# Only when there is a count: an empty one would reach make as a value, and `?=` would keep it,
# running the suite serially.
if [ -n "$WORKERS" ]; then
    set -- -e "PYTEST_WORKERS=$WORKERS" "$@"
fi

if [ -n "$MEMORY" ]; then
    # --memory-swap at the same figure leaves the container no swap to escape into:
    # without it a runaway thrashes the disk for as long as swap lasts instead of
    # being stopped. Over the cap, the kernel kills this container's own process
    # rather than letting the host pick somebody else's.
    set -- --memory "$MEMORY" --memory-swap "$MEMORY" "$@"
fi

if [ -n "${DEVKIT_PORT:-}" ]; then
    set -- -p "$DEVKIT_PORT" "$@"
fi

if [ -n "$DETACH" ]; then
    # no --rm, because a container meant to outlive this shell is one somebody has to
    # be able to find afterwards, including after it has stopped
    set -- -d "$@"
else
    set -- --rm -i "$@"
fi

set -- "$runtime" run \
    --name "$CONTAINER_NAME" \
    --label "$DEVKIT_SERVICE.checkout=$CHECKOUT_LABEL" \
    --label "$PATH_LABEL=$repo_root" \
    "$@"

# A name given outright can be one something else is already using. Detached, that comes
# back as podman's own error and a container left in `Created` holding the name; attached,
# the removal further down would take the other container away on the way out - a rollback
# destroying what it did not create. `--type container`, because a bare `inspect` matches
# images and volumes too and would refuse over an image that shares the name. The derived
# name holds this process's pid and cannot collide, so this asks only about a given one.
if [ -n "${DEVKIT_CONTAINER_NAME:-}" ] &&
    "$runtime" inspect --type container "$CONTAINER_NAME" > /dev/null 2>&1; then
    echo "a container named $CONTAINER_NAME is already there" >&2
    echo "  $runtime rm -f $CONTAINER_NAME   removes it" >&2
    exit 1
fi

if [ -n "$DETACH" ]; then
    "$@" > /dev/null
    echo "started $CONTAINER_NAME${DEVKIT_PORT:+ on $DEVKIT_PORT}" >&2
    echo "  logs:  $runtime logs -f $CONTAINER_NAME" >&2
    echo "  shell: $runtime exec -it $CONTAINER_NAME sh" >&2
    echo "  stop:  $runtime rm -f $CONTAINER_NAME" >&2
    exit 0
fi

# `--rm` removes a container that stops, which covers every way of finishing but one:
# a client killed out from under a container that goes on running. That is what these
# are for, so they are on the signals and not on EXIT - on a normal exit `--rm` has
# already done it, and a second removal is half a second spent on every command.
# shellcheck disable=SC2329  # called from the traps below, which shellcheck cannot follow
cleanup() {
    "$runtime" rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true
}

# Not `exec`: that would replace this shell, and the traps with it.
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

# In the background with `wait`, because a trapped signal is not delivered while a
# foreground command is running - the shell defers it until that command returns, which
# for a container that is not going to stop is the case these traps exist for. Measured:
# TERM delivered at 0.1s, the removal issued at 30.0s with the run in the foreground, and
# at 0.007s with it backgrounded.
#
# `exec 3<&0` and `<&3` because a backgrounded command is given /dev/null for stdin, and
# `make container-shell` is a terminal that has to reach the container.
exec 3<&0
status=0
"$@" <&3 &
child=$!
wait "$child" || status=$?
exit "$status"
