#!/bin/sh
# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
# What this branch obliges you to do, read off the files it touches.
#
# AGENTS.md and the files beside the code hold more rules than anybody recalls at the moment one
# applies. Most are conditional on what a change touches, which is a question a diff answers - so it is
# asked here rather than remembered. Run it before the pull request, and again after a rebase that pulled
# in more files.
#
#   scripts/pr-preflight.sh [base]     base defaults to origin/main
#
# The rules that hold in every repository are here. A repository adds its own in
# scripts/local/preflight.toml (the format is in scripts/ci/preflight_rules.py), which this reads through
# that script: python3 3.11 or newer, for that file alone. Without the file there are no others.
#
# It prints and exits 0 unless it could not look at everything: an unknown base, a rules file it cannot
# read, an origin it could not read, work on origin this checkout has not fetched, a branch git refused to
# compare, or a path with a newline in it. Nothing here is a gate: a checklist that could fail a build
# would be worked around within a week. But the last section answers `none`, which is read as nobody else
# being in these files and so as leave to restructure them - so it is printed only when the scan looked at
# every branch, and anything less says what it could not see.

set -eu

base=${1:-origin/main}

# Every path below is repo-root-relative, and after `--` git reads a pathspec relative to
# the cwd - so from `scripts/`, `-- AGENTS.md` means `scripts/AGENTS.md` and matches
# nothing. The contention scan then printed "none" while the collision was real. Same
# question from the other side: `git ls-files --others` lists only what is under the cwd,
# so an untracked file elsewhere in the tree fell out of the rule list too.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
toplevel=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf 'not inside a git repository\n' >&2
    exit 1
}
cd -- "$toplevel"

# The scan reads the refs this checkout happens to hold, and they go stale in hours here -
# so what origin actually holds is asked for. Read, not fetched: `git fetch` would answer
# the same question and rewrite this repository's refs to do it, and `--prune` would delete
# the only local pointer to a branch somebody had force-pushed over, with no reflog entry
# left to recover the tip from. A checklist has no business doing either. What this checkout
# has not fetched is reported instead, with the one command that fixes it.
remote_listing=''
remote_read=no
if remote_listing=$(git ls-remote --heads origin 2>/dev/null); then
    remote_read=yes
fi

if ! merge_base=$(git merge-base "$base" HEAD 2>/dev/null); then
    printf 'cannot find a merge base with %s\n' "$base" >&2
    exit 1
fi

# The paths `git <command> <arguments>` lists, one to a line. Asked for with -z, because the
# readable listing is not the path: git quotes and escapes a path outside printable ASCII -
# a byte above it, a `"`, a backslash, a tab - and the escaped spelling matches no rule
# below and no file as a pathspec. NUL-delimited, git hands over the bytes it has. A line
# cannot hold a newline, so one in a path becomes a \001 byte, which the scan below
# reports rather than turning into a pathspec that matches nothing.
#
# Through a file rather than a pipe, because a pipe answers with tr's status: a listing git
# could not give would read as an empty one, and the script would say "nothing changed".
# The exit leaves the command substitution the call is in, which fails the assignment, and
# set -e stops the script there.
soh=$(printf '\001')
listing=$(mktemp)
rules_facts=$(mktemp)
rules_errors=$(mktemp)
trap 'rm -f "$listing" "$rules_facts" "$rules_errors"' EXIT
paths() {
    _command=$1
    shift
    git "$_command" -z "$@" > "$listing" || exit 1
    tr '\n\0' '\001\n' < "$listing"
}

# --no-renames, because a rename is reported as its destination alone and the question here
# is who else is in the path this branch is moving away from. AGENTS.md wrote the scan for
# "a split, an extraction, a rename across a module", and with rename detection on, the one
# path that matters was never in the list to ask about.
changed=$(paths diff --name-only --no-renames "$merge_base" HEAD; paths diff --name-only --no-renames; paths diff --name-only --no-renames --staged; paths ls-files --others --exclude-standard)
changed=$(printf '%s\n' "$changed" | sort -u | sed '/^$/d')

# What this branch adds, as opposed to what it edits. The rules differ: a licence header
# is owed by a new file and by no other. Rename detection stays on here, so that a file
# this branch only moved is not asked for one.
added=$(paths diff --name-only --diff-filter=A "$merge_base" HEAD; paths diff --name-only --diff-filter=A --staged; paths ls-files --others --exclude-standard)
added=$(printf '%s\n' "$added" | sort -u | sed '/^$/d')

if [ -z "$changed" ]; then
    printf 'nothing changed against %s\n' "$base"
    exit 0
fi

touches() {
    printf '%s\n' "$changed" | grep -qE -- "$1"
}

adds() {
    printf '%s\n' "$added" | grep -qE -- "$1"
}

say() {
    printf '\n  %s\n' "$1"
    shift
    for _line in "$@"; do
        printf '      %s\n' "$_line"
    done
}

# The rules of this repository, read before anything is printed: a file that cannot be used stops the
# run here, and does not leave a checklist that looks complete and is not.
rules_file=${PREFLIGHT_RULES:-scripts/local/preflight.toml}
tab=$(printf '\t')
newline='
'
if [ -f "$rules_file" ]; then
    python=${PREFLIGHT_PYTHON:-python3}
    if ! command -v "$python" > /dev/null 2>&1; then
        printf 'preflight: %s cannot be read: it needs python3, 3.11 or newer, and there is none\n' "$rules_file" >&2
        exit 1
    fi
    if ! "$python" -I "$script_dir/ci/preflight_rules.py" "$rules_file" > "$rules_facts" 2> "$rules_errors"; then
        cat "$rules_errors" >&2
        exit 1
    fi
    # grep reads a pattern it cannot parse as "nothing matches", which is the answer this must never give
    # wrongly: so each pattern is tried once, on nothing.
    while IFS="$tab" read -r _kind _value; do
        case $_kind in
            touches | adds | reads | reads_in)
                _rc=0
                printf '' | grep -E -- "$_value" > /dev/null 2>&1 || _rc=$?
                if [ "$_rc" -gt 1 ]; then
                    printf 'preflight: %s: grep cannot read the pattern %s\n' "$rules_file" "$_value" >&2
                    exit 1
                fi
                ;;
            *) : ;;
        esac
    done < "$rules_facts"
fi

printf 'Against %s, this branch touches %s file(s).\n' "$base" "$(printf '%s\n' "$changed" | wc -l | tr -d ' ')"

# The lines this branch adds in the changed files that match the pattern $1: what is in the diff against the
# merge base, read against the worktree rather than against HEAD - every rule here fires on uncommitted work,
# and a rule that asked for a commit first would be silent exactly while the file is being written - and,
# whole, a file the branch adds that no diff shows yet.
added_lines() {
    printf '%s\n' "$changed" | grep -E -- "$1" | while IFS= read -r _f; do
        case $_f in *"$soh"*) continue ;; *) : ;; esac
        git diff "$merge_base" -- ":(literal)$_f" | sed -n 's/^+//p'
        if printf '%s\n' "$added" | grep -qxF -- "$_f" && [ -f "$_f" ]; then
            cat -- "$_f"
        fi
    done
}

# One rule, once its facts have been read: printed when it applies.
r_title=''
fire_rule() {
    [ -n "$r_title" ] || return 0
    _matched=no
    if [ -n "$r_touches" ] && touches "$r_touches"; then _matched=yes; fi
    if [ -n "$r_adds" ] && adds "$r_adds"; then _matched=yes; fi
    if [ "$_matched" = yes ] && [ -n "$r_reads" ]; then
        added_lines "${r_reads_in:-$r_touches}" | grep -qE -- "$r_reads" || _matched=no
    fi
    if [ "$_matched" = yes ]; then
        printf '\n  %s\n' "$r_title"
        printf '%s\n' "$r_say" | while IFS= read -r _line; do
            printf '      %s\n' "$_line"
        done
    fi
}

if [ -f "$rules_file" ]; then
    while IFS="$tab" read -r _kind _value; do
        case $_kind in
            rule)
                fire_rule
                r_title=$_value r_touches='' r_adds='' r_reads='' r_reads_in='' r_say=''
                ;;
            touches) r_touches=${r_touches:+$r_touches|}$_value ;;
            adds) r_adds=${r_adds:+$r_adds|}$_value ;;
            reads) r_reads=$_value ;;
            reads_in) r_reads_in=${r_reads_in:+$r_reads_in|}$_value ;;
            say) r_say=${r_say:+$r_say$newline}$_value ;;
            *) : ;;
        esac
    done < "$rules_facts"
    fire_rule
fi

if touches '^(pyproject\.toml|uv\.lock|\.python-version|\.tool-versions)$'; then
    say 'dependencies' \
        'uv.lock is the source of truth. Production installs it with uv sync --frozen.'
fi

# Asked of what the branch adds rather than of what it changes: the header is owed by a
# new file and by no other, and a block that printed on every Python branch would stop
# being read on the one where it applied.
if adds '\.py$'; then
    say 'new Python files' \
        'Apache 2.0 licence header, enforced by the insert-license hook.'
fi

# The rules that apply to every branch, and the ones that are easiest to skip.
say 'every branch' \
    'New files are invisible to the hooks until staged - git add them before make check,' \
    'or the first CI run is the first time they are linted.' \
    'Show every new assertion failing against the code as it was, and put what it' \
    'reported in the PR.' \
    'make container-quality-base, then make container-verify. Re-run the base after a rebase.' \
    'The ratchet is argued with, not lowered: a Quality-Allow: trailer in the commit that' \
    'makes the regression, never a smaller MAX_CYCLOMATIC.' \
    '/simplify before the push, then verify again - the pass below reads what was pushed,' \
    'so simplifying after it spends the pass on code you are about to rewrite. Only what' \
    'this branch added; if it touches a new file, run this scan again.' \
    'After the push and before the PR, a fresh subagent attacks the branch - never a fork.'

# Who else is in these files. Asked against each branch merge base rather than against
# main, because `git diff origin/main origin/<branch>` also reports every commit that
# branch is merely behind by.
#
# The file list becomes a pathspec, so it is split on newlines rather than on whitespace:
# a path with a space in it is one pathspec, and split on spaces it becomes two that match
# nothing - which makes the section answer `none`, the one answer it must never give
# wrongly. `set -f` so a `*` or `?` in a name is not expanded against the worktree on
# the way past, and `:(literal)` so git does not expand it either once it has one.
#
# The branch this checkout is on is excluded by its upstream, not by its own name: a local
# branch may be called something else, and on a detached HEAD `--abbrev-ref` answers
# `HEAD`. Either way the name was wrong and this branch reported a collision with itself.
here_branch=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) ||
    here_branch=origin/$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)

saved_ifs=$IFS
IFS='
'
set -f
# shellcheck disable=SC2086  # split on newlines only, which is what IFS above says
set -- $changed
set +f
IFS=$saved_ifs

pathspecs=''
newline_path=no
for _p in "$@"; do
    # A newline in a path came through `paths` as a \001, and the pathspec made of it matches
    # nothing - so the scan must not answer "none" over it. A path holding a \001 of its own
    # is reported the same way, and matches as itself all the same.
    case $_p in
        *"$soh"*) newline_path=yes ;;
        *) : ;;
    esac
    pathspecs="$pathspecs:(literal)$_p
"
done
saved_ifs=$IFS
IFS='
'
set -f
# shellcheck disable=SC2086  # one pathspec per line, as built just above
set -- $pathspecs
set +f
IFS=$saved_ifs

printf '\n  branches that touch the same files\n'
found=no
unreadable=no
for ref in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin); do
    case $ref in
        origin | origin/HEAD | origin/main | "$here_branch") continue ;;
        *) : ;;
    esac
    # Your own pushed work, under whatever name it went up as. Excluding by upstream alone
    # left two ways to collide with yourself: a branch pushed as `mine:feat/other` has no
    # upstream at all, and in a fork layout `@{upstream}` names a remote that is not origin.
    # A ref HEAD already contains is one of those or is merged, and neither can conflict.
    if git merge-base --is-ancestor "$ref" HEAD 2>/dev/null; then
        continue
    fi
    # An unrelated history has no merge base, and dropping the ref there answered "not a
    # collision" about a branch that rewrites the same file. Name it instead - the reader
    # can compare it by hand, which is what this section is for. Not a failure: one orphan
    # branch on origin would otherwise make every run here exit non-zero for good.
    if ! ref_base=$(git merge-base "$base" "$ref" 2>/dev/null); then
        printf '      %s (no merge base with %s - compare it by hand)\n' "$ref" "$base"
        found=yes
        continue
    fi
    # Not `2>/dev/null`: git failing here - too many pathspecs for one argv, most likely -
    # printed nothing, which is indistinguishable from finding nothing.
    if ! overlap=$(git diff --name-only "$ref_base" "$ref" -- "$@" 2>&1); then
        printf '      %s (could not be compared: %s)\n' "$ref" "$(printf '%s' "$overlap" | head -1)"
        unreadable=yes
        continue
    fi
    if printf '%s' "$overlap" | grep -q .; then
        printf '      %s\n' "$ref"
        found=yes
    fi
done

# Branches this checkout has never fetched are branches the loop above could not look at,
# and saying "none" over them is the failure this whole section is about. Counted from what
# origin answered rather than from what a fetch wrote here.
if [ "$remote_read" = no ]; then
    printf '      (origin could not be read, so this is only what was already fetched)\n'
    unreadable=yes
else
    unseen=$(
        printf '%s\n' "$remote_listing" | while read -r _sha _ref; do
            [ -n "$_sha" ] || continue
            _name=${_ref#refs/heads/}
            _have=$(git rev-parse --verify --quiet "refs/remotes/origin/$_name") || _have=''
            [ "$_have" = "$_sha" ] || printf 'x\n'
        done | wc -l | tr -d ' '
    )
    if [ "$unseen" -gt 0 ]; then
        printf '      (%s branch(es) on origin are not in this checkout, so this list\n' "$unseen"
        printf '       cannot see them - git fetch, then run this again)\n'
        unreadable=yes
    fi
fi

if [ "$newline_path" = yes ]; then
    printf '      (a changed path holds a newline, which a list of paths one to a line\n'
    printf '       cannot hold - this list cannot see that file)\n'
    unreadable=yes
fi

if [ "$found" = no ] && [ "$unreadable" = no ]; then
    printf '      none\n'
else
    printf '\n      They say who to talk to, not whether you may merge.\n'
    printf '      git merge-tree --write-tree %s HEAD answers that, when you ask it.\n' "$base"
fi

printf '\n'

# A scan that could not look at every branch has not answered the question, and the whole
# point of the section is that "none" is trustworthy.
if [ "$unreadable" = yes ]; then
    printf 'the branch list above is incomplete - see the notes in it\n' >&2
    exit 1
fi
