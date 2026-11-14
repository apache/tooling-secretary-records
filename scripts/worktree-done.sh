#!/bin/sh
# Finish with a linked worktree: remove it, and everything the container scripts made for it.
#
#   scripts/worktree-done.sh ../tooling-secretary-records-<topic>
#
# Removes the worktree, then the containers, network and volumes made for it (its virtualenv,
# node_modules and Playwright's node_modules). The local branch goes too where everything on
# it is already on a remote; with commits that exist only here, the branch is kept and said
# so, since the worktree going does not take them.
#
# Refuses, removing nothing, unless the path is the top of a linked worktree of this
# repository that is not locked, whose `git status` answers and is clean, and which is not
# on a detached HEAD holding commits nothing else does. Git removes the worktree before
# anything else goes, and itself refuses one that is not clean.
#
# Environment: SR_RUNTIME as in container.sh.

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=scripts/checkout-id.sh
. "$script_dir/checkout-id.sh"
# shellcheck source=scripts/checkout-resources.sh
. "$script_dir/checkout-resources.sh"

refuse() {
    echo "$1: nothing was removed" >&2
    exit 1
}

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <worktree path>" >&2
    exit 2
fi
[ -d "$1" ] || refuse "$1 is not a directory"
worktree=$(CDPATH='' cd -- "$1" && pwd)
# Git spells paths with symlinks resolved, and the scripts label with the path as reached.
physical=$(CDPATH='' cd -- "$1" && pwd -P)
top=$(git -C "$worktree" rev-parse --show-toplevel 2>/dev/null) || refuse "$worktree is not in a git worktree"
[ "$top" = "$physical" ] || refuse "$worktree is inside the worktree $top, not its top"
primary=$(dirname -- "$(git -C "$worktree" rev-parse --path-format=absolute --git-common-dir)")

# The worktree as git lists it: the first entry is the primary, and a locked one says so.
git -C "$primary" worktree list --porcelain > "${TMPDIR:-/tmp}/worktree-done.$$" ||
    refuse "git could not list the worktrees"
entry=$(awk -v w="worktree $physical" '
    /^worktree / { n++; this = ($0 == w) }
    this { print (n == 1 ? "primary" : "linked"); if ($1 == "locked") print "locked" }
' "${TMPDIR:-/tmp}/worktree-done.$$")
rm -f "${TMPDIR:-/tmp}/worktree-done.$$"
case "$entry" in
    '') refuse "$worktree is not a worktree of $primary" ;;
    primary*) refuse "$worktree is the primary checkout" ;;
    *locked*) refuse "$worktree is locked (git worktree unlock it first)" ;;
    *) ;;
esac

# `oom` is conmon's marker that a container from here hit its memory cap
# (docs/troubleshooting.md): a fact about a run, not work anybody wrote.
status=$(git -C "$worktree" status --porcelain) || refuse "git status failed in $worktree"
changes=$(printf '%s\n' "$status" | grep -v '^?? oom$' || true)
if [ -n "$changes" ]; then
    echo "$worktree has changes:" >&2
    printf '%s\n' "$changes" | sed 's/^/  /' >&2
    refuse "$worktree is not clean"
fi

branch=$(git -C "$worktree" symbolic-ref --quiet --short HEAD || true)
# Commits on HEAD that no remote-tracking ref holds. Read from the refs this checkout has
# fetched: `git fetch` first, or a branch pushed from elsewhere reads as only here.
only_here=$(git -C "$worktree" rev-list --count HEAD --not --remotes) || refuse "git could not count the commits"
if [ -z "$branch" ] && [ "$only_here" -gt 0 ]; then
    refuse "$worktree is on a detached HEAD with $only_here commit(s) nothing else holds"
fi

suffix=$(checkout_suffix "$worktree")
rm -f "$worktree/oom"
git -C "$primary" worktree remove "$worktree"
echo "removed worktree $worktree"

# What carries this worktree's path, and what carries no path but a name derived from it -
# never something labelled with another checkout, whatever its name says.
if runtime=$(container_runtime); then
    for kind in $KINDS; do
        if ! listed=$(listing "$kind"); then
            echo "could not list the ${kind}s: what is left of them stays, for container-gc.sh" >&2
            continue
        fi
        printf '%s\n' "$listed" | while IFS="$(printf '\t')" read -r name path; do
            [ -n "$name" ] || continue
            if [ "$path" = "$worktree" ] || [ "$path" = "$physical" ] || { [ -z "$path" ] && [ "$(suffix_of "$name")" = "$suffix" ]; }; then
                # shellcheck disable=SC2046
                if "$runtime" $(remove_of "$kind") "$name" >/dev/null; then echo "removed $kind $name"; fi
            fi
        done
    done
else
    echo "no container runtime found: what it made stays, for container-gc.sh" >&2
fi

if [ -n "$branch" ]; then
    if [ "$only_here" -eq 0 ]; then
        git -C "$primary" branch -D "$branch" >/dev/null
        echo "removed branch $branch: everything on it is on a remote"
    else
        echo "kept branch $branch: $only_here commit(s) on it are not on any remote"
    fi
fi
