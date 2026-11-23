#!/bin/sh
# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
# Which checkout a script is running in, so that several working copies of this
# repository can run containers side by side.
#
# Work goes on in a linked git worktree per topic, often with several sessions at once.
# Container scripts that name their volumes, containers and networks the same way whichever
# checkout they run in make those sessions share one virtualenv, one node_modules and one
# e2e server - and `podman rm -f <service>-e2e-server` in one of them kills the suite
# running in another. Deriving the names from the checkout makes the isolation the default
# rather than something each session has to remember to ask for.
#
# The primary checkout keeps the plain names. It is one thing, so it cannot
# collide with itself, and renaming its volumes would throw away the virtualenv
# and node_modules already provisioned on every machine that has them.

# What devkit.conf gives `$1`, or nothing. Read with sed rather than sourced, so that a file of
# the repository is never run by a script that runs before anything else has been checked.
devkit_conf() {
    # shellcheck disable=SC2154 # script_dir is set by the script that sources this one
    sed -n "s/^$1=//p" "$script_dir/../devkit.conf" 2>/dev/null | head -n 1
}

# The name goes into image, volume, container and network names, into sed patterns and into a path under
# /run, so it is what every one of them accepts and nothing else: lower case letters, digits, `-` and `_`,
# starting with a letter or a digit. A trailing carriage return or a comment is not part of one.
DEVKIT_SERVICE=$(devkit_conf DEVKIT_SERVICE)
case $DEVKIT_SERVICE in
    '' | [!a-z0-9]* | *[!a-z0-9_-]*)
        echo "devkit.conf has no usable DEVKIT_SERVICE (lower case letters, digits, - and _): $script_dir/../devkit.conf" >&2
        exit 1
        ;;
    *) : ;;
esac
# Where the container mounts the working tree.
# shellcheck disable=SC2034
DEVKIT_RUN_DIR=/run/$DEVKIT_SERVICE

# Prints the suffix to hang on a volume, container or network name: empty in the
# primary checkout, "-<worktree>" in a linked worktree. A linked worktree's .git
# is a file pointing at the parent repository, which is what tells the two apart.
checkout_suffix() {
    _root=$1
    if [ -d "$_root/.git" ]; then
        return
    fi
    # container and volume names take [a-zA-Z0-9][a-zA-Z0-9_.-]*, and a directory
    # name does not have to. The name is printed without its newline before tr
    # sees it: tr would otherwise translate that newline too, and the name would
    # end in the separator.
    _name=$(basename -- "$_root")
    printf -- '-%s' "$(printf '%s' "$_name" | tr -c 'a-zA-Z0-9_.-' '-')"
}

# Prints the git directory shared by every worktree, when this is a linked one.
# Its .git file points there by absolute path, and that path is outside the bind
# mount - so git inside the container cannot see the repository at all and
# `make check` dies before the first hook runs. Mounting this at the same
# absolute path it has on the host is what makes the pointer resolve.
worktree_git_dir() {
    _root=$1
    if [ -d "$_root/.git" ] || ! command -v git >/dev/null 2>&1; then
        return
    fi
    git -C "$_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true
}
