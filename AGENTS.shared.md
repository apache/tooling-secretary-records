<!-- Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy. -->

# AGENTS.shared.md

The rules every Tooling repository shares: the agent rules, and what holds them. This file comes from
`apache/tooling-devkit`. A repository's own `AGENTS.md` reads it first and adds the project shape and the
rules that are only its own; it cannot make a rule here weaker.

The scripts and `.claude/` files this file names are kit files too (see "Changing a file that comes from
the kit"). The `make` targets it names come from each repository's own Makefile, which is not a kit file.
The names a repository gives its own images, volumes and containers come from `devkit.conf`. The
variables the scripts read all start with `DEVKIT_`.

## Non-negotiables

- **Ask before building an ambiguous feature.** When a feature request admits
  more than one reading — which surface it touches, where the data lives, what
  "done" looks like — ask which one is meant before writing code. Do not pick
  the most plausible reading and build it. A wrong guess costs a full
  implementation and its tests, and it is cheaper to ask one question than to
  revert one. Once the reading is settled, build the whole thing without
  further check-ins.
- **Everything runs in a container.** All builds, linters, and tests run inside
  the dev container (Podman or Docker). Never install or run Python, Node,
  `uv`, `pytest`, or `yarn` on the host - the machine that holds your
  credentials and everything else you have. (A disposable environment is the
  exception, below.) From a plain host shell, use the wrapper targets rather than
  improvising `docker run` invocations:

  | Command | What it runs |
  | ------- | ------------ |
  | `make container-verify` | everything that gates a push - check, lint, test, snapshot, quality - at once, behind one exit code; a stage that passed is not run again until what it reads changes |
  | `make container-verify-fresh` | the same, every stage run whether it passed before or not |
  | `make container-check` | the pre-commit hooks, over what changed since `origin/main` |
  | `make container-check-all` | every hook over every file, as `verify` and CI run them |
  | `make container-test` | pytest + vitest |
  | `make container-pytest ARGS='...'` | one question: `ARGS='tests/api/test_x.py -k rename -q'`, serial |
  | `make container-test-fast` | while iterating: what this change most likely breaks, first failure stops it; exits 10 when the change needs `container-test` (CONTRIBUTING.md). Not the gate - `container-verify` is |
  | `make container-test-coverage` | pytest with coverage instrumentation (slower, python only) |
  | `make container-test-py314` | the tests in a venv of their own on 3.14 (`DEVKIT_PYTHON`); 3.14 is the default too |
  | `make container-quality` | the quality ratchet (complexity, dead code, duplication) |
  | `make container-quality-base` | measure the merge base the ratchet compares against |
  | `make container-snapshot-base` | record what the app answers with the merge base |
  | `make container-snapshot` | record it with this checkout, and compare |
  | `make container-snapshot-full SNAPSHOT_REF=...` | the same, everything whatever the change reaches: before a promotion, against `origin/production` |
  | `make e2e-test` | Playwright, app and browser both containerised |
  | `make e2e-quick FILTER=...` | one spec, reusing the app container (`DEVKIT_KEEP=1`) |
  | `make e2e-typecheck` | `tsc` over the specs, in seconds - running them checks nothing |
  | `make container-shell` | an interactive shell |
  | `make container-keep` | the app on the e2e fixtures, detached on :8080, for looking at by hand: sign in at `/e2e/login/<uid>?roles=...` |
  | `make container-stop` | takes that one away again |

  **Where the whole environment is disposable - an isx box, say - the same
  commands run without the container.** The rule is about what the toolchain can
  reach, and a box with a throwaway filesystem and no credentials of yours
  answers it. Such an environment sets `DEVKIT_ISOLATED=1` in the environment the
  agent is started in (for isx, the template's `env`, see
  [CONTRIBUTING.md](CONTRIBUTING.md#a-disposable-environment-isx); not
  anything an agent or a command line sets). With it, the table above works
  unchanged: `scripts/container.sh` runs the command in the checkout, and the
  `e2e-*` wrappers run Playwright and `tsc` on the box. The guard stops refusing
  `uv`, `yarn`, `make check` and the like, and goes on holding every other rule
  here. Three settings need a container of their own and are refused there rather
  than run as something else: `DEVKIT_PYTHON` (so `make container-test-py314`),
  `DEVKIT_DETACH` and `DEVKIT_PORT` (so `make container-keep`); run `scripts/run-dev.sh`
  for the app. What exists only to bound a container does nothing there, and says
  nothing: the memory cap, `DEVKIT_MEMORY`, the sizing and its gate slots
  (`DEVKIT_SIZE_WORKERS`), `DEVKIT_CONTAINER_NAME`, `DEVKIT_KEEP`,
  `DEVKIT_E2E_WORKERS`, and `make container-stop`, which has nothing to stop. The
  deadline, `DEVKIT_TIMEOUT`, does apply. The box needs the packages of
  [CONTRIBUTING.md](CONTRIBUTING.md) Option C, `make init` for yarn, and what
  `make e2e-test-host` needs (node, subversion, and the system libraries Chromium
  runs on) for the e2e suite.

  The `container-*` rows go through `scripts/container.sh`, which gives each one a name, a
  memory cap and an hour's deadline: a command that hangs stops by itself, and one that
  overruns its memory is charged to itself rather than to whichever process the host's OOM
  killer picks. The `e2e-*` rows do not - `scripts/e2e-container.sh` builds its own
  invocation, and has none of that yet. A container meant to outlive its command wants none
  of it either, which is what the last two rows are for: neither `container-keep` nor
  `container-shell` expires. `scripts/container.sh`'s header has the environment variables
  for anything they do not cover.

  The rows that run the whole suite - `container-verify`, `container-test` and the like - also
  size themselves to the machine, and each holds one of its gate slots while it runs: a lock
  every worktree of the repository shares, one on a 15 GiB machine. A run that finds every slot
  held waits and says who holds them, rather than starting beside them and being OOM-killed, so
  there is no need to gate one at a time. `PYTEST_WORKERS` or `DEVKIT_MEMORY` given outright
  replaces the size, not the wait; CI keeps its pinned settings. What the slots do not cover - a
  narrow pytest, the fast stage, an e2e server, a clone kept elsewhere - is in
  [docs/troubleshooting.md](docs/troubleshooting.md#a-gate-waits-for-a-slot).

  Reach for `container-pytest` while you are working and `container-verify` before you
  push. The narrow one is serial where `container-test` is not: each xdist worker builds
  an svn repository of its own first, so for a handful of tests that setup costs more
  than the parallelism saves, and interleaved output is not worth reading anyway.

  `make e2e-test` reaches the app by container ip over plain http, which is
  deliberately not a secure context - browser APIs gated on one are absent
  there, so a feature depending on one fails in the suite instead of in front of
  a user. Pass a filter to narrow it: `scripts/e2e-container.sh presentation`.

  Starting the app container costs about seventy seconds, which dwarfs a narrow
  run. `make e2e-quick` sets `DEVKIT_KEEP=1`, which leaves it up and reuses it next
  time, taking a three-test
  run from 108s to 40s. The `e2e/` directory is bind-mounted, so spec edits need
  no restart; a change under `app/` or `assets/` does, so drop `DEVKIT_KEEP` (or
  remove the server container) after one. Run the whole suite without it.
- **Work in a worktree of your own, and expect company.** Several sessions run
  in this repository at once, one per topic, each in its own linked git
  worktree. Make one rather than taking over an existing checkout:

  ```sh
  git worktree add ../tooling-agenda-<topic> -b feat/<topic> origin/main
  ```

  Never switch another worktree's branch, and never bare `git stash` — the stash
  stack is shared with every worktree, so `git stash pop` can take somebody
  else's work. Set work aside with a temporary commit instead.

  **Finish with one by removing what it made, not only the directory.** Once its
  PR has merged, or its work is otherwise over:

  ```sh
  scripts/worktree-done.sh ../tooling-agenda-<topic>
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
  through `info/exclude`; `state/` is the checkout's own scratch, deleted with it, so keep what
  must last elsewhere), a rebase stopped half way, a worktree inside it, a running container
  or process, a branch made from main
  with no commit of its own yet, or use in the last six hours, since a session
  next door may be in it. The script's header has the full list, and
  `worktree-done.sh` refuses the same things when run by hand. Everything else is listed with the reason it stays. A worktree kept
  for changes after its work merged is usually a draft from before a rebase: look
  before you force it.

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
  beside `refs/remotes/origin` finds those. On a wide file list it names most of the
  branches there are, so add it when the list is narrow.

  Taking the list from the branch rather than naming a path is the point. A split
  edits the file it is about and leaves an import line in every consumer, and it is
  those that collide: a scan for the two files being restructured comes back clear, and the
  conflict still lands on somebody else's branch through a component that only gained an import.

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
  `DEVKIT_VENV_VOLUME`, `DEVKIT_NODE_VOLUME`, `DEVKIT_E2E_SERVER`, `DEVKIT_E2E_NETWORK`.

  What isolation cannot give you is a machine to yourself. Runs are slower while
  other sessions are busy - a full e2e suite has taken anywhere from 8 to 15
  minutes on the same commit - and a timing-sensitive assertion can fail under
  that load. Re-run a lone failure before believing it, and say plainly which it
  turned out to be.
- **`make check` must pass before you commit.** It runs the `prek` pre-commit
  hooks (ruff, pyright, djlint, stylelint, shellcheck, markdownlint, zizmor,
  license headers, `uv-lock`, and import-linter, which holds the
  boundaries that `.importlinter` names).
  It runs them over the files changed since `origin/main` - committed, staged, modified or
  untracked - and over every file when a deletion, a rename or a config change reaches
  files the list does not name (`scripts/ci/changed.py`); `make check-all` runs every hook
  over every file, and `make container-verify` runs that before a push.
  Fix every error and warning. Do not disable a hook or commit with `--no-verify`.
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

  A worktree on a merge base that another one has measured with the same meters copies
  that reading rather than taking its own (`scripts/ci/bases.py`); so does the behaviour
  base below.

  Do not lower `MAX_CYCLOMATIC` or `MAX_COGNITIVE` in `scripts/ci/quality.py` (or
  their eslint counterparts) to make a reading pass — that forgives the code
  rather than fixing it. A regression the change is *for* is allowed by saying so
  in the commit that makes it:

  ```text
  Quality-Allow: file-length app/example.py
  ```

  The reason is the commit's subject, so the argument sits where it already is.
  Nothing to clean up, and nothing for two branches to conflict on: the trailer is
  read from the commits this branch adds, so it goes when they are behind the base.
  It must be a real trailer, in the block at the end of the message — git decides
  that, so a commit that merely mentions `Quality-Allow:` grants nothing.
  [docs/quality-ratchet.md](docs/quality-ratchet.md) has the whole picture.
- **What the app answers may not change unless a commit says it should.**
  `tests/behaviour` records every GET route as each reader is answered, the
  files the app renders to commit or publish, what each scheduled task sends and
  stores, and what a fixed sequence of writes through the API changes - svn,
  mail, LDAP (a server in memory), the feed, every table
  (`tests/behaviour/scenarios.py`) - all from the test fixture, on a pinned
  clock. Nothing of it is
  committed: like the quality ratchet, it is recorded with the commit the branch
  would merge into and with the branch, and the two are compared file by file
  (`scripts/ci/behaviour.py`). CI takes the base from the run on `main`; locally
  you record it yourself:

  ```sh
  make container-snapshot-base   # once per pull; re-run after rebasing
  make container-snapshot        # record this checkout, and compare
  ```

  A refactor leaves every file as it was. A difference there is the refactor
  changing behaviour, and is fixed in the code. A change that is *meant* to
  alter what the app answers names what it alters, in the trailer block of the commit
  that alters it, and the pull request says why:

  ```text
  Behaviour-Allow: http/committees/*.txt
  ```

  The value is a path in the record, or a glob over paths (`*` crosses `/`) that
  starts with a directory written out: `http/*.txt`, never `*.txt`, `*/x.txt` or
  `**/x`, and never globs that alone or together match the whole record.
  `state/behaviour.diff` has every difference to read before writing one. A
  trailer that allows nothing that changed is reported, and only the branch's
  own commits count - not a merge, nor a branch merged in. The nightly full
  run does not honour an allowance the gate would refuse.

  A change to the harness itself - `tests/behaviour`, the suite's conftest,
  config and fixture data, `scripts/ci/behaviour*.py` - says why with
  `Behaviour-Harness: <why>`, and changes nothing under `app/`: the harness
  first, recorded against the app it has, then the change to the app, recorded by
  the new harness. A branch doing both is not compared. A new GET route fails
  the walk until `tests/behaviour/routes.py` can fill its parameters; anything
  rewritten before it is recorded is a named rule in
  `tests/behaviour/normalise.py`, with the reason it could not be pinned and
  examples both ways. Do not add one to quiet a difference a change made: a rule
  hides whatever it matches, from every later change too.

  A pull request records only the parts its change can reach - read off which
  of `app/` each part ran, kept beside the base's record - and everything when
  it touches what that cannot see: templates, static files, data, migrations,
  the fixture, the harness, the dependencies, the Makefile, CI, or code that
  runs at startup (`EVERYTHING_WHEN` in `scripts/ci/behaviour_selection.py`). The
  `What the app answers, in full` workflow is the backstop: every night that main
  has moved it records everything and compares it with the last full run,
  failing with the commit range where something changed unallowed, and its
  dispatch takes a base to compare a commit about to be promoted against.
- **Show a new assertion failing before you trust it.** Run it against the code
  as it was before your change, and put what it reported in the pull request. A
  test that has only ever passed has not been shown to test anything: `0 < 30`
  against a card inside a hidden pane is green, `13.2 < 30` is green once the card
  is visible but the rule is still wrong, and so is a property list cast in a way
  that makes every name compile to the same one. Assert the
  rule, not a bound on it — `height < 30` says a heading is no longer set as a
  heading, and says nothing about whether it is set as a label.
- **`uv.lock` is the source of truth for Python dependencies.**
  Production installs it with `uv sync --frozen --no-default-groups`.
  Change dependencies through `uv` and let the `uv-lock` hook keep the lock current.
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
| `.claude/commands/adversarial.md` | the prompt the pre-PR pass is given, and the classes it attacks |

The guard stands down inside the dev container, where running `uv` and `yarn` is
the whole idea. Where `DEVKIT_ISOLATED=1` is set it stands down for the toolchain
rules only. If it refuses something this repository actually asks for, that
is a bug in the guard: add the case to the kit's guard test - what it refuses, and the half
that matters, what it must go on allowing - and fix it in the kit (below) rather than
working around it.

Whatever else lands in `.claude/` stays ignored - a session's own approvals
accrue in `settings.local.json`, and its linked worktree lives under
`worktrees/`. An agent that is not Claude Code gets none of this and is held to
the same rules by reading them.

## Changing a file that comes from the kit

Some files in this repository are copies of files in `apache/tooling-devkit`: this one, the guard, the
commands and the scripts. A line near the top of each copy says where to change it.
Two files cannot carry it, `.claude/settings.json` and `scripts/ci/LICENSE-template.txt`, and the check
covers them all the same.

`scripts/devkit-sync.sh <ref>` copies the files that the kit lists in `devkit.files` and writes the commit
to `devkit.lock`. `scripts/devkit-sync.sh --check` compares each file with the kit at that commit. Not in
the kit yet, and the next items of apache/tooling-migration#50: the Action that opens a sync pull
request, the CI job that runs the check in each repository, and the extension points named below, all
but the stages of the gate (`scripts/local/verify.toml`).

- **Do not edit a copied file in this repository.** A copy that differs from the kit at the commit in
  `devkit.lock` is drift. The check fails on it, and CI does once the repository runs the check there.
- **To change a copied file, open a pull request in `apache/tooling-devkit`.** When it is merged, run
  `scripts/devkit-sync.sh <ref>` here, or merge the sync pull request that the kit opens.
- **If the drift check fails, a copy was edited.** Move the change to the kit, and run the sync again.
- **If the change is urgent or only for this repository,** use the first of these that fits:
  a value in `devkit.conf`; an extension point of the kit (a local file the shared file reads or calls,
  such as `scripts/local/verify.toml`, where a repository names the stages of its gate, or
  `scripts/local/<name>.sh`, or extra guard rules in `.claude/guard.local`, which can add a restriction and
  cannot remove a shared rule); not merging the sync pull request yet, which is not drift because the
  check compares with the commit in `devkit.lock`; naming the file in `DEVKIT_SKIP` in `devkit.conf` and
  keeping it as a local file; and, last, an override in `devkit.lock` with an issue and a date
  (`override <path> <issue> until <YYYY-MM-DD>`).
- **A change to the shared rules or the guard is reviewed by the Tooling PMC.**

## Pull requests

- Branch from `main` and open the PR against `main`. Make `make check` and
  `make test` pass first.
- **`scripts/pr-preflight.sh` says which rules this branch has taken on**, read
  off the files it touches: the rules of the kit, and those the repository adds in
  `scripts/local/preflight.toml`. It also runs the contention scan, so who else is
  in these files is answered without assembling the loop by hand. It prints and
  exits 0 - the gate is `make container-verify`, and a checklist that could fail a
  build gets worked around. It exits non-zero on a base it cannot resolve, on a
  rules file it cannot use, and when the scan could not look at everything: an
  `origin` it could not read, work on `origin` this checkout has not fetched, a
  branch git refused to compare, a path with a newline in it. Its answer to who else
  is in these files is `none`, which is read as leave to restructure them, so it is
  printed only when the scan saw every branch. It reads `origin` rather than
  fetching from it - a checklist has no business rewriting your refs - and names
  the fetch for you to run.
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
  says the cut was safe - and it runs only the stages whose files the cut touched
  (`scripts/ci/verify.py`), so a comment cut costs the hooks, not the suite. And if it touched a file the change had not touched before,
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
  goes stale: after round after round of bot review, one adversarial pass still found a
  button whose label promised six items and committed twenty-four, a rollback that
  deleted a row it never added, and tests that could not fail.

- **Read a review with `scripts/pr-review.sh <pr>`, not by eye.** It prints every
  round, the newest review in full, every `Suppressed comments` block from every
  round, every comment on the conversation, the threads still open, and the checks
  on the head commit; it exits non-zero when the newest review predates the head
  commit, when a thread is unanswered, and when a check failed, has not finished,
  or never ran. Your own reviews do not count towards that: replying to a thread
  posts a review, so answering a finding would otherwise mark the head as
  reviewed by the act of answering.

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
  none of the above, and it can be where every finding is, under a
  correct "did not run" and a correct "0 unresolved". Those are printed too, and
  they are the one section the exit status says nothing about — a comment records
  nowhere whether it was answered, so the script counts them and leaves the
  judgement to you.

  Answer a finding you decline where the next reader will find it — in the PR, or
  in the commit that declines it.
- **A review is tied to a commit, so re-request it after every push**, and do not
  read a clean round as a verdict. Three reviews of one commit can find nothing twice and a
  real defect, in the same unchanged code, the third time. One clean round means nothing was
  found this time.
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

- **Answer what was asked, and stop.** A question about one file does not get a whole data model
  back.
- **Write in [ASD-STE100](https://www.asd-ste100.org/) (Simplified Technical
  English).** This rule applies to pull request descriptions, to comments on issues
  and pull requests, and to commit messages. Use these rules:

  - Keep each sentence short. Use not more than 20 words in an instruction and not
    more than 25 words in a description.
  - Write about one topic in each sentence.
  - Use the active voice and the simple tenses.
  - Use one word for one thing. Do not use a different word for variety.
  - Do not use idioms or figurative language.

  Code comments, `docs/` and the AGENTS files keep their current style.
- **The account holder approves the text first.** They answer the reply, and a
  comment cannot be unsent.
- **Say who wrote it**, on a line of its own at the end - the agent by name, and
  the GitHub username it was posted for. The guard refuses a `gh` command that
  posts a body without it.

  ```text
  Posted by Claude on behalf of @<github-username>
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
  shape the code is avoiding and something could reintroduce - `mail.py` on why an
  unconfigured instance sends nothing, rather than defaulting to a relay that would
  address real mail to the people a resolution names. Cut it when it only narrates the
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
