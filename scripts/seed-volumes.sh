#!/bin/sh
# Give a checkout's own .venv and node_modules volumes a starting point, rather
# than provisioning them from nothing.
#
# Sourced, not run: it needs $runtime and $IMAGE_NAME from the script that
# sources it, the same way checkout-id.sh is sourced for its names.
#
# A checkout's own volume starts empty, and provisioning one from scratch is a
# full `uv sync` and `yarn install` - minutes, on every new worktree. Copy the
# volume the checkout was sharing until now instead: both trees are
# self-contained and live at the same path inside the container, so a copy is
# valid, and whatever the branch pins differently is then synced incrementally
# the way it always was. Once per volume, and only when there is one to copy.
#
# This lives in a file of its own because two scripts mount those volumes.
# container.sh seeded them and e2e-container.sh did not, which is not a
# difference anybody chose: whichever of the two ran first in a new worktree
# decided whether the volume arrived seeded or empty, and podman creates an empty
# one for a name it has never seen. Losing that race left the application
# container dead on `Couldn't find the node_modules state file`, one directory
# layer below anything the harness prints.

# $runtime, $IMAGE_NAME and $suffix belong to the sourcing script, which has to have them
# set by the time it calls seed_volume - not by the time it sources this, which
# both scripts do at the top, before either variable exists. shellcheck cannot
# see that from here.
# shellcheck disable=SC2154

volume_exists() {
    "$runtime" volume inspect "$1" >/dev/null 2>&1
}

seed_volume() {
    _volume=$1
    _from=$2
    if volume_exists "$_volume"; then
        return 0
    fi
    if [ "$_volume" = "$_from" ] || ! volume_exists "$_from"; then
        own_volume "$_volume" "$_from$suffix"
        return 0
    fi

    # One seeder at a time. Two runs in one checkout - `make container-test` and
    # `make e2e-test` started together, say - would otherwise both get past the
    # check above: one `volume create` then fails and takes the whole script down
    # with it under `set -e`, and worse, the loser would go on to use a volume the
    # winner is still filling. mkdir is the atomic primitive every shell has
    # (Copilot, #601). The waiter does not seed: by the time the lock is gone the
    # volume is there, and if it is not, uv and yarn provision it as they always
    # did for a checkout with nothing to copy.
    _lock="${TMPDIR:-/tmp}/secretary-records-seed-$_volume.lock"
    if ! mkdir "$_lock" 2>/dev/null; then
        echo "waiting for another run to finish seeding $_volume..." >&2
        _waited=0
        while [ -d "$_lock" ] && [ "$_waited" -lt 600 ]; do
            sleep 1
            _waited=$((_waited + 1))
        done
        return 0
    fi
    # In a subshell, so the trap below is the subshell's own. A trap is per shell and
    # there is one slot per signal: set here it would replace the caller's - and
    # e2e-container.sh sets one before this runs, to remove its application container -
    # and clearing it afterwards would leave the caller with no cleanup at all. That is
    # a leak on exactly one run, the first in a new worktree, which is the run this file
    # exists for (Copilot, #650).
    #
    # An interrupted seed must not leave the lock behind for the next run to wait out, so
    # the trap fires on the way out however the subshell leaves - including normally,
    # which is what removes the lock in the ordinary case.
    (
        trap 'rmdir "$_lock" 2>/dev/null || true' EXIT INT TERM HUP

        echo "seeding $_volume from $_from..." >&2
        own_volume "$_volume" "$_from$suffix"
        "$runtime" run --rm \
            -v "$_from:/seed-from:ro" \
            -v "$_volume:/seed-to" \
            "$IMAGE_NAME" \
            sh -euc 'cp -a /seed-from/. /seed-to/ 2>/dev/null || true'
    )
}
