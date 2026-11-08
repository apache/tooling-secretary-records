#!/bin/sh
# Which checkout made a volume, container or network, and so whether it may be removed.
#
# Sourced, not run, like checkout-id.sh. The container scripts create volumes, containers and
# networks for a checkout, and `git worktree remove` has no hook to take them away again - so
# a worktree finished with it alone leaves ~250 MB of virtualenv and node_modules behind.
# worktree-done.sh removes a checkout's own when its work is over; container-gc.sh finds
# what was left by checkouts that are gone.
#
# What a checkout makes for itself carries its path (PATH_LABEL). secretary-records.checkout, which
# container.sh also sets, names the checkout for a person reading `podman ps`; this one is
# for the scripts that remove things.

PATH_LABEL=secretary-records.checkout-path
# In this order wherever all three are removed: a network or volume a container is still
# attached to cannot go. Read by the sourcing scripts.
# shellcheck disable=SC2034
KINDS="container network volume"

# The runtime the container scripts use: SR_RUNTIME, else podman, else docker. Prints nothing
# and fails where there is none.
container_runtime() {
    if [ -n "${SR_RUNTIME:-}" ]; then
        printf '%s\n' "$SR_RUNTIME"
    elif command -v podman >/dev/null 2>&1; then
        echo podman
    elif command -v docker >/dev/null 2>&1; then
        echo docker
    else
        return 1
    fi
}

# Create a volume, labelled with this checkout, where it does not exist yet - but only when
# its name is the one this checkout derives ($2). A name given outright (SR_VENV_VOLUME and
# the like) can be shared, or be the primary's: labelled with whichever checkout made it
# first, it would be removed along with that checkout. $runtime, $repo_root and $suffix belong
# to the sourcing script.
# shellcheck disable=SC2154
own_volume() {
    if [ "$1" != "$2" ] || [ -z "$suffix" ]; then
        return 0
    fi
    "$runtime" volume inspect "$1" >/dev/null 2>&1 ||
        "$runtime" volume create --label "$PATH_LABEL=$repo_root" "$1" >/dev/null 2>&1 || true
}

list_of() {
    case "$1" in
        container) echo "ps -a --format {{.Names}}" ;;
        *) echo "$1 ls --format {{.Name}}" ;;
    esac
}
remove_of() {
    case "$1" in
        container) echo "rm -f" ;;
        *) echo "$1 rm" ;;
    esac
}

# Every name of a kind, a tab, and the checkout path it is labelled with - empty for none.
# Asked of `inspect`, which spells labels the same way on podman and docker, where the
# listing formats do not; and only of what has the label at all, which is few. Fails, rather
# than printing a shorter list, where the runtime cannot answer.
# shellcheck disable=SC2154,SC2046
listing() {
    case "$1" in
        container) _template="{{index .Config.Labels \"$PATH_LABEL\"}}" ;;
        *) _template="{{index .Labels \"$PATH_LABEL\"}}" ;;
    esac
    _all=$("$runtime" $(list_of "$1"))
    _labelled=$("$runtime" $(list_of "$1") --filter "label=$PATH_LABEL")
    for _name in $_all; do
        _path=''
        if printf '%s\n' "$_labelled" | grep -qxF -- "$_name"; then
            _path=$("$runtime" "$1" inspect --format "$_template" "$_name")
        fi
        printf '%s\t%s\n' "$_name" "$_path"
    done
}

# --- what was made before the labels -----------------------------------------------------
#
# What was made before PATH_LABEL carries none, and is known only by its name, which the
# scripts derive from the checkout (checkout_suffix). Until container-gc.sh finds nothing
# unlabelled, the names below have to be the ones the scripts use.

# The suffix a name was derived from, or nothing for a name that is not one of the scripts'.
suffix_of() {
    printf '%s\n' "$1" | sed -n -E \
        -e 's/^secretary-records-(venv|node|e2e-node)(-.+)$/\2/p' \
        -e 's/^secretary-records-run(-.+)-[0-9]+$/\1/p' \
        -e 's/^secretary-records-keep(-.+)$/\1/p' \
        -e 's/^secretary-records-e2e-server(-.+)$/\1/p' \
        -e 's/^secretary-records-e2e-net(-.+)$/\1/p'
}

# The suffix of every checkout of this repository still on disk, one a line.
live_suffixes() {
    git -C "$1" worktree list --porcelain | sed -n 's/^worktree //p' | while IFS= read -r _path; do
        if [ -d "$_path" ]; then printf '%s\n' "$(checkout_suffix "$_path")"; fi
    done
}
