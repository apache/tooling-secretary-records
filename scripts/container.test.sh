#!/bin/sh
# What container.sh asks the runtime for, and what it must go on asking for.
#
# Four of those arguments are there for nobody's command, and each because its absence
# cost a day: a container with no name is one nobody can attribute, with no deadline is
# one that spins forever, with no memory cap is one whose overrun is charged to whichever
# process the host's OOM killer picks, and `--rm` alone leaves behind any container whose
# client was killed out from under it.
#
# The runtime is replaced by a stub on PATH, which records its argv and exits. Two things
# follow from that, and both are limits worth knowing:
#
#   - Position is asserted explicitly, because the recorded argv is flat. A flag after the
#     image name is one podman passes to the command instead of acting on, and reads
#     exactly like a flag that works.
#   - The inner script is recorded but never run, so what this can say about the deadline
#     is that it is passed in and that the script's shape is right. It cannot say that it
#     fires. Running a container here is not an option: `make check` is itself run inside
#     one, where there is no runtime to nest. The deadline's enforcement has been measured
#     by hand instead - see the comment it guards in container.sh.
#
# Run by prek (see .pre-commit-config.yaml), and so by `make check`.

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
script="$here/container.sh"

failures=0
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# `volume` matters more than it looks: it makes seed-volumes.sh believe the volumes are
# there, and so stops it issuing a `run` of its own that would overwrite the argv this file
# reads. `inspect` answers `INSPECT_STATUS` so the collision check can be driven both ways,
# `run` exits `RUN_STATUS`, and `BLOCK_RUN` holds it open for the signal cases.
cat > "$work/podman" <<'STUB'
#!/bin/sh
case "$1" in
    image) exit 0 ;;
    volume) exit 0 ;;
    inspect) exit "${INSPECT_STATUS:-1}" ;;
    run)
        shift
        printf '%s\n' "$@" > "$ARGV_FILE"
        [ -n "${BLOCK_RUN:-}" ] && sleep 5
        exit "${RUN_STATUS:-0}"
        ;;
    rm) shift; printf '%s\n' "$@" >> "$RM_FILE"; exit 0 ;;
    *) echo "stub: unexpected call: $*" >&2; exit 64 ;;
esac
STUB
chmod +x "$work/podman"

status=0
capture() {
    : > "$work/argv"
    : > "$work/rm"
    status=0
    ARGV_FILE="$work/argv" \
        RM_FILE="$work/rm" \
        PATH="$work:$PATH" \
        SR_RUNTIME=podman \
        "$@" \
        "$script" true > "$work/out" 2>&1 || status=$?
}

fail() {
    echo "FAIL: $1" >&2
    echo "  argv was (to the image name):" >&2
    sed -n '/^secretary-records-dev$/q;p' "$work/argv" | sed 's/^/    /' >&2
    failures=$((failures + 1))
}

# Everything before the image name: what podman acts on. After it is the command, which
# podman hands over untouched - so a flag that lands there does nothing at all.
flags() {
    sed -n '/^secretary-records-dev$/q;p' "$work/argv"
}

has_pair() {
    flags | awk -v flag="$1" -v value="$2" '
        $0 == flag { expecting = 1; next }
        expecting && $0 == value { found = 1 }
        { expecting = 0 }
        END { exit found ? 0 : 1 }
    '
}

pair_matches() {
    flags | awk -v flag="$1" -v pattern="$2" '
        $0 == flag { expecting = 1; next }
        expecting && $0 ~ pattern { found = 1 }
        { expecting = 0 }
        END { exit found ? 0 : 1 }
    '
}

has_flag() {
    flags | grep -qxF -- "$1"
}

lacks_flag() {
    ! has_flag "$1"
}

# --- the four, as they are by default -----------------------------------------------

capture env

# The value and its shape, not just the flag: `--name` with nothing after it is what an
# empty default produces, and the pid is what keeps two runs in one checkout apart.
pair_matches "--name" "^secretary-records-run.*-[0-9][0-9]*$" ||
    fail "named for the checkout and the process, so concurrent runs do not collide"
pair_matches "--label" "^secretary-records[.]checkout=..*$" || fail "a label naming the checkout, to sweep on"
has_pair "--memory" "6g" || fail "a default memory cap, so an overrun is charged to itself"
has_pair "--memory-swap" "6g" || fail "no swap to escape the cap into"
has_flag "-i" || fail "stdin reaches the container, or container-shell has no terminal"
has_flag "--rm" || fail "a container that stops removes itself"
has_pair "-w" "/run/secretary-records" || fail "the working directory is the bind mount"
flags | grep -q ':/run/secretary-records:z$' || fail "the working tree is still mounted"
grep -qxF "SR_TIMEOUT=3600" "$work/argv" || fail "a default deadline reaches the container"
[ "$status" -eq 0 ] || fail "a command that succeeds is reported as succeeding"

# The shape of the inner script, which is as far as a stub can see. The `exit` is the
# load-bearing half: without it busybox ash tail-execs the timeout, which then becomes pid
# 1 and signals a parent that ignores it - so the deadline silently never fires.
grep -q 'timeout -s TERM -k 30' "$work/argv" || fail "the container is told to enforce the deadline"
grep -A1 'timeout -s TERM -k 30' "$work/argv" | grep -q 'exit' ||
    fail "and does not tail-exec it, which is the shape in which it never fires"

# --- and as a long-lived container needs them ----------------------------------------

capture env SR_TIMEOUT=0
grep -qxF "SR_TIMEOUT=" "$work/argv" || fail "SR_TIMEOUT=0 removes the deadline rather than setting one"

capture env SR_MEMORY=0
lacks_flag "--memory" || fail "SR_MEMORY=0 leaves the cap off entirely"

capture env SR_TIMEOUT=90 SR_MEMORY=2g
grep -qxF "SR_TIMEOUT=90" "$work/argv" || fail "SR_TIMEOUT carries through"
has_pair "--memory" "2g" || fail "SR_MEMORY carries through"

capture env SR_NODE_INSTALL=skip
grep -qxF "SR_NODE_INSTALL=skip" "$work/argv" ||
    fail "SR_NODE_INSTALL reaches the container, or its documented escape hatch does nothing"

# --- a container meant to outlive the shell that started it ---------------------------

capture env SR_DETACH=1 SR_CONTAINER_NAME=secretary-records-keep SR_PORT=8080:8080
has_flag "-d" || fail "SR_DETACH starts the container detached"
has_pair "-p" "8080:8080" || fail "SR_PORT publishes the port"
has_pair "--name" "secretary-records-keep" || fail "a detached container is one you can name"
has_pair "--memory" "6g" || fail "and is capped like any other - the branches share one argv"
grep -qxF "SR_TIMEOUT=" "$work/argv" ||
    fail "a detached container has no deadline - it is started to outlive this shell"
lacks_flag "--rm" ||
    fail "a detached container is not removed the moment it stops, or it cannot be inspected"

# Anything but 1 and 0 is refused: `SR_DETACH=false` read as true would start a container,
# say so, and return 0 with the command's output and status never reaching the caller.
capture env SR_DETACH=false
[ "$status" -ne 0 ] || fail "an unreadable SR_DETACH is refused rather than taken for true"

# --- a name that is already taken -----------------------------------------------------

capture env SR_CONTAINER_NAME=secretary-records-keep INSPECT_STATUS=0
[ "$status" -ne 0 ] || fail "a name another container holds is refused, not run into"
[ ! -s "$work/rm" ] || fail "and refusing it removes nothing - that container is not ours"

# The derived name holds a pid and cannot collide, so the check must not ask about it -
# asking would make every ordinary run pay an inspect.
capture env INSPECT_STATUS=0
[ "$status" -eq 0 ] || fail "the derived name is not put through the collision check"

# --- what the exit status says --------------------------------------------------------

capture env RUN_STATUS=7
[ "$status" -eq 7 ] || fail "a command that failed is reported with the status it failed with"

# --- the removal `--rm` cannot do -----------------------------------------------------

# A client killed while its container runs on is the case `--rm` misses, and the only one a
# trap can answer. The stub holds the run open so there is something to interrupt, and the
# run is backgrounded in container.sh precisely so the signal is not deferred until it ends.
signalled() {
    : > "$work/argv"
    : > "$work/rm"
    ARGV_FILE="$work/argv" RM_FILE="$work/rm" BLOCK_RUN=1 \
        PATH="$work:$PATH" SR_RUNTIME=podman \
        "$script" true > "$work/out" 2>&1 &
    blocked=$!

    waited=0
    while [ ! -s "$work/argv" ] && [ "$waited" -lt 100 ]; do
        sleep 0.1
        waited=$((waited + 1))
    done

    kill -"$1" "$blocked" 2>/dev/null || true

    # A second at most: the removal has to happen on the signal, not when the run it was
    # waiting on finally ends. Five seconds from now the stub returns and `--rm` would do
    # it anyway, which is the difference this is here to tell.
    waited=0
    while [ ! -s "$work/rm" ] && [ "$waited" -lt 10 ]; do
        sleep 0.1
        waited=$((waited + 1))
    done

    wait "$blocked" 2>/dev/null || true
}

signalled TERM
grep -q . "$work/rm" || fail "TERM removes the container, promptly, which --rm cannot do"

# The name it removed, not merely that it removed something.
started=$(flags | grep -A1 -xF -- '--name' | tail -1)
grep -qxF "$started" "$work/rm" || fail "and removes the container this run started, not another"

# INT cannot be delivered here: a shell sets it to ignore in a job it backgrounds, so the
# harness above reaches TERM and nothing else. At a terminal Ctrl-C reaches container.sh
# because it is in the foreground process group, which is the case the trap is for - so
# what is left to assert is that the trap is set at all.
grep -q "trap 'cleanup; exit 130' INT" "$script" ||
    fail "Ctrl-C has a trap too, which this can check for and cannot exercise"

if [ "$failures" -ne 0 ]; then
    echo "$failures check(s) failed" >&2
    exit 1
fi

echo "container.sh: all checks passed"
