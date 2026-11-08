#!/bin/sh
# What worktree-done.sh and container-gc.sh remove, and - the half that matters - what they
# must leave alone: a live checkout's things, the primary's, another project's, a name given
# outright, and a worktree git would not remove.
#
# A throwaway repository with a bare remote and linked worktrees stands in for this one, the
# scripts are copied into it, and the runtime is a stub that answers from lists of names and
# records every removal and creation. Run by prek (see .pre-commit-config.yaml), and so by
# `make check`.

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
failures=0
work=$(CDPATH='' cd -- "$(mktemp -d)" && pwd -P)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

cat > "$work/bin/podman" <<'STUB'
#!/bin/sh
# Answers from $STUB_DIR/{containers,networks,volumes}, one "<name><tab><checkout path>" a
# line (the path empty for what has none). Removals go to $REMOVED and creations to $CREATED,
# as "<kind> <name>[ <label>]". `--filter label=<key>` keeps what has a path.
tab=$(printf '\t')
file='' verb=''
case "$1" in
    ps) file=containers verb=ls ;;
    rm) echo "container $3" >> "$REMOVED"; exit 0 ;;
    container | network | volume) file=${1}s verb=$2 ;;
    *) echo "stub: unexpected call: $*" >&2; exit 64 ;;
esac
kind=${file%s}
eval "last=\${$#}"
case "$verb" in
    rm) echo "$kind $3" >> "$REMOVED"; exit 0 ;;
    create) echo "$kind $last ${4:-}" >> "$CREATED"; exit 0 ;;
    inspect)
        if [ "$#" -eq 3 ]; then
            # an existence check: no --format
            cut -f1 "$STUB_DIR/$file" | grep -qxF -- "$last"
            exit
        fi
        awk -F "$tab" -v n="$last" '$1 == n { print $2 }' "$STUB_DIR/$file"
        exit 0
        ;;
    ls) ;;
    *) echo "stub: unexpected call: $*" >&2; exit 64 ;;
esac
labelled=''
case "$*" in *--filter*) labelled=1 ;; esac
while IFS="$tab" read -r name path; do
    if [ -n "$labelled" ] && [ -z "$path" ]; then continue; fi
    printf '%s\n' "$name"
done < "$STUB_DIR/$file"
STUB
chmod +x "$work/bin/podman"

fail() {
    echo "FAIL: $1" >&2
    failures=$((failures + 1))
}
removed() { grep -qxF -- "$1" "$work/removed"; }
nothing_removed() {
    if [ -s "$work/removed" ]; then fail "$1 - and removed: $(tr '\n' ' ' < "$work/removed")"; fi
}

# A repository, its remote, and worktrees: finished and pushed, a commit only here,
# uncommitted work, locked, holding only conmon's oom marker, and one removed behind git's back.
setup() {
    rm -rf "$work/remote.git" "$work/repo" "$work"/repo-* "$work/stub"
    mkdir -p "$work/stub"
    : > "$work/removed"
    : > "$work/created"
    git init -q --bare "$work/remote.git"
    git init -q -b main "$work/repo"
    mkdir -p "$work/repo/scripts"
    cp "$here/checkout-id.sh" "$here/checkout-resources.sh" "$here/worktree-done.sh" \
        "$here/container-gc.sh" "$work/repo/scripts/"
    (
        cd "$work/repo"
        git config user.email t@example.org
        git config user.name test
        printf 'state/\n' > .gitignore
        git add scripts .gitignore
        git commit -qm init
        git remote add origin "$work/remote.git"
        git push -q origin main
        for wt in pushed local dirty locked deleted oomonly; do
            git worktree add -q -b "feat/$wt" "../repo-$wt"
        done
        git worktree lock ../repo-locked
    )
    for wt in pushed oomonly; do
        (cd "$work/repo-$wt" && git commit -q --allow-empty -m "$wt" && git push -q origin "feat/$wt")
    done
    (cd "$work/repo-local" && git commit -q --allow-empty -m "only here")
    echo work > "$work/repo-dirty/notes.txt"
    : > "$work/repo-oomonly/oom"
    mkdir -p "$work/repo-oomonly/state" && echo ignored > "$work/repo-oomonly/state/x"
    rm -rf "$work/repo-deleted"

    for wt in pushed local dirty locked deleted; do
        printf '%s\n' "secretary-records-venv-repo-$wt" "secretary-records-node-repo-$wt" "secretary-records-e2e-node-repo-$wt"
    done > "$work/stub/volumes"
    # the primary's plain names, and another project's that look a little like ours
    printf '%s\n' secretary-records-venv secretary-records-node secretary-records-cert vscode secretary-records-venv-elsewhere_x >> "$work/stub/volumes"
    # labelled: by the path, whatever the name says
    printf '%s\t%s\n' secretary-records-venv-agent-gone "$work/no-longer-here" secretary-records-venv-agent-live "$work/repo-local" \
        volume-of-pushed "$work/repo-pushed" >> "$work/stub/volumes"
    printf '%s\n' secretary-records-e2e-server-repo-pushed secretary-records-run-repo-deleted-4242 secretary-records-run-1234 secretary-records-e2e-server \
        secretary-records-keep-repo-deleted > "$work/stub/containers"
    # the last: a name derived from the pushed worktree, but labelled as another checkout's
    printf '%s\t%s\n' run-of-a-gone-one "$work/no-longer-here" run-of-pushed "$work/repo-pushed" \
        secretary-records-run-repo-pushed-77 "$work/repo-local" >> "$work/stub/containers"
    printf '%s\n' secretary-records-e2e-net-repo-pushed secretary-records-e2e-net-repo-deleted secretary-records-e2e-net podman > "$work/stub/networks"
    printf '%s\t%s\n' net-of-a-live-one "$work/repo-local" >> "$work/stub/networks"
}

run() {
    : > "$work/removed"
    status=0
    PATH="$work/bin:$PATH" SR_RUNTIME=podman STUB_DIR="$work/stub" REMOVED="$work/removed" \
        CREATED="$work/created" "$@" > "$work/out" 2>&1 || status=$?
}

# --- container-gc.sh -------------------------------------------------------------------------

setup
run "$work/repo/scripts/container-gc.sh"
[ "$status" -eq 0 ] || fail "the dry run exited $status: $(cat "$work/out")"
nothing_removed "the dry run removed something"
grep -q 'secretary-records-venv-repo-deleted' "$work/out" || fail "the dry run did not list the gone worktree's volume"
if grep -q 'repo-pushed\|repo-local\|repo-dirty\|repo-locked' "$work/out"; then
    fail "the dry run listed a live worktree's: $(cat "$work/out")"
fi
if ! grep -q 'left alone' "$work/out" || ! grep -q '^  volume secretary-records-venv-elsewhere_x' "$work/out"; then
    fail "a name that only looks like ours was not reported as left alone: $(cat "$work/out")"
fi

run "$work/repo/scripts/container-gc.sh" --yes
for name in "volume secretary-records-venv-repo-deleted" "volume secretary-records-node-repo-deleted" \
    "volume secretary-records-e2e-node-repo-deleted" "container secretary-records-run-repo-deleted-4242" \
    "container secretary-records-keep-repo-deleted" "network secretary-records-e2e-net-repo-deleted" \
    "volume secretary-records-venv-agent-gone" "container run-of-a-gone-one"; do
    removed "$name" || fail "--yes left '$name', which belongs to a checkout that is gone"
done
for name in secretary-records-venv secretary-records-node secretary-records-cert vscode secretary-records-venv-elsewhere_x secretary-records-run-1234 \
    secretary-records-e2e-server secretary-records-e2e-net podman secretary-records-venv-repo-pushed secretary-records-e2e-server-repo-pushed \
    secretary-records-venv-agent-live net-of-a-live-one run-of-pushed volume-of-pushed secretary-records-run-repo-pushed-77; do
    if grep -q " $name\$" "$work/removed"; then fail "--yes removed '$name', which is not a gone checkout's"; fi
done

# A worktree of another clone of this repository, beside this one, is not this one's to judge.
setup
mkdir -p "$work/repo-of-another-clone"
echo secretary-records-venv-repo-of-another-clone >> "$work/stub/volumes"
run "$work/repo/scripts/container-gc.sh" --yes
if removed "volume secretary-records-venv-repo-of-another-clone"; then fail "a directory beside the primary had its volume swept"; fi
rm -rf "$work/repo-of-another-clone"

# It must not take a worktree list git could not give for "every checkout is gone".
setup
real_git=$(command -v git)
printf '#!/bin/sh\ncase "$*" in *"worktree list"*) exit 128 ;; esac\nexec %s "$@"\n' "$real_git" > "$work/bin/git"
chmod +x "$work/bin/git"
run "$work/repo/scripts/container-gc.sh" --yes
[ "$status" -ne 0 ] || fail "a sweep that could not list the worktrees did not refuse"
nothing_removed "a sweep that could not list the worktrees did not refuse"
rm "$work/bin/git"

# --- worktree-done.sh ------------------------------------------------------------------------

setup
run "$work/repo/scripts/worktree-done.sh" "$work/repo-pushed"
[ "$status" -eq 0 ] || fail "finishing a pushed worktree exited $status: $(cat "$work/out")"
if [ -d "$work/repo-pushed" ]; then fail "the pushed worktree is still there"; fi
for name in "volume secretary-records-venv-repo-pushed" "volume secretary-records-node-repo-pushed" \
    "volume secretary-records-e2e-node-repo-pushed" "container secretary-records-e2e-server-repo-pushed" \
    "network secretary-records-e2e-net-repo-pushed" "container run-of-pushed" "volume volume-of-pushed"; do
    removed "$name" || fail "finishing it left '$name'"
done
others=$(grep -v -e repo-pushed -e '^container run-of-pushed$' -e '^volume volume-of-pushed$' "$work/removed" || true)
[ -z "$others" ] || fail "finishing it removed another checkout's: $others"
if removed "container secretary-records-run-repo-pushed-77"; then fail "finishing it removed what is labelled as another checkout's"; fi
if git -C "$work/repo" rev-parse --verify -q feat/pushed >/dev/null; then
    fail "its branch is all on the remote and was kept"
fi

run "$work/repo/scripts/worktree-done.sh" "$work/repo-local"
[ "$status" -eq 0 ] || fail "finishing a worktree with a local-only commit exited $status"
git -C "$work/repo" rev-parse --verify -q feat/local >/dev/null || fail "a branch with a commit only here was deleted"
grep -q 'kept branch feat/local' "$work/out" || fail "keeping the branch was not said: $(cat "$work/out")"

run "$work/repo/scripts/worktree-done.sh" "$work/repo-oomonly"
[ "$status" -eq 0 ] || fail "a worktree holding only conmon's oom marker and ignored files was refused: $(cat "$work/out")"

# Each refusal removes nothing at all, not merely fails.
for case in "repo-dirty:a worktree with uncommitted work" "repo-locked:a locked worktree" \
    "repo:the primary checkout" "repo/scripts:a directory inside the primary" \
    "repo-dirty/..:a path that is not a worktree"; do
    path=${case%%:*} what=${case#*:}
    run "$work/repo/scripts/worktree-done.sh" "$work/$path"
    [ "$status" -ne 0 ] || fail "$what was not refused"
    nothing_removed "$what was refused"
done
[ -f "$work/repo-dirty/notes.txt" ] || fail "the uncommitted work is gone"
[ -d "$work/repo-locked" ] || fail "the locked worktree is gone"

# A worktree whose status git cannot give is not taken for a clean one.
setup
echo garbage > "$(git -C "$work/repo-pushed" rev-parse --path-format=absolute --git-path index)"
echo untracked > "$work/repo-pushed/untracked-work.txt"
run "$work/repo/scripts/worktree-done.sh" "$work/repo-pushed"
[ "$status" -ne 0 ] || fail "a worktree whose git status failed was not refused"
nothing_removed "a worktree whose git status failed was refused"
[ -f "$work/repo-pushed/untracked-work.txt" ] || fail "the untracked work beside a broken index is gone"

# A detached HEAD with a commit nothing holds would leave it to the reflog.
setup
(cd "$work/repo-pushed" && git checkout -q --detach && git commit -q --allow-empty -m stray)
run "$work/repo/scripts/worktree-done.sh" "$work/repo-pushed"
[ "$status" -ne 0 ] || fail "a detached HEAD with a commit nothing holds was not refused"
nothing_removed "a detached HEAD was refused"
[ -d "$work/repo-pushed" ] || fail "the detached worktree was removed"

# --- what gets labelled ----------------------------------------------------------------------

# Only the names a checkout derives are labelled with it: one given outright may be shared.
: > "$work/created"
(
    # shellcheck disable=SC2034
    runtime=podman repo_root="$work/repo-pushed" suffix=-repo-pushed
    PATH="$work/bin:$PATH" STUB_DIR="$work/stub" CREATED="$work/created"
    export PATH STUB_DIR CREATED
    # shellcheck source=scripts/checkout-resources.sh
    . "$here/checkout-resources.sh"
    own_volume secretary-records-venv-repo-pushed-new secretary-records-venv-repo-pushed-new
    own_volume secretary-records-venv secretary-records-venv-repo-pushed
    own_volume shared-venv secretary-records-venv-repo-pushed
    suffix=''
    own_volume secretary-records-venv secretary-records-venv
)
grep -q "^volume secretary-records-venv-repo-pushed-new secretary-records.checkout-path=$work/repo-pushed\$" "$work/created" ||
    fail "a name the checkout derives was not created labelled with it: $(cat "$work/created")"
if grep -q '^volume secretary-records-venv \|^volume shared-venv ' "$work/created"; then
    fail "a name given outright, or the primary's, was labelled: $(cat "$work/created")"
fi

if [ "$failures" -gt 0 ]; then
    echo "$failures failure(s)" >&2
    exit 1
fi
echo "worktree cleanup: all cases pass"
