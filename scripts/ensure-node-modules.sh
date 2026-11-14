#!/bin/sh
# Make this checkout's node_modules volume match this checkout's lockfile.
#
# Runs inside the dev container, where the repository is bind-mounted and
# node_modules is a named volume of the checkout's own. Both container entry
# points call it, because both mount that volume and only one of them used to
# provision it - the difference seed-volumes.sh already exists to have stopped
# being accidental.
#
# `uv` re-reads its lockfile on every `uv run`, so the virtualenv volume looks
# after itself. yarn does not: it installs when asked and not otherwise, so
# something here has to decide when to ask.
#
# What it used to ask was whether node_modules held a state file at all. That is
# true of a volume seeded from another checkout - seed_volume copies the state
# file along with everything else - so a worktree whose branch pinned a
# dependency the seed did not have never installed it, and said so as a
# type-check failure several layers down:
#
#     Error: Cannot find module '@testing-library/svelte'
#
# So the question is not "has an install happened here" but "does what is
# installed match what this checkout pins". The answer is the content of the
# three files that decide most of an install, named by `git hash-object` - which
# is content addressing and needs no repository, so it says the same thing about
# a bind mount, a seeded volume and a branch nobody has committed yet.
#
# Most, not all: a patch under assets/.yarn/patches, a yarn release swapped in
# place under an unchanged yarnPath, and YARN_* in the environment all change
# what an install produces and are not read here. Each of those normally moves
# the lockfile too - a patch locator carries a hash of the patch - so this is a
# gap worth knowing about rather than one worth widening the fingerprint for.

set -eu

assets=${1:-/run/secretary-records/assets}
stamp="$assets/node_modules/.sr-install-stamp"
lock="$assets/node_modules/.sr-install-lock"

# The escape hatch for the deadlock this would otherwise be. `yarn install
# --immutable` refuses a lockfile that disagrees with package.json, and this runs
# in the preamble every container command goes through - so a half-finished
# dependency change would lock you out of `make container-shell`, which is where
# the repair has to be run from. Skipping is per-invocation and says so.
if [ "${SR_NODE_INSTALL:-}" = "skip" ]; then
    exit 0
fi

fingerprint=$(
    cd "$assets" && git hash-object yarn.lock .yarnrc.yml package.json
)

matches_stamp() {
    [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$fingerprint" ]
}

if matches_stamp; then
    exit 0
fi

# One installer at a time. Two runs in one checkout - `make container-test` and
# `make e2e-test` started together, say, one per entry point - would otherwise
# both find the mismatch and both install into the one volume, and the loser
# would write a stamp certifying a tree the winner was still building. That is
# worse than the race it replaces: the wrong tree is then *certified*, so nothing
# looks again until the lockfile next moves. mkdir is the atomic primitive every
# shell has, and node_modules is the one thing both runs share, so the lock lives
# there (the linker skips entries beginning with a dot, which is why the state
# file survives an install and so does this).
if mkdir "$lock" 2>/dev/null; then
    trap 'rmdir "$lock" 2>/dev/null || true' EXIT INT TERM HUP
else
    echo "waiting for another run to finish installing node_modules..." >&2
    waited=0
    while [ -d "$lock" ] && [ "$waited" -lt 600 ]; do
        sleep 1
        waited=$((waited + 1))
    done
    # The winner has written its stamp by now; if it has not, it died, and this
    # run installs rather than waiting on something that is not coming back.
    if matches_stamp; then
        exit 0
    fi
fi

if [ -e "$assets/node_modules/.yarn-state.yml" ]; then
    echo "node_modules does not match this checkout's lockfile; installing..." >&2
else
    echo "installing node_modules for this checkout..." >&2
fi

# Cleared before yarn runs, not after it fails. A stamp left standing over an
# install that was interrupted describes a tree that no longer exists - yarn
# prunes before it fetches - and the next run believes it. That is the same claim
# of completeness over an incomplete tree that this file exists to stop, one step
# along.
rm -f "$stamp"

if ! (cd "$assets" && yarn install --immutable) >&2; then
    cat >&2 <<'MSG'

node_modules could not be installed, so this command did not run.

If yarn refused the lockfile, package.json and yarn.lock disagree and the fix is
to update the lockfile - from inside the container, since the toolchain does not
run on the host:

    SR_NODE_INSTALL=skip scripts/container.sh sh -c 'cd assets && yarn install'

SR_NODE_INSTALL=skip is what gets you a shell while this is broken; it skips
this check for the one command it is set on.
MSG
    exit 1
fi

printf '%s\n' "$fingerprint" > "$stamp"
