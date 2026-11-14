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
        SR_ISOLATED='' \
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
        PATH="$work:$PATH" SR_RUNTIME=podman SR_ISOLATED='' \
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

# --- where the whole environment is disposable ----------------------------------------

# SR_ISOLATED=1 means no container: the stub runtime is on PATH and must not be asked for
# anything, and the command runs in this checkout with its own exit status. The node check
# is skipped because it needs yarn, which is not what is under test.
isolated() {
    : > "$work/argv"
    status=0
    ARGV_FILE="$work/argv" RM_FILE="$work/rm" PATH="$work:$PATH" SR_RUNTIME=podman \
        SR_ISOLATED=1 SR_NODE_INSTALL=skip "$@" > "$work/out" 2>&1 || status=$?
}

isolated "$script" true
[ "$status" -eq 0 ] || fail "SR_ISOLATED=1 runs the command"
[ ! -s "$work/argv" ] || fail "and asks the runtime for nothing: a container here would rebuild the image"

# From somewhere else: run from the checkout root, as `make check` does, it would pass
# whether or not the script changed directory.
# shellcheck disable=SC2016  # the inner shell expands these, not this one
isolated sh -c 'cd "$1" && exec "$2" pwd' sh "$work" "$script"
[ "$(cat "$work/out")" = "$(CDPATH='' cd -- "$here/.." && pwd)" ] ||
    fail "the command runs in this checkout, where the container would have mounted it"

# shellcheck disable=SC2016  # the inner shell expands this, not this one
isolated "$script" sh -c 'printf %s "$PATH"'
case $(cat "$work/out") in
    "$HOME/.local/bin":*) : ;;
    *) fail "yarn, which corepack puts in ~/.local/bin, is on the PATH the command gets" ;;
esac

isolated "$script" sh -c 'exit 7'
[ "$status" -eq 7 ] || fail "and its exit status is the command's own, under a deadline"
isolated env SR_TIMEOUT=0 "$script" sh -c 'exit 7'
[ "$status" -eq 7 ] || fail "and with no deadline"

# A signal sent to the script has to reach the command: `timeout` is a child, and a parent
# killed without passing it on leaves the command running for up to the whole deadline.
#
# The command says which process it is, rather than being found by name: a name matches
# another session's process, and a command that never started would read as one that stopped.
rm -f "$work/pid"
# shellcheck disable=SC2016  # the inner shell expands these, not this one
SR_ISOLATED=1 SR_NODE_INSTALL=skip SR_TIMEOUT=60 \
    "$script" sh -c 'echo $$ > "$1"; exec sleep 31' sh "$work/pid" > /dev/null 2>&1 &
blocked=$!
waited=0
while [ ! -s "$work/pid" ] && [ "$waited" -lt 50 ]; do
    sleep 0.1
    waited=$((waited + 1))
done
if [ ! -s "$work/pid" ]; then
    fail "the command the signal test starts did not start"
else
    kill -TERM "$blocked" 2>/dev/null || true
    # Looked at a second after the signal and not after the script has ended: a script that
    # waits for the command it did not stop only ends when the command does, and by then
    # there is nothing left to find.
    sleep 1
    if kill -0 "$(cat "$work/pid")" 2>/dev/null; then
        kill -KILL "$(cat "$work/pid")" 2>/dev/null || true
        fail "TERM to the script stops the command it started, not only the script"
    fi
fi
status=0
wait "$blocked" 2>/dev/null || status=$?
[ "$status" -eq 143 ] || fail "a script stopped by TERM says so with 143, as the container path does"

# The status is the script's own and not whatever the command left: a command that handles
# TERM and exits 7 must still give 143. A command killed by the TERM gives it anyway, so the
# case above cannot tell a script that says 143 from one that passes the status on.
rm -f "$work/pid"
# shellcheck disable=SC2016  # the inner shell expands these, not this one
SR_ISOLATED=1 SR_NODE_INSTALL=skip SR_TIMEOUT=60 \
    "$script" sh -c 'trap "exit 7" TERM; echo $$ > "$1"; while :; do sleep 0.2; done' sh "$work/pid" \
    > /dev/null 2>&1 &
blocked=$!
waited=0
while [ ! -s "$work/pid" ] && [ "$waited" -lt 50 ]; do
    sleep 0.1
    waited=$((waited + 1))
done
kill -TERM "$blocked" 2>/dev/null || true
status=0
waited=0
# bounded: the command is left running if the script never ends
while kill -0 "$blocked" 2>/dev/null && [ "$waited" -lt 50 ]; do
    sleep 0.1
    waited=$((waited + 1))
done
if kill -0 "$blocked" 2>/dev/null; then
    kill -KILL "$blocked" 2>/dev/null || true
    [ ! -s "$work/pid" ] || kill -KILL "$(cat "$work/pid")" 2>/dev/null || true
    fail "a script given TERM ends, even when its command handles the signal"
fi
wait "$blocked" 2>/dev/null || status=$?
[ "$status" -eq 143 ] || fail "and says 143 whatever status its command ended with (was $status)"

# What the command reads has to reach it: it is started in the background, where stdin
# would otherwise be /dev/null.
printf 'hello' | isolated "$script" cat
[ "$(cat "$work/out")" = hello ] || fail "standard input reaches the command, under a deadline"

# --- the two e2e wrappers, in a layout of their own ----------------------------------
#
# A copy of what they need, so that what they remove (`state-e2e`) and build is nobody's
# checkout, and with stand-ins for the tools: what is under test is which they call, with
# which arguments, and what they say afterwards.
fake="$work/repo"
mkdir -p "$fake/scripts" "$fake/assets" "$fake/e2e" "$work/stubs" "$work/home"
for f in container.sh e2e-container.sh e2e-typecheck.sh checkout-id.sh checkout-resources.sh \
    seed-volumes.sh; do
    cp "$here/$f" "$fake/scripts/$f"
done
# Every stand-in says what it was called with and where it ran, so that a call made from
# the wrong directory, or not made, is something to assert on. STUB_FAIL is `name` for
# every call of that tool, or `name:first-argument` for one of them.
cat > "$work/stubs/stub" <<'STUB'
#!/bin/sh
name=$(basename "$0")
printf '%s' "$name" >> "$STUB_LOG"
for a in "$@"; do printf ' [%s]' "$a" >> "$STUB_LOG"; done
printf ' @ %s\n' "$(pwd)" >> "$STUB_LOG"
[ "${STUB_FAIL:-}" = "$name" ] && exit 3
[ "${STUB_FAIL:-}" = "$name:${1:-}" ] && exit 3
exit 0
STUB
chmod +x "$work/stubs/stub"
for tool in npm npx yarn; do
    ln -s stub "$work/stubs/$tool"
done
# What the scripts call to match node_modules to the lockfile, in place of the real one,
# which is skipped by SR_NODE_INSTALL in every other case here.
cp "$work/stubs/stub" "$fake/scripts/ensure-node-modules.sh"

e2e_isolated() {
    : > "$work/stub.log"
    status=0
    STUB_LOG="$work/stub.log" HOME="$work/home" PATH="$work/stubs:$PATH" \
        SR_ISOLATED=1 SR_NODE_INSTALL=skip "$@" > "$work/out" 2>&1 || status=$?
}

mkdir -p "$fake/state-e2e"
: > "$fake/state-e2e/left-over"
e2e_isolated "$fake/scripts/e2e-container.sh" -g 'two words'
[ "$status" -eq 0 ] || fail "the isolated e2e run succeeds when the tools do"
grep -qxF "npx [playwright] [test] [-g] [two words] @ $fake/e2e" "$work/stub.log" ||
    fail "Playwright runs in e2e/, with its arguments as they were given, a quoted one still one"
grep -qxF "yarn [build] @ $fake/assets" "$work/stub.log" ||
    fail "the assets are built every run, in assets/, or the suite runs against the last bundle"
grep -qxF "ensure-node-modules.sh [$fake/assets] @ $fake" "$work/stub.log" ||
    fail "node_modules is matched to the lockfile first, for this checkout's assets/"
grep -qxF "npm [install] [--no-audit] [--no-fund] @ $fake/e2e" "$work/stub.log" ||
    fail "the specs' own dependencies are installed, quietly, in e2e/"
grep -qxF 'npx [playwright] [install] [chromium] @ '"$fake/e2e" "$work/stub.log" ||
    fail "and the browser"
[ ! -e "$fake/state-e2e" ] || fail "the fixture copy of the last run is removed, or the suite starts from it"
grep -qxF 'e2e: PASSED' "$work/out" || fail "and says so on stdout, which survives a pipe"

e2e_isolated env STUB_FAIL=npx:playwright "$fake/scripts/e2e-container.sh"
[ "$status" -eq 3 ] || fail "a failing suite is reported with the status Playwright gave"
grep -q '^e2e: FAILED' "$work/out" || fail "and says so on stdout"

e2e_isolated env STUB_FAIL=yarn "$fake/scripts/e2e-container.sh"
[ "$status" -eq 3 ] || fail "a build that fails stops the run"
grep -q 'playwright' "$work/stub.log" && fail "and the suite does not run against what it left"

e2e_isolated env STUB_FAIL=ensure-node-modules.sh "$fake/scripts/e2e-container.sh"
[ "$status" -eq 3 ] || fail "an install that fails stops the run"
grep -q '^e2e: FAILED' "$work/out" || fail "and still ends in the line a pipe can read"
grep -q 'yarn' "$work/stub.log" && fail "before anything is built"

e2e_isolated "$fake/scripts/e2e-typecheck.sh"
[ "$status" -eq 0 ] || fail "the isolated typecheck succeeds when tsc does"
grep -qxF "npm [run] [--silent] [typecheck] @ $fake/e2e" "$work/stub.log" ||
    fail "the typecheck runs in e2e/"
grep -qxF "npm [install] [--no-audit] [--no-fund] [--loglevel=error] @ $fake/e2e" "$work/stub.log" ||
    fail "after the dependencies are installed, there"
grep -qxF 'e2e-typecheck: PASSED' "$work/out" || fail "and says so on stdout"
e2e_isolated env STUB_FAIL=npm:run "$fake/scripts/e2e-typecheck.sh"
[ "$status" -eq 3 ] || fail "a failing typecheck is reported with its status"
grep -q '^e2e-typecheck: FAILED' "$work/out" || fail "and says so on stdout"
e2e_isolated env STUB_FAIL=npm:install "$fake/scripts/e2e-typecheck.sh"
grep -q 'typecheck' "$work/stub.log" && fail "an install that fails stops it before the typecheck"

# container.sh in the same layout: it matches node_modules to the lockfile before it runs
# the command, in this checkout's assets/.
e2e_isolated "$fake/scripts/container.sh" true
grep -qxF "ensure-node-modules.sh [$fake/assets] @ $fake" "$work/stub.log" ||
    fail "container.sh provisions node_modules before the command, for this checkout"
e2e_isolated env STUB_FAIL=ensure-node-modules.sh "$fake/scripts/container.sh" true
[ "$status" -ne 0 ] || fail "and does not run the command when it cannot"

isolated env SR_TIMEOUT=1 "$script" sleep 5
[ "$status" -ne 0 ] || fail "the deadline applies without a container, or a hung command is never stopped"

# What needs a container of its own is refused rather than run as something else: a
# `container-test-py314` that ran on the default Python would report a different answer.
isolated env SR_PYTHON=3.14 "$script" true
[ "$status" -eq 2 ] || fail "SR_PYTHON is refused, not ignored"
isolated env SR_DETACH=1 "$script" true
[ "$status" -eq 2 ] || fail "SR_DETACH is refused, not ignored"
isolated env SR_PORT=8080:8080 "$script" true
[ "$status" -eq 2 ] || fail "SR_PORT is refused, not ignored"

# Only 1 turns it on: another value is a container, as it was before this existed.
capture env SR_ISOLATED=0
[ -s "$work/argv" ] || fail "SR_ISOLATED=0 still runs in a container"

if [ "$failures" -ne 0 ]; then
    echo "$failures check(s) failed" >&2
    exit 1
fi

echo "container.sh: all checks passed"
