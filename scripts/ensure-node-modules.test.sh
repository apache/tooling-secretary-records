#!/bin/sh
# What ensure-node-modules.sh decides, on trees it builds itself.
#
# The case that matters is the one the existence check got wrong: node_modules
# carrying another checkout's state file, which is what seed_volume leaves
# behind, against a lockfile this checkout pins differently. That has to install.
#
# yarn is stubbed. What is under test is when the script asks for an install and
# whether it records one that did not finish - not what yarn does when it runs,
# which is yarn's business and takes forty seconds.
#
# Run by prek (see .pre-commit-config.yaml), and so by `make check`.

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
subject="$script_dir/ensure-node-modules.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM HUP

failures=0

fail() {
    printf '\nFAIL: %s\n' "$1" >&2
    [ "$#" -lt 2 ] || printf '%s\n' "$2" >&2
    failures=$((failures + 1))
}

# An assets/ tree with the three files the fingerprint reads, and a stub yarn
# that records having been called. $1 names the case; $2 is the lockfile body.
new_assets() {
    assets="$work/$1/assets"
    mkdir -p "$assets/node_modules" "$work/$1/bin"
    printf '%s\n' "$2" > "$assets/yarn.lock"
    printf 'nodeLinker: node-modules\n' > "$assets/.yarnrc.yml"
    printf '{"name":"assets"}\n' > "$assets/package.json"

    # It writes the state file real yarn writes, so that the guard this replaces
    # behaves here as it behaves in life. Without that, the old guard reinstalls on
    # every run under the stub and the case below fails against it for a reason that
    # is not the one it names.
    cat > "$work/$1/bin/yarn" <<EOF
#!/bin/sh
printf 'ran\n' >> "$work/$1/yarn-calls"
[ "\${STUB_YARN_EXIT:-0}" -eq 0 ] || exit "\$STUB_YARN_EXIT"
printf 'installed here\n' > "$assets/node_modules/.yarn-state.yml"
EOF
    chmod +x "$work/$1/bin/yarn"
    : > "$work/$1/yarn-calls"
    stub_path="$work/$1/bin"
}

# Run the subject with the stub ahead of the real yarn, and keep its status.
run_it() {
    set +e
    output=$(PATH="$stub_path:$PATH" sh "$subject" "$assets" 2>&1)
    status=$?
    set -e
}

installs() {
    [ -s "$work/$1/yarn-calls" ]
}

# --- an empty node_modules installs ----------------------------------------------------
# The case the existence check did get right, kept so that fixing the other one cannot
# quietly cost this one.

new_assets empty 'lock-a'
run_it
[ "$status" -eq 0 ] || fail "an empty node_modules exited $status" "$output"
installs empty || fail 'an empty node_modules did not install' "$output"

# --- the same tree a second time does not install again --------------------------------
# Without this the fix would be "install every time", which passes every case below and
# adds forty seconds to every container command.

run_it
[ "$status" -eq 0 ] || fail "an up-to-date node_modules exited $status" "$output"
[ "$(wc -l < "$work/empty/yarn-calls")" -eq 1 ] ||
    fail 'an up-to-date node_modules installed a second time' "$output"

# --- a volume seeded from another checkout installs -------------------------------------
# The bug. seed_volume copies node_modules wholesale, .yarn-state.yml included, so the
# existence check saw an install that had happened somewhere else against another
# lockfile and asked for nothing. What reached the developer was a type error naming a
# module that was never installed here.

new_assets seeded 'lock-b'
printf 'installed elsewhere\n' > "$assets/node_modules/.yarn-state.yml"
printf 'the other checkout stamp\n' > "$assets/node_modules/.sr-install-stamp"
run_it
[ "$status" -eq 0 ] || fail "a seeded node_modules exited $status" "$output"
installs seeded || fail 'a volume seeded from another checkout did not install' "$output"
case $output in
    *"does not match this checkout's lockfile"*) ;;
    *) fail 'a seeded volume was not told apart from an empty one' "$output" ;;
esac

# --- a lockfile that moves under an installed tree installs ------------------------------
# The same shape from the other direction: same volume, same checkout, a branch that
# pins something new. `git pull` is enough to reach it.

new_assets moved 'lock-a'
run_it
printf 'lock-a-plus-one\n' > "$assets/yarn.lock"
run_it
[ "$(wc -l < "$work/moved/yarn-calls")" -eq 2 ] ||
    fail 'a changed lockfile did not install' "$output"

# --- .yarnrc.yml and package.json count too ----------------------------------------------
# .yarnrc.yml decides how the install runs, down to which yarn does it. package.json is
# what --immutable checks the lockfile against, so a change there has to reach yarn even
# when the lockfile has not moved - that mismatch is the error --immutable exists to raise.

new_assets yarnrc 'lock-a'
run_it
printf 'nodeLinker: pnp\n' > "$assets/.yarnrc.yml"
run_it
[ "$(wc -l < "$work/yarnrc/yarn-calls")" -eq 2 ] ||
    fail 'a changed .yarnrc.yml did not install' "$output"

new_assets manifest 'lock-a'
run_it
printf '{"name":"assets","dependencies":{"x":"1"}}\n' > "$assets/package.json"
run_it
[ "$(wc -l < "$work/manifest/yarn-calls")" -eq 2 ] ||
    fail 'a changed package.json did not install' "$output"

# --- an install that fails is not recorded as one that happened --------------------------
# The stamp is the claim that what is on the volume matches the lockfile. Written before
# yarn returned, a failed install would leave that claim standing and the next run would
# believe it - the same silence this whole file is about, one layer along.

new_assets broken 'lock-a'
STUB_YARN_EXIT=1
export STUB_YARN_EXIT
run_it
unset STUB_YARN_EXIT
[ "$status" -ne 0 ] || fail 'a failed install exited 0' "$output"
[ ! -f "$assets/node_modules/.sr-install-stamp" ] ||
    fail 'a failed install left a stamp saying it had succeeded' "$output"

STUB_YARN_EXIT=0
run_it
[ "$(wc -l < "$work/broken/yarn-calls")" -eq 2 ] ||
    fail 'the run after a failed install did not try again' "$output"

# --- an install that fails does not leave the *previous* stamp standing ------------------
# The case above starts with no stamp, so it says nothing about the one that matters: a tree
# installed against an older lockfile, whose install for the new one is interrupted. yarn
# prunes before it fetches, so what is on disk then matches neither lockfile - and going back
# to the old branch must not find a stamp saying it matches the old one.

new_assets interrupted 'lock-a'
run_it
[ -f "$assets/node_modules/.sr-install-stamp" ] || fail 'the first install wrote no stamp' "$output"

printf 'lock-b\n' > "$assets/yarn.lock"
STUB_YARN_EXIT=1
export STUB_YARN_EXIT
run_it
unset STUB_YARN_EXIT
[ "$status" -ne 0 ] || fail 'an interrupted install exited 0' "$output"

printf 'lock-a\n' > "$assets/yarn.lock"
run_it
[ "$(wc -l < "$work/interrupted/yarn-calls")" -eq 3 ] ||
    fail 'going back to the old lockfile trusted a stamp an interrupted install left' "$output"

# --- both entry points still call it ------------------------------------------------------
# Nothing else notices if one stops. The whole point of the file is that container.sh and
# e2e-container.sh ask the same question, which is the difference seed-volumes.sh was
# extracted to stop being accidental - and deleting either call is a one-line edit that
# every other case here would survive.
#
# Every tree above is under mktemp and so outside any repository, which is what covers the
# other half of running from e2e-container.sh's container: it tars the tree in with .git
# excluded, and git hash-object is content addressing that needs no repository to be in.

# A line that runs it, not a line that mentions it: both files name the script in a comment,
# and matching the name alone passed with the call deleted.
for caller in container.sh e2e-container.sh; do
    grep -Eq '^[[:space:]]*sh [^#]*ensure-node-modules\.sh' "$script_dir/$caller" ||
        fail "$caller no longer calls ensure-node-modules.sh"
done

if [ "$failures" -ne 0 ]; then
    printf '\n%s ensure-node-modules case(s) failed\n' "$failures" >&2
    exit 1
fi

printf 'ensure-node-modules.sh: every case holds\n'
