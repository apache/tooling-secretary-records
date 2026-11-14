#!/bin/sh
# What worktree-sweep.sh calls finished, and - the half that matters - what it must leave alone:
# anything whose removal could take work nobody committed or something still running, and work
# that did not reach main. Also the refusals worktree-done.sh holds for every caller.
#
# A throwaway repository with a bare remote and linked worktrees stands in for this one, ignoring
# what this one's .gitignore files ignore. `gh` is a stub that runs the sweep's own --jq over the
# pull requests a commit is answered with, refuses a repository other than the one origin names,
# and fails, or has no such commit, where told to; the container runtime is a stub with running
# containers, one stopped one, and a failure where told to; the finisher is a stub that records
# what it was handed and refuses where told to. Run by prek (see .pre-commit-config.yaml), and so
# by `make check`.

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
failures=0
work=$(CDPATH='' cd -- "$(mktemp -d)" && pwd -P)
busy_pid=''
trap 'if [ -n "$busy_pid" ]; then kill "$busy_pid" 2> /dev/null || true; fi; rm -rf "$work" "$work.link"' EXIT
mkdir -p "$work/bin" "$work/gh"

cat > "$work/bin/gh" << 'STUB'
#!/bin/sh
# gh api repos/<repo>/commits/<sha>/pulls --jq <filter>
repo=$(printf '%s\n' "$2" | sed -E 's#^repos/(.*)/commits/.*#\1#')
[ "$repo" = "$EXPECT_REPO" ] || { echo "stub: asked about $repo, not $EXPECT_REPO" >&2; exit 64; }
sha=$(printf '%s\n' "$2" | sed -E 's#.*/commits/([0-9a-f]+)/pulls#\1#')
[ -f "$GH_DIR/$sha.fail" ] && { echo "HTTP 403: rate limit" >&2; exit 1; }
[ -f "$GH_DIR/$sha.unpushed" ] && { echo "No commit found for SHA: $sha (HTTP 422)" >&2; exit 1; }
if [ -f "$GH_DIR/$sha" ]; then jq -s '.' "$GH_DIR/$sha"; else echo '[]'; fi | jq -r "$4"
STUB
cat > "$work/bin/runtime" << 'STUB'
#!/bin/sh
# ps [-a] --format {{.Names}}, and container inspect --format <label template> <name>
[ -z "${RUNTIME_FAIL:-}" ] || { echo "cannot connect to the runtime" >&2; exit 125; }
case "$1" in
    ps)
        echo secretary-records-run-x
        echo secretary-records-keep-named
        case "$*" in *" -a"* | *" --all"*) echo secretary-records-run-stopped ;; esac
        ;;
    container)
        case "$5" in
            secretary-records-run-x) echo "$RUNNING" ;;
            secretary-records-keep-named) echo '<no value>' ;;
            secretary-records-run-stopped) echo "$STOPPED" ;;
        esac
        ;;
esac
exit 0
STUB
cat > "$work/bin/finish" << 'STUB'
#!/bin/sh
echo "$1" >> "$FINISHED"
[ "$1" != "${FAIL_ON:-}" ]
STUB
chmod +x "$work/bin/gh" "$work/bin/runtime" "$work/bin/finish"

fail() {
    echo "FAIL: $1" >&2
    failures=$((failures + 1))
}

git init -q --bare "$work/remote.git"
git clone -q "$work/remote.git" "$work/repo" 2> /dev/null
cd "$work/repo"
git config user.email t@example.org
git config user.name t
for f in $(git -C "$here/.." ls-files '.gitignore' '*/.gitignore'); do
    mkdir -p "$(dirname "$f")"
    cp "$here/../$f" "$f"
    git add "$f"
done
# tracked, as in this repository: an untracked directory holding only ignored files is itself listed
mkdir -p app/client/static app/sub assets/.yarn/releases
: > app/client/static/index.html
: > app/sub/mod.py
: > assets/.yarn/releases/yarn.cjs
git add app/client/static/index.html app/sub/mod.py assets/.yarn/releases/yarn.cjs
git commit -q -m base
git push -q origin HEAD:main 2> /dev/null
git fetch -q origin
printf '%s\n' notes.md statement.md >> "$(git rev-parse --git-common-dir)/info/exclude"

# a timestamp, not "10 hours ago": not every touch reads words
stamp=$(date -d "@$(($(date +%s) - 36000))" +%Y%m%d%H%M.%S)
idle() {
    gitdir=$(git -C "$1" rev-parse --path-format=absolute --git-dir)
    for f in HEAD index logs/HEAD; do
        if [ -e "$gitdir/$f" ]; then touch -t "$stamp" "$gitdir/$f"; fi
    done
}
branch_with_commit() {
    git worktree add -q "$work/$1" -b "$1" origin/main
    git -C "$work/$1" commit -q --allow-empty -m "$1"
}
landed() {
    branch_with_commit "$1"
    git -C "$work/$1" push -q origin "$1:main" 2> /dev/null
}
# answer <worktree> <number> <head sha> <base> <merged at, or null>: a pull request holding its HEAD,
# as GitHub's API spells it
answer() {
    merged=null closed=null
    [ "$5" = null ] || merged="\"$5\""
    [ "$5" = closed ] && merged=null
    [ "$5" = null ] || closed='"2026-11-10T00:00:00Z"'
    printf '{"number": %s, "head": {"sha": "%s", "ref": "%s"}, "base": {"ref": "%s"}, "merged_at": %s, "closed_at": %s}\n' \
        "$2" "$3" "$1" "$4" "$merged" "$closed" >> "$work/gh/$(git -C "$work/$1" rev-parse HEAD)"
}
head_of() { git -C "$work/$1" rev-parse HEAD; }

# a topic claimed before main moves on, and rebased onto it later
git worktree add -q "$work/rebased" -b rebased origin/main

# finished: on main, holding everything the toolchain writes again; merged into main at this
# head; on main holding only conmon's oom marker; with a container that has stopped; made from
# somebody else's branch, which then landed
landed landed
for d in .venv .pytest_cache .ruff_cache .complexipy_cache htmlcov app/client/static/assets assets/node_modules \
    assets/coverage e2e/node_modules e2e/test-results e2e/screenshots app/sub/__pycache__ state/jscpd \
    state/behaviour-base state-e2e/data state-preview/state-e2e; do
    mkdir -p "$work/landed/$d"
    : > "$work/landed/$d/x"
done
echo '*' > "$work/landed/.complexipy_cache/.gitignore"
for f in .coverage coverage.json assets/.yarn/install-state.gz assets/stats.html state/quality-base.json \
    state/behaviour.diff state-preview/config.yaml state/cert.pem state/key.pem; do
    mkdir -p "$(dirname "$work/landed/$f")"
    : > "$work/landed/$f"
done
branch_with_commit squashed
answer squashed 17 "$(head_of squashed)" main 2026-11-10T00:00:00Z
landed oom
: > "$work/oom/oom"
landed stopped
landed theirs
git -C "$work/theirs" push -q origin theirs:theirs 2> /dev/null
git fetch -q origin
git worktree add -q "$work/review" -b review origin/theirs
# git cannot read it - listed before the others, which must still be listed; and one whose
# gitdir resolves but whose index does not read, so status fails where the idle read does not
landed broken
rm "$work/broken/.git"
landed corrupt
echo garbage > "$(git -C "$work/corrupt" rev-parse --path-format=absolute --git-dir)/index"
# not on main: merged at a later head; closed unmerged; merged into a stacked base; never
# merged; gh could not answer; never pushed
branch_with_commit behind
answer behind 18 0000000000000000000000000000000000000000 main 2026-11-10T00:00:00Z
branch_with_commit closed
answer closed 19 "$(head_of closed)" main closed
branch_with_commit stacked
answer stacked 20 "$(head_of stacked)" feat/base 2026-11-10T00:00:00Z
branch_with_commit unmerged
branch_with_commit ghfail
: > "$work/gh/$(head_of ghfail).fail"
branch_with_commit local
: > "$work/gh/$(head_of local).unpushed"
# on main, but: changes; a file that only looks like the oom marker; an untracked file git is
# told not to show; ignored files somebody made, by .gitignore and by info/exclude; edits under
# skip-worktree and under assume-unchanged; a rebase stopped half way; a running container, by
# label through a symlink and by name; a process working in it; another worktree inside it; locked
landed dirty
echo work > "$work/dirty/notes.txt"
landed boom
: > "$work/boom/boom.txt"
landed hidden
echo draft > "$work/hidden/draft.md"
git -C "$work/hidden" config --worktree status.showUntrackedFiles no 2> /dev/null ||
    git config status.showUntrackedFiles no
landed made
echo 'base: {}' > "$work/made/config.yaml"
landed excluded
echo mine > "$work/excluded/notes.md"
# only the top-level state/ is scratch: a nested one (.gitignore's `state/` matches at any depth),
# and a name that merely starts with "state", are somebody's
landed nested
mkdir -p "$work/nested/app/sub/state"
echo mine > "$work/nested/app/sub/state/mine.md"
landed stately
echo mine > "$work/stately/statement.md"
# finished although state/ holds what it ran and fetched: the checkout's own scratch
landed database
mkdir -p "$work/database/state/fetched"
: > "$work/database/state/secretary-records.db"
: > "$work/database/state/secretary-records.log"
: > "$work/database/state/fetched/page-00000.json"
landed skipped
echo edit >> "$work/skipped/.gitignore"
git -C "$work/skipped" update-index --skip-worktree .gitignore
landed assumed
echo edit >> "$work/assumed/.gitignore"
git -C "$work/assumed" update-index --assume-unchanged .gitignore
branch_with_commit rebasing
git -C "$work/rebasing" push -q origin rebasing:main 2> /dev/null
GIT_SEQUENCE_EDITOR='sed -i 1ibreak' git -C "$work/rebasing" rebase -q -i HEAD~1 > /dev/null 2>&1
landed running
ln -s "$work" "$work.link"
landed named
landed busy
(cd "$work/busy" && exec sleep 600) &
busy_pid=$!
landed parent
git worktree add -q "$work/parent/.claude/worktrees/agent" -b agent origin/main
echo work > "$work/parent/.claude/worktrees/agent/work.txt"
landed locked
git worktree lock "$work/locked"
# on main without a commit of its own: a topic just claimed, and one claimed and then rebased
git worktree add -q "$work/claimed" -b claimed origin/main
git fetch -q origin
git -C "$work/rebased" rebase -q origin/main
# on main and finished, but in use: all of it recent, and only its index recent
landed recent
landed staging
git fetch -q origin
for w in landed squashed oom stopped review corrupt behind closed stacked unmerged ghfail local dirty boom hidden made \
    excluded nested stately database skipped assumed rebasing running named busy parent locked claimed rebased; do
    idle "$work/$w"
done
gitdir=$(git -C "$work/staging" rev-parse --path-format=absolute --git-dir)
touch -t "$stamp" "$gitdir/HEAD" "$gitdir/logs/HEAD"
# what gh is asked about is origin's owner/repo, however origin is spelled
git remote set-url origin git@github.com:apache/tooling-secretary-records.git

sweep() {
    PATH="$1" GH_DIR="$work/gh" EXPECT_REPO=apache/tooling-secretary-records FINISHED="$work/finished" \
        RUNNING="$work.link/running" STOPPED="$work/stopped" SR_RUNTIME="${RT-$work/bin/runtime}" \
        SR_WORKTREE_DONE="$work/bin/finish" FAIL_ON="${FAIL_ON:-}" RUNTIME_FAIL="${RUNTIME_FAIL:-}" \
        sh "$here/worktree-sweep.sh" ${2:+"$2"} > "$work/out" 2>&1
}
says() { grep -qF -- "$1" "$work/out" || fail "$2: no \"$1\" in: $(tr '\n' '|' < "$work/out")"; }
silent() { if grep -qF -- "$1" "$work/out"; then fail "$2: \"$1\" in: $(tr '\n' '|' < "$work/out")"; fi; }

rm -f "$work/finished"
sweep "$work/bin:$PATH" || fail "the listing exited non-zero: $(tr '\n' '|' < "$work/out")"
says "done $work/landed (on origin/main)" "what the toolchain writes again is not work"
says "done $work/squashed (#17 merged at this head)" "a worktree merged into main at its head is finished"
says "done $work/oom (on origin/main)" "conmon's oom marker is not work"
says "done $work/stopped (on origin/main)" "a stopped container is not a running one"
says "done $work/review (on origin/main)" "a branch made from somebody else's is finished once it landed"
says "keep $work/broken (git cannot read it)" "a worktree git cannot read is kept, and the sweep goes on"
says "keep $work/corrupt (git cannot read it)" "a worktree whose status fails is kept"
says "keep $work/behind (not on origin/main, and no pull request merged into main at this head)" "merged at another head is kept"
says "keep $work/closed (not on origin/main, and no pull request" "a pull request closed unmerged is kept"
says "keep $work/stacked (not on origin/main, and no pull request" "a pull request merged into a stacked base is kept"
says "keep $work/unmerged (not on origin/main, and no pull request" "work nothing merged is kept"
says "keep $work/ghfail (not on origin/main, and gh could not answer" "gh failing is said, not read as no pull request"
says "note: gh could not answer for apache/tooling-secretary-records" "gh failing is noted"
says "keep $work/local (not on origin/main, and GitHub does not have this head)" "a head never pushed is not a gh failure"
says "keep $work/dirty (changes: 1 file(s))" "a worktree with changes is kept"
says "keep $work/boom (changes: 1 file(s))" "only the oom marker itself is exempt"
says "keep $work/hidden (changes: 1 file(s))" "untracked files count even where git is told not to show them"
says "keep $work/made (holds ignored files that are not regenerable: config.yaml)" "an ignored file somebody made is kept"
says "keep $work/excluded (holds ignored files that are not regenerable: notes.md)" "a file hidden by info/exclude is kept"
says "keep $work/nested (holds ignored files that are not regenerable: app/sub/state/)" "a nested state/ is somebody's"
says "keep $work/stately (holds ignored files that are not regenerable: statement.md)" "a name that starts with state is somebody's"
says "done $work/database (on origin/main)" "state/ is the checkout's own scratch, a database and fetched data included"
says "keep $work/skipped (1 tracked file(s) marked skip-worktree" "an edit under skip-worktree is kept"
says "keep $work/assumed (1 tracked file(s) marked skip-worktree or assume-unchanged)" "an edit under assume-unchanged is kept"
says "keep $work/rebasing (a git operation is under way in it (rebase-merge))" "a rebase stopped half way is kept"
says "keep $work/running (a container made for it is running)" "a container labelled through a symlink is found"
says "keep $work/named (a container made for it is running)" "a container known only by its name is found"
says "keep $work/busy (a process is working in it)" "a process working in it keeps it"
says "keep $work/parent (holds the worktree $work/parent/.claude/worktrees/agent)" "a worktree holding another is kept"
says "keep $work/locked (locked)" "a locked worktree is kept"
says "keep $work/claimed (its branch was made from main and has had no commit of its own)" "a topic just claimed is kept"
says "keep $work/rebased (its branch was made from main and has had no commit of its own)" "a claimed topic rebased onto main is still claimed"
says "keep $work/recent (used 0h ago)" "a worktree in use is kept"
says "keep $work/staging (used 0h ago)" "a worktree whose index moved is in use"
grep -qF "$work/repo " "$work/out" && fail "the primary checkout is listed"
[ -e "$work/finished" ] && fail "the listing finished something: $(tr '\n' ' ' < "$work/finished")"

# --yes hands the finisher exactly the finished ones, goes on past one that refuses, and says so

expected=$(printf '%s\n' "$work/database" "$work/landed" "$work/oom" "$work/review" "$work/squashed" "$work/stopped" | sort)
rm -f "$work/finished"
sweep "$work/bin:$PATH" --yes || fail "--yes exited non-zero"
got=$(sort "$work/finished" 2> /dev/null || true)
[ "$got" = "$expected" ] || fail "--yes finished: $(printf '%s' "$got" | tr '\n' ' '), not: $(printf '%s' "$expected" | tr '\n' ' ')"
rm -f "$work/finished"
if FAIL_ON="$work/landed" sweep "$work/bin:$PATH" --yes; then fail "--yes exited 0 when the finisher refused one"; fi
got=$(sort "$work/finished" 2> /dev/null || true)
[ "$got" = "$expected" ] || fail "--yes stopped at a refusal: it finished $(printf '%s' "$got" | tr '\n' ' ')"

# a container runtime that cannot answer keeps what would be finished, and says so
RUNTIME_FAIL=1 sweep "$work/bin:$PATH" || fail "the listing with a failing runtime exited non-zero"
says "keep $work/landed (on origin/main, but the container runtime could not say what runs for it)" "a runtime failure is not read as nothing running"
says "note: $work/bin/runtime could not list its containers" "a runtime failure is noted"

# origin spelled ssh:// asks about the same repository
git remote set-url origin ssh://git@github.com/apache/tooling-secretary-records
sweep "$work/bin:$PATH" || fail "the listing with an ssh:// origin exited non-zero"
says "done $work/squashed (#17 merged at this head)" "an ssh:// origin is read as owner/repo"

# without gh or a container runtime, only what is on main is found, and it says what it could not see
nogh="$work/nogh"
mkdir -p "$nogh"
for tool in git sh awk sed grep stat date dirname basename wc tr head cat rm touch sort readlink; do
    p=$(command -v "$tool") && ln -sf "$p" "$nogh/$tool"
done
RT='' sweep "$nogh" || fail "the listing without gh exited non-zero: $(tr '\n' '|' < "$work/out")"
says "keep $work/squashed (not on origin/main, and no pull request" "without gh a squash-merged worktree is kept"
says "note: no gh" "without gh it says what it could not see"
says "note: no container runtime found" "without a container runtime it says what it could not see"
silent "keep $work/local (not on origin/main, and GitHub" "without gh nothing is said about GitHub"

# worktree-done.sh itself refuses what status does not show, and each keeps its work
for w in parent hidden excluded skipped rebasing; do
    if SR_RUNTIME=false sh "$here/worktree-done.sh" "$work/$w" > "$work/out" 2>&1; then
        fail "worktree-done.sh removed $w: $(tr '\n' '|' < "$work/out")"
    fi
done
[ -f "$work/parent/.claude/worktrees/agent/work.txt" ] || fail "the worktree inside parent lost its work"
[ -f "$work/hidden/draft.md" ] || fail "hidden lost its untracked file"
[ -f "$work/excluded/notes.md" ] || fail "excluded lost its file"
grep -q edit "$work/skipped/.gitignore" || fail "skipped lost its edit"
[ -d "$work/rebasing" ] || fail "rebasing was removed mid-rebase"

# a git that cannot list the ignored files leaves a worktree kept, not found empty
mkdir -p "$work/failgit"
real_git=$(command -v git)
printf '#!/bin/sh\ncase "$*" in *ls-files*) exit 1 ;; esac\nexec %s "$@"\n' "$real_git" > "$work/failgit/git"
chmod +x "$work/failgit/git"
held=$(PATH="$work/failgit:$PATH" sh -c '. "$1"; held_ignored "$2"' _ "$here/worktree-held.sh" "$work/landed")
[ -n "$held" ] || fail "a failed listing of ignored files reads as nothing held"

if [ "$failures" -gt 0 ]; then
    echo "worktree-sweep: $failures failure(s)" >&2
    exit 1
fi
echo "worktree-sweep: all cases pass"
