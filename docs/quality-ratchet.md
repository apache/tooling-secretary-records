# The quality ratchet

Nine meters measure things that make the codebase harder to work in - branchy
functions, code nothing calls, copy-paste, untested lines - and CI fails when one
of them gets worse than it was.

None of them is a threshold you have to meet. Every reading is compared against
the same meter read on the commit the branch would merge into, so the codebase
starts out passing by definition. What it cannot do is get worse.

```sh
scripts/container.sh make quality-base    # measure the merge base, once per pull
scripts/container.sh make quality         # what CI runs, against that
scripts/container.sh make quality-show    # the readings, worst first
```

## Why a base rather than a threshold

In BAT, whose ratchet this is, setting `max-complexity = 12` outright would have
failed on 48 findings across `app/`, so it would have had to be either a flag day
nobody had time for or a permanently red build everyone learns to ignore. The bar
is set at the value we actually want - 12 branches, 15 cognitive - and the
comparison records how far past it each file already was. New code meets the bar
because the base has no reading for it.

This repository started from a commit with no code at all, so every reading of its
first base is empty, and everything after it meets the bar unless a commit argues
otherwise. A meter that finds nothing to read in a tree - no `app/`, no
`assets/eslint.config.js` - reads it as empty rather than failing, which is what
lets that first base be measured.

## Code that moved is not code that arrived

Keying on a path is what makes "new code meets the bar" work, and it is also the
one thing that could make it lie. Both halves of a move leave the same shape
behind as an arrival: a key with no base reading. Renaming a 600-line file used to
go red on every meter that keys on its path, and the only way through was a
`Quality-Allow:` trailer whose subject argued for a regression that had not
happened.

Two things tell a move from an arrival, and they are deliberately unequal.

**A rename is proved.** `check` asks git which files were renamed between the base
commit and the tree in front of it, and reads the base through that answer, so a
reading arrives at the path its file now has. Nothing is inferred: git says a
rename or it does not, and a shallow clone that cannot walk to the base finds none
and compares exactly as it did before. This is why the quality job checks out with
`fetch-depth: 0`.

**A split is argued.** A split leaves the original file in place, so git reports
the new file as an addition and no rename detection can see it. What can be seen
is the arithmetic: on `file-length` alone, keys with no base reading are judged
together against what the same meter freed, plus one preamble per new file
(`SPLIT_PREAMBLE`, 80 lines - a license header, a module docstring and the imports
the new file no longer shares). Lines carved out of a longer file cost nothing;
a new long file with nothing freed to account for it is the regression it always
was. Such a key is reported as `moved` rather than hidden.

`file-length` alone, because a line is the same line wherever it sits. A count of
findings is not: a new branchy function is a different function from the one
somebody simplified, so the complexity meters have no allowance and a fix in one
file still cannot pay for a regression in another. Renaming a file with findings
in it is fixed by the first mechanism, not this one.

## Why it is measured rather than committed

It used to be committed, as `scripts/ci/quality-baseline.json`, and that file was
the single worst thing about the ratchet:

- **Every branch had to carry it, so every rebase conflicted on it.** Eight open
  pull requests conflicted on that one file and nothing else in August 2026. It is
  generated, so neither side of the conflict is right: one branch's copy describes
  a tree that no longer exists once it is rebased, and resolving it by taking a
  side produced both committed conflict markers and silently reverted readings.
- **An improvement failed the build too**, so a gain had to be re-recorded by hand
  or it sat there as headroom the next change could spend.
- **The recorded numbers came from somebody's laptop** and were compared against
  numbers measured on a CI runner.

Measuring both sides removes all three. A gain is locked in the moment it merges,
because the next branch is compared against a base that already has it; there is
no file to conflict on; and both readings come from the same place.

## Where the base comes from

**In CI**, from the run on `main` that produced the commit this branch would merge
into. That run publishes its readings to the actions cache under
`quality-counts-<sha>` (and `quality-python-coverage-<sha>`,
`quality-frontend-coverage-<sha>` from the jobs that run a suite); the pull
request restores them by the sha of `HEAD^1`, which for the merge commit GitHub
builds a pull request as *is* that commit.

The comparison is therefore between the merge result and the branch point, which
is the right question: an improvement somebody else made after this branch was cut
is present on both sides, and so is not reported as this branch making anything
worse.

**The cache is a rendezvous, not the source of the answer.** On a miss - a base
older than the cache keeps, or the first run after this landed - CI measures the
base itself, from a worktree of that commit. For the seven meters that read source
that is a second pass of the linters, half a minute or so. For the two coverage
meters it means running the suite against the base as well, which is the whole
reason those readings are worth caching.

So nothing has to have run on `main` first, and the check is never quietly
skipped. Only if measuring the base *fails* - which is not the branch's fault -
does the meter report as unmeasured and pass, rather than turning a pull request
red for something none of them did. The output says so plainly.

`main` itself publishes rather than checks. What a merge does to the numbers was
decided on the pull request, against this same reading; checking again afterwards
could only fail on a commit nobody can change any more.

**Locally**, `make quality-base` checks the merge base out into a throwaway
worktree and measures it there, into `state/quality-base.json`, which is not
committed. It costs a checkout and a second pass of the linters, so it is worth
re-running after a pull rather than before every check.

The tools always come from your checkout, even when the source being read is
another commit's - including `assets/node_modules`, which is symlinked into the
throwaway worktree rather than installed again. Both sides of a comparison are
then one toolchain reading two trees, rather than two installs that may differ.

`quality-base` cannot measure the two coverage meters: that would mean running
both suites against another commit. Locally the ratchet covers the seven meters
that read source.

## Allowing a regression

Sometimes the regression is the point of the change - splitting one long function
into several makes the file longer, which is the trade. Say so in the commit that
makes it, with a `Quality-Allow:` trailer naming the meter and the key:

```text
refactor: one function per post-meeting action, not one for all of them

Nine complexity readings go away and the file grows by 283 lines, because the
work is now named in it. Shortening it again means splitting the module, which
is not what this change is for.

Quality-Allow: file-length scripts/ci/quality.py
```

The reason is the commit's subject, which is where the argument already is: the
case for a regression is the case for the change. There is no ceiling because
there is nothing to bound - the trailer is read from the commits between the base
and here, so it is gone the moment they are behind the base. Nothing to clean up
afterwards, and no file for two branches to conflict on.

The change is still reported, marked `(allowed)` - a regression somebody argued
for is exactly the one worth seeing in the output.

A trailer naming a key that did not regress is reported too, so that it can be
dropped: the message would otherwise argue for a regression the change does not
make, which is the kind of thing a later reader believes. That covers a key that
did not move and a key that *improved* - a trailer arguing for a regression on
something the change made better is the most wrong a trailer can be.

### There used to be a file

`scripts/ci/quality-allowances.json` held allowances that were meant to outlive a
branch and be bounded by a `ceiling` while they did. Neither half held up.

A regression cannot outlive its branch. The base is the commit the branch would
merge into, so the moment a change merges the base carries the reading it made,
and every later branch is measured against it - there is nothing left to permit.
And the bound was the harmful part: a ceiling is an absolute reading, so it goes
stale as soon as the base moves. One entry recorded at 1588 against a base that
had since fallen to 1483 silently permitted seventy-two lines nobody had argued
for.

What was left was a file every branch touching it had to conflict on, and an
entry to sweep up after each merge. Both are gone with it.

## The meters

| Meter | What it counts | Tool |
| ----- | -------------- | ---- |
| `python-cyclomatic` | functions past 12 branches, or with too many arguments, returns or statements | ruff `C901`, `PLR0911`-`PLR0915` |
| `python-cognitive` | functions scoring past 15 on cognitive complexity | complexipy |
| `python-dead-code` | commented-out code, unused arguments | ruff `ERA001`, `ARG001`-`ARG003` |
| `frontend-complexity` | the same two shapes, for TS and Svelte | eslint `complexity`, `sonarjs/cognitive-complexity` |
| `frontend-dead-code` | unused files, exports, types and dependencies | knip |
| `duplication` | copy-pasted blocks, per language | jscpd |
| `file-length` | files past 500 lines, by how long they are | `wc -l`, in effect |
| `python-coverage` | line coverage of `app/` | pytest-cov |
| `frontend-coverage` | line coverage of `assets/src/js/libs` | vitest v8 |

Cyclomatic and cognitive complexity are both there because they are blind to
different things. Cyclomatic counts branches, so a flat twelve-case `match` scores
as badly as twelve nested `if`s; cognitive complexity charges for nesting and
forgives the flat switch. A function only both tools like is one you can actually
read.

`file-length` is the coarsest of them and measures something none of the others
do: how much has to be read before a change to that file can be made safely.
Every function in a 1500-line module can be under both complexity bars while the
module remains the place where a change is most likely to break something nobody
saw. It is also the meter you can move without touching a line of logic, which is
the point - the fix is to put the seams in, and the number falls out.

### Reading it with something that is not a person

`check` and `show` both take `--json`, which says exactly what the printed output
says: the status, which meters moved, and for each one which key went from what to
what.

```sh
scripts/container.sh uv run scripts/ci/quality.py check --base state/quality-base.json --json
```

Both renderings come from one evaluation rather than two code paths, so they
cannot drift apart. Use it when something is deciding what to fix rather than
reading a report - the printed form names the meter, the json names the function.

### What a key is

Each meter reports `key -> number`, and a readings file holds those pairs. The
keys never contain a line number, so inserting a line above a finding is not a
change; and they are relative to the tree that was measured, so the same file in
two commits produces the same key and the two readings can be compared.

- The ruff meters key on file and rule (`app/access.py::PLR0913`), holding a count.
- `python-cognitive` keys on the function itself, holding its score - which makes
  it the strictest of the set. A grandfathered function may be left alone or
  improved, but it may not get one point worse.
- The rest key on file and rule, or on the tool's own category.

### One deadband, for coverage only

Coverage is not reproducible to the last decimal: the same suite measured serially
and under `-n auto` on the same tree read 66.46 and 66.43, and CI's worker count
is whatever the runner has. So the two `direction="up"` meters ignore a move of
less than `PERCENT_SLACK` (0.1) in either direction — small enough not to be
somewhere to hide, since 0.1% of `app/` is about fourteen lines, and large enough
that the build does not fail on arithmetic.

Counts get no slack. One more branchy function is one more.

### Where the bars live

`MAX_CYCLOMATIC`, `MAX_COGNITIVE` and `MAX_LINES` in
[`scripts/ci/quality_meters.py`](../scripts/ci/quality_meters.py), and the first two have
frontend equivalents in `assets/eslint.config.js` - which has to hold them too,
so an editor shows a function going over the moment it does.

Because both sides of a comparison are measured by *your* checkout's copy of
`quality.py`, changing one of these bars moves both readings at once and shows up
as nothing having changed. That is deliberate - it keeps a bar change from
drowning the branch in phantom findings - and it is also why the review of such a
change matters: the tool cannot tell you a bar was lowered.

`MAX_LINES` is 500 because that is roughly where BAT's tree divided: twenty-one
files were past it and the next one down was at 491. It is not a claim that 500 is
the right length for a file. `scripts/ci/quality.py` itself is past it, carried
over from BAT under a `Quality-Allow:` trailer on the commit that added it.

Lowering one of those forgives code rather than fixing it. Raising one is a real
decision, and belongs in a commit that says why.

## Where each meter runs in CI

The seven fast meters run in the `quality` job of
[`build.yml`](../.github/workflows/build.yml), which needs nothing but a checkout
and the two installs.

The two coverage meters need a suite to have run, so they live in the jobs that
run one: `python-coverage` in the `test` job (on 3.12 only - the number is a
property of the tests, not the interpreter), `frontend-coverage` in the
`frontend` job. Each restores its own cache entry, because each is written by a
different job. Run locally without a coverage report, they skip with a note rather
than failing.

### The one reading that depends on your machine

In BAT, `python-coverage` is measured in CI from a run without the ~38MB published
minutes corpus, so a local figure there can read a point or so above the one CI
compares against. Nothing here is skipped that way yet; a suite that skips tests
for want of data it cannot keep in the repository brings the same caveat with it.

## Adding a meter

Write a function returning `{key: number}` and add a `Meter` to `METERS` in
`quality_meters.py`. Give it `direction="up"` if a bigger number is better, and
`needs_report` if it reads something a test run produces rather than measuring the
tree itself. Nothing has to be recorded: the first branch that runs after it lands
finds no base reading for it, reports it as unmeasured, and the run on `main`
publishes its first reading.

Leave `move_allowance` unset unless the meter's readings are the same unit
wherever they sit, so that one key's reading arriving as another's falls is one
measurement in a new place rather than two events. `file-length` is the only one
that is, and a count of findings is not.

Read the tree through `TREE` rather than through `ROOT`. `ROOT` is the checkout
the script lives in, which is where the tools are; `TREE` is what is being
measured, and is somewhere else entirely when the base is.

## What this deliberately does not do

It does not gate on absolute quality, or block a merge on a number nobody agreed
to. It compares against the commit the branch would merge into, not against the
tip of `main`: against the tip, a meter somebody else improved after the branch
was cut would read as this branch making it worse.

It also does not measure the Playwright suite's coverage. `frontend-coverage` is
scoped to `assets/src/js/libs`, the modules vitest is written against; the Svelte
components are exercised end-to-end instead, and counting them here would report
a number about the wrong suite.
