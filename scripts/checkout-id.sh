#!/bin/sh
# Which checkout a script is running in, so that several working copies of this
# repository can run containers side by side.
#
# Sander keeps a linked git worktree per topic and often has several sessions
# going at once. The container scripts used to name their volumes, containers and
# networks the same way whichever checkout they ran in, so those sessions shared
# one virtualenv, one node_modules and one e2e server - and `podman rm -f
# secretary-records-e2e-server` in one of them killed the suite running in another. Deriving the
# names from the checkout makes the isolation the default rather than something
# each session has to remember to ask for.
#
# The primary checkout keeps the plain names. It is one thing, so it cannot
# collide with itself, and renaming its volumes would throw away the virtualenv
# and node_modules already provisioned on every machine that has them.

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
    # sees it: tr would otherwise translate that newline too, and a name ending
    # in the separator is what came back the first time this was run.
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
