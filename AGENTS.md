# AGENTS.md

Operating rules for AI coding agents working in this repository (Secretary
Records, the replacement for the Whimsy Secretary Workbench). Claude Code loads
this file via `CLAUDE.md`; other agents read it directly.

This file is the short, must-follow contract. For full environment setup see
[CONTRIBUTING.md](CONTRIBUTING.md), and for the end-to-end harness see
[e2e/README.md](e2e/README.md). When something behaves strangely - a pull request
with no checks, a suite that fails a different spec each run, a quality job red on
a change that touched nothing it measures - look in
[docs/troubleshooting.md](docs/troubleshooting.md) before investigating it fresh.
If any instruction here conflicts with an explicit request from the user, ask
before overriding it.

What the tool must do is [REQUIREMENTS.md](REQUIREMENTS.md), and the tool it
replaces is [WORKBENCH.md](WORKBENCH.md). Both are agreed with the Secretary:
change them by agreement, never as a side effect of a feature.

The scaffolding - this contract, the container scripts, the guard, the quality
ratchet, the review scripts - is the Board Agenda Tool's
([apache/tooling-agenda](https://github.com/apache/tooling-agenda), "BAT"),
adapted. The rules were learned there, so an issue or pull request number cited
in them (#705, #729, #785, ...) is BAT's, not this repository's.

## Non-negotiables

- **Ask before building an ambiguous feature.** When a feature request admits
  more than one reading — which surface it touches, where the data lives, what
  "done" looks like — ask which one is meant before writing code. Do not pick
  the most plausible reading and build it. A wrong guess costs a full
  implementation and its tests, and it is cheaper to ask one question than to
  revert one. Once the reading is settled, build the whole thing without
  further check-ins.
- **The queue holds personal data, and nothing here may leak it.** ICLAs and the
  rest carry names, residence addresses and signatures (REQUIREMENTS.md, N-3).
  Logs, telemetry, error messages and test output never contain document
  contents or extracted fields. Fixtures are synthetic - never a real submission,
  not even redacted (N-8) - so a dev instance and the test suites never need
  production mail. Every route that reads the queue is role-gated, not only the
  ones that change it (R-1).
- **Before any interface change, read the design book *and* load the
  `frontend-design` skill.** Both, together, every time.
  [`docs/design-book.html`](docs/design-book.html) is the design system this
  tool shares with BAT (REQUIREMENTS.md, N-6 and section 5) — the chip
  vocabulary, the colour and type tokens, the component set, the keyboard
  model — and the skill is how to make well the choices the book leaves open.
  Neither is enough alone: the book by itself yields a change that is
  consistent and characterless, the skill by itself yields a good idea phrased
  in the wrong vocabulary. Read the chapter that covers what you are touching
  before you touch it, and follow
  [CONTRIBUTING.md](CONTRIBUTING.md#design-changes) for which changes may just
  be built, which need a line in the PR description, and which need an issue and
  a change to the book. The copy here is BAT's; BAT's is the source, so a change
  to the book is made there first and copied here.
- **Everything runs in a container.** All builds, linters, and tests run inside
  the dev container (Podman or Docker). Never install or run Python, Node,
  `uv`, `pytest`, or `yarn` on the host - the machine that holds your
  credentials and everything else you have. (A disposable environment is the
  exception, below.) From a plain host shell, use the wrapper targets rather than
  improvising `docker run` invocations:

  | Command | What it runs |
  | ------- | ------------ |
  | `make container-verify` | everything that gates a push - check, lint, test, snapshot, quality - behind one exit code |
  | `make container-check` | the pre-commit hooks |
  | `make container-test` | pytest + vitest |
  | `make container-pytest ARGS='...'` | one question: `ARGS='tests/test_webapp.py -k login -q'`, serial |
  | `make container-test-coverage` | pytest with coverage instrumentation (slower, python only) |
  | `make container-test-py314` | the same as `container-test`, on the 3.14 CI matrix version |
  | `make container-quality` | the quality ratchet (complexity, dead code, duplication) |
  | `make container-quality-base` | measure the merge base the ratchet compares against |
  | `make container-snapshot-base` | record what the app answers with the merge base |
  | `make container-snapshot` | record it with this checkout, and compare |
  | `make container-snapshot-full SNAPSHOT_REF=...` | the same, everything whatever the change reaches, against any commit |
  | `make e2e-test` | Playwright, app and browser both containerised |
  | `make e2e-quick FILTER=...` | one spec, reusing the app container (`SR_KEEP=1`) |
  | `make e2e-typecheck` | `tsc` over the specs, in seconds - running them checks nothing |
  | `make container-shell` | an interactive shell |
  | `make container-keep` | the app on the e2e fixtures, detached on :8080, for looking at by hand: sign in at `/e2e/login/<uid>?roles=...` |
  | `make container-stop` | takes that one away again |

  **Where the whole environment is disposable - a throwaway virtual machine or
  sandbox, say - the same commands run without the container.** The rule is about
  what the toolchain can reach, and a box with a throwaway filesystem and no
  credentials of yours answers it. Such an environment sets `SR_ISOLATED=1` in the
  environment the agent is started in (see
  [CONTRIBUTING.md](CONTRIBUTING.md#a-disposable-environment); not anything an
  agent or a command line sets). With it, the table above works unchanged:
  `scripts/container.sh` runs the command in the checkout, and the `e2e-*`
  wrappers run Playwright and `tsc` on the box. The guard stops refusing `uv`,
  `yarn`, `make check` and the like, and goes on holding every other rule here.
  Three settings need a container of their own and are refused there rather than
  run as something else: `SR_PYTHON` (so `make container-test-py314`), `SR_DETACH`
  and `SR_PORT` (so `make container-keep`); run `scripts/run-dev.sh` for the app.
  What exists only to bound a container does nothing there, and says nothing: the
  memory cap, `SR_MEMORY`, `SR_CONTAINER_NAME`, `SR_KEEP`, and `make
  container-stop`, which has nothing to stop. The deadline, `SR_TIMEOUT`, does
  apply. What the box needs installed is listed in the same section of
  [CONTRIBUTING.md](CONTRIBUTING.md#a-disposable-environment).

  The `container-*` rows go through `scripts/container.sh`, which gives each one a name, a
  memory cap and an hour's deadline: a command that hangs stops by itself, and one that
  overruns its memory is charged to itself rather than to whichever process the host's OOM
  killer picks. The `e2e-*` rows do not - `scripts/e2e-container.sh` builds its own
  invocation, and has none of that yet. A container meant to outlive its command wants none
  of it either, which is what the last two rows are for: neither `container-keep` nor
  `container-shell` expires. `scripts/container.sh`'s header has the environment variables
  for anything they do not cover. They are BAT's with `SR_` for `BAT_`, and the
  containers, volumes and networks are named `secretary-records-*`, so the two
  repositories never share one.

  Reach for `container-pytest` while you are working and `container-verify` before you
  push. The narrow one is serial where `container-test` is not: each xdist worker pays its
  own setup first, so for a handful of tests that costs more than the parallelism saves,
  and interleaved output is not worth reading anyway.

  `make e2e-test` reaches the app by container ip over plain http, which is
  deliberately not a secure context - browser APIs gated on one are absent
  there, so a feature depending on one fails in the suite instead of in front of
  a user. Pass a filter to narrow it: `scripts/e2e-container.sh home`.

  Starting the app container costs most of a minute, which dwarfs a narrow run.
  `make e2e-quick` sets `SR_KEEP=1`, which leaves it up and reuses it next time.
  The `e2e/` directory is bind-mounted, so spec edits need no restart; a change
  under `app/` or `assets/` does, so drop `SR_KEEP` (or remove the server
  container) after one. Run the whole suite without it.
- **Work in a worktree of your own, and expect company.** Several sessions run
  in this repository at once, one per topic, each in its own linked git
  worktree. Make one rather than taking over an existing checkout:

  ```sh
  git worktree add ../tooling-secretary-records-<topic> -b feat/<topic> origin/main
  ```

  Never switch another worktree's branch, and never bare `git stash` — the stash
  stack is shared with every worktree, so `git stash pop` can take somebody
  else's work. Set work aside with a temporary commit instead.

  **Finish with one by removing what it made, not only the directory.** Once its
  PR has merged, or its work is otherwise over:

  ```sh
  scripts/worktree-done.sh ../tooling-secretary-records-<topic>
  ```

  It removes the worktree, its virtualenv and node_modules volumes, its e2e server
  and network, and the local branch where everything on it is on the remote; it
  refuses a worktree with changes. A bare `git worktree remove` leaves the volumes
  behind, ~250 MB a checkout, and enough of them filled the disk.
  `scripts/container-gc.sh` lists what such removals left
  (`--yes` removes it, `--sizes` says how much it holds), and is safe to run from
  any session: what a checkout still on disk made is never a candidate. Things
  made before the scripts labelled them are judged by name, against this
  repository's worktrees and the directories beside it - a second clone kept
  elsewhere is not one it can see.

  **Start a session by finishing what earlier ones left.** `git fetch`, then

  ```sh
  scripts/worktree-sweep.sh          # which worktrees are finished, and why each other one is kept
  scripts/worktree-sweep.sh --yes    # finishes each finished one with worktree-done.sh
  ```

  A worktree is finished when its HEAD is on `origin/main`, or a pull request
  merged into `main` with its HEAD as the head - a squash merge leaves no
  ancestry, and a branch gone from `origin` can as well be one never pushed. It
  stays, whatever its work, when removing it could take something nobody
  committed or something still running: changes or untracked files, an ignored
  file the toolchain does not write again (`config.yaml`, anything hidden
  through `info/exclude`), a rebase stopped half way, a worktree inside it, a
  running container or process, a branch made from main with no commit of its
  own yet, or use in the last six hours, since a session next door may be in it.
  `state/` is not among them: it is the checkout's own scratch, deleted with it,
  so keep what must last elsewhere. The script's header has the full list, and
  `worktree-done.sh` refuses the same things when run by hand. Everything else is
  listed with the reason it stays. A worktree kept for changes after its work
  merged is usually a draft from before a rebase: look before you force it.

  Before a change that moves a lot of code — a split, an extraction, a rename
  across a module — find out who else is in it. Ask about **every file the branch
  touches**, not the one you set out to change, and ask against the merge base
  rather than against `main`:

  ```sh
  mine=$(git diff --name-only "$(git merge-base origin/main HEAD)" HEAD)
  for b in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin); do
    mb=$(git merge-base origin/main "$b") || continue
    git diff --name-only "$mb" "$b" -- ${mine:?nothing on this branch yet} | grep -q . && echo "$b"
  done
  ```

  **That reads what has been pushed, and a session next door has not pushed yet.** The
  branch most likely to collide is the one being written this afternoon, in a sibling
  worktree, and no ref under `refs/remotes` mentions it. Ask the worktrees directly:

  ```sh
  for w in $(git worktree list --porcelain | awk '/^worktree /{print $2}'); do
    [ "$w" = "$(git rev-parse --show-toplevel)" ] && continue
    git -C "$w" diff --name-only HEAD -- ${mine:?nothing on this branch yet} \
      | grep -q . && echo "uncommitted in $w"
  done
  ```

  Neither loop sees a local branch whose work is committed and unpushed. `refs/heads`
  beside `refs/remotes/origin` finds those, and on a wide file list it names most of the
  branches a busy repository carries - in BAT, 79 for one touching 40 files, against 5
  from the remote alone. Add it when the list is narrow.

  Taking the list from the branch rather than naming a path is the point. A split
  edits the file it is about and leaves an import line in every consumer, and it is
  those that collide: #785 was scanned for the two files it was restructuring,
  came back clear, and still landed a conflict onto somebody else's branch through
  a component it had only added an import to.

  `${mine:?}` rather than `$mine` because an empty list is not an empty
  question: with no pathspec left, `git diff` matches every file and the scan
  answers that every branch collides. It fails loudly instead.

  `git diff origin/main origin/<branch> -- <path>` is the tempting form and it
  lies: it also reports every commit the branch is merely *behind* by, so a stale
  branch that never touched the file is listed and a busy one is lost in the
  noise.

  The answer keeps for as long as nobody opens a branch, which is not long. It
  says who to talk to, not whether you may merge — `git merge-tree --write-tree
  origin/main HEAD` answers that one, at the moment you ask it.

  The container scripts isolate themselves: volumes, the e2e server and its
  network are all named after the checkout they run in
  (`scripts/checkout-id.sh`), so two sessions cannot sync one another's
  virtualenv mid-test, and the e2e cleanup cannot kill a suite running next
  door. The primary checkout keeps the plain names. A new worktree's volumes are
  seeded by copying the primary checkout's, so the first run costs a copy rather
  than a full `uv sync` and `yarn install`. Name them outright with
  `SR_VENV_VOLUME`, `SR_NODE_VOLUME`, `SR_E2E_SERVER`, `SR_E2E_NETWORK`.

  What isolation cannot give you is a machine to yourself. Runs are slower while
  other sessions are busy - a full BAT e2e suite has taken anywhere from 8 to 15
  minutes on the same commit - and a timing-sensitive assertion can fail under
  that load. Re-run a lone failure before believing it, and say plainly which it
  turned out to be.
- **`make check` must pass before you commit.** It runs the `prek` pre-commit
  hooks (ruff, pyright, djlint, stylelint, shellcheck, markdownlint, zizmor,
  license headers, `uv-lock`/`uv-export`). Fix every error and warning. Do not
  disable a hook or commit with `--no-verify`.
- **The quality ratchet may not slip.** `make container-quality` measures
  complexity, dead code and duplication against the same meters read on the commit
  the branch would merge into, and CI fails when one gets worse. Nothing has to be
  fixed to merge — the numbers just may not climb. An improvement needs no
  recording: it is locked in as soon as it merges, because the next branch is
  measured against a base that has it.

  Nothing is committed, so there is no generated file to conflict on in a rebase.
  The base is a measurement: CI takes it from its own cache, written by the run on
  `main`, and locally you write it yourself, into `state/`:

  ```sh
  make container-quality-base    # once per pull; re-run after rebasing
  make container-quality
  ```

  Do not lower `MAX_CYCLOMATIC` or `MAX_COGNITIVE` in `scripts/ci/quality_meters.py` (or
  their eslint counterparts) to make a reading pass — that forgives the code
  rather than fixing it. A regression the change is *for* is allowed by saying so
  in the commit that makes it:

  ```text
  Quality-Allow: file-length scripts/ci/quality.py
  ```

  The reason is the commit's subject, so the argument sits where it already is.
  Nothing to clean up, and nothing for two branches to conflict on: the trailer is
  read from the commits this branch adds, so it goes when they are behind the base.
  It must be a real trailer, in the block at the end of the message — git decides
  that, so a commit that merely mentions `Quality-Allow:` grants nothing.
  [docs/quality-ratchet.md](docs/quality-ratchet.md) has the whole picture.
- **What the app answers may not change unless a commit says it should.**
  `tests/behaviour` records every GET route as each reader is answered, and
  what a fixed sequence of writes through the API changes - every table, and
  the pages the writes reach (`tests/behaviour/scenarios.py`) - all from the test
  fixture, on a pinned clock. The fixture is synthetic (N-8), which is what lets
  the difference be printed in a CI step summary and kept as an artifact; a record
  made from anything else would publish it. The app has no route that writes yet, so that
  sequence is empty: the change that adds the first write adds its step, and a
  step for each reader it must refuse. Nothing of the record is committed: like
  the quality ratchet, it is recorded with the commit the branch would merge
  into and with the branch, and the two are compared file by file
  (`scripts/ci/behaviour.py`). CI takes the base from the run on `main`;
  locally you record it yourself:

  ```sh
  make container-snapshot-base   # once per pull; re-run after rebasing
  make container-snapshot        # record this checkout, and compare
  ```

  A refactor leaves every file as it was. A difference there is the refactor
  changing behaviour, and is fixed in the code. A change that is *meant* to
  alter what the app answers names what it alters, in the trailer block of the
  commit that alters it, and the pull request says why:

  ```text
  Behaviour-Allow: http/admin/*.txt
  ```

  The value is a path in the record or a glob over paths (`*` crosses `/`), never
  a bare `*` or `**`, and never one that matches every file the base record holds
  (`*.txt` does, since every file of it ends so); `state/behaviour.diff` has every
  difference to read before writing one. A trailer that allows nothing that changed is reported, and only
  the branch's own commits count - not a merge, nor a branch merged in.

  A change to the harness itself - `tests/behaviour`, the suite's conftest and
  fixture data, `scripts/ci/behaviour*.py`, `scripts/ci/trailers.py` - says why
  with `Behaviour-Harness: <why>`, and changes nothing under `app/`: the harness
  first, recorded against the app it has, then the change to the app, recorded
  by the new harness. A branch doing both is not compared. The one exception is
  the change that brings the harness, whose base has none: it records, and has
  nothing to compare with. A new GET route fails the walk until
  `tests/behaviour/routes.py` can fill its parameters; anything rewritten before
  it is recorded is a named rule in `tests/behaviour/normalise.py`, with the
  reason it could not be pinned and examples both ways. Do not add one to quiet a
  difference a change made: a rule hides whatever it matches, from every later
  change too.

  A pull request records only the parts its change can reach - read off which
  of `app/` each part ran, kept beside the base's record - and everything when
  it touches what that cannot see: templates, static files, data, migrations,
  the fixture, the harness, the dependencies, the Makefile, CI, or code that
  runs at startup (`EVERYTHING_WHEN` in `scripts/ci/behaviour_selection.py`). The
  `What the app answers, in full` workflow is the backstop: every night that main
  has moved it records everything and compares it with the last full run,
  failing with the commit range where something changed unallowed. It reads the
  trailers of main's own commits for that, so **a squash merge keeps the
  branch's `Behaviour-Allow:` and `Behaviour-Harness:` lines in the trailer
  block at the end of the squashed message** - GitHub's default message strings
  each commit's message into a list, where they are no longer trailers. Its
  first base is made by dispatching it once with a base, as its header says.
- **Show a new assertion failing before you trust it.** Run it against the code
  as it was before your change, and put what it reported in the pull request. A
  test that has only ever passed has not been shown to test anything, and
  BAT has shipped three that tested nothing: `0 < 30` against a card
  inside a hidden pane (#705), the same line reading `13.2 < 30` once the card
  was visible but the rule still wrong (#729), and a property list cast in a way
  that made every name compile to the same one. All three were green. Assert the
  rule, not a bound on it — `height < 30` says a heading is no longer set as a
  heading, and says nothing about whether it is set as a label.
- **`uv.lock` is the source of truth for Python dependencies.**
  `requirements.txt` is generated by the `uv-export` hook — never hand-edit it.
  Change dependencies through `uv` and let the hook regenerate the export.
- **New Python files need the Apache 2.0 license header** (enforced by the
  `insert-license` hook).

## Some of these are held for you

The rules above that read as absolutes are enforced rather than remembered, so
that breaking one takes a decision rather than a lapse. The part of the Claude
Code harness that belongs to the repository rather than to whoever is running it
is committed under `.claude/`:

| Path | What it is |
| ---- | ---------- |
| `.claude/settings.json` | the permission allowlist - the container wrappers and the read-only `git` and `gh` calls, so a session does not stall waiting to be told it may run `git log` - and the hook registration |
| `.claude/hooks/guard-bash.sh` | refuses a command that runs the toolchain on the host, stages with a wildcard - `git add -A`, the `-a` that spells it on the commit, or a list a command printed, `git add $(git diff --name-only)` - reaches into the shared stash, passes `--no-verify`, or posts an issue or pull request body that does not say who wrote it |
| `.claude/hooks/guard-bash.test.sh` | what the guard refuses, and - the half that matters - what it must go on allowing. Run by `make check` |
| `.claude/commands/adversarial.md` | the prompt the pre-PR pass is given, and the classes it attacks |

The guard stands down inside the dev container, where running `uv` and `yarn` is
the whole idea. Where `SR_ISOLATED=1` is set it stands down for the toolchain
rules only. If it refuses something this repository actually asks for, that
is a bug in the guard: add the case to the test and fix it rather than working
around it.

Whatever else lands in `.claude/` stays ignored - a session's own approvals
accrue in `settings.local.json`, and its linked worktree lives under
`worktrees/`. An agent that is not Claude Code gets none of this and is held to
the same rules by reading them.

## Project shape

| Layer | Technology | Location |
| ----- | ---------- | -------- |
| Backend | Python 3.12+ / Quart (async) via `asfquart` | `app/` |
| Frontend | Svelte 5 runes / TypeScript / Vite, islands in Jinja pages | `assets/src/`, `app/client/templates/` |
| Database | SQLite / SQLModel / Alembic | `app/db/` |

- Pages: routes in `app/client/routes.py`, templates in `app/client/templates/`,
  each Svelte island an entry in `assets/src/js/pages/` (and in the `input` list
  of `assets/vite.config.js`).
- API routes: add a module under `app/api/routes/` and import it in
  `app/api/routes/__init__.py`, or its routes do not exist.
- Roles: `app/models/role.py`, worked out per request in `app/access.py`.
  Every mutating route carries `require_role(..., to="...")`, and so does every
  route that reads the queue.
- Database: add models in `app/db/models.py` plus a sequential Alembic
  migration (`0001`, `0002`, ...) in `app/db/migrations/versions/`; migrations
  auto-apply on startup, and startup refuses to serve when the models and the
  migrated schema disagree.
- Admin pages: `app/admin/`, for the Admin role alone.
- Frontend: components in `assets/src/js/components/`, pure logic in
  `assets/src/js/libs/`. Use Svelte 5 runes (`$state`, `$derived`, `$props`).

### Rules that live where the code does

Two things are true only once you are in a particular directory, and each is
written down there rather than here. Claude Code loads one when it touches that
directory; anything else should read the file named below before changing what is
in it.

| Where | What it holds |
| ----- | ------------- |
| [assets/AGENTS.md](assets/AGENTS.md) | where components go, the Documentation page, both test layers, and the traps BAT already hit |
| [e2e/README.md](e2e/README.md) | the end-to-end harness: the test login, the one shared server, and what is absent outside a secure context |

This file is the contract; those are the detail. A rule that applies everywhere
belongs here, and one that applies to a directory belongs in it — where it is read
by whoever is about to break it, and not by everybody else.

## Changing the UI requires tests

Both layers: **vitest** unit tests alongside the code in `assets/src/`, and
**Playwright** end-to-end tests in `e2e/tests/` for the user-facing flow. CI runs
them as the `frontend` and `e2e` jobs, and a UI change without matching test
updates is incomplete. [assets/AGENTS.md](assets/AGENTS.md) has the rest.

## Adding tables to the DB

Personal data is the secretarial roles' alone. Admin - the Tooling PMC and infra root - keeps the
tool running and is never sent a personal field: not shown, and not selected or serialised either,
so it does not travel over the wire to them.

The admin data browser (`/admin/data`, `app/admin/routes.py`) shows a table only
where `BROWSABLE` lists it, and only the columns listed there: an admin does not
read the queue (REQUIREMENTS.md, section 2), and a browser read from the metadata
would show a submission's name and address the moment its model existed. Each new
table goes into `BROWSABLE`, with no column holding document contents or personal
fields, or into `NOT_BROWSABLE`, in the pull request that adds it -
`tests/test_admin_data.py` fails until it does.

## Pull requests

- Branch from `main` and open the PR against `main`. Make `make check` and
  `make test` pass first.
- **`scripts/pr-preflight.sh` says which rules this branch has taken on**, read
  off the files it touches: the design book and both test layers for an interface
  change, `require_role` and a refusal test for a route, the Documentation page for a
  control somebody can now use, a migration for a new table, and the licence header
  for a new Python file. It also runs the contention scan, so who else is in these files
  is answered without assembling the loop by hand. It prints and exits 0 - the
  gate is `make container-verify`, and a checklist that could fail a build gets
  worked around. It exits non-zero on a base it cannot resolve, and when the scan
  could not look at everything: an `origin` it could not read, work on `origin`
  this checkout has not fetched, a branch git refused to compare, a path git
  hands back quoted. Its answer to who else is in these files is `none`, which is
  read as leave to restructure them, so it is printed only when the scan saw
  every branch. It reads `origin` rather than fetching from it - a checklist has
  no business rewriting your refs - and names the fetch for you to run.
- **`/simplify` between two runs of the gate: verify, simplify, verify, push.** It
  asks what is easy not to ask of your own branch - is this bigger than it had to be -
  and cuts: scaffolding the change no longer needs, a helper generalised past its one
  caller, an abstraction added for a second case that never arrived, a comment
  narrating the edit rather than warning about a shape. It is not a bug hunt; that is
  the pass below.

  **Only what this branch added.** "Ask before removing functionality or code that
  appears intentional" in Conventions is not suspended for it, and an abstraction
  somebody else left is exactly the code that rule is about: outside the branch's own
  diff, simplify proposes and you ask.

  Before the push, because the pass below reads what was pushed and not your working
  tree - simplify afterwards and the pass spends itself on code you are about to
  rewrite. It rewrites files in place, so the second `make container-verify` is what
  says the cut was safe. And if it touched a file the change had not touched before,
  `scripts/pr-preflight.sh` was answered about the old list: run it again, or its
  `none` is about files nobody scanned.

  Skip it where the pass below is skipped, and for the same reason: a branch of prose
  has nothing for it to cut, and running the gate twice over one buys nothing.

  It is a skill rather than a prompt this repository ships. What makes `/adversarial`
  a file is a list of classes that grows with what it catches, and there is no
  equivalent here to keep, so `.claude/commands/simplify.md` is deliberately absent -
  argue for it in a PR rather than adding one quietly. Where a session does not have
  the skill the rule is the reading, not the command: go through the diff once asking
  only what could come out. Say what came out in the same breath as the pass.

- **A subagent attacks the branch after the push and before the pull request.**
  `make container-verify`, push, then start the pass. The order is the file's
  usual one and this does not change it: the local gate comes before every push,
  this push included. What the pass overlaps with is the CI run that push started
  - both read the same commit, and neither waits on the other. Whatever the pass
  finds becomes commits, which go through `make container-verify` before they are
  pushed like any others.

  A PR that is already open takes the same steps, with the re-request where the
  opening would be: verify, push, run the pass, fix what it finds, push that, and
  ask for the review then. What the pass must not come after is the request, or
  its findings arrive as another round on a review already under way.

  Skip it where the branch touches no code under `app/`, `assets/`, `e2e/`,
  `scripts/` or `.claude/hooks/`. Ten minutes against a docs change buys nothing.
  The hooks are on that list because the guard is the code with the widest reach
  in the repository - a change to it decides what every other session may run -
  and the rule as first written exempted it. The rest of `.claude/` is prompts,
  where a pass has nothing to attack.

  **The prompt is `/adversarial`**, in `.claude/commands/adversarial.md` - the
  classes to attack, what the agent may read, and what it may not do. It is a file
  rather than a paragraph to retype because the list grows: a pass that finds
  something fitting none of the classes adds one.

  Two things in it are worth knowing without opening it. A **fresh agent, never a
  fork** - a fork inherits the reasoning that wrote the branch and agrees with it.
  And one of the classes is the tests: which new assertion would still pass if the
  behaviour it names were wrong. That is not the rule about showing a failure over
  again; it catches what that rule cannot, a test that fails against the old code
  for a reason other than the one it is named for, and so goes on passing when the
  behaviour regresses.

  Answer what it finds the way you would answer a reviewer - a finding you
  decline is declined in the commit that declines it, since there is no PR yet to
  say it in. Say in the PR description that the pass ran and what came of it, so
  that a pass which found nothing leaves a trace and a reader can tell it from
  one that never happened.

  It earns its place on the shape of what it finds rather than on a count, which
  goes stale: #836 had round after round of bot review, each finding something
  real, and one adversarial pass afterwards still found a button that read "Add 6
  meetings" and committed twenty-four, a rollback that deleted a special meeting
  the calendar already held, and three of that branch's own tests that could not
  fail.

- **Read a review with `scripts/pr-review.sh <pr>`, not by eye.** It prints every
  round, the newest review in full, every `Suppressed comments` block from every
  round, every comment on the conversation, the threads still open, and the checks
  on the head commit; it exits non-zero when the newest review predates the head
  commit, when a thread is unanswered, and when a check failed, has not finished,
  or never ran. Your own reviews do not count towards that: replying to a thread
  posts a review, so answering a finding would otherwise mark the head as
  reviewed by the act of answering — which is what happened on #838.

  The checks are read here for the same reason the suppressed blocks are. `gh pr
  checks` prints nothing when there is nothing to print, and nothing is what a
  branch that conflicts with `main` looks like: no workflow started, no output, no
  tick either way. No checks at all is not a pass, nor is one still running, nor
  one that was skipped — each is said in words, with the merge state beside it so
  that an empty list says which of the reasons it is. What it cannot see is a
  workflow its `paths:` filter took out of the run, which leaves no entry at all.

  Each of those is a way a review has been misread here. A Copilot review saying
  "generated no new comments" has often found something and collapsed it into a
  suppressed block, and what was in there was not stylistic: a route that let
  anyone signed in overwrite a report's own text, a change that deleted a
  report's attachment from the meeting, a regex whose stated safety property was
  false, and a test that asserted a 404 from an id that never existed. A
  suppressed finding opens no thread and is not among the pull request's review
  comments, so a clean round after it leaves it visible only in a body nobody has
  a reason to reopen — which is why the script sweeps every round rather than the
  last. And a hand-written `gh api --jq` filter that matches nothing prints
  nothing, which reads exactly like a review with no findings.

  A review also does not have to be a review: a comment on the conversation is
  none of the above, and on #751–#753 it was where every finding was, under a
  correct "did not run" and a correct "0 unresolved". Those are printed too, and
  they are the one section the exit status says nothing about — a comment records
  nowhere whether it was answered, so the script counts them and leaves the
  judgement to you.

  Answer a finding you decline where the next reader will find it — in the PR, or
  in the commit that declines it.
- **A review is tied to a commit, so re-request it after every push**, and do not
  read a clean round as a verdict. #729 had three reviews of one commit: the
  first two found nothing and the third found a real defect in that same
  unchanged code. One clean round means nothing was found this time.
  [docs/troubleshooting.md](docs/troubleshooting.md) has the two ways of asking
  for a review that look right and are not.
- **User-facing UI features must include screenshot(s) in the PR.** Capture them
  from the e2e spec that exercises the feature (`page.screenshot(...)`, see
  [e2e/README.md](e2e/README.md)); they land in `e2e/screenshots/` on the host,
  since the suite writes into the bind-mounted `e2e/` directory, and CI attaches
  the same directory to every run as the `e2e-screenshots` artifact.

  Then publish them and paste what it prints:

  ```sh
  scripts/pr-screenshots.sh <pr> e2e/screenshots/shot.png
  ```

  You cannot drag them in the way [CONTRIBUTING.md](CONTRIBUTING.md) tells a
  human to: that host (`github.com/user-attachments/...`) is authenticated by a
  browser session and has no API. The script puts them in a secret gist instead,
  and checks each url with an unauthenticated `curl` before printing it, because
  that is all a browser or GitHub's image proxy does. Two other routes look right
  and are not - the script says which, and why they answer `200` right up until
  the repository they are tried on is private.

  A gist raw url is readable by anyone holding it, even when the gist is secret.
  That is the same exposure a dragged-in image already has - GitHub serves those
  from an anonymized url that needs no authentication either - but do not put
  anything in one that you would not attach by hand. Say in the PR description
  which gist holds them, so it can be deleted when the PR closes.
- Stage only the files you changed (`git add <path>`), never `git add -A` or
  `git add .`.

## Writing on an issue or a pull request

A comment from a session goes up under somebody's account, and is read by
colleagues.

- **Answer what was asked, and stop.** A request for one file answered with a whole
  data model mixes the question with others the asker is keeping apart, and they
  then have to say so before they can read the answer.
- **Write in [ASD-STE100](https://www.asd-ste100.org/) (Simplified Technical
  English).** This rule applies to pull request descriptions, to comments on issues
  and pull requests, and to commit messages. Use these rules:

  - Keep each sentence short. Use not more than 20 words in an instruction and not
    more than 25 words in a description.
  - Write about one topic in each sentence.
  - Use the active voice and the simple tenses.
  - Use one word for one thing. Do not use a different word for variety.
  - Do not use idioms or figurative language.

  Code comments, `docs/` and the AGENTS files keep their current style. BAT's #964
  is about these.
- **The account holder approves the text first.** They answer the reply, and a
  comment cannot be unsent.
- **Say who wrote it**, on a line of its own at the end - the agent by name, and
  the GitHub username it was posted for. The guard refuses a `gh` command that
  posts a body without it.

  ```text
  Posted by Claude on behalf of @sstriker
  ```

  [Apache Magpie](https://github.com/apache/magpie) does the same for an
  agent-drafted review, in a footer that also says whether a maintainer confirmed
  it. Precedent rather than a standard, and the one to follow where the two meet -
  a third wording for one idea helps nobody.

## Reading your own results

Five ways a step that did nothing has looked here like a step that worked - and, in
the last one, a claim nobody checked looking like one somebody did. Each is a thing to
check rather than a thing to remember.

- **An exit status does not survive a pipe.** `make container-check | tail -20`
  reports tail's, which is 0 whatever the check did. The guard refuses that shape
  for the container wrappers, and the e2e wrappers end with a line saying `PASSED`
  or `FAILED` because a line does survive a pipe. Where you need both the output
  and the answer, send it to a file: `cmd > /tmp/out.log 2>&1; echo "exit=$?"`.
- **A refused command runs none of its parts.** The guard aborts the whole call,
  so `sed -i ... && make -n foo` edits nothing when the `make -n` is what it
  objected to. Re-run the parts you still want; do not assume the ones before the
  refusal landed.
- **Read what a step does, not what it is called.** A CI step named "Install
  Playwright and its browser" installed both, so a check added after it ran after
  the browser and not before, and said the opposite in five places. Timing and
  order come from the run - `gh api .../jobs` prints each step's name, conclusion
  and clock - never from the name or the config.
- **Measure options together, not one at a time.** Three tsconfig flags each
  reported zero errors alone and three between them, because measuring one at a
  time left `strict` off and the errors depended on it.
- **Do not state a number, or a claim about what the code does, that you have not
  just produced.** A measurement in a commit message, a PR or a code comment comes
  from a command run in that session; a statement about what some code does comes
  from having just read or run it. Name the receipt where it is cheap - the command,
  or the `file:line` - so that a reader can ask for one.

  This is the one that catches the other four, because it does not let a claim past
  without evidence attached. It is broken most often by a sentence that is merely
  plausible, in prose written to justify code that is correct: `path.glob` "reads the
  directory before the loop starts" (it is lazy - a file created after the call is
  still yielded); a check that "answers it 401" (it raises 401 and its handler returns
  403); a copy guarding what a reload "adds roles and emails to" (`emails` is a list,
  grown by a different loader, and a list mutated mid-iteration does not raise - it
  yields the wrong sequence and says nothing). Each cost one grep to check. Two of
  these three are on `main` as this is written; the third was caught only because a
  review round happened to run.

## Conventions

- Commit subjects use a conventional-commit prefix: `feat:`, `fix:`, `chore:`,
  `docs:`, `test:`, `style:`, or `ci:`.
- No emojis in commits, pull requests, issues, or code. Keep prose concise and
  direct.
- **A comment says what the code does, not how it got that way.** The question is
  whether an edit could bring the old shape back. Keep the history when it names a
  shape the code is avoiding and something could reintroduce - `app/config.py` on why an
  instance without an smtp block sends nothing, rather than defaulting to a relay that would
  address real mail to the people who sent their paperwork. Cut it when it only narrates the
  edit that happened: "this used to be a bare `except`" describes a version nobody can
  reach, and ages into fiction once the thing it compares against is forgotten. Git
  keeps the history either way; a comment earns its past tense by warning, not by
  recounting.

  It also bounds the receipt that "Reading your own results" asks for: a `file:line`, a
  command, or an issue number - not a paragraph. A justification that needs one belongs
  in `docs/`, where somebody who is not already inside the function can read it.
- Do not remove or downgrade code to silence type errors from an outdated
  dependency — upgrade the dependency instead.
- Ask before removing functionality or code that appears intentional.
