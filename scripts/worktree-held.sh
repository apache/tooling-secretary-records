#!/bin/sh
# What in a worktree is somebody's own although `git status` does not show it, so that removing the
# worktree would take it: worktree-sweep.sh keeps such a worktree, and worktree-done.sh refuses it.
#
# Sourced, not run, like checkout-id.sh.

# Ignored paths that are the checkout's own to throw away, as `git ls-files --directory` spells them:
# what the toolchain writes again, and state/, the checkout's scratch. Whatever a local run or a
# script left there - a database, a log, a fetched corpus - is made or fetched again, never the
# record, which lives on the server. Any other ignored path is somebody's: whatever a person hid in
# a worktree, through .gitignore, info/exclude or a global excludes file, is theirs.
HELD_REGENERABLE='^(\.venv/|\.pytest_cache/|\.ruff_cache/|\.complexipy_cache/|\.coverage$|coverage\.json$|htmlcov/|app/client/static/assets/|assets/node_modules/|assets/\.yarn/install-state\.gz$|assets/coverage/|assets/stats\.html$|e2e/node_modules/|e2e/test-results/|e2e/screenshots/|state/|state-e2e[^/]*/|state-preview/)|(^|/)__pycache__/'

# The first few ignored paths in worktree $1 that are not regenerable, one to a line. When git cannot
# list them, that is said as a held path, so a worktree nobody could look into is kept.
held_ignored() {
    _ignored=$(git -C "$1" ls-files --others --ignored --exclude-standard --directory 2> /dev/null) || {
        echo "(git could not list the ignored files)"
        return 0
    }
    [ -n "$_ignored" ] || return 0
    printf '%s\n' "$_ignored" | grep -vE "$HELD_REGENERABLE" | head -n 3 || true
}

# How many tracked files in worktree $1 are marked skip-worktree or assume-unchanged, whose edits
# status never shows; `ls-files -v` tags them S and in lower case.
held_hidden_edits() {
    git -C "$1" ls-files -v | grep -c '^[a-zS]' || true
}

# The rebase, merge, cherry-pick, revert or bisect under way in the worktree whose git dir is $1,
# whose state git removes with the worktree; nothing when there is none.
held_operation() {
    for _op in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
        if [ -e "$1/$_op" ]; then
            echo "$_op"
            return
        fi
    done
}
