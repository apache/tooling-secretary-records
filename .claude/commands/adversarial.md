---
description: Hand a pushed branch to a fresh subagent and ask it to break the change
argument-hint: "[branch] (defaults to the current one)"
---

# Attack the branch before the pull request

AGENTS.md asks for this after the push and before the PR is opened, and for a revision
of an open PR, before the push that asks for re-review. Read that section for the order
and the scope; this is the prompt it tells you to write.

Work out the branch (`$ARGUMENTS`, or `git rev-parse --abbrev-ref HEAD`), confirm it is
pushed and that `origin/<branch>` is what you mean to have reviewed, then spawn **one
fresh general-purpose subagent** - never a fork, which would inherit the reasoning that
wrote the branch and agree with it. Give it the prompt below with the branch filled in.

`make container-verify` has already run - it gates the push, and the push comes first (as does `/simplify`, so what you are reading is meant to be the final shape).
While the pass runs, watch the CI run that push started; the two read the same commit and
neither waits on the other. Do not edit the worktree until the pass reports: it is reading
the pushed commit, but a tree changing under it costs it reasoning about whether your
edits are part of what it was asked about.

## The prompt

> You are doing an adversarial pre-PR review in the apache/tooling-secretary-records repository.
> Read AGENTS.md first. **Change nothing, commit nothing, post nothing to GitHub.**
> Report findings.
>
> The change is `git diff origin/main...origin/<branch>` - what was pushed, not `HEAD`,
> which carries anything committed since. Read that, not the working tree, which
> `make container-verify` rewrites as `make check` runs formatters that fix in place.
>
> Attack these classes, in this order. They are a floor, not the list - anything else you
> find is welcome, but these are the ones that have cost this repository real defects.
> Class 4 applies to every branch. Classes 1 to 3 and 5 are about the web application, and
> class 6 is about the harness - on a branch that touches no route and no component, that
> is where to spend the time.
>
> 1. **Async request/response.** For every fetch: a response arriving late, after a newer
>    one, after the surface closes, after an error, after a success. What is left on
>    screen, and what does it claim? What happens when the *first* read of the page fails?
> 2. **Existing model vocabulary.** For every new read of a model, what does that model
>    already answer? Find what the model it reads already exposes and compare; a second
>    reading of the same data usually disagrees with the first somewhere.
> 3. **Write paths.** What does a rollback not restore - including a row it never added?
>    What does a failed write claim in its response, its log line, its commit message?
>    What does a reader see during the window between the change and the commit?
> 4. **The tests themselves.** Which new assertion would still pass if the behaviour it
>    names were wrong? Check each one by making the behaviour wrong, not by reading it.
> 5. **Bounds and inputs.** Every number the client sends: what does the server do with
>    one far outside what the form can produce? Every field a user can empty.
> 6. **A guard, a script or a CI check: what walks past it, and what does it wrongly
>    stop?** Both halves, because they fail in opposite directions. Find a spelling of the
>    thing it refuses that it lets through - the enumerated list that omits a case, the
>    parse that stops at the first word it does not know. Then find something the
>    repository's own instructions ask for that it now refuses, since that is what gets it
>    worked around and then switched off. And ask what it does when it cannot answer:
>    a check that says "nothing found" when it failed to look is the worst of the three.
>
> Verify a suspicion before reporting it. Run things in the dev container - never `uv`,
> `pytest`, `yarn` or `node` on the host - and never pipe a container wrapper into
> `head`/`tail`, which a guard refuses; redirect to a file and read the file. You may
> write throwaway scripts inside the worktree and delete them; `git status` must be clean
> when you finish, and say so if it was not clean when you started.
>
> Report findings only, most serious first. For each: the `file:line`, what is wrong, a
> concrete scenario (inputs to wrong behaviour), and the smallest change that fixes it.
> Separate **CONFIRMED** (you ran something that demonstrates it) from **PLAUSIBLE**
> (reasoning only). Say explicitly if a class has nothing in it. Do not pad the list, and
> do not state a claim you have not checked.

## When it reports

Verify each finding yourself before acting - some are wrong, and a wrong one acted on is
worse than one ignored. Fix the real ones with the failing assertion shown first, the way
every other change here is made. A finding you decline is declined in the commit that
declines it, since there is no pull request yet to say it in.

Then say in the PR description that the pass ran and what came of it. A pass that found
nothing leaves no other trace, and a reader cannot otherwise tell it from one that never
happened.

## Growing the list

The classes above are what has paid so far. When a pass finds something that fits none of
them, add the class here rather than remembering it - that is the whole reason this is a
file and not a paragraph somebody retypes.

Class 6 arrived that way, on the first branch to be attacked that had no route and no
component in it: the five classes it was given were all web-application shaped, and what
the pass found - a parse that stopped at the first unrecognised option, and five commit
messages the guard refused for words inside them - fitted none of them.
