#!/bin/sh
# Read a pull request's review in full - including the part the headline hides.
#
# A Copilot review that says "generated no new comments" has often found something and
# collapsed it into a `Suppressed comments (N)` block. Measured over #729, #730 and #731:
# thirteen rounds, eight of which found something, and three of those eight put every
# finding in that block under a headline saying there were none.
#
# A review is also tied to the commit it ran against and does not carry forward, so a
# review of an earlier push says nothing about the branch as it stands now.
#
# And a review does not have to be a review. A comment on the pull request itself is not
# one, opens no thread, and is not among the pull request's review comments - so a script
# that reads reviews can report "did not run" and "0 unresolved", correctly, over a
# timeline full of findings. That happened on #751-#753, which is why the conversation is
# swept too.
#
# Reading all of that by hand takes four `gh api` calls with a `--jq` filter on each, and
# a filter that matches nothing prints nothing - which looks exactly like a clean review.
# That is the failure this script exists to remove: every section says how many it found,
# and says "none" in words when it found none.
#
# Usage:
#   scripts/pr-review.sh <pr-number>
#
# And answering a review is not being reviewed. A reply to a review thread is posted as a
# review, by whoever replied - so on #838 the author's own reply sat on the head commit
# above a reviewer's newest round on a superseded one, and the verdict called that clean.
# The rounds below print every review; only the ones the author did not write count
# towards the verdict.
#
# The checks are read here too, for the same reason the suppressed blocks are: `gh pr
# checks` prints a table and prints nothing when there is nothing to print, and an empty
# print is what a branch that conflicts with main looks like - no workflow started, no
# output, no tick either way. Counted and said in words below, where "0 checks" cannot be
# read as "0 failures".
#
# Exit status:
#   0  a review by somebody other than the author is on the branch head, no thread is
#      unresolved, and every check on that commit has finished and passed
#   1  there is something to address: a finding, an unresolved thread, a review that
#      predates the head commit, or a check that failed, has not finished, or never ran
#   2  the script could not answer - bad usage, no gh, or the API refused
#
# Conversation comments are printed and counted but never change the status: nothing on a
# comment records whether it was answered, and a status that guessed would be wrong often
# enough to teach its reader to ignore it.
#
# Prints to stdout, so it is safe to page. Requires `gh` (authenticated); it does not
# need jq, which is not installed on the host - `gh api --jq` carries its own.

set -eu

pr=${1:-}
if [ -z "$pr" ] || [ -n "${pr#"${pr%%[!0-9]*}"}" ]; then
    echo "usage: scripts/pr-review.sh <pr-number>" >&2
    exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "gh is not installed, and this reads the review through it" >&2
    exit 2
fi

fail() {
    echo "pr-review: $1" >&2
    exit 2
}

# A reviewer that could not run still leaves a review, and its body is the reason it
# could not rather than anything about the code. Found by running this against its own
# pull request while the Actions budget was spent: Copilot posted "The job was not
# started because recent GitHub Actions payments have failed", on the head commit, with
# no threads - which this script read as a clean round, which is the very thing it exists
# to stop. A round that did not happen is not a round that found nothing. Copilot's own
# failure is a body that opens "Copilot encountered an error" (#682, #808, #880, #950,
# #951, #953), anchored because a real review of a change like this one quotes it.
did_not_run() {
    printf '%s' "$1" |
        grep -qiE 'job was not started|spending limit|payments have failed|workflow run failed|could not be completed|^Copilot encountered an error and was unable to review'
}

head_sha=$(gh pr view "$pr" --json headRefOid --jq '.headRefOid') ||
    fail "could not read #$pr - does it exist, and is gh logged in?"
[ -n "$head_sha" ] || fail "#$pr reported no head commit"

# Who opened it, so the verdict can tell a review of this branch from an answer to one.
# Replying to a review thread posts a review, under the account replying - so a session
# that answers a finding and then asks whether the branch has been reviewed is told yes,
# by its own reply, while the reviewer's newest round still sits on a superseded commit.
# Seen on #838: `sstriker COMMENTED 4215da67` above `copilot COMMENTED 05b1992c`, verdict
# clean, nothing having read the head.
#
# Refused rather than defaulted, because there is no safe default: an empty author excludes
# nobody, which is the behaviour this exists to remove, and reinstating it exactly when the
# API is having a bad minute is the worst moment for it. A script that cannot tell whose
# reviews these are cannot answer the question, and says so.
author=$(gh pr view "$pr" --json author --jq '.author.login') ||
    fail "could not tell who opened #$pr, so cannot tell a review from an answer to one"
[ -n "$author" ] || fail "#$pr reports no author, so cannot tell a review from an answer to one"

title=$(gh pr view "$pr" --json title --jq '.title') || title='(unknown)'
state=$(gh pr view "$pr" --json state,isDraft --jq '"\(.state)\(if .isDraft then " (draft)" else "" end)"') ||
    state='(unknown)'

printf '#%s %s\n' "$pr" "$title"
printf 'state: %s\nhead:  %s\n\n' "$state" "$head_sha"

# --- Rounds -----------------------------------------------------------------
#
# Every review, oldest first, so that "the last two said nothing and the third found a
# bug" is visible rather than inferred. It happened on #729: three reviews of 53f5076f,
# the first two clean, the third holding the defect the branch shipped to fix.

rounds=$(gh api --paginate "repos/:owner/:repo/pulls/$pr/reviews" \
    --jq '.[] | [.submitted_at, .user.login, .state, .commit_id, .id] | @tsv') ||
    fail "could not list the reviews on #$pr"

if [ -z "$rounds" ]; then
    printf 'ROUNDS: none. Nobody has reviewed this yet.\n\n'
else
    printf 'ROUNDS (oldest first)\n'
    printf '%s\n' "$rounds" | while IFS='	' read -r when who what commit _id; do
        mark='  '
        [ "$commit" = "$head_sha" ] && mark='* '
        note=''
        [ -n "$author" ] && [ "$who" = "$author" ] && note=' (the author - answering a review, not one)'
        printf '%s%s  %-34s %-9s %s%s\n' "$mark" "$when" "$who" "$what" "$(printf '%.8s' "$commit")" "$note"
    done
    printf '\n(* is on the branch head; anything else reviewed code that has since moved)\n\n'
fi

# --- The latest review from each reviewer, in full --------------------------
#
# The whole body, minus the per-file summary table, which is long and says nothing a diff
# does not. The suppressed block is inside the body and is therefore included: printing
# the body in full is the point of this script.

latest=$(printf '%s\n' "$rounds" | awk -F'\t' 'NF { seen[$2] = $5 } END { for (who in seen) print seen[who] "\t" who }')

# Set by the loop below, and read by the verdict: a round that reports its own failure to
# start says nothing about the code, so it must not be counted as one that found nothing.
stillborn=''

if [ -n "$latest" ]; then
    # Fed by a heredoc rather than a pipe, so that `stillborn` set in here survives: a
    # pipe would run the loop in a subshell and the verdict would never see it.
    while IFS='	' read -r id who; do
        [ -n "$id" ] || continue
        # Refused rather than read as empty: an empty body is a clean round below, so a read
        # that failed would make a round that did not run look like one that found nothing.
        body=$(gh api "repos/:owner/:repo/pulls/$pr/reviews/$id" --jq '.body') ||
            fail "could not read review $id on #$pr"
        printf -- '--- latest review from %s ------------------------------------------\n' "$who"
        if [ -z "$body" ]; then
            printf '(this review has no body - it may be an approval with no text)\n\n'
            continue
        fi
        # A reply quoting "the job was not started" is describing a round that did not run,
        # not being one - so neither the banner nor the verdict is its to set. Answering a
        # reviewer in their own words is the ordinary way to answer them.
        if [ "$who" != "$author" ] && did_not_run "$body"; then
            printf '>> THIS REVIEW DID NOT RUN. Its body is the reason it could not:\n\n'
            printf '%s\n\n' "$body"
            printf '>> This round read nothing, whatever an earlier one did. Ask again once\n>> whatever stopped it is fixed.\n\n'
            stillborn="$stillborn $who"
            continue
        fi
        printf '%s\n' "$body" | awk '
            /<summary>Show a summary per file<\/summary>/ { skipping = 1 }
            skipping && /<\/details>/ { skipping = 0; next }
            !skipping { print }
        '
        printf '\n'
        # Said outright rather than left to be noticed: an absent block is a fact worth
        # reading, and its absence in the output could otherwise be this script failing.
        if printf '%s' "$body" | grep -q 'Suppressed comments'; then
            printf '>> This review has a Suppressed comments block. It is printed above.\n\n'
        else
            printf '>> No suppressed block in this review.\n\n'
        fi
    done <<EOF
$latest
EOF
fi

# --- Every suppressed block, not only the newest one ------------------------
#
# A suppressed finding lives in the body of the round that raised it and nowhere else: it
# opens no thread, and it is not among the pull request's review comments. Checked on
# #731, where the block named `BAT_PLAYWRIGHT_IMAGE` and no inline comment did.
#
# So a round that reports one, followed by a clean round, leaves the finding visible only
# in a body nobody has any reason to open again. Every round is swept here for that
# reason, and each block says which round it came from.

printf -- '--- suppressed blocks, every round -------------------------------------\n'
found_suppressed=0
if [ -n "$rounds" ]; then
    while IFS='	' read -r when _who _what commit id; do
        [ -n "$id" ] || continue
        body=$(gh api "repos/:owner/:repo/pulls/$pr/reviews/$id" --jq '.body') ||
            fail "could not read review $id on #$pr, so cannot say whether it suppressed anything"
        printf '%s' "$body" | grep -q 'Suppressed comments' || continue
        found_suppressed=1
        printf '\n[%s on %s]\n' "$when" "$(printf '%.8s' "$commit")"
        printf '%s\n' "$body" | awk '
            /<summary>Suppressed comments/ { inside = 1 }
            inside { print }
            inside && /<\/details>/ { inside = 0 }
        '
    done <<EOF
$rounds
EOF
fi
# The subshell a pipe would create cannot set this, which is why the loop above is fed by
# a heredoc: the flag has to survive it.
if [ "$found_suppressed" -eq 0 ]; then
    printf 'None, in any round.\n'
fi
printf '\nThese open no threads and are not review comments, so nothing below tracks\nwhether they were answered. That is yours to do.\n\n'

# --- The conversation, which is not a review --------------------------------
#
# A comment on the pull request itself is not a review, opens no thread, and is not among
# the pull request's review comments - so nothing above sees it. It is also where a review
# written by a person rather than by Copilot usually lands.
#
# On #751, #752 and #753 it was where every real finding was. The sections above reported
# "THIS REVIEW DID NOT RUN" and "unresolved threads: 0", both correctly, while three
# comments carrying eight findings between them sat in the timeline unmentioned - so the
# script answered exactly the question it was asked and still sent its reader away with
# the wrong idea. That is the same failure as a `--jq` filter matching nothing.
#
# Printed whole, oldest first, for the reason the rest of this exists: a count of findings
# is not the findings.

comments=$(gh api --paginate "repos/:owner/:repo/issues/$pr/comments" \
    --jq '.[] | [.created_at, .user.login, .id] | @tsv') ||
    fail "could not read the comments on #$pr"

comment_count=0
if [ -n "$comments" ]; then
    comment_count=$(printf '%s\n' "$comments" | grep -c '' || true)
fi

# When the head commit was made, so each comment can say whether it arrived after the code
# it is about. Not a verdict - a comment is as easily answered by a reply as by a push, and
# a reply is only another comment - but of the things knowable from here it is the useful
# one. Left empty if the commit cannot be read, and then simply not said.
head_date=$(gh api "repos/:owner/:repo/commits/$head_sha" --jq '.commit.committer.date') || head_date=''

printf -- '--- conversation comments: %s ------------------------------------------\n' "$comment_count"
if [ "$comment_count" -eq 0 ]; then
    printf 'None. Nothing has been said here outside the reviews.\n'
else
    while IFS='	' read -r when who id; do
        [ -n "$id" ] || continue
        stamp="$when  $who"
        if [ -n "$head_date" ]; then
            stamp="$stamp$(awk -v a="$when" -v b="$head_date" \
                'BEGIN { print (a > b) ? "  (after the head commit)" : "  (predates the head commit)" }')"
        fi
        body=$(gh api "repos/:owner/:repo/issues/comments/$id" --jq '.body') || body='(could not be read)'
        printf '\n[%s]\n%s\n' "$stamp" "$body"
    done <<EOF
$comments
EOF
fi
printf '\nA comment resolves nothing and a reply is only another comment, so nothing\nhere tracks whether one was answered. That is yours to do.\n\n'

# --- Threads still open -----------------------------------------------------
#
# What is actually left to answer. `isOutdated` means the code it points at has moved,
# which usually means it was addressed and nobody pressed resolve - but not always, so
# they are listed rather than filtered out.

# The owner and name are asked for rather than written in: every other call here goes
# through `:owner/:repo`, which gh fills in from the checkout, and a graphql query is the
# one place that templating does not reach. Written in, the script reads a fork's pull
# request numbers against apache/tooling-secretary-records - answering about somebody else's #731.
owner=$(gh repo view --json owner --jq '.owner.login') || fail "could not tell which repository this is"
name=$(gh repo view --json name --jq '.name') || fail "could not tell which repository this is"

# `$pr` in the query is GraphQL's own variable, bound by -F, so the single quotes are
# deliberate - the shell must not touch it. `$owner` and `$name` are bound the same way,
# and for the same reason: a name is not something to splice into a query by hand.
# shellcheck disable=SC2016
threads=$(gh api graphql -F pr="$pr" -F owner="$owner" -F name="$name" -f query='
    query($pr: Int!, $owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $pr) {
          reviewThreads(first: 100) {
            nodes {
              isResolved
              isOutdated
              path
              line
              comments(first: 1) { nodes { author { login } body } }
            }
          }
        }
      }
    }' --jq '
    .data.repository.pullRequest.reviewThreads.nodes[]
    | select(.isResolved == false)
    | "\(.path):\(.line // "?")\t\(if .isOutdated then "outdated" else "current " end)\t\(.comments.nodes[0].author.login)\t\(.comments.nodes[0].body | gsub("\n"; " ") | .[0:160])"
    ') || fail "could not read the review threads on #$pr"

open_count=0
if [ -n "$threads" ]; then
    open_count=$(printf '%s\n' "$threads" | grep -c '' || true)
fi

printf -- '--- unresolved threads: %s ---------------------------------------------\n' "$open_count"
if [ "$open_count" -eq 0 ]; then
    printf 'None. Every thread on this pull request is resolved.\n\n'
else
    printf '%s\n' "$threads" | while IFS='	' read -r where age who what; do
        printf '  %s  [%s]  %s\n      %s\n' "$where" "$age" "$who" "$what"
    done
    printf '\n'
fi

# --- Checks on the head commit ----------------------------------------------
#
# A review is not the only thing that can be misread into a clean verdict, and CI is read
# by hand more often than reviews are. `gh pr checks` prints a table and says nothing when
# there is nothing to print - and on gh 2.45, which is what the host carries, it has no
# `--json`, so a filter written against the newer flag fails on every call. Swallowed by a
# retry loop that is right to tolerate a flaky API, that is thirty minutes of "nothing has
# settled yet" over a command that never ran.
#
# Only SUCCESS is a pass. Three things that are not, and each looks green to something:
# **no checks at all** - usually a branch that conflicts with main, which stops the
# workflows before they start, and once a spent Actions budget; **a run still going**,
# which is what a script asking "is it green yet" mostly sees; and **a job that was
# skipped**, where its own `if:` was false and the pull request still draws a tick.
#
# What this cannot see, and does not claim to: a *workflow* filtered out by `paths:` leaves
# no entry in the rollup at all. In BAT, #916 touched only AGENTS.md and has seven entries
# with no `corpus` among them; #917 has eight, with it. So a branch whose change a workflow's
# `paths:` list fails to name reads here as every check passing. Catching it would mean
# knowing which jobs this branch ought to have run, which is a different question from
# reading what did; `scripts/pr-preflight.sh` is where that one belongs.
#
# The rollup is the head commit's by construction - it hangs off the pull request's own
# head - so unlike a review it cannot be about code that has since moved.

# Held in a variable rather than written inline so that `pr-review.test.sh` can lift it out
# and run it over fixture json. The stub standing in for `gh` answers with the TSV this
# produces, so inline it is the one part of the section no case reaches - the part with the
# most reasoning in it. Single-quoted: every `$` and `.` here is jq's, not the shell's.
# shellcheck disable=SC2016
rollup_filter='
    .statusCheckRollup // [] | .[]
    | if .__typename == "CheckRun" then
          [(.name // "?"),
           (if .status != "COMPLETED" then (.status // "?") else (.conclusion // "NO_CONCLUSION") end),
           (.detailsUrl // "")]
      else
          [(.context // .name // "?"), (.state // "?"), (.targetUrl // "")]
      end
    | @tsv'

checks=$(gh pr view "$pr" --json statusCheckRollup --jq "$rollup_filter") ||
    fail "could not read the checks on #$pr"

# Two counts, and no vocabulary of this script's own. `SUCCESS` is the only state that is a
# pass, and the rest are printed as GitHub spells them, which keeps them right as GitHub
# adds to them.
#
# Sorting the rest into failing / waiting / skipped is the tempting shape and a worse one.
# The listing below already shows each state, so buckets buy wording; and a list of states
# to match on is a list to keep in step with GitHub, where whatever it fails to name falls
# through to whichever answer the code reaches last - "all N checks passed", here.
count_rows() {
    [ -n "$1" ] || { echo 0; return; }
    printf '%s\n' "$1" | grep -c '' || true
}

check_count=$(count_rows "$checks")
passed=$(count_rows "$(printf '%s\n' "$checks" | awk -F'\t' 'NF && $2 == "SUCCESS"')")

# Lazily computed by GitHub: the first request returns UNKNOWN and *starts* the calculation,
# and the answer is there a moment later. Hence the second ask, and the pause before it -
# two calls back to back both answer UNKNOWN.
#
# A call that *failed* is UNREADABLE, never UNKNOWN. Spelling this `|| echo UNKNOWN` folds
# the two together, which answers "GitHub has not finished working out whether it merges"
# over a gh that exited non-zero - the same sentence whether the question was asked or not.
# It also puts the failure past the suite, which cannot fail loudly on a call whose failure
# the script has already swallowed.
read_merge_state() {
    if answer=$(gh pr view "$pr" --json mergeStateStatus --jq '.mergeStateStatus' 2>/dev/null) &&
        [ -n "$answer" ]; then
        printf '%s' "$answer"
    else
        printf 'UNREADABLE'
    fi
}

merge_state=$(read_merge_state)
# Only an open pull request has a merge state to compute; a merged or closed one answers
# UNKNOWN for ever, so re-asking buys a second gh call and a second of wall clock to be
# told the same thing.
if [ "$merge_state" = "UNKNOWN" ] && [ "${state#*MERGED}" = "$state" ] && [ "${state#*CLOSED}" = "$state" ]; then
    sleep 1
    merge_state=$(read_merge_state)
fi

# Said whatever the checks say, rather than only when there are none. A conflict that
# arrives *after* the push - main moved, which is the common way - leaves a full set of
# green checks beside a branch that does not merge, and the state was printed as a bare
# token mid-sentence with no words anywhere near it.
# Only the three states that change what the reader does next. BLOCKED and UNSTABLE each
# meant "read the list above", which the list above already is, and CLEAN says nothing a
# healthy pull request needs told. The state itself is printed either way.
say_merge_state() {
    case "$merge_state" in
    DIRTY) printf 'The branch CONFLICTS with the base. Nothing merges, and a push that\nresolves it is what starts the checks again.\n' ;;
    UNKNOWN) printf 'GitHub has not finished working out whether it merges; ask again shortly.\n' ;;
    UNREADABLE) printf 'The merge state could not be read - the gh call for it failed. This says\nnothing about whether the branch merges.\n' ;;
    # Spelled out rather than left off, so the silence is a decision a reader can see, and
    # because shellcheck asks for it (SC2249).
    *) ;;
    esac
}

printf -- '--- checks on the head commit: %s -------------------------------------\n' "$check_count"
if [ "$check_count" -eq 0 ]; then
    printf 'NONE RAN. This is not a pass - nothing has been run against this code.\n'
    printf 'Merge state is %s.\n' "$merge_state"
    say_merge_state
    printf '\n'
else
    printf '%s of %s passed.  merge state: %s\n' "$passed" "$check_count" "$merge_state"
    say_merge_state
    if [ "$passed" -ne "$check_count" ]; then
        printf '\n'
        printf '%s\n' "$checks" | awk -F'\t' 'NF && $2 != "SUCCESS"' | while IFS='	' read -r name what url; do
            printf '  %-28s %-16s %s\n' "$name" "$what" "$url"
        done
    fi
    printf '\n'
fi

# --- What that adds up to ---------------------------------------------------

# Everything below asks "has anybody read this branch", and the author's own reviews
# cannot answer it: a reply to a thread is posted as a review, so answering a finding
# would otherwise mark the head as reviewed by the act of answering. They are printed
# above like any other round - what they are not is evidence.
reviews_by_others=$(printf '%s\n' "$rounds" | awk -F'\t' -v me="$author" 'NF && (me == "" || $2 != me)')

newest_commit=$(printf '%s\n' "$reviews_by_others" | awk -F'\t' 'NF { last = $4 } END { print last }')
stale=0
if [ -n "$reviews_by_others" ] && [ "$newest_commit" != "$head_sha" ]; then
    stale=1
fi

# Said before the verdict rather than folded into it, and deliberately not counted in the
# exit status. There is no answered flag on a comment to read, and a status that guessed
# would be wrong often enough to teach its reader to ignore it - which is worse than one
# that does not claim to know. What it can do is refuse to let the last line be read alone.
if [ "$comment_count" -ne 0 ]; then
    printf 'Note: %s comment(s) in the conversation above. No verdict below covers them.\n\n' "$comment_count"
fi

if [ -z "$reviews_by_others" ]; then
    if [ -n "$rounds" ]; then
        printf 'VERDICT: unreviewed - every round above is the author. Ask for one.\n'
    else
        printf 'VERDICT: unreviewed. Ask for one before reading anything into this.\n'
    fi
    exit 1
fi
if [ -n "$stillborn" ]; then
    printf 'VERDICT: the newest review from%s did not run - its body says why. A round\n' "$stillborn"
    printf 'that did not happen is not a round that found nothing.\n'
    exit 1
fi
if [ "$stale" -eq 1 ]; then
    printf 'VERDICT: the newest review ran against %s, not the head. Ask for another.\n' \
        "$(printf '%.8s' "$newest_commit")"
    exit 1
fi
if [ "$open_count" -ne 0 ]; then
    printf 'VERDICT: %s thread(s) still open on the head commit.\n' "$open_count"
    exit 1
fi

# After the review verdicts rather than before them, because a reviewed branch with a red
# check and an unreviewed branch with a green one are different problems and the first line
# should name the one that is actually in the way.
#
# One condition rather than one per state. What the status carries is whether anything is
# outstanding; which thing it is, the section above has already printed. A verdict apiece
# for no checks, a failure, a run still going, nothing having passed and a conflict is that
# listing restated in prose, and five more places for the two to disagree.
if [ "$passed" -ne "$check_count" ] || [ "$check_count" -eq 0 ] || [ "$merge_state" = "DIRTY" ] ||
    [ "$merge_state" = "UNREADABLE" ]; then
    printf 'VERDICT: the review is answered, and the checks are not clear: %s of %s\n' "$passed" "$check_count"
    printf 'passed, merge state %s. The section above says which and why. An empty\n' "$merge_state"
    printf 'list, a run still going and a job that was skipped are each not a pass.\n'
    exit 1
fi

printf 'VERDICT: the newest review is on the head commit, nothing is unresolved, and\n'
printf 'all %s checks passed.\n' "$check_count"
printf 'That is one clean round, which is not the same as being right - #729 had two\n'
printf 'before the third found a real defect in code neither had changed.\n'
exit 0
