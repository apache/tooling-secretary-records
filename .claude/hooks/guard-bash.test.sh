#!/bin/sh
# What guard-bash.sh refuses, and what it must go on allowing.
#
# The second half is the half that matters. A guard that refuses too much is worse than
# no guard at all: it is discovered mid-task, worked around, and then switched off. Every
# allowed case below is a command this repository's own instructions ask for.
#
# Run by prek (see .pre-commit-config.yaml), and so by `make check`, which means it runs
# inside the dev container - where the guard would otherwise stand itself down. Hence
# SR_GUARD_FORCE.

# shellcheck disable=SC2016  # every case is a command line, handed to the guard as its literal text

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
guard="$here/guard-bash.sh"

SR_GUARD_FORCE=1
export SR_GUARD_FORCE

failures=0

# A JSON string, the way Claude Code would have written it: backslashes and quotes
# escaped, and a real newline sent as \n - which is the form the guard has to read a
# multi-line command out of.
as_json_string() {
    printf '%s' "$1" |
        sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' |
        awk 'NR > 1 { printf "\\n" } { printf "%s", $0 }'
}

payload_for() {
    printf '{"session_id":"t","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"%s","description":"%s"}}' \
        "$(as_json_string "$1")" "$(as_json_string "${2:-a command}")"
}

run_guard() {
    status=0
    output=$(payload_for "$1" "${2:-}" | sh "$guard" 2>&1) || status=$?
}

allow() {
    run_guard "$@"
    if [ "$status" -ne 0 ]; then
        printf 'refused a command it should allow: %s\n  %s\n' "$1" "$output" >&2
        failures=$((failures + 1))
    fi
}

refuse() {
    run_guard "$@"
    if [ "$status" -ne 2 ]; then
        printf 'allowed a command it should refuse: %s\n' "$1" >&2
        failures=$((failures + 1))
    fi
}

# The toolchain, on the host
refuse 'uv run pytest'
refuse 'pytest tests/api -q'
refuse 'cd assets && yarn test'
refuse 'npm install'
refuse 'python3 -c "import app"'
refuse 'echo starting; pytest'
refuse 'cd assets
yarn test'
refuse 'sudo npm install -g corepack'
refuse 'NODE_ENV=test yarn build'
refuse '/usr/local/bin/uv sync'
refuse 'make test'
refuse 'make check'
refuse 'make lint'
refuse 'make e2e-test-host'
refuse 'make'

# A wrapper is not a way around any of this. Everything above can be written as a string
# handed to a shell, and a guard that reads only the first word of a part would take
# `sh -c` as the whole of what is being run.
refuse "sh -c 'pytest tests/api'"
refuse 'bash -c "uv run pytest"'
refuse "sh -c 'git add -A'"
refuse "sh -c \"sh -c 'yarn build'\""
refuse '/bin/sh -c "make test"'
refuse 'timeout 600 make test'
refuse 'timeout --signal=KILL 600 pytest'
allow "sh -c 'ls e2e/screenshots'"
allow 'timeout 600 scripts/container.sh make test'

# The toolchain, in the container, which is the whole point of the rule
allow 'scripts/container.sh make test'
allow "scripts/container.sh sh -c 'uv run pytest tests/api -q'"
allow 'SR_PYTHON=3.14 scripts/container.sh make test'
allow 'scripts/e2e-container.sh presentation'
# The form AGENTS.md asks for, and the one the guard's own refusal message suggests: the
# separator belongs to the string the shell is handed, not to the line the guard is reading.
allow "scripts/container.sh sh -c 'cd assets && yarn test'"
allow 'scripts/container.sh sh -c "cd assets && yarn run svelte-check"'
allow "scripts/container.sh sh -c 'cd assets; yarn format'"
allow "timeout 600 scripts/container.sh sh -c 'cd assets && yarn test'"
# and a real separator outside the quotes still splits, so this is still the host
refuse "scripts/container.sh make test && yarn test"
# `make e2e-quick FILTER=...` is a container wrapper too - AGENTS.md lists it beside e2e-test
allow 'make e2e-quick FILTER=presentation'
allow 'make e2e-quick'

# A make wrapper takes its target and NAME=value, and nothing else: each of these ran its payload on
# the host when the rule was a list of things to refuse.
# a second target is the host's own
refuse 'make container-check test'
refuse 'make e2e-quick e2e-test-host'
# a flag, in any spelling make accepts
refuse "make container-check --eval='X:=1'"
refuse "make container-check --ev='X:=1'"
refuse 'make container-check -nf other.mk'
refuse 'make container-check -fother.mk'
refuse 'make container-check --file=other.mk'
refuse 'make container-check -C /tmp/planted'
refuse 'make container-check -I /tmp/planted'
# a value make itself reads, or one assigned another way
refuse 'make container-check MAKEFILES=/tmp/planted.mk'
refuse 'make container-check MAKEFLAGS=--eval=x'
refuse 'make container-check SHELL=/tmp/x'
refuse 'make container-check SHELL:=/tmp/x'
refuse "make container-check X!='touch f'"
# a value that reaches past how its recipe uses it - ARGS is in double quotes there, the others bare
refuse "make container-pytest ARGS='\"; uv run pytest; echo \"'"
refuse "make container-pytest ARGS='\$(touch x)'"
refuse "make container-pytest ARGS='\${shell touch x}'"
refuse "make container-pytest ARGS='\`touch x\`'"
refuse "make e2e-quick FILTER='home; uv run pytest'"
refuse "make e2e-quick FILTER='home & uv run pytest'"
refuse "make e2e-quick FILTER='\${shell touch x}'"
refuse "make container-keep KEEP_PORT='18081:8080 uv run pytest'"
refuse "make container-keep KEEP_NAME='x;touch f'"
refuse "make container-keep KEEP_CMD='uv run pytest; sh'"
refuse "make container-keep KEEP_CMD='sh -c \"\$(touch x)\"'"
# the same values in front of make, which make reads as its own
refuse "FILTER='x;touch f' make e2e-quick"
# a quoted value with a space in it is one word, and what runs is past it
refuse "X='a b' uv run pytest"
refuse "FILTER='a b' make test"
refuse 'env MAKEFLAGS=--eval=x make container-check'
# make reached another way
refuse "nice make container-check --eval='X:=1'"
refuse "\"make\" container-check --eval='X:=1'"
# and what the docs give still passes: plain values, quoted, and the Makefile's own KEEP_CMD shape
allow "make container-pytest ARGS='tests/test_access.py -k rename -q'"
allow 'make container-pytest ARGS=tests/test_access.py'
allow "make container-test ARGS='-q | tee out'"
allow "make container-keep KEEP_PORT=127.0.0.1:18080:8080"
allow "make container-keep KEEP_CMD='sh -c \"cd assets && yarn dev\"'"
allow 'make container-keep KEEP_CMD=scripts/run-preview.sh'
allow 'SR_KEEP=1 make e2e-quick FILTER=presentation'
# SR_RUNTIME is run as a program, and only the SR_* names the scripts read are values at all
refuse 'make container-check SR_RUNTIME=/tmp/planted'
refuse 'SR_RUNTIME=./x make container-check'
refuse "make container-check 'SR_X\$(touch f)=1'"
refuse 'make container-check SR_X!=id'
refuse 'make container-check SR_KEEP:=1'
allow 'make container-test SR_RUNTIME=docker'
allow 'SR_MEMORY=0 make container-verify'
# the target itself, which check_part matched as `container-*`
refuse "make container-check=1"
refuse "make container-x!='touch f'"
# what a command prints, inside the quotes where a value is otherwise read
refuse 'make container-pytest ARGS="$(cat args)"'
# a redirection's `&` does not end the command, so what follows it is still make's
refuse 'make container-check 2>&1 test'
refuse 'make container-check >&2 test'
refuse 'make container-check &>/tmp/c.log test'
allow 'make container-check >/tmp/c.log 2>&1'
allow 'make container-check 2>/tmp/c.log'
# a quoted `>` is text, not a redirection whose file the rule skips
refuse "make container-check '>' test"
refuse 'make container-check \> test'
allow "make container-pytest ARGS='-k a>b'"
# make found by its path, under its other name, or behind a shell keyword
refuse '/usr/bin/make container-check test'
refuse 'gmake container-check test'
refuse 'gmake test'
refuse 'if make test; then echo ok; fi'
refuse 'while make test; do :; done'
refuse '! make test'
# ...and what the docs write still passes: a trailing comment, a continued line, a runner's options
allow 'make container-quality-base    # once per pull; re-run after rebasing'
allow 'make container-check  # the pre-commit hooks'
allow "make container-pytest \\
  ARGS='tests/test_access.py -q'"
allow 'timeout --signal=KILL 600 make container-check'
allow 'nice -n 10 make container-test'

# a command the guard cannot read before it runs, or one spelled round the rules
refuse '\uv run pytest'
refuse "'uv' run pytest"
refuse 'u""v run pytest'
refuse '\u\v run pytest'
refuse "eval 'uv run pytest'"
refuse "builtin eval 'uv run pytest'"
refuse 'x=uv; $x run pytest'
refuse '$(echo uv) run pytest'
allow 'make container-check'
allow 'make container-test'
allow 'make container-quality-update'
allow 'make e2e-test'
# tsc over the specs, in the Playwright image - a container wrapper like the two above it,
# and the only thing that type-checks e2e/ at all
allow 'make e2e-typecheck'
allow 'scripts/e2e-typecheck.sh'
allow 'SR_KEEP=1 scripts/e2e-container.sh home'
# the same check run on the host is still the host, whichever way it is spelt
refuse 'npx tsc --noEmit -p e2e/tsconfig.json'
refuse 'npm run typecheck'

# A pipeline's exit status is its last command's, so trimming a long run with head or tail
# throws away the answer and reports 0 in its place.
refuse 'make e2e-test | tail -25'
refuse 'scripts/e2e-container.sh 2>&1 | tail -40'
refuse 'make container-check 2>&1 | grep -viE "Passed|Skipped" | tail -8'
refuse 'SR_KEEP=1 scripts/e2e-container.sh chrome-density | head -30'
refuse 'make container-test | tail -12'
refuse 'scripts/container.sh make check | tail -5'
# ...but only for the wrappers, and only where the status is what was thrown away
allow 'git log --oneline | head -5'
allow 'ls scripts/ | tail -20'
allow 'gh pr checks 731 | head'
allow 'cat e2e/README.md | tail -40'
# grep is left alone: it discards the status too, but a rule against it is one nobody
# could work under, and an unfollowable rule gets the whole guard switched off
allow 'make container-check 2>&1 | grep -c Passed'
# the fix the refusal asks for, which has to keep working
allow 'make container-check > /tmp/check.log 2>&1'
allow 'tail -40 /tmp/check.log'
# reading a review is gh on the host, which is where gh lives
allow 'scripts/pr-review.sh 731'
# a wrapper in one pipeline does not condemn a head or tail in the next
allow 'make container-check > /tmp/c.log 2>&1; tail -5 /tmp/c.log'

# A subshell ends a command the way a semicolon does. While `(` and `)` were ordinary
# characters, the bracket stuck to the word beside it: the first word of `(git add -A)` was
# `(git`, which no rule here is about, so the whole of it went through unread.
refuse '(git add -A)'
refuse 'cd assets && (yarn install)'
refuse '(uv run pytest -q)'
refuse '{ git add . ; }'
# ...and the other half: a wrapper at the end of a subshell was refused for a target named
# `e2e-typecheck)`, which is not one this repository has.
allow '(make container-quality-base && make container-verify)'
allow '(make container-check && make e2e-typecheck)'
allow 'echo one && (git status)'

# Staging
refuse 'git add -A'
refuse 'git add .'
refuse 'git add --all'
refuse 'git add -u'
refuse 'git add -- .'
allow 'git add app/webapp.py'
allow 'git add -- app/webapp.py assets/src/js/libs/greeting.ts'
allow 'git add .claude/settings.json'

# A list a command printed is not a list of names. `git add $(git diff --name-only)` stages
# every file the worktree has changed - whatever else is in it included - and so does each
# way of handing git that list, on the line or down a pipe.
refuse 'git add $(git diff --name-only)'
refuse 'git add -- $(git diff --name-only)'
refuse 'git add $(git status --porcelain | cut -c4-)'
refuse 'git add "$(git diff --name-only)"'
refuse 'git add `git diff --name-only`'
refuse 'git diff --name-only | xargs git add'
refuse 'git diff --name-only | xargs -r git add --'
refuse 'git diff --name-only | xargs -I{} git add {}'
refuse 'git diff --name-only | xargs -n 1 git add'
refuse 'git ls-files -m | git add --pathspec-from-file=-'
refuse 'git add --pathspec-from-file changed.txt'
# a substitution in git's own options split the line there, and left `add -A` unread
refuse 'git -C $(git rev-parse --show-toplevel) add -A'
# and one inside another closes at its own bracket, not at the first
refuse 'git -C $(dirname $(pwd)) add -A'
refuse "git -C \$(echo ')') add -A"
# a path built around one still names a file
allow 'git add "$(git rev-parse --show-toplevel)/app/foo.py"'
allow 'git add $(git rev-parse --show-toplevel)/app/foo.py'
allow 'git -C "$(git rev-parse --show-toplevel)" add app/foo.py'
allow 'git -C $(git rev-parse --show-toplevel) add app/foo.py'
allow 'git commit -m "$(cat msg.txt)"'
allow 'echo $(git diff --name-only)'
allow 'xargs git log -1 < shas.txt'
# the substitutions a session writes all day, which have to stay as allowed as they were
allow 'sha=$(git rev-parse HEAD) && git push --force-with-lease=feat/x:$sha origin feat/x'
allow "gh api graphql -f query='mutation(\$pr:ID!){requestReviews(input:{pullRequestId:\$pr}){pullRequest{number}}}' -f pr=\"\$(gh pr view 951 --json id --jq .id)\""
allow "$(cat <<'CASE'
gh pr create --draft --title "feat: x" --body "$(cat <<'EOF'
## What

A body that runs `make container-verify`, never `uv run pytest` or `git add -A`.

Posted by Claude on behalf of @sstriker
EOF
)"
CASE
)"
# a message written the same way is the message, wherever it puts an apostrophe or a backtick
allow "$(cat <<'CASE'
git commit -q -m "$(cat <<'EOF'
fix: the guard refuses `git add -A` and `uv run pytest`

Co-Authored-By: Claude
EOF
)"
CASE
)"
allow "$(cat <<'CASE'
git commit -q -m "$(cat <<'EOF'
fix: the guard doesn't refuse this

Co-Authored-By: Claude
EOF
)"
CASE
)"
# ...and what follows it is still read: an apostrophe in the message ran the substitution
# to the end of the line and took the push with it
refuse "$(cat <<'CASE'
git commit -q -m "$(cat <<'EOF'
fix: it doesn't
EOF
)" && git push --no-verify
CASE
)"
# an escaped one is text, in double quotes or out
allow 'git commit -m "fix: \`uv run pytest\` is refused"'
allow 'scripts/container.sh sh -c "echo \$(python3 --version)"'
# and in single quotes nothing substitutes at all
allow "scripts/container.sh sh -c 'echo \$(uv --version)'"
allow "git commit -m 'fix: \`pytest -q\` is refused'"
refuse "$(cat <<'CASE'
gh pr create --draft --title "feat: x" --body "$(cat <<'EOF'
## What

A body with nobody's name under it.
EOF
)"
CASE
)"
# and the command inside one is still a command
refuse 'echo $(uv run pytest)'
refuse 'echo `pytest -q`'
refuse 'echo "$(yarn test)"'

# The shared stash stack
refuse 'git stash'
refuse 'git stash pop'
refuse 'git stash save wip'
refuse 'git stash drop'
refuse 'git stash push -u'
allow 'git stash push -u -m podling-names-wip'
allow 'git stash list'
allow 'git stash apply 0d2e4a1'

# The pre-commit gate
refuse 'git commit --no-verify -m "fix: thing"'
refuse 'git commit -n -m "fix: thing"'
refuse 'git push --no-verify'
allow 'git commit -m "fix: read the office through a comma"'
allow 'git push -n origin HEAD'
allow 'git push origin HEAD'

# What a -m carries is a message. Read as flags, it refused sentences this repository has
# every reason to write: the first two are refused by the guard on main, and the third by
# the -a rule below had the message not been skipped first.
allow 'git commit -m "docs: --no-verify is never passed here"'
allow 'git commit -m "chore: -n is not an escape hatch"'
allow 'git commit -m "guard: refuse git commit -a"'
allow 'git commit --amend -m "fix: thing"'

# `git commit -a` stages every modified file, which is `git add -u` under another name -
# and that one is refused a few cases above.
refuse 'git commit -a -m "fix: thing"'
refuse 'git commit --all -m "fix: thing"'
refuse 'git commit -am "fix: thing"'
# git bundles short options, so `a` is not always the first letter of the cluster.
refuse 'git commit -va -m "fix: thing"'
refuse 'git commit -qva -m "fix: thing"'
# ...but a cluster stops at the first option that takes a value, since what follows it is
# that value: `-ma` is `-m a` and `-qma` is `-q -m a`, and in both the `a` is the message.
# One case per letter that takes a value, because they are not otherwise checked: dropping
# any of the other six from the list left all 206 cases passing.
allow 'git commit -ma'
allow 'git commit -qma'
allow 'git commit -Fa'
allow 'git commit -ca'
allow 'git commit -Ca'
allow 'git commit -ta'
allow 'git commit -ua'
allow 'git commit -Sa'

# git's own options come before the subcommand. Reading the word after `git` as the
# subcommand took `-C` for one, and every rule above was one `-C` away from not applying.
# The directory is a word to parse past, not a place: nothing here resolves it, and no
# such worktree need exist.
refuse 'git -C ../tooling-secretary-records-topic add -A'
refuse 'git -C../tooling-secretary-records-topic add -A'
refuse 'git -C ../tooling-secretary-records-topic commit --no-verify -m "fix: thing"'
refuse 'git -C ../tooling-secretary-records-topic commit -am "fix: thing"'
refuse 'git -c user.name=x -C ../tooling-secretary-records-topic add -A'
refuse 'git --no-pager -C ../tooling-secretary-records-topic stash pop'
refuse 'git --git-dir=../topic/.git --work-tree=../topic add .'
refuse 'git --git-dir ../topic/.git add -A'
refuse 'git --namespace ns add -A'
allow 'git -C ../tooling-secretary-records-topic add app/webapp.py'
allow 'git -C ../tooling-secretary-records-topic log --oneline -5'

# The subcommand is the first word that is NOT an option. Stopping the walk at the first
# unrecognised one made the fix a single flag wide: git has more options of its own than
# any list here will hold, and each was a way past every rule above.
refuse 'git --no-optional-locks add -A'
refuse 'git --glob-pathspecs add -A'
refuse 'git --icase-pathspecs commit --no-verify -m "fix: thing"'
refuse 'git -p stash pop'
refuse 'git --no-advice --literal-pathspecs add .'
refuse 'git --attr-source=HEAD add -A'
# ...and one whose value is the next word must skip that word too, or the value becomes
# the subcommand. `git --attr-source HEAD status` runs status; `git --exec-path add -A`
# prints the exec path and stages nothing, which is why that one takes no separated value.
refuse 'git --attr-source HEAD add -A'
refuse 'git --config-env user.name=X add -A'
# --shallow-file is one of these and is in no list of options that take a path. It is in
# handle_options() in git.c, which is where the list had to be read from, and
# `git --shallow-file "" add -A` does stage everything.
refuse 'git --shallow-file /dev/null add -A'
refuse 'git --shallow-file "" commit --no-verify -m "fix: thing"'

# A quoted value with a space in it is one value. Read as two words it moved the
# subcommand along by one, and the whole call went unchecked.
refuse 'git -C "../my worktree" add -A'
refuse "git -C '../my worktree' commit --no-verify -m 'fix: thing'"
refuse 'git --git-dir "../my repo/.git" add -A'

# A backslash is the other way to write that space, and the way a shell's own completion
# writes it. Emitting the space it escaped split the value back into two words, which moved
# the subcommand along by one and left the call unchecked - the hole the quoted spelling
# above closes, reopened by the other spelling of the same path.
refuse 'git -C ../my\ worktree add -A'
refuse 'git -C ../my\ worktree commit --no-verify -m "fix: thing"'
refuse 'git -C ../my\ worktree stash pop'
refuse 'git --git-dir ../my\ repo/.git add -A'
# ...and the same split, costing the other way round: a value that came apart into words
# was read as flags, so a message spelled without quotes was refused for the sentence in it.
allow 'git commit -m fix:\ refuse\ -a\ everywhere'
allow 'git commit -m docs:\ --no-verify\ is\ never\ passed\ here'
allow 'git add app/actions/my\ notes.md'
allow 'git stash push -u -m podling\ names\ wip'

# ...but a quoted single word is just a quoted word. `git add '*'` is quoted precisely so
# that git sees the glob rather than the shell, and it is the shape the rule is for.
refuse "git add '*'"
refuse 'git add "."'
refuse "git commit '--no-verify' -m 'fix: thing'"

# A quote nobody closed is a command no shell will run, but it must not be a way past
# either: what it holds is read by the same rule as what a closed one holds.
refuse "git commit '-a"
allow 'git commit -m "fix: an unterminated -a'

# An empty pair of quotes is dropped by a shell, so the guard has to drop it too. Standing
# in for it with a placeholder broke the flag either side of it into a word that matched
# nothing.
refuse "git add ''-A"
refuse 'git add ""-A'
refuse 'git add -A""'
refuse "git commit --no-ver''ify -m 'fix: thing'"
# Empty quotes at the end of a word, where keeping the placeholder would append it to the
# flag: `--no-verify_` is not `--no-verify`, and the rule is matched on the whole word.
refuse "git commit --no-verify'' -m 'fix: thing'"
refuse 'git commit --all"" -m "fix: thing"'
# ...but a word that is nothing but empty quotes is an argument and keeps its place, or
# the option before it takes the subcommand as its value instead.
refuse "git --shallow-file '' add -A"
refuse 'git --git-dir "" add -A'

# git bundles short options for `add` as well as for `commit`, and -A is the flag this
# whole rule is named after. Reading a cluster on one side and whole words on the other
# left the shorter spelling of the older rule open.
refuse 'git add -vA'
refuse 'git add -Af'
refuse 'git add -fA'
refuse 'git add -vu'
allow 'git add -p'
allow 'git add -n app/webapp.py'
allow 'git add -f build/generated.js'

# A stash message attached to its option is still a message. Only the spaced spellings were
# looked for, so these were refused for having none.
allow 'git stash push -u -m"podling-names-wip"'
allow 'git stash push -u --message="podling-names-wip"'

# Every way a message carries what looks like a flag. These five were refused by the
# option-skipping this replaced, and the refusal named an `-a` the session never wrote -
# which on a branch whose commits are about that very flag leaves no wording that
# satisfies the guard.
allow "git commit -m \$'fix: the guard refuses -a now'"
allow 'git commit -m"fix: refuse -a everywhere"'
allow 'git commit --message="fix: refuse -a everywhere"'
allow 'git commit -m "fix: the \"guard\" refuses -a"'
allow 'git commit --author="Sander -a Striker <s@example.invalid>" -m "fix: thing"'

# An apostrophe written the way a shell takes one. Nothing before this refused it; it is
# here because the `\'` is what the outside-quotes backslash rule is for, and without that
# rule the rest of the sentence reads as unquoted words.
allow "git commit -m 'don'\''t refuse this -a'"
# The escaped character is the word, so dropping it takes the flag with it: `git add \-A`
# is `git add -A` to a shell, and `A` to a guard that read the backslash and forgot what
# came after it.
refuse 'git add \-A'
refuse 'git commit \--no-verify -m "fix: thing"'
# ...and a plain single-quoted run is the one place a backslash escapes nothing. `'don\'t
# -a'` closes at that quote, which leaves `-a` a bare word - so reading it the way `$'...'`
# below is read would swallow a flag into a sentence that never held one.
refuse "git commit -m 'don\\'t -a'"

# `$'...'` is the other place an apostrophe gets written, and there the backslash escapes
# it from inside the quotes rather than between two runs of them. Read as a plain single
# quote, the `\'` closed the run early and the rest of the sentence became bare words - so
# a message saying what this branch is about was refused for the flags in it.
allow "git commit -m \$'don\\'t refuse this -a'"
allow "git commit -m \$'don\\'t skip --no-verify here'"
allow "git commit -m \$'fix: thing\nthe body mentions -a'"
# ...and the same reading let a flag hide in one. `$'-a'` is the word `-a`, and leaving the
# `\$` in front of it made a word that matched no rule here.
refuse "git commit \$'-a' -m 'fix: thing'"
refuse "git commit \$'--no-verify' -m 'fix: thing'"
refuse "git add \$'-A'"
# An empty one is an empty argument, the same as `''`, and has to keep its place or the
# option before it takes the subcommand for its value.
refuse "git --shallow-file \$'' add -A"

# -S and --gpg-sign take a value only when it is attached, so reading the next word as
# theirs swallowed the flag after them - and `git commit -S -a -m x` is an ordinary
# command that walked past both the new rule and the one already on main.
refuse 'git commit -S -a -m "fix: thing"'
refuse 'git commit -S --no-verify -m "fix: thing"'
refuse 'git commit --gpg-sign --no-verify -m "fix: thing"'
refuse 'git commit --gpg-sign -a -m "fix: thing"'
allow 'git commit -S -m "fix: thing"'

# Everything else this session does all day
allow 'ls -la app/actions'
allow 'git log --oneline -5 | head'
allow 'git status -sb'
allow 'gh pr view 621 --json body'
allow 'grep -rn create_app app'

# The description is not the command. Claude writes prose there, and prose about
# running pytest on the host must not be read as running pytest on the host.
allow 'ls e2e/screenshots' 'Check the screenshots before running pytest and yarn test'

# A heredoc body is not commands either. Writing an issue, a pull request description or a
# commit message means putting prose into a file, and prose about this repository names the
# very tools the guard refuses. The shell runs none of it - a quoted delimiter is not even
# expanded - so neither may this. Written with a heredoc of its own, since quoting a
# multi-line command inline is exactly the awkwardness these cases are about.
allow "$(cat <<'CASE'
cat > body.md <<'MD'
Measured over app, scripts and server.py with:

    uv run ruff check --select G004

MD
CASE
)"
allow "$(cat <<'CASE'
cat > body.md <<'MD'
Every one of these is prose about a command, not a command:

yarn build
npm install
git add -A
git stash pop
git commit --no-verify
pytest tests/api -q
make test

Posted by Claude on behalf of @sstriker
MD
gh api -X POST repos/:owner/:repo/issues -F body=@body.md
CASE
)"
allow "$(cat <<'CASE'
git commit -F - <<'MSG'
fix: the guard reads a heredoc body as commands

The body below was refused as uv on the host:

uv run pytest tests/api -q
MSG
CASE
)"
# every spelling of the operator: bare, double-quoted, and the tab-stripping form
allow "$(cat <<'CASE'
cat <<EOF > note.txt
pytest tests/api -q
EOF
CASE
)"
allow "$(cat <<'CASE'
cat <<"EOF" > note.txt
make test
EOF
CASE
)"
allow "$(printf 'cat <<-EOF > note.txt\n\tyarn test\n\tEOF\n')"

# The half that matters: the guard still sees the real commands around a heredoc.
refuse "$(cat <<'CASE'
uv run pytest
cat > body.md <<'MD'
prose
MD
CASE
)"
refuse "$(cat <<'CASE'
cat > body.md <<'MD'
prose
MD
yarn build
CASE
)"
refuse "$(cat <<'CASE'
cat > body.md <<'MD'
prose
MD
git add -A
CASE
)"
# The terminator is a line of its own, so a body naming it in passing is still a body.
refuse "$(cat <<'CASE'
cat > body.md <<'MD'
the MD in this line ends nothing
MD
pytest tests/api
CASE
)"
# `<<<` is a here-string: it feeds one word and opens no body.
refuse "$(cat <<'CASE'
grep -q x <<<"marker"
uv run pytest
CASE
)"
# A `<<` inside quotes opens nothing: the shell parses no redirection there. Without that,
# an invented delimiter that some later line happens to match takes every command between
# the two out of the guard's view - which is the one way this change could hide a command
# rather than merely read too few.
refuse "$(cat <<'CASE'
git log --format="%h<<%s" -1
yarn test
CASE
)"

refuse "$(cat <<'CASE'
echo "a << EOF is not a heredoc"
uv run pytest
EOF
CASE
)"
refuse "$(cat <<'CASE'
grep -n 'x << MD' notes.txt
yarn build
MD
CASE
)"

# A `#` starts a comment, and the rest of that line is data too - so a `<<` in one opens no
# body either, and cannot be used to hide the lines after it.
refuse "$(cat <<'CASE'
echo hi # <<EOF
uv run pytest
EOF
CASE
)"
# but `#` starts one only at the start of a word: the fragment below belongs to the url, so
# the heredoc after it is real and its body is still a body
allow "$(cat <<'CASE'
curl -s https://example.org/page#section <<EOF
yarn test
EOF
CASE
)"

# Anything posted to an issue or a pull request says who wrote it.
refuse 'gh issue comment 773 --body "Fixed in the next release."'
refuse 'gh pr comment 800 --body "Rebased."'
refuse 'gh pr create --title "feat: a thing" --body "It does the thing."'
refuse 'gh pr review 800 --request-changes --body "This needs a test."'
refuse 'gh issue edit 773 --body "Updated."'
# a body written to a file moments earlier, which is the shape a long comment takes
refuse "$(cat <<'CASE'
cat > /tmp/guard-case-body.md <<MD
Fixed in the next release.
MD
gh issue comment 773 --body-file /tmp/guard-case-body.md
CASE
)"

# The line makes each of those fine.
allow "$(cat <<'CASE'
gh issue comment 773 --body "Fixed in the next release.

Posted by Claude on behalf of @sstriker"
CASE
)"
# A line of its own is the whole of the rule: a signature inside a sentence is refused the
# same as none at all, because it is not what a reader scanning the foot of a comment sees.
refuse 'gh pr comment 800 --body "Done. Posted by Claude on behalf of @sstriker, thanks."'
allow "$(cat <<'CASE'
cat > /tmp/guard-case-body.md <<MD
Fixed in the next release.

Posted by Claude on behalf of @sstriker
MD
gh issue comment 773 --body-file /tmp/guard-case-body.md
CASE
)"

# Two posts in one call: one signature cannot answer for two, and the second carrying it
# would otherwise satisfy the first.
refuse "$(cat <<'CASE'
gh issue comment 773 --body "First, unsigned."
gh issue comment 796 --body "Second.

Posted by Claude on behalf of @sstriker"
CASE
)"
# even when both carry it, because the count is what makes the search answerable
refuse "$(cat <<'CASE'
gh issue comment 773 --body "First.

Posted by Claude on behalf of @sstriker"
gh issue comment 796 --body "Second.

Posted by Claude on behalf of @sstriker"
CASE
)"

# A path is not the punctuation it arrives wrapped in. Each of these names a file that is there
# and signed, and each was read with its wrapping still attached - so a signed comment was
# refused for a file that "was not there".
signed=$(mktemp) || exit 1
unsigned=$(mktemp) || exit 1
printf 'Fixed.\n\nPosted by Claude on behalf of @sstriker\n' > "$signed"
printf 'Fixed.\n' > "$unsigned"
spaced="$(mktemp -d)/with space"
mkdir -p "$spaced"
cp "$signed" "$spaced/body.md"
trap 'rm -rf "$signed" "$unsigned" "$spaced"' EXIT

allow "gh pr comment 800 --body-file $signed"
allow "gh pr comment 800 --body-file \"$signed\""
refuse "gh pr comment 800 --body-file $unsigned"
# gh takes a flag's value attached as well as apart, and a flag before the noun. Each of these
# reaches the api with a body, and each used to skip the rule without it ever running.
refuse 'gh issue comment 773 -b"Fixed."'
refuse 'gh issue comment 773 -b="Fixed."'
refuse 'gh --repo o/r issue comment 1 -b "Fixed."'
refuse 'gh -R o/r pr comment 1 --body "Fixed."'
# prose is full of dollars, and asking whether a body held one to spot `$(cat file)` switched the
# rule off for most of what this repository writes
# shellcheck disable=SC2016  # the literal dollars are the point
refuse 'gh issue comment 773 --body "The fix was in $PATH handling. No signature."'
# shellcheck disable=SC2016  # the literal dollars are the point
refuse 'gh pr comment 800 --body "Costs $5 a month."'

# The signature starts its line; what the shell puts after it is the shell's business. Requiring
# it to end the line refused every command that carries a flag after the body, which is ordinary.
allow "$(cat <<'CASE'
gh issue create --title "feat: a thing" --body "It does the thing.

Posted by Claude on behalf of @sstriker" --label enhancement
CASE
)"
allow "$(cat <<'CASE'
gh pr comment 800 --body "Done.

Posted by Claude on behalf of @sstriker" && echo posted
CASE
)"

# An unsigned comment whose own text names a path that holds a signed file is allowed, and this
# records the trade rather than hiding it. Closing it meant skipping files whenever `--body`
# appeared, and `--body` appears in the title of any issue written about the flag: an issue
# titled "document the --body flag" with a signed --body-file was refused. A guard that refuses
# somebody doing it right is one they switch off, and this is a nudge, not a boundary.
allow "gh issue comment 809 --body \"The guard reads --body-file $signed when you pass one. No signature here.\""
# ...and the shapes that trade bought back
allow "gh issue create --title \"document the --body flag\" --body-file $signed"
allow "gh pr comment 800 --body-file \"$spaced/body.md\""
# shellcheck disable=SC2016  # the literal is the point: a path the shell has still to expand
allow 'gh pr comment 800 --body-file "$BODY_FILE"'
# a body the shell has still to build is refused: asking whether the value held a `$` to spot
# one switched the rule off for "costs $5 a month" and "echo exit=$?", which is most of the
# prose here. The narrower wrong is refusing this shape.
refuse "gh pr comment 800 --body \"\$(cat $signed)\""
# `gh api` is not covered, and these record that rather than leaving it to be discovered. It
# reaches the same comments and is the only way one is edited, so the gap is real - it is filed
# rather than closed here, because writing that branch cost three of the five defects this rule
# was reviewed for. See #809.
allow "gh api -X PATCH repos/a/b/issues/comments/1 -F body=@$signed"
allow "gh api -X PATCH repos/a/b/issues/comments/1 -F body=@$unsigned"
# reading through those same paths is most of what the api is used for here, and stays allowed
# whatever happens to the gap above
allow 'gh api repos/apache/tooling-secretary-records/pulls/800/comments'
allow 'gh api repos/a/b/pulls/1/reviews --jq .state'
# Prose about posting is not posting, even inside the body of a post. A signed comment
# explaining how to reply - quoting the command on a line of its own - was counted as a second
# post and refused whole. Counted where the parts are dispatched, it cannot see prose.
allow "$(cat <<'CASE'
gh issue comment 773 --body "To reply, run:

gh issue comment 774 --body ...

Posted by Claude on behalf of @sstriker"
CASE
)"
# ...and two comments in one call are refused whichever gh subcommand posts them
refuse "$(cat <<'CASE'
cat > c.md <<MD
Fixed.

Posted by Claude on behalf of @sstriker
MD
gh pr comment 1 --body-file c.md
gh pr comment 2 --body-file c.md
CASE
)"

# a bare gh reaches the rule with nothing to read, and must not abort the guard
allow 'gh'
allow 'echo one; gh'

# Reading is not posting, and neither is anything else gh does. Every one of these is a
# call this repository asks for, and a guard that refused them would be turned off.
allow 'gh issue view 773'
allow 'gh issue list --state open --limit 100'
allow 'gh pr view 800 --json body'
allow 'gh pr diff 800'
allow 'gh pr checks 800'
allow 'gh run view 123 --log-failed'
allow 'gh api repos/apache/tooling-secretary-records/pulls/800/comments'
allow 'scripts/pr-review.sh 800'
allow 'gh pr edit 800 --add-label ready'
# no body flag: the editor opens and a person types it themselves
allow 'gh pr create --fill'
allow 'gh issue create'
# writing about the rule is not posting under it
allow "$(cat <<'CASE'
cat > /tmp/notes.md <<MD
Run gh issue comment 773 --body "..." to reply.
MD
CASE
)"

if [ "$failures" -ne 0 ]; then
    printf '\n%s guard case(s) failed\n' "$failures" >&2
    exit 1
fi

printf 'guard-bash.sh: every case holds\n'
