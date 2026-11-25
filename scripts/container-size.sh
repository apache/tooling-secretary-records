#!/bin/sh
# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
# How many pytest workers a run of the whole suite gets, how much memory its container may have, and
# the slots that keep the runs of several sessions from asking for more memory than the machine has.
#
# Sourced by container.sh, like checkout-id.sh. A run of the whole suite is sized: one whose command
# is `make test`, `test-python`, `verify`, `verify-fresh` or `test-coverage`, or `uv run
# scripts/ci/verify.py` (gate_suite_workers), or one given DEVKIT_SIZE_WORKERS, the most workers its
# command can use - which the Makefile's container-* targets set. Everything here runs on the host,
# before the container starts, because the memory cap is fixed when it does.
#
# The machine has a number of gate slots, and a run of the whole suite holds one for as long as it
# runs:
#
#   memory(w) = GATE_BASE_MIB + w * GATE_WORKER_MIB
#   slots     = (MemTotal - GATE_SPARE_MIB) / memory(GATE_SLOT_WORKERS), and at least 1
#   workers   = the fewest of  cores / slots (at least 1),
#                              ((MemTotal - GATE_SPARE_MIB) / slots - GATE_BASE_MIB) / GATE_WORKER_MIB,
#                              DEVKIT_SIZE_WORKERS
#   cap       = memory(workers)
#
# One slot on a 15 GiB machine, three on 32 GiB. A run's size comes from MemTotal and the slot
# count alone, so it is the same whenever it starts, and what decides whether it starts is whether a
# slot is free - nothing waits on MemAvailable. Where even one worker does not fit a slot, the run
# has one worker and a cap of what the slot holds, and says so.
#
# A slot is a lock file, <service>-gate-slot.<n> (bat-gate-slot.<n> in tooling-agenda), in the repository's common git directory, which every
# worktree shares. A run takes the first it can lock with `flock -n`. Where every slot is held it
# blocks on slot 0, so that the kernel wakes it the moment that slot is let go, and tries the others
# again every DEVKIT_GATE_POLL seconds; it says which checkouts hold them. Several runs waiting on one
# slot are woken in no promised order.
#
# The lock is on descriptor 8, which `podman run` inherits - and under podman so do conmon and the
# network helper (slirp4netns), so the slot is held until the container ends, not only while the
# client lives; under docker, whose daemon starts the container, only the client holds it. The
# kernel lets it go when the last of them ends, however it ends: there is nothing to clean up and
# nothing stale. A container whose client was killed holds its slot until it ends or reaches its
# deadline. What a slot file says (checkout, pid, since) is written after the lock is taken, for
# people, and never read to decide anything. A clone kept elsewhere has slots of its own.
#
# The busybox flock of the dev image, where the self-test runs, has neither -E nor -w. So flock is
# shown to work at all first, with a shared lock that nothing ever contends (gate_slot_dir), and a
# failed `flock -n` on a slot afterwards means that slot is held; and the blocking wait is bounded by
# `timeout`.
#
# PYTEST_WORKERS or DEVKIT_MEMORY given outright replaces what this chooses, and the run still takes a
# slot. CI keeps the fixed settings and takes none.
#
# Three settings exist for scripts/container-size.test.sh and nothing else: DEVKIT_MEMINFO (a file in
# place of /proc/meminfo), DEVKIT_NPROC (in place of nproc) and DEVKIT_GATE_POLL (seconds between tries
# of the other slots while waiting, 5 by default).

# shellcheck disable=SC2154  # $repo_root, $TIMEOUT and $CHECKOUT_LABEL belong to container.sh

# --- The constants, and the runs they were read from ----------------------------------------
#
# Read from whole `make verify` runs on a 16-core, 15 GiB WSL2 machine with Podman, the
# container sampled every two seconds (memory.stat anon, and memory.peak, which counts page cache):
#
#   workers  cap   passed  peak                                   memory(w) here
#   3        8g    yes     anon 7.63 and 7.68 GiB, at the cap     8448 MiB
#   6        6g    no      OOM-killed three times, then the hour  -
#   6        10g   yes     memory.peak 9.05 GiB, cache included   9984 MiB
#
# A worker is not a fixed size: its memory grows with every test it runs, so each of three ends at
# 2.4 to 2.5 GiB, where the one the kernel killed among six held 1.3. What a run holds is mostly
# the suite's, whatever the count, and a worker adds the slope between the two passing runs -
# (9.05 - 7.68) / 3 GiB, 468 MiB, rounded up. One worker, 7424 MiB, is that line carried down, not
# a measurement. Two gates of three workers started together there, at 8g each and then at 6g, took
# MemAvailable down to 0.50 and 0.35 GiB, and were stopped.
GATE_WORKER_MIB=512
GATE_BASE_MIB=6912

# What is left to everything else, and the run a slot is sized for: the six workers of a gate.
GATE_SPARE_MIB=1024
GATE_SLOT_WORKERS=6

# A run given fewer workers than its command can use takes longer: its deadline grows in proportion,
# to no more than this, unless DEVKIT_TIMEOUT is given.
GATE_DEADLINE_MAX=7200

# The least memory podman takes as a cap. Below it a run has none.
GATE_LEAST_CAP_MIB=6

# --- The arithmetic ---------------------------------------------------------------------------

# The memory, in MiB, of a run with $1 workers.
gate_memory() {
    echo $((GATE_BASE_MIB + $1 * GATE_WORKER_MIB))
}

# The most workers $1 MiB holds, no more than $2 cores and no more than $3. 0 where not even one fits.
gate_fit() {
    _room=$(($1 - GATE_BASE_MIB))
    # sh division truncates towards zero, which would make a small shortfall look like room
    if [ "$_room" -lt "$GATE_WORKER_MIB" ]; then
        echo 0
        return
    fi
    _fit=$((_room / GATE_WORKER_MIB))
    [ "$_fit" -le "$2" ] || _fit=$2
    [ "$_fit" -le "$3" ] || _fit=$3
    echo "$_fit"
}

# How many gate slots a MemTotal of $1 KiB has: at least one.
gate_slots() {
    _slots=$((($1 / 1024 - GATE_SPARE_MIB) / $(gate_memory "$GATE_SLOT_WORKERS")))
    [ "$_slots" -ge 1 ] || _slots=1
    echo "$_slots"
}

# $2 as a plain decimal count, without leading zeros - `08` is 8, not an octal error. $1 names it
# for the refusal.
gate_number() {
    case $2 in
        '' | *[!0-9]*)
            echo "$1: expected a number of workers, got '$2'" >&2
            exit 2
            ;;
        *) ;;
    esac
    _digits=${2#"${2%%[!0]*}"}
    echo "${_digits:-0}"
}

# DEVKIT_MEMORY in MiB: a whole number with b, k, m or g, each also as kb, ki or kib, in either case.
# Anything else is refused rather than run with a cap nobody can read here.
gate_mib_of() {
    _value=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    _n=${_value%%[!0-9]*}
    _unit=${_value#"$_n"}
    _mib=''
    # more digits than that is no memory size, and overflows the arithmetic
    if [ -n "$_n" ] && [ "${#_n}" -le 13 ]; then
        _n=$(gate_number DEVKIT_MEMORY "$_n")
        case $_unit in
            '' | b) _mib=$((_n / 1048576)) ;;
            k | kb | ki | kib) _mib=$((_n / 1024)) ;;
            m | mb | mi | mib) _mib=$_n ;;
            g | gb | gi | gib) _mib=$((_n * 1024)) ;;
            *) ;;
        esac
    fi
    if [ -z "$_mib" ]; then
        echo "DEVKIT_MEMORY: expected a whole number with b, k, m or g (6g, 6144m, 6GiB), got '$1':" \
            "a fraction, a t or a space is refused" >&2
        exit 2
    fi
    if [ "$_mib" -lt 1 ] || [ "$_mib" -gt 1048576 ]; then
        echo "DEVKIT_MEMORY: '$1' is no memory size for a container: under a MiB, or over a TiB" >&2
        exit 2
    fi
    echo "$_mib"
}

# The most workers the command $@ can use, where it is a run of the whole suite; nothing where it
# is not. What the Makefile's container-* targets give as DEVKIT_SIZE_WORKERS, for when the command is
# given to container.sh directly.
gate_suite_workers() {
    case ${1:-} in
        make)
            shift
            for _arg in "$@"; do
                case $_arg in
                    test | test-python | verify | verify-fresh)
                        echo "$GATE_SLOT_WORKERS"
                        return
                        ;;
                    # serial
                    test-coverage)
                        echo 1
                        return
                        ;;
                    *) ;;
                esac
            done
            ;;
        uv)
            case "${2:-} ${3:-}" in
                'run scripts/ci/verify.py' | 'run ./scripts/ci/verify.py') echo "$GATE_SLOT_WORKERS" ;;
                *) ;;
            esac
            ;;
        *) ;;
    esac
}

# The PYTEST_WORKERS given on a make command line among $@, which beats the environment's there.
gate_make_workers() {
    [ "${1:-}" = make ] || return 0
    _found=''
    for _arg in "$@"; do
        case $_arg in
            PYTEST_WORKERS=*) _found=${_arg#*=} ;;
            *) ;;
        esac
    done
    [ -z "${_found:-}" ] || echo "$_found"
}

# Whether this run is one to size: a command that asked for it, outside CI, on a machine whose memory
# can be read.
gate_wanted() {
    # refused here and not through gate_number: this is called as a condition, where a failing
    # command substitution does not end the script
    case ${DEVKIT_SIZE_WORKERS:-} in
        '') return 1 ;;
        *[!0-9]*)
            echo "DEVKIT_SIZE_WORKERS: expected a number of workers, got '$DEVKIT_SIZE_WORKERS'" >&2
            exit 2
            ;;
        *) ;;
    esac
    [ "$(gate_number DEVKIT_SIZE_WORKERS "$DEVKIT_SIZE_WORKERS")" -ge 1 ] || return 1
    # GitHub Actions sets CI=true; a runner has the machine to itself and its settings are pinned
    case ${CI:-} in
        '' | 0 | false) ;;
        *) return 1 ;;
    esac
    [ -r "${DEVKIT_MEMINFO:-/proc/meminfo}" ]
}

gate_meminfo() {
    awk -v key="$1:" '$1 == key { print $2 }' "${DEVKIT_MEMINFO:-/proc/meminfo}"
}

gate_cores() {
    if [ -n "${DEVKIT_NPROC:-}" ]; then echo "$DEVKIT_NPROC"; else nproc 2>/dev/null || echo 1; fi
}

# --- The slots --------------------------------------------------------------------------------

# Whether file $1 can be opened for appending. In a subshell, because a redirection that fails on
# a special builtin such as `exec` or `:` ends the shell rather than failing the command.
gate_openable() {
    (: >> "$1") 2>/dev/null
}

# Where the slot files are, or nothing where there are none to keep: no git directory, one whose
# files cannot be opened, or a flock that does not work. The probe takes a shared lock, which no run
# ever contends, so the only way it fails is a flock that cannot lock at all - not there, or broken.
gate_slot_dir() {
    _common=$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 0
    # in a subshell, so that a probe file that cannot be opened fails the probe and not the run
    (exec 9>> "$_common/$DEVKIT_SERVICE-gate-probe" && flock -s -n 9) 2>/dev/null || return 0
    echo "$_common"
}

# Write who holds slot $1 of $2, just taken, for people to read - in place of whoever held it before.
gate_holding() {
    GATE_SLOT=$1
    printf '%s %s %s\n' "$CHECKOUT_LABEL" "$$" "$(date +%s)" > "$2/$DEVKIT_SERVICE-gate-slot.$1"
}

# Take the first free slot of $1 in $2, on descriptor 8, which stays open - and locked - for the rest
# of container.sh and in the `podman run` it starts. Fails, holding nothing, where every slot is held.
gate_take() {
    _i=0
    while [ "$_i" -lt "$1" ]; do
        _slot="$2/$DEVKIT_SERVICE-gate-slot.$_i"
        if gate_openable "$_slot"; then
            exec 8>> "$_slot"
            # flock was shown to work (gate_slot_dir): a failure here is a slot somebody holds
            if flock -n 8; then
                gate_holding "$_i" "$2"
                return 0
            fi
            exec 8>&-
        fi
        _i=$((_i + 1))
    done
    return 1
}

# Wait up to $1 seconds for slot 0 of $2, blocked in flock so that the kernel wakes this run the
# moment the slot is let go. In the background with `wait`, so that a signal ends the wait at once;
# the lock the child takes is on the descriptor this shell shares with it, and stays when it exits.
gate_block() {
    _slot="$2/$DEVKIT_SERVICE-gate-slot.0"
    gate_openable "$_slot" || { sleep "$1" & wait $! || true; return 1; }
    exec 8>> "$_slot"
    _got=0
    timeout "$1" flock -x 8 &
    wait $! || _got=$?
    if [ "$_got" -eq 0 ]; then
        gate_holding 0 "$2"
        return 0
    fi
    exec 8>&-
    return 1
}

# Who holds the slots of $1 in $2, one line each, as their files say.
gate_holders() {
    _i=0
    _at=$(date +%s)
    while [ "$_i" -lt "$1" ]; do
        # a slot file that is not there, or cannot be read, says nothing - and under `set -e` a
        # failing awk would otherwise end the run that is only waiting
        awk -v now="$_at" 'NF == 3 {
            printf "  %s (pid %s), for %dm%02ds\n", $1, $2, (now - $3) / 60, (now - $3) % 60
        }' "$2/$DEVKIT_SERVICE-gate-slot.$_i" 2>/dev/null || true
        _i=$((_i + 1))
    done
}

# Size this run, and take a slot, waiting for one where all are held. Sets WORKERS, MEMORY and
# TIMEOUT for container.sh; MEMORY is left as it was where DEVKIT_MEMORY was given.
gate_size() {
    _max=$(gate_number DEVKIT_SIZE_WORKERS "$DEVKIT_SIZE_WORKERS")
    _given_workers=''
    # `auto` is xdist's own word for "you choose", which is what this does
    case ${PYTEST_WORKERS:-auto} in
        auto) ;;
        *) _given_workers=$(gate_number PYTEST_WORKERS "$PYTEST_WORKERS") ;;
    esac
    # serial, to the Makefile, as 1 is
    [ "$_given_workers" != 0 ] || _given_workers=1
    _given_mib=''
    case ${DEVKIT_MEMORY:-} in
        '' | 0) ;;
        *) _given_mib=$(gate_mib_of "$DEVKIT_MEMORY") ;;
    esac

    _total=$(gate_meminfo MemTotal)
    _slots=$(gate_slots "$_total")
    _per_slot=$(((_total / 1024 - GATE_SPARE_MIB) / _slots))
    _cores=$(($(gate_cores) / _slots))
    [ "$_cores" -ge 1 ] || _cores=1

    if [ -n "$_given_workers" ]; then
        WORKERS=$_given_workers
    else
        WORKERS=$(gate_fit "${_given_mib:-$_per_slot}" "$_cores" "$_max")
    fi
    _cap=''
    if [ "$WORKERS" -lt 1 ]; then
        WORKERS=1
        _cap=$_per_slot
        [ -n "$_given_mib" ] || echo "container.sh: one worker needs $(gate_memory 1)m and this machine's" \
            "slot holds ${_per_slot}m: one worker, capped at that" >&2
    fi
    if [ -z "${DEVKIT_MEMORY:-}" ]; then
        MEMORY="${_cap:-$(gate_memory "$WORKERS")}m"
        if [ -n "$_cap" ] && [ "$_cap" -lt "$GATE_LEAST_CAP_MIB" ]; then
            echo "container.sh: under ${GATE_LEAST_CAP_MIB}m is no cap podman takes: no cap" >&2
            MEMORY=''
        fi
    fi
    if [ -z "${DEVKIT_TIMEOUT:-}" ] && [ -n "$TIMEOUT" ] && [ "$WORKERS" -lt "$_max" ]; then
        TIMEOUT=$((TIMEOUT * _max / WORKERS))
        [ "$TIMEOUT" -le "$GATE_DEADLINE_MAX" ] || TIMEOUT=$GATE_DEADLINE_MAX
    fi

    _dir=$(gate_slot_dir)
    if [ -n "$_dir" ]; then
        _said=-
        while ! gate_take "$_slots" "$_dir"; do
            # said again when who holds the slots changes, and not at every try
            _now=$(gate_holders "$_slots" "$_dir" | sed 's/, for .*//')
            if [ "$_now" != "$_said" ]; then
                echo "container.sh: waiting for one of this machine's $_slots gate slot(s), held by:" >&2
                gate_holders "$_slots" "$_dir" >&2
                _said=$_now
            fi
            if gate_block "${DEVKIT_GATE_POLL:-5}" "$_dir"; then
                break
            fi
        done
        _where="slot $GATE_SLOT of $_slots"
    else
        _where='no slot: no git directory to share them in, or no flock that works'
        echo "container.sh: $_where - sized, but not kept from other runs" >&2
    fi
    echo "container.sh: $WORKERS pytest workers in ${MEMORY:-no cap}, deadline ${TIMEOUT:-none}s ($_where)" >&2
}
