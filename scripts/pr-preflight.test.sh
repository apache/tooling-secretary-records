#!/bin/sh
# What pr-preflight.sh answers, on repositories it builds itself.
#
# The section worth testing is the last one. "none" there means "nobody else is in these
# files", and a session reads it as leave to restructure them - so a scan that cannot look
# must not print it. Every case below either checks that claim or checks a rule block that
# was silent when it should have spoken.
#
# A real git repository per case rather than a stub, because everything the script gets
# wrong it gets wrong in how it asks git: a pathspec resolved against the wrong directory,
# a ref with no merge base, a path git globs when it should not.
#
# Run by prek (see .pre-commit-config.yaml), and so by `make check`.

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
script="$here/pr-preflight.sh"

# This runs from `make check`, which means it runs on every check once these files exist -
# so it must not touch, or be steered by, anything outside its own temp directories. A
# global `core.hooksPath` made the first `git commit` run somebody's own pre-commit hook and
# took the whole suite down with it; an exported GIT_DIR made the suite commit into a real
# repository. pr-review.test.sh has none of this exposure because it never runs git.
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_SYSTEM=/dev/null
export GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_CEILING_DIRECTORIES

failures=0
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fail() {
    printf '%s\n' "$1" >&2
    shift
    for _l in "$@"; do
        printf '    %s\n' "$_l" >&2
    done
    failures=$((failures + 1))
}

# A repository with an `origin` it can fetch from, since the script reads it before it
# scans. The remote is a bare clone on disk, so "origin" is real and no network is used.
#
# $1 names the case; the caller then commits into $work/$1/local and pushes branches to
# $work/$1/origin.git.
new_repo() {
    _name=$1
    _root=$work/$_name
    mkdir -p "$_root"
    git init -q --bare -b main "$_root/origin.git"
    git init -q -b main "$_root/local"
    git -C "$_root/local" config user.email preflight@example.invalid
    git -C "$_root/local" config user.name 'Preflight Test'
    git -C "$_root/local" config commit.gpgsign false
    git -C "$_root/local" remote add origin "$_root/origin.git"
    mkdir -p "$_root/local/app" "$_root/local/scripts"
    printf 'x\n' > "$_root/local/app/thing.py"
    printf 'y\n' > "$_root/local/scripts/tool.sh"
    git -C "$_root/local" add app/thing.py scripts/tool.sh
    git -C "$_root/local" commit -qm 'first'
    git -C "$_root/local" push -q origin main
    git -C "$_root/local" fetch -q origin
    repo=$_root/local
}

# Run the script inside $repo (or a subdirectory of it) and keep its output and status.
run_in() {
    _dir=$1
    status=0
    output=$(cd "$_dir" && sh "$script" 2>&1) || status=$?
}

says() {
    printf '%s' "$output" | grep -qE "$1"
}

# --- a branch that shares a file is named; one that shares none is not -----------------
# The base case. It would have caught nothing so far, and every case after it is a way
# this one has been true while the answer was still wrong.

new_repo shared
git -C "$repo" checkout -qb other origin/main
printf 'other\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'other touches app/thing.py'
git -C "$repo" push -q origin other
git -C "$repo" checkout -qb elsewhere origin/main
printf 'z\n' > "$repo/scripts/unrelated.sh"
git -C "$repo" add scripts/unrelated.sh
git -C "$repo" commit -qm 'elsewhere touches nothing of mine'
git -C "$repo" push -q origin elsewhere
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'mine touches app/thing.py'

run_in "$repo"
says 'origin/other' || fail 'a branch sharing a file was not named' "$output"
says 'origin/elsewhere' && fail 'a branch sharing nothing was named' "$output"
[ "$status" -eq 0 ] || fail "a readable scan exited $status"

# --- the same question from a subdirectory gets the same answer ------------------------
# A pathspec after `--` is resolved against the cwd, and these paths are root-relative:
# from scripts/, `-- app/thing.py` means `scripts/app/thing.py` and matches nothing. The
# scan then printed "none" with nothing to say it had not looked.

run_in "$repo"
root_output=$output
run_in "$repo/scripts"
if [ "$output" != "$root_output" ]; then
    fail 'the answer changed when run from a subdirectory' \
        '--- from the root ---' "$root_output" '--- from scripts/ ---' "$output"
fi

# --- a path with a space in it is one pathspec, not two --------------------------------
# Split on whitespace it becomes two that match nothing, and the scan reports "none".

new_repo spaces
git -C "$repo" checkout -qb other origin/main
printf 'x\n' > "$repo/a b.txt"
git -C "$repo" add 'a b.txt'
git -C "$repo" commit -qm 'other adds a spaced path'
git -C "$repo" push -q origin other
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' > "$repo/a b.txt"
git -C "$repo" add 'a b.txt'
git -C "$repo" commit -qm 'mine adds the same spaced path'

run_in "$repo"
says 'origin/other' || fail 'a collision on a path with a space was missed' "$output"

# --- a glob character in a path is a character, not a wildcard -------------------------
# `-- 'x?y.txt'` is globbed by git itself, so a file nobody shares is reported as shared.

new_repo globs
git -C "$repo" checkout -qb other origin/main
printf 'x\n' > "$repo/xay.txt"
git -C "$repo" add xay.txt
git -C "$repo" commit -qm 'other adds xay.txt'
git -C "$repo" push -q origin other
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' > "$repo/x?y.txt"
git -C "$repo" add 'x?y.txt'
git -C "$repo" commit -qm 'mine adds x?y.txt'

run_in "$repo"
says 'origin/other' && fail 'a ? in a path matched somebody else file' "$output"

# --- a ref with no merge base is reported, not dropped ---------------------------------
# `merge-base || continue` answered "not a collision" about a branch that rewrites the
# same file, which is the one answer this section must never give wrongly.

new_repo orphan
git -C "$repo" checkout -q --orphan lonely
git -C "$repo" rm -q -rf . > /dev/null 2>&1 || true
mkdir -p "$repo/app"
printf 'orphan\n' > "$repo/app/thing.py"
git -C "$repo" add app/thing.py
git -C "$repo" commit -qm 'an unrelated history that rewrites app/thing.py'
git -C "$repo" push -q origin lonely
git -C "$repo" checkout -q main
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'mine touches app/thing.py'

run_in "$repo"
says 'origin/lonely' || fail 'a ref with no merge base vanished from the scan' "$output"
says ' none$' && fail 'it said none while an unrelated history rewrote the same file' "$output"

# --- a comparison git refuses is reported, not read as "no overlap" --------------------
# The real trigger is an argv too long for one exec, which needs some twelve thousand
# untracked files to provoke. A tree object removed from under a ref fails the same
# `git diff` the same way, for a couple of milliseconds: merge-base walks commits and
# still succeeds, and the diff cannot read the tree.

new_repo unreadable
git -C "$repo" checkout -qb broken origin/main
printf 'broken\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'broken touches app/thing.py'
git -C "$repo" push -q origin broken
git -C "$repo" fetch -q origin
git -C "$repo" checkout -q main
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'mine touches app/thing.py'
broken_tree=$(git -C "$repo" rev-parse 'origin/broken^{tree}')
rm -f "$repo/.git/objects/$(printf '%s' "$broken_tree" | cut -c1-2)/$(printf '%s' "$broken_tree" | cut -c3-)"

run_in "$repo"
says 'could not be compared' ||
    fail 'a comparison git refused was read as no overlap' "$output"
# ...and it says why. Without git's own line the reader is told a comparison failed and
# nothing about which of the several reasons it was.
says 'unable to read tree' ||
    fail 'the reason git refused was thrown away' "$output"
says ' none$' && fail 'it said none over a branch it could not compare' "$output"
[ "$status" -ne 0 ] || fail 'a scan that could not compare a branch still exited 0' "$output"

# --- a path git hands back quoted matches nothing, and is said so ----------------------
# core.quotePath=false stops the quoting for a non-ASCII byte and not for a `"`, so the
# pathspec built from it matches no file - and the scan would otherwise answer "none".

new_repo quoted
git -C "$repo" checkout -qb other origin/main
printf 'x\n' > "$repo/a\"b.txt"
git -C "$repo" add 'a"b.txt'
git -C "$repo" commit -qm 'other adds a quoted path'
git -C "$repo" push -q origin other
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' > "$repo/a\"b.txt"
git -C "$repo" add 'a"b.txt'
git -C "$repo" commit -qm 'mine adds the same quoted path'

run_in "$repo"
says 'quote, a backslash or a control character' ||
    fail 'a path git quoted back was not reported as unseeable' "$output"
says ' none$' && fail 'it said none over a path it could not turn into a pathspec' "$output"

# --- ...and the note names every character that provokes it ----------------------------
# git C-quotes a path holding a control character too, and there it is the note rather than
# the detection that has been wrong: a name with a tab in it was reported as holding "a
# quote or a backslash", so the reader looks for two characters the name does not contain
# and cannot act on the one thing the scan is telling them it could not see.

new_repo control
git -C "$repo" checkout -qb mine origin/main
control_path=$(printf 'a\tb.txt')
printf 'mine\n' > "$repo/$control_path"
git -C "$repo" add -- "$control_path"
git -C "$repo" commit -qm 'mine adds a path with a tab in it'

run_in "$repo"
says 'control character' ||
    fail 'a path quoted for a control character was described as a quote or a backslash' \
        "$output"
says ' none$' && fail 'it said none over a path with a control character in it' "$output"
[ "$status" -ne 0 ] || fail 'a scan that could not see a path still exited 0' "$output"

# --- the branch you are on is excluded by its upstream, not by its name ----------------
# A local branch may be called something else, and then the scan reported a collision
# with itself.

new_repo upstream
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'mine touches app/thing.py'
git -C "$repo" push -q origin mine:feat/renamed
git -C "$repo" branch --set-upstream-to=origin/feat/renamed mine > /dev/null 2>&1
git -C "$repo" fetch -q origin

run_in "$repo"
says 'origin/feat/renamed' && fail 'the branch reported a collision with itself' "$output"

# --- a component that fetches is asked about before it is committed --------------------
# Every other rule here fires on uncommitted work. This one asking for a commit first
# made it silent exactly while the component was being written.

new_repo uncommitted
git -C "$repo" checkout -qb mine origin/main
mkdir -p "$repo/assets/src/js/components"
printf '<script>\n  const r = await fetch("/api/x");\n</script>\n' \
    > "$repo/assets/src/js/components/Thing.svelte"

run_in "$repo"
says 'a component that fetches' ||
    fail 'an uncommitted component that fetches was not asked about' "$output"

git -C "$repo" add assets/src/js/components/Thing.svelte
git -C "$repo" commit -qm 'add a component that fetches'
run_in "$repo"
says 'a component that fetches' ||
    fail 'a committed component that fetches was not asked about' "$output"

# --- ...and so is a component that already existed and gains a fetch -------------------
# The case the two reads are actually for. With the component new, it is in both the diff
# and the added-files list, so either read alone satisfied the assertion above.

new_repo edited
mkdir -p "$repo/assets/src/js/components"
printf '<script>\n  const x = 1;\n</script>\n' > "$repo/assets/src/js/components/Old.svelte"
git -C "$repo" add assets/src/js/components/Old.svelte
git -C "$repo" commit -qm 'a component that does not fetch'
git -C "$repo" push -q origin main
git -C "$repo" fetch -q origin
git -C "$repo" checkout -qb mine origin/main
printf '<script>\n  const r = await fetch("/api/x");\n</script>\n' \
    > "$repo/assets/src/js/components/Old.svelte"
git -C "$repo" commit -qam 'teach it to fetch'

run_in "$repo"
says 'a component that fetches' ||
    fail 'an existing component that gained a fetch was not asked about' "$output"

# --- a rename is asked about under the name it is moving away from ---------------------
# git reports a rename as its destination only, so the path somebody else is in was never
# in the list to ask about - and that is the split-and-extract case the scan exists for.

new_repo renamed
git -C "$repo" checkout -qb other origin/main
printf 'other\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'other edits app/thing.py'
git -C "$repo" push -q origin other
git -C "$repo" checkout -qb mine origin/main
git -C "$repo" mv app/thing.py app/renamed.py
git -C "$repo" commit -qm 'mine renames it away'

run_in "$repo"
says 'origin/other' || fail 'a rename hid the path somebody else was in' "$output"

# --- your own work is excluded however it was pushed -----------------------------------
# Excluding by upstream alone left two ways to collide with yourself: pushed under another
# name without -u, and a fork layout where @{upstream} is not on origin.

new_repo pushed_as
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'mine touches app/thing.py'
git -C "$repo" push -q origin mine:feat/other-name
git -C "$repo" fetch -q origin

run_in "$repo"
says 'origin/feat/other-name' &&
    fail 'a branch pushed under another name collided with itself' "$output"

# --- a branch on origin that this checkout has not fetched is not silently missed -------
# The scan can only look at refs it has, and "none" over an unfetched branch is the answer
# this section must never give.

new_repo unfetched
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'mine touches app/thing.py'
# A second clone pushes a branch; this checkout never fetches it.
git clone -q "$work/unfetched/origin.git" "$work/unfetched/other"
git -C "$work/unfetched/other" config user.email preflight@example.invalid
git -C "$work/unfetched/other" config user.name 'Preflight Test'
git -C "$work/unfetched/other" checkout -qb theirs
printf 'theirs\n' >> "$work/unfetched/other/app/thing.py"
git -C "$work/unfetched/other" commit -qam 'theirs touches app/thing.py'
git -C "$work/unfetched/other" push -q origin theirs

run_in "$repo"
says 'not in this checkout' ||
    fail 'an unfetched branch on origin was not reported' "$output"
says ' none$' && fail 'it said none while a branch on origin was unseen' "$output"
[ "$status" -ne 0 ] || fail 'an incomplete scan still exited 0' "$output"

# ...and it does not fetch on the reader behalf. The ref must still be absent afterwards.
if git -C "$repo" rev-parse --verify --quiet refs/remotes/origin/theirs > /dev/null; then
    fail 'the script fetched, which is a write to the reader repository'
fi

# --- the licence-header block is about new files, not every Python file ----------------

new_repo newpy
git -C "$repo" checkout -qb mine origin/main
printf 'edited\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'edit an existing python file'

run_in "$repo"
says 'new Python files' && fail 'the licence block fired on an edited python file' "$output"

printf 'new\n' > "$repo/app/fresh.py"
run_in "$repo"
says 'new Python files' || fail 'the licence block did not fire on a new python file' "$output"

# --- the every-branch block says "below" about a line that is below it -----------------
# The simplify line points at the subagent line by position, and both are strings in one
# `say` call - so reordering them, or slipping a third rule between, leaves the output
# saying "below" about a line above it with nothing failing. This is that ordering.

new_repo order
git -C "$repo" checkout -qb mine origin/main
printf 'mine\n' >> "$repo/app/thing.py"
git -C "$repo" commit -qam 'anything, so there is a diff to report on'

run_in "$repo"
# Counted before they are located: two matches would make the line numbers below a
# two-line string, and `[ -lt ]` would then fail on "integer expression expected" - the
# case passing or failing for a reason that is not the one it is named for. A phrase that
# prints twice also makes "below" ambiguous, which is worth its own message.
simplify_hits=$(printf '%s\n' "$output" | grep -c '/simplify before the push')
pass_hits=$(printf '%s\n' "$output" | grep -c 'a fresh subagent attacks the branch')
[ "$simplify_hits" -eq 1 ] ||
    fail "the simplify line printed $simplify_hits times, so 'the pass below' names nothing" "$output"
[ "$pass_hits" -eq 1 ] ||
    fail "the pass line printed $pass_hits times, so 'the pass below' names nothing" "$output"

simplify_at=$(printf '%s\n' "$output" | grep -n '/simplify before the push' | cut -d: -f1)
pass_at=$(printf '%s\n' "$output" | grep -n 'a fresh subagent attacks the branch' | cut -d: -f1)
[ "$simplify_at" -lt "$pass_at" ] ||
    fail "simplify says 'the pass below' but printed at line $simplify_at, below $pass_at" "$output"

if [ "$failures" -ne 0 ]; then
    printf '\n%s preflight case(s) failed\n' "$failures" >&2
    exit 1
fi

printf 'pr-preflight.sh: every case holds\n'
