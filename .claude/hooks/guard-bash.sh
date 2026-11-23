#!/bin/sh
# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
# Hold the rules AGENTS.md states as absolutes, at the moment one is about to be broken.
#
# Claude Code runs this before every Bash tool call, handing it the call as JSON on
# stdin. Exiting 2 refuses the call and shows this script's stderr to the model, which
# then knows what to run instead. The rules that earn the machinery are the ones AGENTS.md
# writes as absolutes, plus one it states as a way of misreading a result:
#
#   - every build, linter and test runs in the dev container, never on the host
#   - stage the files you changed; never `git add -A`, nor the `git commit -a` that
#     spells the same thing on the commit, nor a list a command printed - `$(git diff
#     --name-only)` or `xargs git add` - which is the same thing again
#   - the stash stack is shared with every other worktree, so never bare `git stash`
#   - `make check` runs before a commit, so never `--no-verify`
#   - anything posted to an issue or a pull request says who wrote it
#   - a container wrapper piped into head or tail reports the pipe's exit status, so a
#     run that failed reads as one that passed
#
# Known gaps: text piped or redirected into a shell - `echo "uv run pytest" | sh`, or a
# heredoc to sh - is not read as a command, and neither is what `find -exec` or `rg --pre`
# runs, so either can run the toolchain on the host.
#
# A rule the harness holds does not have to be remembered. That is the point of putting
# these here rather than in another paragraph of AGENTS.md: the paragraph is read once
# per session and skipped when the context is long, and this is not read at all.
#
# It stands down inside the dev container, where running uv and yarn is the whole idea.
# /run/<service> is where both the Dockerfile and .devcontainer put the working tree, so its
# presence is the same question as "am I already inside". DEVKIT_GUARD_FORCE=1 overrides
# that, which is how guard-bash.test.sh exercises the guard from inside the container.
#
# A disposable environment that is not that container - an isx box, say - says so with
# DEVKIT_ISOLATED=1, and there only the toolchain rules stand down: a box with a throwaway
# filesystem has nothing to protect from `uv`, and everything else here (staging, the
# stash, --no-verify, the byline on a posted body) is about the repository and its
# colleagues, which the box does not change. The variable is read from the environment
# this hook was started in, which the box sets (an isx template's `env`), and never from the
# command it is judging: `DEVKIT_ISOLATED=1 uv run x` on a host is `uv` on a host.
#
# It holds a rule against a lapse, not against somebody set on breaking it: it reads the shell
# as far as the rules need and no further. Known to walk past it, and left so:
#   - a redirection in front of the command word: `>out uv run pytest`
#   - a glob or a brace that names the command: `/usr/bin/[u]v`, `{uv,} run`
#   - a runner it does not list, or one of its options it does not know: `stdbuf`, `ionice`,
#     `env -S`, `sudo -u`
#   - a value exported earlier on the line or in the session: `export FILTER=...; make e2e-quick`
# Reading make's arguments word by word costs time: a five-part line with a make wrapper in it
# went from 34ms to 47ms.

set -eu

isolated() {
    [ "${DEVKIT_ISOLATED:-}" = 1 ]
}

# The service is read from devkit.conf and never sourced: this runs before anything else has been
# checked, and a file of the repository is not to be run by the code that guards it. But that file is
# the repository's to write, so it must not be able to switch the guard off: the guard stands down only
# where it lives at /run/<service>, which is where the container mounts the working tree, and only for a
# name that is one (see checkout-id.sh). A name like `.`, `..`, `user` or a missing file leaves it on,
# which is the safe answer to "am I already inside".
_service=$(sed -n 's/^DEVKIT_SERVICE=//p' "$(dirname -- "$0")/../../devkit.conf" 2>/dev/null | head -n 1)
case $_service in
    '' | [!a-z0-9]* | *[!a-z0-9_-]*) _service='' ;;
    *) : ;;
esac
_here=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." 2>/dev/null && pwd -P)
if [ "${DEVKIT_GUARD_FORCE:-}" != 1 ] && [ -n "$_service" ] && [ "$_here" = "/run/$_service" ]; then
    exit 0
fi

payload=$(cat)

# The body of a heredoc is data. The shell will not execute it - a quoted `<<'MD'` is not
# even expanded - so nothing in there is a command. Reading it as one refuses an issue body
# that quotes `uv run ...` in a fenced example, and teaches the one habit a guard cannot
# afford to teach: writing the file with a different tool.
#
# The line that opens the heredoc is kept, because the command on it is real, and so is
# everything after the terminator: `uv run ...` before or after a heredoc is still uv on
# the host, and that is the half of this worth testing.
#
# A `<<` the shell would not read as an operator opens nothing, and two places put one on a
# line that is not one: inside quotes, where no redirection is parsed, and after a `#`,
# where the rest of the line is a comment. `git log --format="%h<<%s"` is the innocent
# shape; the harmful one is a `<<` in either position whose invented delimiter some later
# line then matches, dropping every command in between. The scan therefore walks each line
# tracking quote state, the way split_on_separators does below, stops at a comment, and
# opens a body only on a `<<` that survives both.
#
# A body whose terminator never arrives is kept and read. The shell would swallow the rest
# of the line the same way, but agreeing with it there costs more than it is worth: a `<<`
# this misreads would take every command after it out of view, and reading a few lines the
# shell would not is the safe direction for a guard to be wrong in.
drop_heredoc_bodies() {
    awk '
        # The delimiter an operator opens with, and how much of the line it took. Returns the
        # count of characters consumed after the `<<`, and reports the delimiter in DELIM -
        # empty when the operator opens no body at all.
        # \047 and \042 are the quotes, spelled so this program can be held in a shell string
        # without escaping them.
        function read_delimiter(rest,   quote, closing, taken) {
            DELIM = ""
            STRIP = 0
            taken = 0
            # `<<<` is a here-string: it feeds one word, and opens no body
            if (substr(rest, 1, 1) == "<") return 1
            # `<<-WORD` lets the body and its terminator be indented with tabs
            if (substr(rest, 1, 1) == "-") { STRIP = 1; rest = substr(rest, 2); taken = 1 }
            if (match(rest, /^[ \t]+/) > 0) { taken += RLENGTH; rest = substr(rest, RLENGTH + 1) }
            quote = substr(rest, 1, 1)
            if (quote == "\047" || quote == "\042") {
                closing = index(substr(rest, 2), quote)
                if (closing == 0) return taken + 1
                DELIM = substr(rest, 2, closing - 1)
                return taken + closing + 1
            }
            if (quote == "\\") { rest = substr(rest, 2); taken++ }
            if (match(rest, /^[A-Za-z0-9_.-]+/) == 0) return taken
            DELIM = substr(rest, 1, RLENGTH)
            return taken + RLENGTH
        }
        BEGIN { head = 1; tail = 0; held = 0; quote = ""; nested = 0 }
        head <= tail {
            line = $0
            if (dash[head]) sub(/^\t+/, "", line)
            if (line == word[head]) { head++; held = 0 } else { hold[++held] = $0 }
            next
        }
        {
            print
            line = $0
            at = 1
            while (at <= length(line)) {
                char = substr(line, at, 1)
                if (quote == "\042" && substr(line, at, 2) == "$(") {
                    # a substitution inside double quotes is code until its bracket closes, and
                    # a heredoc it opens is a body - the `-m "$(cat <<EOF` every message uses
                    nested++
                    outer[nested] = quote
                    depth[nested] = 1
                    quote = ""
                    at += 2
                } else if (quote != "") {
                    if (char == quote) quote = ""
                    at++
                } else if (char == "\\") {
                    at += 2
                } else if (char == "\047" || char == "\042") {
                    quote = char
                    at++
                } else if (nested > 0 && (char == "(" || char == ")")) {
                    depth[nested] += char == "(" ? 1 : -1
                    if (depth[nested] == 0) { quote = outer[nested]; nested-- }
                    at++
                } else if (char == "#" && (at == 1 || substr(line, at - 1, 1) ~ /[ \t]/)) {
                    # the rest of the line is a comment, and a comment opens no heredoc
                    break
                } else if (char == "<" && substr(line, at + 1, 1) == "<") {
                    at += 2 + read_delimiter(substr(line, at + 2))
                    # a line may open more than one, and they close in the order they opened
                    if (DELIM != "") { tail++; word[tail] = DELIM; dash[tail] = STRIP }
                } else {
                    at++
                }
            }
        }
        END { for (i = 1; i <= held; i++) print hold[i] }
    '
}

# The command, read out of the payload without a JSON parser. jq is in the dev container
# but not reliably on a host, and a guard that fails to start is a guard that silently
# stops guarding. The pattern takes a JSON string body - any escape, or any character
# that is neither quote nor backslash - so a quote inside the command does not end it
# early. An escaped newline becomes a real one, so that the heredocs can be found and
# their bodies dropped, and what is left becomes a `;` - which is what a newline means to
# a shell, and keeps a command written across several lines split into its parts.
command=$(
    printf '%s' "$payload" |
        sed -nE 's/.*"command"[[:space:]]*:[[:space:]]*"((\\.|[^"\\])*)".*/\1/p' |
        sed -e 's/\\n/\
/g' -e 's/\\[rt]/ /g' -e 's/\\"/"/g' -e 's/\\\\/\\/g' |
        drop_heredoc_bodies |
        # a line ending in a backslash goes on on the next: one command, not a `\;` and a second
        awk '{ if (sub(/\\$/, "")) printf "%s", $0; else print }' |
        tr '\n' ';'
)

# Nothing recognisable: allow it. Refusing a payload this cannot read would cost every
# call, to catch the ones it cannot see anyway.
if [ -z "$command" ]; then
    exit 0
fi

# The same call with the heredoc bodies kept, and its newlines with them. Every rule above
# reads a command, and so must not see a body; the comment rule reads the text about to be
# posted, and a body is where that text usually is.
posted_text=$(
    printf '%s' "$payload" |
        sed -nE 's/.*"command"[[:space:]]*:[[:space:]]*"((\\.|[^"\\])*)".*/\1/p' |
        sed -e 's/\\n/\
/g' -e 's/\\[rt]/ /g' -e 's/\\"/"/g' -e 's/\\\\/\\/g'
)

refuse() {
    printf '%s\n\n%s\n\nRefused by .claude/hooks/guard-bash.sh, which holds the rules in AGENTS.md.\n' \
        "$1" "$2" >&2
    exit 2
}

# The word split_on_separators leaves where a command substitution stood, and the word an
# `xargs` adds for the list it reads. Either way it is text a command printed, which no rule
# here can read, and a single word so that what stood beside it stays one word with it.
SUBSTITUTED='__bat_substitution__'

# $1 past its first word as a shell reads it, so that a quoted space is inside the word: the rest
# of `FILTER='x y' make e2e-quick` is `make e2e-quick`, not `y' make e2e-quick`.
past_first_word() {
    _left=$1
    _in=''
    while [ -n "$_left" ]; do
        _c=${_left%"${_left#?}"}
        _left=${_left#?}
        if [ -n "$_in" ]; then
            [ "$_c" = "$_in" ] && _in=''
            continue
        fi
        case $_c in
            "'" | '"') _in=$_c ;;
            \\) _left=${_left#?} ;;
            [[:space:]]) break ;;
            *) : ;;
        esac
    done
    printf '%s' "$_left"
}

# What a part of the line actually runs, past whatever stands in front of it: leading
# whitespace, an opening subshell or quote, `VAR=value` prefixes, and the wrappers that
# take a command as their argument.
strip_prefixes() {
    _rest=$1
    while [ -n "$_rest" ]; do
        case $_rest in
            [[:space:]]* | '('* | '{'* | "'"* | '"'*)
                _rest=${_rest#?}
                continue
                ;;
            *) : ;;
        esac
        case ${_rest%%[[:space:]]*} in
            *=*)
                _rest=$(past_first_word "$_rest")
                continue
                ;;
            sudo | env | time | nohup | exec | command | builtin) : ;;
            # the keywords that open a command: `if make test; then`, `while`, `! make test`
            if | then | elif | else | do | while | until | !) : ;;
            # `timeout 600 make test` runs make, and so does `timeout --signal=KILL 600` - and `nice -n 10`.
            timeout | nice)
                _rest=${_rest#*[[:space:]]}
                while :; do
                    case ${_rest%%[[:space:]]*} in
                        -* | [0-9]*) : ;;
                        *) break ;;
                    esac
                    case $_rest in
                        *[[:space:]]*) _rest=${_rest#*[[:space:]]} ;;
                        *) break ;;
                    esac
                done
                continue
                ;;
            *) break ;;
        esac
        case $_rest in
            *[[:space:]]*) _rest=${_rest#*[[:space:]]} ;;
            *) break ;;
        esac
    done
    printf '%s' "$_rest"
}

# Whether a bundle of short options carries one of the letters named. git lets them be
# written together, so the letter that matters is not always the first: `git add -vA` and
# `git commit -qva` are the whole-tree spellings of `-A` and `-a`.
#
# Read left to right, stopping at the first option that takes a value, because what follows
# that one is the value and not more options: `git commit -qma` is `-q -m a`, and the `a` is
# the message. $1 is the word, $2 the letters that are the finding, $3 the letters that take
# a value. $2 must not be empty - an empty pattern matches every letter.
cluster_carries() {
    _cluster=${1#-}
    _wanted=$2
    _valued=$3
    while [ -n "$_cluster" ]; do
        _letter=${_cluster%"${_cluster#?}"}
        case $_wanted in
            *"$_letter"*) return 0 ;;
            *) : ;;
        esac
        case $_valued in
            *"$_letter"*) return 1 ;;
            *) : ;;
        esac
        _cluster=${_cluster#?}
    done
    return 1
}

# A wildcard stages what this change never touched, which is how a credential, a scratch
# file or a generated tree reaches a commit that was meant to hold three files.
check_git_add() {
    for _word in "$@"; do
        _staged_wide=no
        case $_word in
            --all | --update | . | ./ | :/ | '*') _staged_wide=yes ;;
            # names a command printed, or a file holds: whatever the worktree has changed,
            # this change's files or not. A word with a path beside the substitution names
            # that path, and is not this.
            "$SUBSTITUTED" | --pathspec-from-file | --pathspec-from-file=*)
                refuse "\`git add\` with names a command printed - \`\$(git diff --name-only)\`, \`xargs git add\`, \`--pathspec-from-file\` - stages everything the worktree has changed, not what this change touched." \
                    "Name the files instead: git add app/foo.py assets/src/js/bar.svelte"
                ;;
            --*) : ;;
            # `git add` has no short option that takes a value, so nothing stops the read.
            -*)
                if cluster_carries "$_word" 'Au' ''; then
                    _staged_wide=yes
                fi
                ;;
            *) : ;;
        esac
        if [ "$_staged_wide" = yes ]; then
            refuse "\`git add $_word\` stages files this change never touched." \
                "Name the files instead: git add app/foo.py assets/src/js/bar.svelte"
        fi
    done
}

# The stash stack is one stack for the whole repository, shared by every linked worktree
# and so by every session running in one. A pop here can take the work of a session next
# door, and it cannot be given back.
check_git_stash() {
    case ${1:-} in
        '' | save | pop | clear | drop)
            refuse "\`git stash ${1:-}\` reaches into a stack shared with every other worktree in this repository." \
                "Set work aside with a temporary commit instead. If it really must be the stash: git stash push -u -m <unique-tag>, then apply it by sha."
            ;;
        push)
            # Both spellings of each: `-m tag` and `-m"tag"`, which unquoting leaves as the
            # single word `-m_`. Only the spaced forms were looked for, so the attached ones
            # were refused for having no message when they carry one.
            case " $* " in
                *' -m '* | *' -m'* | *' --message '* | *' --message='*) : ;;
                *)
                    refuse "\`git stash push\` without a message leaves an entry no session can tell apart from anybody else's." \
                        "Tag it: git stash push -u -m <unique-tag>"
                    ;;
            esac
            ;;
        *) : ;;
    esac
}

# A quoted run of several words is a value, not options.
#
# The words this guard walks are the call split on whitespace with the quotes still in
# them, so a commit message is several words and each is read as a flag: `git commit -m
# "guard: refuse git commit -a"` was refused for an `-a` that was part of a sentence. The
# rule that sorts it out is about the quotes rather than about which option preceded them,
# because there is no reliable way to know: `-m`, `-m"..."`, `--author="..."` and `-m $'...'`
# all carry one, and a path like `-C "../my worktree"` carries one too.
#
# So: a quoted run holding whitespace becomes a single `_`, which is a word and not a flag,
# and a quoted run holding one word becomes that word. The second half matters - `git add
# '*'` is quoted precisely so that git sees the glob rather than the shell, and replacing
# it with `_` would narrow the staging rule to the unquoted spelling.
#
# `_` rather than nothing so that a value goes on being a value: `git -C "../my worktree"
# add -A` has to leave `add` as the subcommand, and a run that vanished would make it `-A`.
# An empty run is the exception, and a shell treats it two ways depending on where it is.
# Inside a word it goes: `git add ''-A` is `git add -A`, and standing in for it with a
# placeholder made that word `_-A`, which is a way past the staging rule. As a whole word
# it stays, as an empty argument - `git --shallow-file '' add -A` passes git an empty path
# and stages everything, so that one has to keep its place or `add` becomes the value.
#
# A backslash spells both halves of that too. Outside quotes it escapes the space rather
# than the word, and `-C ../my\ worktree` is the same one value as `-C "../my worktree"` -
# so the escaped whitespace becomes the same `_`, or the value comes apart into words
# again. Inside `$'...'` it escapes the quote, which a plain single-quoted run never does:
# `$'don\'t refuse -a'` is one word in a shell, and reading that `\'` as the end of the run
# left the rest of the sentence as bare flags.
unquote_values() {
    _rest=$1
    _out=''
    _quote=''
    _escapes=no
    _run=''
    _lone=no
    while [ -n "$_rest" ]; do
        _char=${_rest%"${_rest#?}"}
        _rest=${_rest#?}
        if [ -n "$_quote" ]; then
            # Where a backslash escapes what follows - inside double quotes, and inside
            # `$'...'` - it does not close the run: `-m "the \"guard\" refuses -a"` is one
            # message, not three words, and so is `-m $'the guard\'s -a'`.
            if [ "$_escapes" = yes ] && [ "$_char" = "\\" ] && [ -n "$_rest" ]; then
                _run=$_run${_rest%"${_rest#?}"}
                _rest=${_rest#?}
                continue
            fi
            if [ "$_char" = "$_quote" ]; then
                _quote=''
                case $_run in
                    '')
                        # Kept only where the empty quotes are the whole word: nothing
                        # before them, and nothing after them either.
                        if [ "$_lone" = yes ]; then
                            case ${_rest%"${_rest#?}"} in
                                '' | [[:space:]]) _out=${_out}_ ;;
                                *) : ;;
                            esac
                        fi
                        ;;
                    *[[:space:]]*) _out=${_out}_ ;;
                    *) _out=$_out$_run ;;
                esac
                _run=''
            else
                _run=$_run$_char
            fi
            continue
        fi
        case $_char in
            # Outside quotes a backslash makes the next character ordinary, quotes
            # included. `'don'\''t refuse this -a'` is one word an apostrophe was written
            # into, and reading that `\'` as an opening quote left the rest of the sentence
            # unquoted - so the guard refused a commit message for the `-a` in it.
            "\\")
                if [ -n "$_rest" ]; then
                    _next=${_rest%"${_rest#?}"}
                    _rest=${_rest#?}
                    case $_next in
                        # The escaped character joins the word rather than ending it, so
                        # whitespace takes the same placeholder a quoted run of several
                        # words takes: `git -C ../my\ worktree add -A` has to leave `add`
                        # as the subcommand, and a value split back into two words made it
                        # `worktree`, which matched no rule here.
                        [[:space:]]) _out=${_out}_ ;;
                        *) _out=$_out$_next ;;
                    esac
                else
                    _out=$_out$_char
                fi
                ;;
            # `$'...'` is one word in which a backslash escapes what follows. The `$` is
            # part of the quoting rather than of the value, so it goes with the quotes:
            # left in front, `$'-a'` is the word `$-a`, which is the flag under a spelling
            # no rule here matches.
            '$')
                case $_rest in
                    "'"*)
                        _rest=${_rest#?}
                        _quote="'"
                        _escapes=yes
                        case $_out in
                            '' | *[[:space:]]) _lone=yes ;;
                            *) _lone=no ;;
                        esac
                        ;;
                    *) _out=$_out$_char ;;
                esac
                ;;
            "'" | '"')
                _quote=$_char
                # A backslash is an ordinary character inside single quotes and an escape
                # inside double ones: `'don\t'` is `don\t` and `"don\"t"` is `don"t`. So
                # a `\'` in a single-quoted run closes it, and the words after it are bare.
                case $_char in
                    '"') _escapes=yes ;;
                    *) _escapes=no ;;
                esac
                case $_out in
                    '' | *[[:space:]]) _lone=yes ;;
                    *) _lone=no ;;
                esac
                ;;
            *) _out=$_out$_char ;;
        esac
    done
    # An unterminated quote is the shell's problem, not this one's. Keep what was read so
    # that the words before it are still judged.
    if [ -n "$_quote" ]; then
        case $_run in
            '') : ;;
            *[[:space:]]*) _out=${_out}_ ;;
            *) _out=$_out$_run ;;
        esac
    fi
    printf '%s' "$_out"
}

# `git commit -a` stages every tracked file it finds modified. That is `git add -u` under
# another name, and `check_git_add` refuses that one for putting into a commit what the
# change never touched - so the shorter spelling was the way past a rule this file already
# holds.
check_git_commit_staging() {
    for _word in "$@"; do
        _staged_wide=no
        case $_word in
            --all) _staged_wide=yes ;;
            --*) : ;;
            # -m -F -c -C -t -S take a value, and -u takes one too (`--untracked-files=`).
            -*)
                if cluster_carries "$_word" 'a' 'mFcCtuS'; then
                    _staged_wide=yes
                fi
                ;;
            *) : ;;
        esac
        if [ "$_staged_wide" = yes ]; then
            refuse "\`git commit $_word\` stages every modified file, including ones this change never touched." \
                "Name what you changed - git add app/foo.py - and commit without the flag."
        fi
    done
}

# `make check` is the gate, and skipping it moves the failure to CI at best and to main
# at worst. -n means --no-verify to commit and --dry-run to push, so only commit's is a
# refusal.
check_git_verification() {
    _subcommand=$1
    for _word in "$@"; do
        case $_word in
            --no-verify)
                refuse "\`git $_subcommand --no-verify\` skips the pre-commit hooks that AGENTS.md requires to pass." \
                    "Run make container-check, fix what it reports, and commit without the flag."
                ;;
            -n)
                if [ "$_subcommand" = commit ]; then
                    refuse "\`git commit -n\` skips the pre-commit hooks that AGENTS.md requires to pass." \
                        "Run make container-check, fix what it reports, and commit without the flag."
                fi
                ;;
            *) : ;;
        esac
    done
}

check_git() {
    _git=$(unquote_values "$*")
    set -f
    # shellcheck disable=SC2086  # splitting the call into its words is the point
    set -- $_git
    set +f
    shift

    # git's own options come before the subcommand, and reading the subcommand as whatever
    # word follows `git` misses every one of them: `git -C ../other add -A` had `-C` for its
    # subcommand and matched nothing here, so every rule below was one `-C` away from not
    # applying. `-C <dir>` is the tidier way to work in a linked worktree than `cd &&`, which
    # made the better-written form the unchecked one.
    #
    # The subcommand is the first word that is not an option, so anything else beginning
    # with `-` is skipped rather than stopping the walk. Stopping made the fix one flag
    # wide instead of none: `git --no-optional-locks add -A` broke off at a word this list
    # had not heard of, read `--no-optional-locks` as the subcommand, matched nothing, and
    # ran. The named ones are those whose value is the next word rather than attached to
    # it - `git --attr-source HEAD add -A` has to skip the `HEAD` too, or that becomes the
    # subcommand. The list is `handle_options()` in git.c, which is where it had to be read
    # from: the manual page is not a list of which options take a separated value, and
    # taking it for one left out `--shallow-file`, whose omission put `git --shallow-file
    # '' add -A` - which does stage everything - straight past every rule here. It also
    # names `--exec-path` as taking a path, which it does not take separated: `git
    # --exec-path add -A` prints the exec path and stages nothing.
    #
    # An option git adds later that takes a separated value is the one shape still
    # unaccounted for. Nothing here can notice that on its own.
    while [ "$#" -gt 0 ]; do
        case $1 in
            -C | -c | --git-dir | --work-tree | --namespace | --attr-source | --config-env | --super-prefix | --shallow-file)
                shift
                [ "$#" -gt 0 ] && shift
                ;;
            -?*) shift ;;
            *) break ;;
        esac
    done

    case ${1:-} in
        add)
            shift
            check_git_add "$@"
            ;;
        stash)
            shift
            check_git_stash "$@"
            ;;
        commit)
            check_git_verification "$@"
            shift
            check_git_commit_staging "$@"
            ;;
        push) check_git_verification "$@" ;;
        *) : ;;
    esac
}

# The line AGENTS.md requires at the end of anything a session posts, naming the agent and
# the account it went up under.
#
# Anchored where the line starts and nowhere else. What is read here is shell source rather than
# the comment that comes out of it, so the line the signature is on ends in whatever the command
# needed next - the closing quote, then `--label enhancement`, a redirection, a `&&`. Requiring
# the signature to end the line refused every one of those, and `gh issue create --body "..."
# --label x` is an ordinary thing to write. Starting the line is what "a line of its own" means;
# what the shell puts after it is the shell's business.
ATTRIBUTION="^Posted by .+ on behalf of @[A-Za-z0-9][A-Za-z0-9-]*"

# How many comments this call has been found to post. More than one is refused rather than read,
# because a payload-wide search cannot tell which body a signature belongs to: two comments and
# one signature would pass, and signing one of two is the likely lapse rather than an invented
# one. Posting them separately makes each answerable on its own.
#
# Counted as the parts are read rather than by looking for the command in the text. Text is not
# a command: a comment explaining how to reply, quoting `gh issue comment` on a line of its own,
# is not a second post, and refusing the whole - correctly signed - call for it would be wrong. It
# is the same mistake as reading a heredoc body as commands, and `check_line` walks the parts in this
# shell, so counting where they are dispatched costs nothing and cannot see prose.
_posts_seen=0

# Whether the text about to be posted carries that line.
#
# The line is looked for in the whole call, and that is deliberate: the usual shape here writes
# the body with a heredoc and posts it in the same call, so the file `--body-file` names does not
# exist yet when this runs. Scoping the search to the argument would refuse the one shape a long
# comment is actually written in. Where the paths come from is a different question, and
# `body_files` answers it more carefully.
#
# Two things it therefore does not catch, both written down rather than left to be found: an
# unsigned comment in a call that says the line somewhere else - `... --body "no sig"; echo
# "Posted by ..."` - and one whose own body text names a path that happens to hold a signed file.
# Each is a sentence somebody had to write on purpose. This guard is here to stop a lapse rather
# than a decision, and every attempt to close the second cost more than it saved: scoping the
# search refused the heredoc shape, and skipping files once `--body` appeared refused an issue
# whose *title* mentioned `--body`. The count above closes the case that is a lapse.
#
# A path it cannot resolve is not a refusal. `--body-file "$BODY"` names a file whose name this
# does not know, so there is nothing to read and nothing to say about it; refusing there would
# block a shape that is doing everything right.
signed_off() {
    if printf '%s\n' "$posted_text" | grep -Eq "$ATTRIBUTION"; then
        return 0
    fi
    # `--body "$(cat notes.md)"` is refused, and that is the lesser of two wrongs. Treating a
    # body the shell has still to build as unreadable meant asking whether the value held a `$`,
    # and prose holds them all the time - "costs $5 a month", "echo exit=$?" - so the rule
    # switched itself off for half the comments this repository writes. A path is different:
    # `$` in one is a variable, not a sentence, which is why the check below keeps it.
    # Read a line at a time: a path may have a space in it, and splitting on whitespace tested
    # half of one and reported the file missing. The subshell says how it went by exiting, and
    # says so explicitly - an `if` whose condition is false returns 0, which would have made an
    # exhausted loop read as a signature found.
    body_files | {
        while IFS= read -r _file; do
            case $_file in
                *'$'*) exit 0 ;;
                *) : ;;
            esac
            if [ -f "$_file" ] && grep -Eq "$ATTRIBUTION" "$_file"; then
                exit 0
            fi
        done
        exit 1
    }
}

# Every file this call would take a body from, as a path that can be tested.
#
# Read from the command with the heredoc bodies dropped, not from the text: a path written inside
# a comment is prose, and reading it as an argument let an unsigned comment be signed by whatever
# file its own words happened to name.
#
# What a path arrives wrapped in is not part of it: `--body-file "/tmp/c.md"` keeps the shell's
# quotes, and a path tested with them still attached reads a file that is there and signed as a
# file that is not there at all. Every occurrence is taken, not the last one on a line.
#
# Matched rather than split into fields, because a quoted path may have a space in it and fields
# end at one: `--body-file "/tmp/some dir/body.md"` was read as `"/tmp/some` and reported missing.
body_files() {
    printf '%s\n' "$command" | awk '
        {
            line = $0
            while (match(line, /(--body-file|-F)[=[:space:]]+("[^"]*"|\047[^\047]*\047|[^[:space:];|&]+)/)) {
                value = substr(line, RSTART, RLENGTH)
                line = substr(line, RSTART + RLENGTH)
                sub(/^(--body-file|-F)[=[:space:]]+/, "", value)
                # The separator first: a newline became a `;` when this was read, so the token
                # ends `"...";` and a quote stripped at end-of-string would find nothing there.
                sub(/[;|&].*$/, "", value)
                gsub(/^["\047]|["\047]$/, "", value)
                if (value != "") {
                    print value
                }
            }
        }
    '
}

# A comment goes up under somebody's account, and the account is the only signature on it
# unless the text says otherwise.
#
# Only where the session is supplying the text. `gh pr create` with no body flag opens an
# editor for a person to type in, and there is nothing here to check - refusing that would
# be the guard stopping a human from writing their own words.
check_gh() {
    shift
    # `gh --repo o/r issue comment ...` puts a flag where the noun was expected, and gh takes it.
    # Reading only the first two words after `gh` missed every one of those.
    while [ "$#" -gt 0 ]; do
        case ${1:-} in
            *=*) shift ;;
            # the flag and the word it takes: `--repo o/r issue comment` is three words before
            # the noun, and stopping at the first that is not a flag stopped on the repository
            -?*)
                shift
                [ "$#" -gt 0 ] && shift
                ;;
            *) break ;;
        esac
    done
    case "${1:-} ${2:-}" in
        # `gh api` reaches the same comments, and is the only way one is edited. It is not covered:
        # that branch is hard to write without confusing a command with text that looks like one.
        # The gap is known.
        'issue comment' | 'issue create' | 'issue edit' | 'pr comment' | 'pr create' | 'pr edit' | 'pr review') : ;;
        *) return 0 ;;
    esac
    # `-b` and `-F` take their value attached as well as apart - `-bFixed.`, `-b=Fixed.`,
    # `-F=body.md` are all read by gh, and matching only the spaced form let each of them past
    # without the rule ever running.
    case " $* " in
        *' --body '* | *' --body='* | *' --body-file '* | *' --body-file='* | *' -b'* | *' -F'*) : ;;
        *) return 0 ;;
    esac
    _posts_seen=$((_posts_seen + 1))
    if [ "$_posts_seen" -gt 1 ]; then
        refuse "This call posts more than one comment, and one signature cannot answer for two." \
            "Post them one at a time, each ending with its own:

  Posted by Claude on behalf of @<username>"
    fi
    if signed_off; then
        return 0
    fi
    refuse "\`gh ${1:-} ${2:-}\` is posting text with no line saying who wrote it." \
        "End the body with, on a line of its own:

  Posted by Claude on behalf of @<username>

AGENTS.md, \"Writing on an issue or a pull request\"."
}

# The words of $1 as a shell reads them, one to a line: split on whitespace outside quotes, the quotes
# gone, a backslash outside single quotes taking the next character as it is. The words this guard
# otherwise walks keep their quotes and split inside them, and a value cannot be checked that way:
# `ARGS='tests/x.py -k rename -q'` is one value, and what it holds is what make is given.
#
# An unquoted `<` or `>` ends the word before it and is a redirection, printed as $REDIRECTED with its
# target on the next line; a descriptor in front of it (`2>`, `&>`) is part of it. A quoted one is text:
# `ARGS='-k ">"'` is one value. An unquoted `#` that starts a word ends the line.
REDIRECTED='__bat_redirection__'
shell_words() {
    _rest=$1
    _word=''
    _quote=''
    _any=no
    _literal=no
    while [ -n "$_rest" ]; do
        _char=${_rest%"${_rest#?}"}
        _rest=${_rest#?}
        if [ "$_quote" = "'" ]; then
            if [ "$_char" = "'" ]; then _quote=''; else _word=$_word$_char; fi
        elif [ "$_quote" = '"' ]; then
            if [ "$_char" = '"' ]; then
                _quote=''
            elif [ "$_char" = "\\" ] && [ -n "$_rest" ]; then
                _word=$_word${_rest%"${_rest#?}"}
                _rest=${_rest#?}
            else
                _word=$_word$_char
            fi
        else
            case $_char in
                [[:space:]])
                    [ "$_any" = yes ] && printf '%s\n' "$_word"
                    _word=''
                    _any=no
                    _literal=no
                    ;;
                '#')
                    if [ "$_any" = no ]; then
                        return 0
                    fi
                    _word=$_word$_char
                    ;;
                '<' | '>')
                    case $_literal:$_word in
                        no: | no:[0-9] | no:[0-9][0-9] | 'no:&') : ;;
                        *) printf '%s\n' "$_word" ;;
                    esac
                    printf '%s\n' "$REDIRECTED"
                    while :; do
                        case $_rest in
                            ['<>&|']*) _rest=${_rest#?} ;;
                            *) break ;;
                        esac
                    done
                    _word=''
                    _any=no
                    _literal=no
                    ;;
                "'" | '"') _quote=$_char; _any=yes; _literal=yes ;;
                \\)
                    _word=$_word${_rest%"${_rest#?}"}
                    _rest=${_rest#?}
                    _any=yes
                    _literal=yes
                    ;;
                *) _word=$_word$_char; _any=yes ;;
            esac
        fi
    done
    [ "$_any" = yes ] && printf '%s\n' "$_word"
    return 0
}

# Why one `NAME=value` given to a make wrapper - after the target, or in front of `make` - is refused,
# or nothing where it is not. An allowlist, because make has more ways to run what it is given than a
# list of them can hold: a second target (`make container-check test` is the host's `make test`), any
# flag in any abbreviation (`--ev=`, `-nf other.mk`, `-C dir`), `MAKEFILES=`, `X != cmd`, `SHELL:=`.
# Each value is held to how its recipe uses it: ARGS goes into the recipe inside double quotes, so a
# `"`, `$`, backtick or backslash reaches the host's shell; FILTER, KEEP_* and the rest go in bare.
make_assignment_refusal() {
    _name=${1%%=*}
    _value=${1#*=}
    case $1 in
        *=*) : ;;
        *) printf '%s' "\`$1\` is a target or a flag, and a make wrapper takes only its target and NAME=value" && return 0 ;;
    esac
    case $_name in
        ARGS)
            case $_value in
                *[\"\$\`\\]*) printf '%s' "ARGS goes into the recipe in double quotes, and \`$_value\` reaches past them" ;;
                *) : ;;
            esac
            ;;
        FILTER)
            case $_value in
                *[!A-Za-z0-9_./\ -]*) printf '%s' "FILTER is spec names, and \`$_value\` is more" ;;
                *) : ;;
            esac
            ;;
        # the container scripts run DEVKIT_RUNTIME as a program, so it names one of the two runtimes
        DEVKIT_RUNTIME)
            case $_value in
                podman | docker) : ;;
                *) printf '%s' "DEVKIT_RUNTIME is podman or docker, and \`$_value\` is a program run on the host" ;;
            esac
            ;;
        # The list is the kit's, and a repository cannot add to it: a name it could add is `SHELL` or `PATH`. Each
        # DEVKIT_ name here is a setting a person changes for one run, and scripts/guard-names.test.sh holds the
        # list to the scripts and says why each name that is not here is not.
        # the DEVKIT_* names the scripts read, and no others: a pattern let `DEVKIT_X$(...)` and `DEVKIT_X!` through
        KEEP_PORT | KEEP_NAME | PYTEST_WORKERS | QUALITY_REF | MINUTES_READING_REF | SNAPSHOT_REF | FAST_BASE | \
            CHECK_REF | DEVKIT_CONTAINER_NAME | DEVKIT_DETACH | DEVKIT_E2E_HOST | DEVKIT_E2E_NETWORK | DEVKIT_E2E_NODE_VOLUME | \
            DEVKIT_E2E_SERVER | DEVKIT_E2E_URL | DEVKIT_E2E_WORKERS | DEVKIT_IMAGE | DEVKIT_KEEP | DEVKIT_MEMORY | \
            DEVKIT_NODE_INSTALL | DEVKIT_NODE_VOLUME | DEVKIT_PLAYWRIGHT_IMAGE | DEVKIT_PORT | DEVKIT_PYTHON | DEVKIT_SIZE_WORKERS | \
            DEVKIT_TIMEOUT | DEVKIT_VENV_VOLUME)
            case $_value in
                *[!A-Za-z0-9_./:~^-]*) printf '%s' "$_name is a plain value, and \`$_value\` is more" ;;
                *) : ;;
            esac
            ;;
        KEEP_CMD)
            # a script, or `sh -c "..."` with nothing in it that leaves the double quotes - the shape
            # the Makefile itself gives, whose `&&` runs in the container
            case $_value in
                *[!A-Za-z0-9_./-]*)
                    _inner=${_value#sh -c \"}
                    _inner=${_inner%\"}
                    case $_value:$_inner in
                        sh\ -c\ \"*\":*[\"\$\`\\]*) printf '%s' "KEEP_CMD's sh -c reaches past its double quotes" ;;
                        sh\ -c\ \"*\":*) : ;;
                        *) printf '%s' "KEEP_CMD is a script, or sh -c \"...\", and \`$_value\` is neither" ;;
                    esac
                    ;;
                *) : ;;
            esac
            ;;
        *) printf '%s' "\`$_name\` is no value a make wrapper here takes" ;;
    esac
    return 0
}

# A make wrapper given anything but its target and the values above runs it on the host. $1 is the
# whole part as written, so the assignments in front of `make` - which strip_prefixes reads past, and
# which make reads as its own - are held to the same rules. What this cannot see is a variable exported
# earlier on the line or in the session: `export FILTER=...; make e2e-quick`.
check_make_arguments() {
    _make_words=$(shell_words "$1")
    _past_make=no
    _past_target=no
    _redirected=no
    while IFS= read -r _word; do
        # a redirection is the shell's, not make's, and so is the file it names
        if [ "$_redirected" = yes ]; then
            _redirected=no
            continue
        fi
        case $_word in
            "$REDIRECTED")
                _redirected=yes
                continue
                ;;
            # what a command printed is no value anybody has read: `ARGS="$(...)"`
            *"$SUBSTITUTED"*) _why="\`$_word\` holds what a command prints" ;;
            *) _why='' ;;
        esac
        if [ -n "$_why" ]; then
            :
        elif [ "$_past_make" = no ]; then
            case $_word in
                make | */make | gmake | */gmake) _past_make=yes ;;
                # an assignment the shell hands make; anything else here is a runner and its
                # options, which strip_prefixes has read past: `timeout --signal=KILL 600`
                [A-Za-z_]*=*)
                    case ${_word%%=*} in
                        *[!A-Za-z0-9_]*) : ;;
                        *) _why=$(make_assignment_refusal "$_word") ;;
                    esac
                    ;;
                *) : ;;
            esac
        elif [ "$_past_target" = no ]; then
            _past_target=yes
            # check_part matched it as `container-*`, which `container-check=1` is too
            case $_word in
                *[!a-z0-9-]*) _why="\`$_word\` is no target name" ;;
                *) : ;;
            esac
        else
            _why=$(make_assignment_refusal "$_word")
        fi
        if [ -n "${_why:-}" ]; then
            refuse "A make wrapper is given something it would run on the host: $_why." \
                "Give it its target and plain values: make container-pytest ARGS='tests/test_x.py -q'"
        fi
    done <<EOF
$_make_words
EOF
}

# The container wrappers also read DEVKIT_ISOLATED, and the first word of
# `DEVKIT_ISOLATED=1 scripts/container.sh make test` is the wrapper the container rule asks for.
# Left alone, that is the toolchain on a host that said nothing of the kind. So where the
# environment has not set it, a command may not.
#
# The ways of setting it are too many to list - `env`, `nice`, `sudo -u`, `bash -lc`, `eval`,
# a `make` argument, a quoted name - and a list that omits one is a list that is walked past.
# So the question is turned round: a part that names `DEVKIT_ISOLATED` at all is refused, unless
# what it runs is something that only reads text, where the word is a message or a pattern
# (`git commit -m`, `gh ... --body`, `grep`). The bare name and not `DEVKIT_ISOLATED=`, because
# `DEVKIT_ISOLATED+=1`, `read DEVKIT_ISOLATED` and `printf -v DEVKIT_ISOLATED` set it without that text.
# Quotes and backslashes are taken out first, so `"DEVKIT_ISOLATED=1"` and `DEVKIT_ISOLATED\=1` are
# the same word as the bare one; `env -u DEVKIT_ISOLATED`, which asks for a container, is let by.
# `git` and `gh` can run a shell - an alias, `-c core.pager=...`, `rebase -x`, `bisect run` -
# so they count as readers for the subcommands that only read or write text, and for no
# other (see `text_subcommand`). What this cannot see: a script written to a file
# and run afterwards, a word that a command substitution prints (`env $(echo DEVKIT_ISOLATED=1)`),
# which the guard reads as one opaque word everywhere - and reading inside one would refuse
# every commit message that mentions the variable - and a name assembled from pieces.
#
# Whether a `git` or `gh` part ($1, the whole part) is a subcommand that only reads or
# writes text. The options before the subcommand are read for what they are: `-C` takes a
# path, `-c` and `--config-env` run configuration, which can name a shell. `gh`'s `alias`,
# `extension` and `config` are not in the list.
text_subcommand() {
    set -f
    # shellcheck disable=SC2086  # splitting the part into its words is the point
    set -- $1
    set +f
    _tool=${1##*/}
    shift
    while [ "$#" -gt 0 ]; do
        case $1 in
            -C) shift ;;
            -c | -c* | --config-env* | --exec-path* | --git-dir* | --work-tree*) return 1 ;;
            -*) : ;;
            *) break ;;
        esac
        shift
    done
    case $_tool:${1:-} in
        git:grep)
            shift
            ! grep_runs_a_shell "$@"
            ;;
        git:log | git:show | git:diff | git:status | git:commit | git:branch | \
            git:rev-parse | git:ls-files | git:blame | git:tag | git:notes | git:describe | \
            git:shortlog | git:cat-file | git:merge-base | git:rev-list | git:add) return 0 ;;
        gh:pr | gh:issue | gh:run | gh:api | gh:release | gh:search) return 0 ;;
        *) return 1 ;;
    esac
}

# Whether `git grep`'s options ($@, the words after `grep`) hand something to a shell:
# `-O`/`--open-files-in-pager`, in any abbreviation git accepts (`--op`) or cluster (`-nO...`),
# and a word the shell expands (`$opt`), which could be either. git grep takes its options
# first and refuses one after a pattern or a path, so the scan stops at the first word that is
# not an option, and at `--`. A value an option takes is not an option: `-e -DEVKIT_ISOLATED` is
# a pattern. A long option this does not know may take the next word as its value, so that word
# does not end the scan.
grep_runs_a_shell() {
    _value=no
    for _word in "$@"; do
        case $_value in
            yes)
                _value=no
                continue
                ;;
            maybe) _maybe=yes ;;
            *) _maybe=no ;;
        esac
        _value=no
        case $_word in
            --) return 1 ;;
            '$'*) return 0 ;;
            --op*) return 0 ;;
            --regexp | --file | --max-depth | --threads | --context | --after-context | \
                --before-context | --max-count) _value=yes ;;
            --*=*) : ;;
            --*) _value=maybe ;;
            -?*)
                # -e -f -A -B -C -m take a value: attached, or the next word when last
                if cluster_carries "$_word" O efABCm; then
                    return 0
                fi
                _letters=${_word#-}
                _before=${_letters%%[efABCm]*}
                if [ "$_before" != "$_letters" ] && [ -z "${_letters#"$_before"?}" ]; then
                    _value=yes
                fi
                ;;
            *) [ "$_maybe" = yes ] || return 1 ;;
        esac
    done
    return 1
}

check_isolated_assignment() {
    if isolated; then
        return 0
    fi
    # `-u NAME` only as its own words: stripped from the whole part, it ate the end of
    # `X=-u DEVKIT_ISOLATED=1`, which is two assignments, and left `X==1`.
    _flat=$(printf '%s' "$1" | tr -d "'\"\\\\" |
        sed -E 's/(^|[[:space:]])-u[[:space:]]*DEVKIT_ISOLATED([[:space:]]|$)/\1\2/g')
    case $_flat in
        *DEVKIT_ISOLATED*) : ;;
        *) return 0 ;;
    esac
    if reads_text "$_flat"; then
        return 0
    fi
    refuse "DEVKIT_ISOLATED is the environment's to set, and this command sets it." \
        "A disposable environment sets it where the agent is started (for isx, the
template's env). Without it, use the wrappers as they are:
  make container-check | make container-test | make container-quality

AGENTS.md, \"Everything runs in a container\"."
}

# Whether a part ($1, quotes already taken out) runs something that only reads text, where a word
# is a message or a pattern rather than an assignment.
reads_text() {
    _trimmed=$1
    while :; do
        case $_trimmed in
            [[:space:]]* | '('* | '{'* | '!'*) _trimmed=${_trimmed#?} ;;
            *) break ;;
        esac
    done
    _head=${_trimmed%%[[:space:]]*}
    case ${_head##*/} in
        git | gh) text_subcommand "$_trimmed" ;;
        # not `printf`: `printf -v DEVKIT_ISOLATED 1` assigns it, and `set -a` exports it
        grep | rg | echo | cat | jq | head | tail | ls | wc | diff) return 0 ;;
        *) return 1 ;;
    esac
}

# allexport exports every variable assigned after it, and then a reader can assign DEVKIT_ISOLATED
# without naming it the way a setter does - `echo ${DEVKIT_ISOLATED:=1}`, an arithmetic `$[...]`,
# an array subscript - and the wrapper after it reads it from the environment. Every other way
# of exporting it names it in a part that is not a reader, which check_isolated_assignment
# refuses. So where the environment has not set it, allexport is refused in every spelling:
# `set -a` or a cluster holding `a` (`set -euao pipefail`), the same flag to a shell (`bash -a`),
# and the word `allexport` (`set -o allexport`, `SHELLOPTS=allexport`) outside a reader.
check_allexport() {
    if isolated; then
        return 0
    fi
    case $(printf '%s' "$1" | tr -d "'\"\\\\") in
        *allexport*)
            if ! reads_text "$(printf '%s' "$1" | tr -d "'\"\\\\")"; then
                refuse_allexport
            fi
            ;;
        *) : ;;
    esac
    _words=$(unquote_values "$1")
    set -f
    # shellcheck disable=SC2086  # splitting the part into its words is the point
    set -- $_words
    set +f
    _at_command=yes
    while [ "$#" -gt 0 ]; do
        _word=$1
        shift
        if [ "$_at_command" = yes ]; then
            case ${_word##*/} in
                set | sh | bash | dash | ash | zsh | ksh)
                    if exports_all "$@"; then
                        refuse_allexport
                    fi
                    return 0
                    ;;
                # what may stand before a command: a keyword, a prefix, and the prefix's options
                then | do | else | elif | if | while | until | '!' | '{' | time | exec | command | \
                    builtin | sudo | env | nohup | timeout | nice | stdbuf | *=* | -* | [0-9]*) : ;;
                *) _at_command=no ;;
            esac
        fi
    done
}

# Whether the options of `set` or of a shell ($@) turn allexport on: a short cluster holding `a`.
# The options end at `--`, `-`, and the first word that is not one.
exports_all() {
    for _word in "$@"; do
        case $_word in
            -- | -) return 1 ;;
            --*) : ;;
            -*a*) return 0 ;;
            -* | +*) : ;;
            *) return 1 ;;
        esac
    done
    return 1
}

refuse_allexport() {
    refuse "allexport (\`set -a\`, \`set -o allexport\`, \`bash -a\`) exports whatever is assigned after it, DEVKIT_ISOLATED included, under spellings this guard cannot read as setting it." \
        "Export what a command needs by name, or pass it on the command line:
  FOO=1 scripts/container.sh make test

AGENTS.md, \"Everything runs in a container\"."
}

# The command string a shell is handed with `-c`, from the raw part ($1, the shell's name first),
# or nothing. `-c` may share a cluster - `bash -lc '...'`, `sh -ec '...'` - and other options may
# stand before it, `-o pipefail` and `--rcfile file` taking a word of their own. The string is the
# first word after the options; a word before any `-c` is a script, and then there is no string.
command_string() {
    _rest=$1
    _seen_c=no
    _skip=no
    while :; do
        case $_rest in
            *[[:space:]]*) _rest=${_rest#*[[:space:]]} ;;
            *) return 0 ;;
        esac
        _rest=${_rest#"${_rest%%[![:space:]]*}"}
        _word=${_rest%%[[:space:]]*}
        if [ "$_skip" = yes ]; then
            _skip=no
            continue
        fi
        case $_word in
            '') return 0 ;;
            --rcfile | --init-file) _skip=yes ;;
            --*) : ;;
            [-+]*)
                case $_word in
                    -*c*) _seen_c=yes ;;
                    *) : ;;
                esac
                case $_word in
                    *[oO]) _skip=yes ;;
                    *) : ;;
                esac
                ;;
            *)
                if [ "$_seen_c" = yes ]; then
                    printf '%s' "$_rest"
                fi
                return 0
                ;;
        esac
    done
}

check_part() {
    _whole=$1
    check_isolated_assignment "$1"
    check_allexport "$1"
    _stripped=$(strip_prefixes "$1")

    # A shell handed a command string is running that command: `sh -c 'pytest tests'` on
    # the host is pytest on the host, and reading only the first word would let every
    # rule here be stepped around by one wrapper. What follows -c goes back through the
    # same reading, which settles the nested case with it. This never looks inside
    # `scripts/container.sh sh -c '...'`, whose first word is the wrapper and which is
    # the shape the container rule asks for.
    _head=${_stripped%%[[:space:]]*}
    case ${_head##*/} in
        sh | bash | dash | ash | zsh | ksh)
            _inner=$(command_string "$_stripped")
            if [ -n "$_inner" ]; then
                # The command string is quoted as a whole, and the quotes around it
                # are the shell's rather than part of what runs.
                case $_inner in
                    "'"*"'" | '"'*'"')
                        _inner=${_inner#?}
                        _inner=${_inner%?}
                        ;;
                    *) : ;;
                esac
                check_line "$_inner"
                return 0
            fi
            ;;
        *) : ;;
    esac

    set -f
    # shellcheck disable=SC2086  # splitting the part into its words is the point
    set -- $_stripped
    set +f
    if [ "$#" -eq 0 ]; then
        return 0
    fi
    # the command as the shell runs it: `\uv`, `'uv'`, `u""v` and `"make"` are uv and make
    _command=$(printf '%s' "$1" | tr -d "\\\\'\"")
    case ${_command##*/} in
        eval)
            refuse "\`eval\` runs a command this guard cannot read before it runs." \
                "Write the command out: every rule here reads the words it is given."
            ;;
        \$* | "$SUBSTITUTED"*)
            refuse "A command named by a variable or by what another command prints (\`$_command\`) is one this guard cannot read before it runs." \
                "Write the command out: every rule here reads the words it is given."
            ;;
        uv | uvx | yarn | npm | npx | pnpm | node | python | python3 | pytest | prek | ruff | pyright | eslint | vitest | playwright | corepack | vite)
            if ! isolated; then
                refuse "\`${1##*/}\` must not run on the host: AGENTS.md keeps every build, linter and test inside the dev container." \
                    "Run it there instead:
  scripts/container.sh uv run pytest tests/api/test_thing.py -q
  scripts/container.sh sh -c 'cd assets && yarn test'
  make container-check | make container-test | make container-quality"
            fi
            ;;
        make | gmake)
            # `make container-*` and `make e2e-test` are the container wrappers
            # themselves. Every other target runs the toolchain right here.
            case ${2:-} in
                # the container wrappers AGENTS.md lists: everything container-*, and the
                # three e2e targets - `make e2e-test`, `make e2e-quick FILTER=...`, and
                # `make e2e-typecheck`, which runs tsc in the Playwright image
                container-* | e2e-test | e2e-quick | e2e-typecheck) check_make_arguments "$_whole" ;;
                *)
                    if isolated; then
                        return 0
                    fi
                    refuse "\`make ${2:-}\` runs the toolchain on the host." \
                        "Use the wrapper that runs it in the container: any make container-*, and
  make e2e-test | make e2e-quick FILTER=... | make e2e-typecheck"
                    ;;
            esac
            ;;
        git) check_git "$@" ;;
        gh) check_gh "$@" ;;
        # `xargs git add` stages the names a command printed, which is `git add $(...)` with
        # the list arriving down a pipe instead of on the line - so it is read as that.
        xargs)
            shift
            while [ "$#" -gt 0 ] && [ "${1##*/}" != git ]; do
                shift
            done
            if [ "$#" -gt 0 ]; then
                check_git "$@" "$SUBSTITUTED"
            fi
            ;;
        *) : ;;
    esac
}

# The body of a command substitution whose opening has just been read, taken off the front of
# $_rest into $_body; $1 is what closes it. Brackets nest and quotes hide them, so `$(echo
# "(" $(date))` ends at its last bracket. A backtick does not nest, and its body runs to the
# next one. One that never closes runs to the end of the line, as the shell would read it.
take_substitution() {
    _body=''
    _depth=1
    _inner_quote=''
    while [ -n "$_rest" ]; do
        _char=${_rest%"${_rest#?}"}
        _rest=${_rest#?}
        if [ -n "$_inner_quote" ]; then
            [ "$_char" = "$_inner_quote" ] && _inner_quote=''
        elif [ "$1" = '`' ]; then
            [ "$_char" = '`' ] && return 0
        else
            case $_char in
                "'" | '"') _inner_quote=$_char ;;
                '(') _depth=$((_depth + 1)) ;;
                ')')
                    _depth=$((_depth - 1))
                    [ "$_depth" -eq 0 ] && return 0
                    ;;
                *) : ;;
            esac
        fi
        _body=$_body$_char
    done
}

# Each command on the line is judged on its own: a `&&` chain is as much a way to run
# pytest on the host as running it alone, while `scripts/container.sh sh -c '...'` has to
# stay allowed even though the string it passes along names uv. Splitting on the
# separators and reading the first word of each part gets both right.
#
# A separator inside quotes is not one: the string handed to a shell is a single argument,
# and `scripts/container.sh sh -c 'cd assets && yarn test'` - the form AGENTS.md asks
# for, and the one this file's own message suggests - has to survive as one part, so that
# its first word is the wrapper. Splitting through the quotes cut it in half and refused
# the tail as `yarn` run on the host: the guard refusing what the repository asks for.
#
# check_part calls back into this for the string a shell is handed, so the two recurse.
# It terminates because each round consumes at least the shell and its -c.
# The command split on `;`, `|` and `&`, one part per line, leaving a separator inside
# single or double quotes where it is. Walks the string a character at a time, which is
# slow for its length and irrelevant at the length of a command line.
#
# A command substitution - `$(...)`, or backticks - is a command too, and outside single
# quotes it runs where it stands, inside double quotes included; one in an unquoted heredoc
# body runs as well, and is not read, since the bodies are dropped. Its body is split the same
# way and its parts follow the line's own. In the line it leaves $SUBSTITUTED where it stood,
# rather than ending a part at its brackets: that cut `git add $(git diff --name-only)` into
# `git add $` and the diff, and every staging rule read the first as naming nothing.
split_on_separators() {
    _rest=$1
    _out=''
    _substituted=''
    _quote=''
    _newline='
'
    while [ -n "$_rest" ]; do
        _char=${_rest%"${_rest#?}"}
        _rest=${_rest#?}
        _closing=''
        if [ "$_quote" != "'" ]; then
            # the character after a backslash is text: `\$(` and `\`` open nothing
            case $_char in
                \\)
                    _out=$_out$_char${_rest%"${_rest#?}"}
                    _rest=${_rest#?}
                    continue
                    ;;
                '$')
                    case $_rest in
                        '('*)
                            _closing=')'
                            _rest=${_rest#?}
                            ;;
                        *) : ;;
                    esac
                    ;;
                '`') _closing='`' ;;
                *) : ;;
            esac
        fi
        if [ -n "$_closing" ]; then
            take_substitution "$_closing"
            # in a subshell, so that the call cannot reach this one's state
            _substituted=$_substituted$_newline$(split_on_separators "$_body")
            _out=$_out$SUBSTITUTED
            continue
        fi
        if [ -n "$_quote" ]; then
            if [ "$_char" = "$_quote" ]; then
                _quote=''
            fi
        else
            case $_char in
                "'" | '"') _quote=$_char ;;
                # `2>&1`, `>&2` and `&>` are redirections, not the end of a command: split
                # there, `make container-check 2>&1 test` left `test` a command of its own
                '&')
                    case ${_out#"${_out%?}"}${_rest%"${_rest#?}"} in
                        '<'* | '>'* | *'>') : ;;
                        *) _char=$_newline ;;
                    esac
                    ;;
                # `(` and `)` group commands, so they end one as surely as `;` does. Left in
                # place they weld the bracket onto the word beside it: `(git add -A)` is one
                # command whose first word is `(git`, which is not `git`, so the staging rule
                # never looks at it - and `(... && make e2e-typecheck)` is refused, because
                # `e2e-typecheck)` is not a target name.
                ';' | '|' | '(' | ')') _char=$_newline ;;
                *) : ;;
            esac
        fi
        _out=$_out$_char
    done
    printf '%s%s' "$_out" "$_substituted"
}


# The exit status of a pipeline is its LAST command's, so `make container-check | tail`
# reports tail's - which is 0 whether the check passed or failed. A suite that failed one
# test of 336 then reads as one that passed, and it is read that way, because the number
# is the thing a reader trusts over the text scrolling above it.
#
# Only the wrappers are guarded, and only when the pipeline ends in head or tail: those
# are the runs long enough to want to trim and slow enough that nobody runs them twice to
# check. A pipeline ending in grep is left alone - it discards the status too, but it is
# how half of every shell session is written, and a rule nobody can follow is switched off.
is_status_carrying_wrapper() {
    case $1 in
        make | gmake)
            case $2 in
                container-* | e2e-*) return 0 ;;
                *) return 1 ;;
            esac
            ;;
        # the leading path is already stripped by the caller, as it is everywhere else here
        container.sh | e2e-container.sh | e2e-typecheck.sh) return 0 ;;
        *) return 1 ;;
    esac
}


# Whether a pipeline ending here threw away a status worth having, and refuse if it did.
# Reads $_pipeline and $_saw_wrapper, and clears both.
end_of_pipeline() {
    _cmd=$(strip_prefixes "$_pipeline")
    set -f
    # shellcheck disable=SC2086  # splitting the command into its words is the point
    set -- $_cmd
    set +f
    if [ "$_saw_wrapper" -eq 1 ] && [ "$#" -gt 0 ]; then
        case ${1##*/} in
            head | tail)
                refuse "A pipeline ending in \`${1##*/}\` reports \`${1##*/}\`'s exit status, not the command's - so a run that failed reads as one that passed." \
                    "Send it to a file and read the file:
  make container-check > /tmp/check.log 2>&1; echo \"exit=\$?\"
  tail -40 /tmp/check.log

The e2e wrappers also end with a line saying PASSED or FAILED, which does survive a pipe."
                ;;
            *) : ;;
        esac
    fi
    _saw_wrapper=0
    _pipeline=''
}

# One command in a pipeline has ended at a `|`. Remember whether it was a wrapper whose
# status is the answer, since by the end of the pipeline the text of it is long gone.
end_of_segment() {
    _cmd=$(strip_prefixes "$_pipeline")
    set -f
    # shellcheck disable=SC2086  # splitting the command into its words is the point
    set -- $_cmd
    set +f
    if [ "$#" -gt 0 ] && is_status_carrying_wrapper "${1##*/}" "${2:-}"; then
        _saw_wrapper=1
    fi
    _pipeline=''
}

check_pipeline() {
    # `|` continues a pipeline; `;`, `&&`, `||` and a bare `&` end one. Rebuilt here from
    # the raw line rather than taken from split_on_separators, which throws away which
    # separator it was - and that is the whole question this asks.
    #
    # The terminator appended to the line is what flushes the last pipeline, so that the
    # loop has one way out and one way of ending a pipeline rather than two of each.
    _rest="$1;"
    _pipeline=''
    _quote=''
    _saw_wrapper=0

    while [ -n "$_rest" ]; do
        _char=${_rest%"${_rest#?}"}
        _rest=${_rest#?}

        if [ -n "$_quote" ]; then
            [ "$_char" = "$_quote" ] && _quote=''
            _pipeline=$_pipeline$_char
            continue
        fi

        case $_char in
            "'" | '"')
                _quote=$_char
                _pipeline=$_pipeline$_char
                ;;
            '|')
                end_of_segment
                ;;
            '&')
                # `2>&1` and `>&2` are redirections, and `&>file` is bash's spelling of
                # one. None of them ends anything, and reading them as `&` split
                # `make container-check 2>&1 | tail` into two pipelines - which lost the
                # wrapper and let exactly the case this is for through.
                _last=${_pipeline#"${_pipeline%?}"}
                _next=${_rest%"${_rest#?}"}
                if [ "$_last" = '>' ] || [ "$_next" = '>' ]; then
                    _pipeline=$_pipeline$_char
                else
                    end_of_pipeline
                fi
                ;;
            ';')
                end_of_pipeline
                ;;
            *)
                _pipeline=$_pipeline$_char
                ;;
        esac
    done
}

check_line() {
    check_pipeline "$1"
    _parts=$(split_on_separators "$1")

    set -f
    _saved_ifs=$IFS
    IFS='
'
    # shellcheck disable=SC2086  # splitting on the separators is the point
    set -- $_parts
    IFS=$_saved_ifs
    set +f

    for _part in "$@"; do
        check_part "$_part"
    done
}

check_line "$command"

exit 0
