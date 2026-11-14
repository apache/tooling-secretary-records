#!/bin/sh
# Find what the container scripts made for checkouts that no longer exist, and remove it.
#
#   scripts/container-gc.sh          # lists it, and removes nothing
#   scripts/container-gc.sh --sizes  # and how much each volume holds (slow: node_modules
#                                    # is hundreds of thousands of files to count)
#   scripts/container-gc.sh --yes    # removes it
#
# The sweep for what scripts/worktree-done.sh did not get to: a worktree removed with a bare
# `git worktree remove`, or deleted outright. Nothing a live or paused session runs from is
# ever a candidate, nor the primary checkout's:
#
#   - what carries a checkout path (checkout-resources.sh, PATH_LABEL) goes when that path is
#     no longer there;
#   - what carries none - made before the label - goes only where its name is one the scripts
#     derive from a worktree named as AGENTS.md has them, `<repository>-<topic>`, and no such
#     worktree is live. A name that merely looks like one of ours is listed as left alone:
#     that is how what is ours is told from another project's.
#
# Run it from any checkout of this repository: every worktree of it is asked about.
#
# Environment: SR_RUNTIME as in container.sh.

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
# shellcheck source=scripts/checkout-id.sh
. "$script_dir/checkout-id.sh"
# shellcheck source=scripts/checkout-resources.sh
. "$script_dir/checkout-resources.sh"

remove=''
sizes=''
for _arg in "$@"; do
    case "$_arg" in
        --yes) remove=1 ;;
        --sizes) sizes=1 ;;
        *) echo "usage: $0 [--sizes] [--yes]" >&2; exit 2 ;;
    esac
done

if ! runtime=$(container_runtime); then
    echo "no container runtime found" >&2
    exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
primary=$(dirname -- "$(git -C "$repo_root" rev-parse --path-format=absolute --git-common-dir)")
convention="-$(basename -- "$primary")-"
siblings=$(dirname -- "$primary")
live_suffixes "$repo_root" > "$work/live"
# A sweep that could not see the worktrees would take every checkout's things for a gone
# one's. The primary is always listed, so an empty answer is git failing, not no checkouts.
if [ ! -s "$work/live" ]; then
    echo "could not list this repository's worktrees: refusing to sweep" >&2
    exit 1
fi

# gone, lookalike or keep, for a name and the path it is labelled with.
verdict() {
    if [ -n "$2" ]; then
        if [ -d "$2" ]; then echo keep; else echo gone; fi
        return
    fi
    _suffix=$(suffix_of "$1")
    if [ -z "$_suffix" ] || grep -qxF -- "$_suffix" "$work/live"; then
        echo keep
        return
    fi
    # Asked of this repository's worktrees, a checkout of another clone of it would read as gone:
    # a directory of that name beside the primary, where AGENTS.md puts worktrees, counts too.
    if [ -d "$siblings/${_suffix#-}" ]; then
        echo keep
        return
    fi
    case "$_suffix" in
        "$convention"*) echo gone ;;
        *) echo lookalike ;;
    esac
}

tab=$(printf '\t')
found=0
total_kb=0
: > "$work/lookalikes"
for kind in $KINDS; do
    listing "$kind" > "$work/listing"
    while IFS="$tab" read -r name checkout; do
        case $(verdict "$name" "$checkout") in
            gone) echo "$name" >> "$work/gone-$kind" ;;
            lookalike) echo "$kind $name" >> "$work/lookalikes" ;;
            *) ;;
        esac
    done < "$work/listing"
    [ -s "$work/gone-$kind" ] || continue
    while IFS= read -r name; do
        found=$((found + 1))
        size=''
        if [ "$kind" = volume ] && [ -n "$sizes" ]; then
            mount=$("$runtime" volume inspect --format '{{.Mountpoint}}' "$name" 2>/dev/null || true)
            kb=$(du -sk "$mount" 2>/dev/null | cut -f1 || true)
            total_kb=$((total_kb + ${kb:-0}))
            size="$((${kb:-0} / 1024)) MB"
        fi
        printf '  %-9s %-60s %s\n' "$kind" "$name" "$size"
    done < "$work/gone-$kind"
done

if [ -s "$work/lookalikes" ]; then
    echo "left alone: $(wc -l < "$work/lookalikes") unlabelled, named like a checkout's but not one named"
    echo "  ${convention#-}<topic> - remove them by hand if they are:"
    sed 's/^/  /' "$work/lookalikes"
fi
if [ "$found" -eq 0 ]; then
    echo "nothing left behind by a checkout that is gone"
    exit 0
fi
echo "$found left behind by checkouts that are gone${sizes:+, $((total_kb / 1024)) MB in volumes}"
if [ -z "$remove" ]; then
    echo "run with --yes to remove them"
    exit 0
fi

# In KINDS's order, containers first, so nothing still attached holds a volume or network.
for kind in $KINDS; do
    [ -s "$work/gone-$kind" ] || continue
    while IFS= read -r name; do
        # shellcheck disable=SC2046
        "$runtime" $(remove_of "$kind") "$name" >/dev/null
    done < "$work/gone-$kind"
done
echo "removed"
