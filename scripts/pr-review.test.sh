#!/bin/sh
# What pr-review.sh's verdict counts, and what it must not.
#
# The verdict is the one line a reader acts on, and every way it has been wrong here has
# been a way of saying "clean" about a branch nothing had read: a review of a superseded
# commit, a round that reported its own failure to start, and - the case this file was
# added for - the author's own reply, which GitHub posts as a review on the head commit.
#
# `gh` is replaced by a stub on PATH rather than mocked at a seam, because the script
# consumes `gh --jq` output directly: the stub answers each call with what gh would have
# printed after filtering. It is matched on the call, so an unrecognised one fails loudly
# rather than returning empty and being read as "nothing there" - which is the exact
# failure this whole script exists to remove.
#
# Run by prek (see .pre-commit-config.yaml), and so by `make check`.

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
script="$here/pr-review.sh"

failures=0
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

HEAD_SHA=1111111111111111111111111111111111111111
OLD_SHA=2222222222222222222222222222222222222222

# The stub reads the case's answers out of $FIXTURE, one file per call it knows about. A
# missing file is an error, not an empty answer.
#
# That contract is only as strong as the caller: a call whose failure the script swallows
# cannot fail loudly however loud the stub is. A read spelled `gh ... 2>/dev/null || echo
# UNKNOWN` turns the stub's exit 90 into an answer, and every case stays green with that
# fixture deleted. `self_check` below is what holds it shut: each fixture is deleted in
# turn, and the suite has to notice.
#
# Each call is also counted, so a case can assert that the script asked twice - which is
# what the lazy merge-state re-ask does, and what no static fixture can show.
mkdir -p "$work/bin"
cat > "$work/bin/gh" <<'STUB'
#!/bin/sh
answer() {
    [ -n "${CALLS:-}" ] && printf '%s\n' "$1" >> "$CALLS"
    # `<name>.seq` answers successive calls a line at a time - how the merge state is
    # UNKNOWN once and CLEAN on the re-ask. Opt-in by its own file rather than inferred
    # from a fixture having several lines, which would take the checks fixture - several
    # lines by nature - and hand back one check per call.
    if [ -f "$FIXTURE/$1.seq" ]; then
        n=$(grep -c '' < "$FIXTURE/$1.seq")
        asked=$(grep -c "^$1$" < "$CALLS")
        [ "$asked" -ge 1 ] || asked=1
        [ "$asked" -le "$n" ] || asked=$n
        sed -n "${asked}p" < "$FIXTURE/$1.seq"
        return
    fi
    if [ ! -f "$FIXTURE/$1" ]; then
        printf 'stub gh: no fixture for %s (call: %s)\n' "$1" "$*" >&2
        exit 90
    fi
    cat "$FIXTURE/$1"
}
case "$*" in
    *'--json author'*) answer author ;;
    *'--json headRefOid'*) answer head ;;
    *'--json statusCheckRollup'*) answer checks ;;
    *'--json mergeStateStatus'*) answer mergestate ;;
    *'--json title'*) answer title ;;
    *'--json state,isDraft'*) answer state ;;
    *'--json owner'*) answer owner ;;
    *'--json name'*) answer name ;;
    *'/pulls/'*'/reviews/'*) answer "body-$(printf '%s' "$*" | sed 's|.*/reviews/||; s| .*||')" ;;
    *'/pulls/'*'/reviews'*) answer reviews ;;
    *'/issues/'*'/comments'*) answer comments ;;
    *'/commits/'*) answer commitdate ;;
    *'api graphql'*) answer threads ;;
    *)
        printf 'stub gh: unrecognised call: %s\n' "$*" >&2
        exit 91
        ;;
esac
STUB
chmod +x "$work/bin/gh"
PATH="$work/bin:$PATH"
export PATH

# Everything a case does not care about, so each case states only what it is about.
new_case() {
    FIXTURE="$work/fixture"
    export FIXTURE
    rm -rf "$FIXTURE"
    mkdir -p "$FIXTURE"
    printf 'sstriker\n' > "$FIXTURE/author"
    printf '%s\n' "$HEAD_SHA" > "$FIXTURE/head"
    printf 'a pull request\n' > "$FIXTURE/title"
    printf 'OPEN\n' > "$FIXTURE/state"
    printf 'apache\n' > "$FIXTURE/owner"
    printf 'tooling-secretary-records\n' > "$FIXTURE/name"
    printf '2026-01-01T00:00:00Z\n' > "$FIXTURE/commitdate"
    : > "$FIXTURE/reviews"
    : > "$FIXTURE/comments"
    : > "$FIXTURE/threads"
    CALLS="$work/calls"
    export CALLS
    : > "$CALLS"
    # Green by default, so a case about reviews says nothing about checks and vice versa.
    check test pass
    printf 'CLEAN\n' > "$FIXTURE/mergestate"
}

# How many times the script asked gh for one thing in the last run.
asked_for() {
    grep -c "^$1$" < "$CALLS" || true
}

# One row of what `gh pr view --json statusCheckRollup --jq` prints: name, state, url.
# `pass` rather than SUCCESS at the call site, because the states that are neither pass nor
# fail are the point and spelling them out keeps the cases readable.
check() {
    case "$2" in
    pass) what=SUCCESS ;;
    fail) what=FAILURE ;;
    running) what=IN_PROGRESS ;;
    skipped) what=SKIPPED ;;
    *) what=$2 ;;
    esac
    printf '%s\t%s\t%s\n' "$1" "$what" "https://example.invalid/$1" >> "$FIXTURE/checks"
}

# Drop the default green check, for a case that is about the checks themselves.
reset_checks() {
    : > "$FIXTURE/checks"
}

# A branch whose workflows never started, with the merge state that explains it. The file
# exists and is empty - which is the case under test, and is not the same as the stub
# refusing a call it does not know.
no_checks() {
    reset_checks
    printf '%s\n' "${1:-CLEAN}" > "$FIXTURE/mergestate"
}

# One row of what `gh api .../reviews --jq` prints: when, who, state, commit, id.
round() {
    printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" >> "$FIXTURE/reviews"
    printf '%s\n' "${6:-a review body}" > "$FIXTURE/body-$5"
}

expect() {
    what=$1
    want_status=$2
    want_text=$3
    status=0
    output=$(sh "$script" 838 2>&1) || status=$?
    if [ "$status" -ne "$want_status" ]; then
        printf 'FAIL %s: exit %s, wanted %s\n%s\n' "$what" "$status" "$want_status" "$output" >&2
        failures=$((failures + 1))
        return
    fi
    if ! printf '%s' "$output" | grep -qF "$want_text"; then
        printf 'FAIL %s: output does not say "%s"\n%s\n' "$what" "$want_text" "$output" >&2
        failures=$((failures + 1))
    fi
}

# What the last run must NOT have said. The verdict is not the only thing a reader acts on:
# a banner over the wrong review misleads whether or not the exit status is right.
refute_last() {
    what=$1
    unwanted=$2
    if printf '%s' "$output" | grep -qF "$unwanted"; then
        printf 'FAIL %s: output says "%s" and should not\n%s\n' "$what" "$unwanted" "$output" >&2
        failures=$((failures + 1))
    fi
}

# The case this file was added for. Copilot's newest round is on a commit that has been
# superseded; the only review on the head is the author's own reply to a thread. Nothing
# has read this branch, and saying so is the whole job.
new_case
round 2026-01-01T10:00:00Z copilot-pull-request-reviewer COMMENTED "$OLD_SHA" 1 'found something'
round 2026-01-01T11:00:00Z sstriker COMMENTED "$HEAD_SHA" 2 'fixed in abc1234'
expect 'an author reply does not count as a review of the head' 1 'not the head'

# The same shape with nothing but the author: not "reviewed", however many replies.
new_case
round 2026-01-01T11:00:00Z sstriker COMMENTED "$HEAD_SHA" 1 'fixed in abc1234'
expect 'the author alone is unreviewed' 1 'every round above is the author'

# What a real clean round looks like, which the change above must not have broken. Shaped
# like Copilot's, and about a change to this script, so it quotes the words a failed round
# opens with: a pattern that matches more than the failure reads it as one.
COPILOT_CLEAN='## Pull request overview

Teaches pr-review.sh to read a review saying "Copilot encountered an error and was unable to review this pull request" as a round that did not run.

Copilot reviewed 2 out of 2 changed files in this pull request and generated no new comments.'
new_case
round 2026-01-01T11:00:00Z copilot-pull-request-reviewer COMMENTED "$HEAD_SHA" 1 "$COPILOT_CLEAN"
expect 'a reviewer on the head with no threads is clean' 0 'newest review is on the head commit'
refute_last 'a reviewer on the head with no threads is clean' 'THIS REVIEW DID NOT RUN'

# A reviewer on the head, and the author replying after them, still clean: the reply is
# ignored rather than treated as the newest round.
new_case
round 2026-01-01T11:00:00Z copilot-pull-request-reviewer COMMENTED "$HEAD_SHA" 1 'looks fine'
round 2026-01-01T12:00:00Z sstriker COMMENTED "$HEAD_SHA" 2 'thanks'
expect 'an author reply after a clean round is still clean' 0 'newest review is on the head commit'

# Nobody at all.
new_case
expect 'no reviews is unreviewed' 1 'unreviewed'

# The round that reported its own failure to start, which predates this change and must
# keep failing.
new_case
round 2026-01-01T11:00:00Z copilot-pull-request-reviewer COMMENTED "$HEAD_SHA" 1 \
    'The job was not started because recent GitHub Actions payments have failed'
expect 'a round that did not run is not a clean round' 1 'did not run'

# Copilot's other way of not running, word for word as it was posted on #682, #808, #880,
# #950, #951 and #953: a review on the head commit, no threads, and nothing read.
new_case
round 2026-01-01T11:00:00Z copilot-pull-request-reviewer COMMENTED "$HEAD_SHA" 1 \
    'Copilot encountered an error and was unable to review this pull request. You can try again by re-requesting a review.'
expect 'a round that errored is not a clean round' 1 'did not run'

# The author answering that round, in the words the reviewer used. Quoting a reviewer is
# how you answer one, and must not be read as a second reviewer failing to start - nor put
# the loud banner over a reply.
new_case
round 2026-01-01T11:00:00Z copilot-pull-request-reviewer COMMENTED "$HEAD_SHA" 1 'looks fine'
round 2026-01-01T12:00:00Z sstriker COMMENTED "$HEAD_SHA" 2 \
    'The job was not started, you said - it has run since, see above.'
expect 'the author quoting a failed round is not one' 0 'newest review is on the head commit'
refute_last 'the author quoting a failed round is not one' 'THIS REVIEW DID NOT RUN'

# Whose reviews these are decides the whole verdict, so not knowing is not an answer. The
# tempting default - nobody is the author - excludes nobody and reinstates exactly the bug
# above, at the moment the API is least reliable.
new_case
rm -f "$FIXTURE/author"
round 2026-01-01T10:00:00Z copilot-pull-request-reviewer COMMENTED "$OLD_SHA" 1 'found something'
round 2026-01-01T11:00:00Z sstriker COMMENTED "$HEAD_SHA" 2 'fixed in abc1234'
expect 'an unreadable author is refused, not defaulted' 2 'cannot tell a review from an answer'

# --- The checks ------------------------------------------------------------
#
# Each of these is a state that looks green to something: an empty list prints nothing, a
# skipped job draws a tick, and a run still going has no failure in it yet.

reviewed() {
    round 2026-01-01T11:00:00Z copilot-pull-request-reviewer COMMENTED "$HEAD_SHA" 1 "$COPILOT_CLEAN"
}

# The case that started this. A branch conflicting with main starts no workflow, so there
# is nothing to print - and a reader who sees no failures concludes there are none.
new_case
reviewed
no_checks DIRTY
expect 'no checks at all is not a pass' 1 'the checks are not clear: 0 of 0'
refute_last 'no checks at all is not a pass' 'all 0 checks passed'

# ... and it says which of the two reasons it is, rather than leaving the reader to guess.
expect 'a conflicting branch says so' 1 'CONFLICTS with the base'

new_case
reviewed
no_checks UNKNOWN
expect 'an uncomputed merge state is admitted, not guessed' 1 'has not finished working out'

# Each of the four below reaches the same verdict, which is the point of there being one:
# what distinguishes them is the state GitHub gave the check, and that is read off the
# listing rather than out of a sentence this script writes about it.

# A failing check, on a branch whose review is answered: the review verdict would have been
# clean, and that is exactly when a red check goes unnoticed.
new_case
reviewed
reset_checks
check lint pass
check 'test (3.12)' fail
expect 'a failing check is not clean' 1 'test (3.12)'
refute_last 'a failing check is not clean' 'nothing is unresolved, and'

# The state a loop asking "is it green yet" spends most of its time in.
new_case
reviewed
reset_checks
check lint pass
check e2e running
expect 'a check still running is not a pass' 1 'IN_PROGRESS'
refute_last 'a check still running is not a pass' 'all 2 checks passed'

# A job whose own `if:` was false reports SKIPPED and the pull request still draws its tick.
# Not a pass, and held to the same rule as the rest: a softer one - exit 0 with a note -
# has this file asserting that a job which did not run is a job that passed, under a
# section that says the opposite.
new_case
reviewed
reset_checks
check lint pass
check corpus skipped
expect 'a skipped job is not a pass' 1 'SKIPPED'
refute_last 'a skipped job is not a pass' 'all 2 checks passed'

# A state this script has never heard of. There is no list of states to fall off the end
# of now - anything that is not SUCCESS is not a pass - so a new one GitHub invents is
# handled by the rule rather than by remembering to add it.
new_case
reviewed
reset_checks
check lint pass
check deploy SOMETHING_NEW
expect 'an unrecognised state is not a pass' 1 'SOMETHING_NEW'
refute_last 'an unrecognised state is not a pass' 'all 2 checks passed'

# Nothing failed, nothing is waiting, and still nothing passed. Indistinguishable from an
# empty list in what it says about the code. Printing `check_count` here where `passed`
# belongs says "all 2 checks passed" two lines under a summary reading "0 of 2 passed".
new_case
reviewed
reset_checks
check a skipped
check b NEUTRAL
expect 'nothing passing is not everything passing' 1 '0 of 2'
refute_last 'nothing passing is not everything passing' 'checks passed'

# A conflict that arrived after the push - main moved - leaves a full set of green checks
# beside a branch that does not merge. With no sentence of its own the state is a bare
# token mid-line, and the verdict reads clean over it.
new_case
reviewed
reset_checks
check lint pass
printf 'DIRTY\n' > "$FIXTURE/mergestate"
expect 'green checks on a branch that does not merge' 1 'CONFLICTS with the base'
refute_last 'green checks on a branch that does not merge' 'nothing is unresolved, and'

# A gh call that failed is not a GitHub that has not decided. Folding the two together
# answered "has not finished working out" over a call that never came back - and hid the
# stub's own failure, which is what `self_check` below holds shut.
new_case
reviewed
no_checks UNREADABLE
expect 'an unreadable merge state says so' 1 'could not be read'
refute_last 'an unreadable merge state says so' 'has not finished working out'

# The lazy computation: UNKNOWN first, the real answer on the re-ask. No static fixture can
# show this - the stub has to answer differently the second time, and the count is what says
# the script asked at all.
new_case
reviewed
reset_checks
check lint pass
printf 'UNKNOWN\nCLEAN\n' > "$FIXTURE/mergestate.seq"
expect 'an unknown merge state is asked again' 0 'merge state: CLEAN'
if [ "$(asked_for mergestate)" -ne 2 ]; then
    printf 'FAIL the re-ask happens: gh was asked for the merge state %s time(s), wanted 2\n' \
        "$(asked_for mergestate)" >&2
    failures=$((failures + 1))
fi

# ... and not on a merged pull request, where it is UNKNOWN for ever and the re-ask buys a
# second gh call and a second of wall clock to be told the same thing.
new_case
printf 'MERGED\n' > "$FIXTURE/state"
reviewed
reset_checks
check lint pass
printf 'UNKNOWN\nCLEAN\n' > "$FIXTURE/mergestate.seq"
expect 'a merged pull request is not asked twice' 0 'merge state: UNKNOWN'
if [ "$(asked_for mergestate)" -ne 1 ]; then
    printf 'FAIL no re-ask when merged: asked %s time(s), wanted 1\n' "$(asked_for mergestate)" >&2
    failures=$((failures + 1))
fi

# What the section is actually for when something is red: the name, the state and the url of
# each check that is not passing. The count alone was asserted, so deleting the whole listing
# left the suite green.
new_case
reviewed
reset_checks
check lint pass
check 'test (3.12)' fail
expect 'a failing check is named, not just counted' 1 'test (3.12)'
refute_last 'a failing check is named, not just counted' 'https://example.invalid/lint'

# A rollup that could not be read is not a rollup with nothing in it. Without the loud
# failure this reads as "NONE RAN", which is a different and confident wrong answer.
new_case
reviewed
rm -f "$FIXTURE/checks"
expect 'an unreadable rollup is refused, not read as empty' 2 'no fixture for checks'

# All green, which the cases above must not have broken.
new_case
reviewed
reset_checks
check lint pass
check e2e pass
expect 'every check passing is clean' 0 'all 2 checks passed'

# The checks do not override the review. An unreviewed branch with a green run is still
# unreviewed, and the first line has to name the thing that is actually in the way.
new_case
reset_checks
check lint pass
expect 'green checks do not make a branch reviewed' 1 'unreviewed'

# --- The jq filter, over real json -------------------------------------------
#
# The stub answers with the TSV the filter produces, so every case above starts *after* it -
# which leaves the part of the section with the most reasoning in it as the one part nothing
# reaches. Reading `.state` on a CheckRun, inverting the `__typename` test and dropping the
# `// []` null guard each break every real pull request while leaving such a suite green.
#
# So the filter is lifted out of the script by text and run over fixture json. jq is in the
# dev container, where prek and CI run this, and absent on the host - so on the host this
# says out loud that it did not run, rather than passing quietly, which is the failure the
# whole file is about.
filter_case() {
    want=$(printf '%s' "$3")
    got=$(printf '%s' "$2" | jq -r "$rollup_filter" 2>&1) || got="jq failed: $got"
    if [ "$got" != "$want" ]; then
        printf 'FAIL the filter, %s:\n  wanted: %s\n  got:    %s\n' "$1" "$want" "$got" >&2
        failures=$((failures + 1))
    fi
}

if command -v jq > /dev/null 2>&1; then
    # Lifted by text rather than duplicated, so the thing under test is the thing that runs.
    rollup_filter=$(sed -n "/^rollup_filter='\$/,/^    | @tsv'\$/p" "$script" | sed "1d; \$s/    | @tsv'/    | @tsv/")
    if [ -z "$rollup_filter" ]; then
        printf 'FAIL the filter could not be lifted out of pr-review.sh - has it been renamed?\n' >&2
        failures=$((failures + 1))
    fi

    filter_case 'a completed CheckRun reads its conclusion' \
        '{"statusCheckRollup":[{"__typename":"CheckRun","name":"e2e","status":"COMPLETED","conclusion":"FAILURE","detailsUrl":"u"}]}' \
        'e2e	FAILURE	u'

    # The one that matters most: a running job has no conclusion yet, and reading `.state`
    # or `.conclusion` here reports every in-flight check as unrecognised.
    filter_case 'a running CheckRun reads its status, not its conclusion' \
        '{"statusCheckRollup":[{"__typename":"CheckRun","name":"e2e","status":"IN_PROGRESS","conclusion":null,"detailsUrl":"u"}]}' \
        'e2e	IN_PROGRESS	u'

    filter_case 'a completed CheckRun with no conclusion is named, not blank' \
        '{"statusCheckRollup":[{"__typename":"CheckRun","name":"e2e","status":"COMPLETED","conclusion":null,"detailsUrl":"u"}]}' \
        'e2e	NO_CONCLUSION	u'

    # The other node type. A StatusContext carries `context` and `state`, and reading a
    # CheckRun's fields off one gives three nulls.
    filter_case 'a StatusContext reads context and state' \
        '{"statusCheckRollup":[{"__typename":"StatusContext","context":"ci/external","state":"EXPECTED","targetUrl":"u"}]}' \
        'ci/external	EXPECTED	u'

    # A pull request with no rollup at all - the null, not the empty list.
    filter_case 'a null rollup is no rows rather than an error' \
        '{"statusCheckRollup":null}' \
        ''

    # A name carrying a tab would shift every field right of it if @tsv did not escape.
    filter_case 'a tab inside a name cannot shift the columns' \
        '{"statusCheckRollup":[{"__typename":"CheckRun","name":"a\tb","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"u"}]}' \
        'a\tb	SUCCESS	u'
else
    printf 'NOTE: jq is absent here, so the rollup filter was NOT exercised.\n' >&2
    printf '      It is covered when this runs in the dev container (make check).\n' >&2
fi

# --- Does the suite itself fail when it cannot look? ------------------------
#
# Everything above rests on the stub failing loudly for a call it cannot answer. That holds
# only if the script lets the failure through: written `gh ... 2>/dev/null || echo UNKNOWN`,
# the merge-state read turned the stub's exit 90 into a confident "GitHub has not finished
# working out whether it merges", and deleting that fixture left the whole suite green.
#
# So each fixture the new section depends on is deleted in turn, and the suite has to
# notice. A check that says "nothing found" when it failed to look is the worst of the
# three, and that goes for this file as much as for what it tests.
self_check() {
    new_case
    reviewed
    rm -f "$FIXTURE/$1"
    status=0
    output=$(sh "$script" 838 2>&1) || status=$?
    if [ "$status" -eq 0 ]; then
        printf 'FAIL a missing %s fixture is swallowed: exited 0 over a gh call that failed\n%s\n' \
            "$1" "$output" >&2
        failures=$((failures + 1))
    fi
}

self_check checks
self_check mergestate
# The newest round's body: read as empty, a round that errored would be a clean one.
self_check body-1

# An older round's body, read only by the sweep for suppressed blocks: dropped, a finding
# that round suppressed would go unsaid.
new_case
round 2026-01-01T10:00:00Z copilot-pull-request-reviewer COMMENTED "$OLD_SHA" 1 'found something'
round 2026-01-01T11:00:00Z copilot-pull-request-reviewer COMMENTED "$HEAD_SHA" 2 "$COPILOT_CLEAN"
rm -f "$FIXTURE/body-1"
expect 'an unreadable older round is refused, not skipped' 2 'cannot say whether it suppressed anything'

if [ "$failures" -ne 0 ]; then
    printf '\n%s pr-review case(s) failed\n' "$failures" >&2
    exit 1
fi

printf 'pr-review.sh: the verdict counts what it says it counts\n'
