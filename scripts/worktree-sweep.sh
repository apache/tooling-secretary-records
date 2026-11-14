#!/bin/sh
# Find the worktrees whose work is over, and finish them with worktree-done.sh.
#
#   scripts/worktree-sweep.sh        # lists every linked worktree, finished or kept and why
#   scripts/worktree-sweep.sh --yes  # finishes each one listed as done, as soon as it is judged
#
# A worktree's work is over when its HEAD is on origin/main, or a pull request merged into main
# with its HEAD as the head - a squash merge leaves no ancestry to follow, and a branch gone from
# origin can as well be one never pushed. It is kept, whatever its work, when removing it could
# take something nobody committed or something still running:
#
#   - git cannot read it, or it is locked;
#   - another worktree is inside it (.claude/worktrees/ is Claude Code's own): git removes those
#     with it, changes and all, and its status does not see them;
#   - it has changes, untracked files included whatever status.showUntrackedFiles says - bar
#     conmon's `oom` marker;
#   - it holds what worktree-held.sh finds that status does not show: an ignored file the
#     toolchain does not write again, edits under skip-worktree or assume-unchanged, a rebase or
#     the like stopped half way. state/ is not among them: it is the checkout's scratch, and goes
#     with the worktree whatever it holds - keep what must last somewhere else;
#   - a container made for it is running (make container-keep, an open container-shell), or a
#     process has its working directory in it;
#   - its branch was made from main and has had no commit of its own: a topic just claimed is on
#     origin/main from its first minute;
#   - it was used in the last SR_SWEEP_IDLE_HOURS hours (default 6), by its HEAD, index and
#     reflog - a session that only reads moves none of them, which the checks above stand in for.
#
# When something cannot be asked - the container runtime fails, gh fails - what it would have
# answered is kept, and a note says so.
#
# Reads origin as this checkout last fetched it: `git fetch` first. Asks GitHub through `gh`;
# without it, or when it cannot answer, only what is on origin/main is found, and it says so.
#
# Environment: SR_SWEEP_IDLE_HOURS; SR_RUNTIME as in container.sh; SR_WORKTREE_DONE, the
# finisher (tests stand one in).

set -eu
# never write the index while reading it: a status that refreshes it would move the clock it is read by
export GIT_OPTIONAL_LOCKS=0

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=scripts/checkout-id.sh
. "$script_dir/checkout-id.sh"
# shellcheck source=scripts/checkout-resources.sh
. "$script_dir/checkout-resources.sh"
# shellcheck source=scripts/worktree-held.sh
. "$script_dir/worktree-held.sh"
finish=${SR_WORKTREE_DONE:-$script_dir/worktree-done.sh}
idle_hours=${SR_SWEEP_IDLE_HOURS:-6}

yes=''
case "${1:-}" in
    '') ;;
    --yes) yes=1 ;;
    *) echo "usage: $0 [--yes]" >&2; exit 2 ;;
esac

primary=$(dirname -- "$(git rev-parse --path-format=absolute --git-common-dir)")
# owner/repo from origin's url: git@host:o/r, ssh://git@host/o/r, https://host/o/r, .git or not
repo=$(git -C "$primary" remote get-url origin | sed -E 's#^[a-z+]+://([^@/]+@)?[^/]+/##; s#^[^@/]+@[^:]+:##; s#\.git$##')
have_gh=''
command -v gh > /dev/null 2>&1 && have_gh=1
now=$(date +%s)
listing="${TMPDIR:-/tmp}/worktree-sweep.$$"
trap 'rm -f "$listing" "$listing.err"' EXIT
tab=$(printf '\t')
git -C "$primary" worktree list --porcelain > "$listing"
notes=''
note() { case "$notes" in *"$1"*) ;; *) notes="$notes
note: $1" ;; esac; }

# What is running for a checkout: each running container's label, as the physical path, and its name.
# The scripts label with the path as reached, and git spells it with symlinks resolved.
running=''
runtime_failed=''
if runtime=$(container_runtime); then
    if names=$("$runtime" ps --format '{{.Names}}' 2> /dev/null); then
        for name in $names; do
            path=$("$runtime" container inspect --format "{{index .Config.Labels \"$PATH_LABEL\"}}" "$name" 2> /dev/null) || path=''
            case "$path" in
                '' | '<no value>') path='' ;;
                *) path=$(CDPATH='' cd -- "$path" 2> /dev/null && pwd -P) || true ;;
            esac
            running="$running
$name$tab$path"
        done
    else
        runtime_failed=1
        note "$runtime could not list its containers, so a worktree that would be finished is kept"
    fi
else
    note "no container runtime found, so a running container was not looked for"
fi
running_for() {
    sfx=$(checkout_suffix "$1")
    printf '%s\n' "$running" | while IFS="$tab" read -r name path; do
        [ -n "$name" ] || continue
        if [ "$path" = "$1" ] || { [ -z "$path" ] && [ "$(suffix_of "$name")" = "$sfx" ]; }; then echo "$name"; fi
    done
}

# Working directories of every process this user can see, physical; empty where /proc is absent.
cwds=$(for p in /proc/[0-9]*/cwd; do readlink "$p" 2> /dev/null || true; done)

# "<path>\tlocked" or "<path>\t" for each linked worktree; the first entry is the primary
linked() {
    awk '/^worktree / { if (p != "") print p "\t" l; n++; p = (n == 1 ? "" : substr($0, 10)); l = "" }
         /^locked/ { l = "locked" }
         END { if (p != "") print p "\t" l }' "$listing"
}

mtime() { stat -c %Y "$1" 2> /dev/null || stat -f %m "$1"; }

last_used() {
    newest=0
    for f in "$1/HEAD" "$1/index" "$1/logs/HEAD"; do
        [ -e "$f" ] || continue
        t=$(mtime "$f")
        [ "$t" -gt "$newest" ] && newest=$t
    done
    echo "$newest"
}

# Why the work at this HEAD is over, or nothing; "?" where gh could not answer, "unpushed" where
# GitHub has no such commit.
over() {
    sha=$1
    if git -C "$primary" merge-base --is-ancestor "$sha" origin/main 2> /dev/null; then
        echo "on origin/main"
        return
    fi
    [ -n "$have_gh" ] || return 0
    # every pull request that holds the commit, as "<number> <head> <base> <merged at>"; this one is
    # over only where it merged into main at this very head - into a stacked base it is not on main
    if ! pulls=$(gh api "repos/$repo/commits/$sha/pulls" \
        --jq '.[] | "\(.number) \(.head.sha) \(.base.ref) \(.merged_at)"' 2> "$listing.err"); then
        if grep -q 'No commit found' "$listing.err"; then echo unpushed; else echo "?"; fi
        return
    fi
    pr=$(printf '%s\n' "$pulls" | awk -v sha="$sha" '$2 == sha && $3 == "main" && $4 != "null" { print $1; exit }')
    [ -n "$pr" ] && echo "#$pr merged at this head"
    return 0
}

# Sets verdict (done or keep) and reason for one worktree.
judge() {
    wt=$1
    verdict=keep
    if [ "$2" = locked ]; then reason="locked"; return; fi
    if [ ! -d "$wt" ]; then reason="gone from disk; git worktree prune forgets it"; return; fi
    # read before anything below looks at it: git status refreshes the index, which is one of the
    # files this reads
    if ! gitdir=$(git -C "$wt" rev-parse --path-format=absolute --git-dir 2> /dev/null) || ! used=$(last_used "$gitdir"); then
        reason="git cannot read it"
        return
    fi
    if inner=$(awk -v p="worktree $wt/" 'index($0, p) == 1 { print substr($0, 10); exit }' "$listing") && [ -n "$inner" ]; then
        reason="holds the worktree $inner"
        return
    fi
    op=$(held_operation "$gitdir")
    if [ -n "$op" ]; then reason="a git operation is under way in it ($op)"; return; fi
    if ! porcelain=$(git -C "$wt" status --porcelain --untracked-files=normal 2> /dev/null); then
        reason="git cannot read it"
        return
    fi
    changes=$(printf '%s\n' "$porcelain" | grep -vx '?? oom' | grep -c . || true)
    if [ "$changes" -gt 0 ]; then reason="changes: $changes file(s)"; return; fi
    hidden=$(held_hidden_edits "$wt")
    if [ "$hidden" -gt 0 ]; then reason="$hidden tracked file(s) marked skip-worktree or assume-unchanged"; return; fi
    made=$(held_ignored "$wt" | tr '\n' ' ')
    if [ -n "$made" ]; then reason="holds ignored files that are not regenerable: ${made% }"; return; fi
    if [ -n "$(running_for "$wt")" ]; then reason="a container made for it is running"; return; fi
    if printf '%s\n' "$cwds" | awk -v w="$wt" '$0 == w || index($0, w "/") == 1 { found = 1 } END { exit !found }'; then
        reason="a process is working in it"
        return
    fi
    branch=$(git -C "$wt" symbolic-ref --quiet --short HEAD || true)
    if [ -n "$branch" ]; then
        # oldest last; an empty reflog has expired (gc.reflogExpire, 90 days), which says nothing
        log=$(git -C "$wt" reflog show --format=%gs "refs/heads/$branch" -- 2> /dev/null || true)
        from=$(printf '%s\n' "$log" | sed -n '$s/^branch: Created from //p')
        case "$from" in
            origin/main | main | HEAD | refs/remotes/origin/main)
                if ! printf '%s\n' "$log" | grep -qE '^(commit|cherry-pick|revert)|: Merge made'; then
                    reason="its branch was made from main and has had no commit of its own"
                    return
                fi
                ;;
            *) ;;
        esac
    fi
    hours=$(( (now - used) / 3600 ))
    if [ "$hours" -lt "$idle_hours" ]; then reason="used ${hours}h ago"; return; fi
    if ! head=$(git -C "$wt" rev-parse HEAD 2> /dev/null); then reason="git cannot read it"; return; fi
    why=$(over "$head")
    case "$why" in
        '') reason="not on origin/main, and no pull request merged into main at this head" ;;
        unpushed) reason="not on origin/main, and GitHub does not have this head" ;;
        '?')
            reason="not on origin/main, and gh could not answer for a pull request"
            note "gh could not answer for $repo, so a squash-merged worktree reads as kept"
            ;;
        *)
            if [ -n "$runtime_failed" ]; then
                reason="$why, but the container runtime could not say what runs for it"
            else
                verdict="done"
                reason=$why
            fi
            ;;
    esac
}

status=0
while IFS="$tab" read -r wt lock; do
    [ -n "$wt" ] || continue
    judge "$wt" "$lock"
    echo "$verdict $wt ($reason)"
    # finished as soon as it is judged, so nothing has long to start in it between the two
    if [ "$verdict" = "done" ] && [ -n "$yes" ]; then
        "$finish" "$wt" < /dev/null || status=1
    fi
done << EOF
$(linked)
EOF
[ -n "$have_gh" ] || note "no gh, so only work on origin/main was found; a squash-merged worktree reads as kept"
[ -z "$notes" ] || printf '%s\n' "$notes" | sed '/^$/d'
exit "$status"
