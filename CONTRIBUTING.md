# Contributing to Secretary Records

This guide covers everything you need to develop, test, and submit changes. The
development setup is the Board Agenda Tool's
([apache/tooling-agenda](https://github.com/apache/tooling-agenda)), so somebody
who has worked on one has worked on both.

## Prerequisites

- **Container runtime**: [Docker](https://docs.docker.com/get-started/get-docker/) or
  [Podman](https://podman.io/docs/installation)
- **An ASF account**, to sign in through ASF OAuth

> All development, testing, and builds happen inside containers.
> You do not need Python, Node.js, or any other tooling installed on your host.

## Never use real submissions

The documents this tool handles carry names, residence addresses and signatures.
A development instance, a test fixture or a screenshot never holds a real one,
not even redacted: fixtures are synthetic (REQUIREMENTS.md, N-8). Logs and error
messages never contain document contents or extracted fields (N-3).

## Quick Start

### 1. Clone the repository

```shell
git clone git@github.com:apache/tooling-secretary-records.git
cd tooling-secretary-records
```

### 2. Give yourself a role

`config.yaml` is gitignored and made from the example. The roles are a configured
list for now (`roles:` in it; REQUIREMENTS.md R-3 asks for the Secretary and
Assistant Secretary to come from `committee-info.txt` later). Put your own Apache
id in one of them to see the tool as that role:

```shell
cp config.yaml.example config.yaml
# then, under roles:, e.g.  secretarial_team: [<your-asf-id>]
```

Members of the Tooling PMC are the Admin role whatever the lists say.

### 3. Choose your development method

#### Option A: Devcontainer (recommended)

The `.devcontainer/` setup works with both Docker and Podman and provides
a fully configured VS Code development environment.

**For Podman users**, add this to your VS Code settings:

```json
{
  "dev.containers.dockerPath": "podman"
}
```

Then open the project in VS Code and select **"Reopen in Container"** from
the command palette. The container will build, install dependencies, and
set up SSL certificates automatically. It writes `config.yaml` from the
example, so add your role again afterwards.

Once inside the container, start the application:

```shell
make dev
```

The app is available at <https://localhost> and, directly, at
<https://localhost:4500>. Sign in at the first: ASF OAuth refuses a redirect URI
that carries a port, so signing in at `:4500` fails with "Invalid redirect URI
specified". `make dev` starts a Caddy reverse proxy (`scripts/Caddyfile`) that
gives the app that port-less address, which is why the container publishes port
443 as well.

File edits on your host are reflected immediately inside the container.
The Vite dev watcher handles frontend hot reload, and Quart's debug mode
reloads on Python file changes.

#### Option B: Docker/Podman Compose

Build and run without VS Code:

```shell
# Build the image
docker compose build        # or: podman compose build

# Start the container
docker compose up            # or: podman compose up
```

The container runs the full setup automatically (dependencies, assets, SSL
certs) and starts the application. Access it at <https://localhost>, where
signing in works (see above); <https://localhost:4500> reaches the app without
the proxy. Publishing port 443 can need a privileged runtime, as a rootless one
may refuse to bind it.

> The browser will warn about an untrusted SSL certificate. Either override
> the warning, or extract and install the CA certificate:
>
> ```shell
> docker compose cp secretary-records:/run/secretary-records/state/rootCA.pem .
> ```

To open a shell in the running container:

```shell
docker compose exec secretary-records /bin/sh
```

#### Option C: The container scripts

Without VS Code or Compose, `make container-keep` starts the dev server in a
detached container on <http://localhost:8080>, and `make container-stop` takes
it away. It needs a `config.yaml` (step 2).

## Architecture Overview

| Layer | Technology | Location |
| ----- | ---------- | -------- |
| Backend | Python 3.12+ / Quart (async) via `asfquart` | `app/` |
| Frontend | Svelte 5 / TypeScript / Vite | `assets/src/` |
| Database | SQLite / SQLModel / Alembic | `app/db/` |
| Templates | Jinja2 | `app/client/templates/` |
| Styling | Bootstrap 5 with the design book's tokens | `assets/src/css/` |

The backend serves server-rendered Jinja2 pages with Svelte islands mounted
client-side. SVN stays the source of record; the database is workflow state and
cache, and must be rebuildable from raw mail plus SVN (REQUIREMENTS.md,
section 5).

**Key directories:**

- `app/access.py` and `app/models/role.py` — who holds which role, and `require_role`
- `app/api/routes/` — the JSON API
- `app/client/` — the pages, and the dev-only test login
- `app/admin/` — server status and the data browser
- `app/db/models.py` — database models
- `app/db/migrations/versions/` — Alembic migrations
- `app/md/documentation/` — the in-app Documentation page
- `assets/src/js/components/` — Svelte components

## Making Changes

### Backend (Python)

- Add new API routes in a module under `app/api/routes/` and import it in
  `app/api/routes/__init__.py`
- Put `require_role(..., to="...")` on every route that changes something and
  on every route that reads the queue, and test that a role without it is
  refused
- Add new database models in `app/db/models.py`, then create an Alembic
  migration in `app/db/migrations/versions/`

### Frontend (Svelte/TypeScript)

- Components go in `assets/src/js/components/`, pure logic in `assets/src/js/libs/`
- A page's island is an entry in `assets/src/js/pages/`, listed in
  `assets/vite.config.js`
- The frontend uses Svelte 5 runes (`$state`, `$derived`, `$props`)
- When you change the UI, add or update the frontend tests so the affected
  behaviour stays covered: unit tests (vitest) in `assets/src/` and, for
  user-facing flows, the Playwright end-to-end tests in `e2e/tests/`
  (see [Running tests](#running-tests))
- When you add or change a user-facing UI feature, include screenshot(s) in
  the pull request showing the new or updated screens. Let the Playwright e2e
  test capture them: call `page.screenshot(...)` in the spec that exercises the
  feature (see [`e2e/README.md`](e2e/README.md)). The images land in
  `e2e/screenshots/` (gitignored) after `make e2e-test`, and CI attaches the
  same directory to every run as the `e2e-screenshots` artifact, so you can
  download them from a run instead of reproducing them locally. Drag the image
  into the PR description, or publish them with `scripts/pr-screenshots.sh`.

### Design changes

The interface has a design book: [`docs/design-book.html`](docs/design-book.html).
Open it in a browser — it is a single self-contained page, in 22 chapters across
five parts, and every specimen in it is live markup rather than a screenshot. It
covers the colour and type tokens, the information types (how a person, a date, a
revision, a report body are rendered), the component set and the states each one
must answer, the keyboard and accessibility model, and a page-by-page account of
BAT's routes.

The book is BAT's, and this repository keeps a copy of it: this tool follows the
same design system (REQUIREMENTS.md, N-6 and section 5). BAT's copy is the
source. A change to the book is made there first and copied here, so the two
tools do not drift apart. The tokens in `assets/src/css/app.css` are the book's,
under the book's own `--bat-*` names.

Read the chapter that covers what you are touching before you touch it. Most
interface questions — which button variant, which heading level, whether a value
gets a chip — already have an answer there, and answering them again per page is
how inconsistency accumulates.

#### When you may just build it

A design system that cannot be extended gets worked around, and a system anyone
can extend stops being one. Three gates, matched to what is actually at stake:

| Change | What it needs |
| ------ | ------------- |
| **Using the system** — a new page from existing components, a new column, new copy | Nothing. Build it. If composing a page needs a discussion, the book has failed. |
| **A new configuration** — a component variant, a new empty state, a new field type | A line in the PR description saying which existing thing it extends. Cheap to review, and it stops two people inventing the same variant twice. |
| **A new token, hue, chip, icon meaning or type role** | An issue first, and a change to the book in the same PR. |

That third gate is the one that matters. In BAT, the Bootstrap class `bg-warning` came to
mean three different things — flagged, awaiting pre-approval, and raises board
issues — because no gate existed and each addition was locally reasonable.

#### Rules that keep it honest

- **The book changes in the same commit as the code.** A design book that
  documents last quarter's product is worse than none, because it is trusted.
- **A chip marks an exception, not a fact.** Roles, offices, dates and counts are
  facts and belong in a labelled column. If a value is true of nearly every row,
  it is a column.
- **Name tokens and classes for meaning, scoped to their component.**
  `--stop`, not `--red`; `.sr-chip-count`, not `.n`. A short generic name collides
  silently the moment two components nest, and equal specificity means whichever
  rule comes second wins.
- **No new hardcoded `px` font sizes and no `transition: all`.** Both break text
  zoom or animate properties nobody intended.
- **Every UI change ships both layers of tests**, as
  [Frontend](#frontend-sveltetypescript) already requires. A component without a
  test is a component that will drift.

### Workflow changes

What the design book is to the interface, the **Documentation page** is to the
workflow: [`app/md/documentation/`](app/md/documentation/), one markdown file per
section, composed by `client.documentation` and rendered at `/documentation`.

It is the only account of the process a user ever reads: a section per role,
what a click commits and when, and what happens automatically. So it is where
somebody looks to find out what the tool does, and the place they are misled
from when it is stale.

Read the section that covers what you are touching before you touch it. If you
cannot find the behaviour you are about to change described anywhere, that is
worth noticing: either it is genuinely internal, or the page has a hole in it and
your PR is a good moment to fill it.

#### When a change needs the page changed with it

| Change | What it needs |
| ------ | ------------- |
| **Internals** — a refactor, a query, a migration, anything with no observable change in what a user can do | Nothing. |
| **A control moves, is renamed, or changes its keystroke** | The sentence that names it, updated in the same PR. Usually one line. |
| **Someone can now do something they could not, or a role gains or loses a control** | The role's section, updated in the same PR — and if it is a new act rather than a new button, say what it commits and when. |
| **A new step in the process, or a change to what happens automatically** | `overview.md` as well, since that is where the order of work is written down. |

#### Rules that keep it honest

- **The page changes in the same commit as the code**, for the reason the design
  book does: documentation that describes last quarter's product is worse than
  none, because it is trusted.
- **Say who, not just what.** Every claim about a control is a claim about a role,
  and the page is read by people checking whether something is theirs. If a route
  refuses a role, the page has to say so — those two drifting apart is the failure
  this page exists to prevent.
- **Do not put screenshots in the page.** A screenshot of a layout starts rotting
  the day it is taken. PR screenshots are a separate thing and are still required.
- **Link between sections by anchor** — `#doc-secretary`, `#doc-admin` — rather
  than by "above" and "below".
- **Write for the person doing the job**, not for the person who built it.
  Repository paths, config keys and module names belong in `docs/`; what belongs
  here is what to press and what happens next.

`docs/` is for people changing the code, and this is for people using the tool.
When both need saying, put the account here and link to `docs/` for the depth.

### Database migrations

Migrations use Alembic with sequential numbering (`0001`, `0002`, ...).
`app/db/migrations/env.py` numbers a generated one for you:

```shell
scripts/container.sh uv run alembic -c app/db/alembic.ini revision --autogenerate -m "<description>"
```

The app applies pending migrations on startup, and refuses to start when the
models and the migrated schema disagree. The admin data browser shows only the
tables and columns `BROWSABLE` lists, and a test fails on a table that is in neither
`BROWSABLE` nor `NOT_BROWSABLE` (see AGENTS.md).

## Code Quality

### Pre-commit hooks

The project uses [prek](https://pypi.org/project/prek/) (a pre-commit hook
runner) with the hooks defined in `.pre-commit-config.yaml`. Run all checks
with:

```shell
make check
```

A new file is invisible to the hooks until it is staged: `git add` it first.

This runs the following checks on all files:

**Guards** (fast, and specific to mistakes that have bitten BAT before):

- **secure-context-only browser APIs** — `crypto.randomUUID` is undefined
  outside a secure context, so it throws when the dev server is reached over
  plain http on anything but localhost.
- **a type assertion that erases the check** — `as unknown as T` and `as any`
  in the frontend and the specs.
- **a committed `.env`** — it would hold credentials.
- **the scripts' own tests** — the Bash guard, the review reader, the preflight
  scan, the container wrapper, node_modules provisioning and the worktree
  cleanup each have a `.test.sh` beside them, run when they change.

**Python:**

- **ruff** — Linting and formatting (rules: isort, pycodestyle, pyflakes,
  pep8-naming, ruff-checks, flake8-tidy-imports, flake8-type-checking,
  pyupgrade)
- **pyright** — Static type checking (Python 3.12, excludes tests and
  migrations)

**Templates & styles:**

- **djlint** — Jinja2 HTML template linting and formatting
- **stylelint** — CSS linting with auto-fix
- **yarn format:check** — Prettier, over `assets/src/**/*.{css,js,svelte}`. Its
  slower siblings, svelte-check and eslint, run separately - see
  [Linting the frontend](#linting-the-frontend).

**General:**

- **shellcheck** — Shell script linting
- **markdownlint** — Markdown linting (excludes tests, and REQUIREMENTS.md and
  WORKBENCH.md, which change by agreement rather than by a fix)
- **zizmor** — GitHub Actions security scanning
- **check-yaml**, **check-toml** — YAML/TOML validation
- **end-of-file-fixer**, **trailing-whitespace**, **mixed-line-ending**
- **insert-license** — Ensures Apache 2.0 license headers on Python files
- **uv-lock**, **uv-export** — Keeps `uv.lock` and `requirements.txt` in
  sync

### Running tests

```shell
make test
```

Tests use pytest with async support. Always run tests inside the container.

The suite runs in parallel (pytest-xdist), across as many workers as the machine
has cores, capped at six. Override the count when a run needs it:

```shell
make test PYTEST_WORKERS=1    # serial - readable output, and no interleaving
```

Reach for the serial run when a failure is hard to read among other workers'
output, or when a test hangs and you need to see which one. `make test-coverage`
is serial already.

The fixtures in `tests/conftest.py` are `app` (built, never started), `running_app`
(started, so the database exists and is migrated) and `authenticated_client`, which
signs in through the same dev-only `/e2e/login/<uid>?roles=...` route the
Playwright suite uses.

#### From a plain host shell

`make test` and `make check` assume you are already inside the container (the
devcontainer, or `docker compose exec secretary-records`). To run them from an
ordinary shell without installing anything on the host, use the `container-`
targets. They use the same Dockerfile and run the same commands, with the working
tree bind-mounted:

```shell
make container-verify       # check, lint, test and quality: what gates a push
make container-check        # the pre-commit hooks
make container-test         # pytest + vitest
make container-test-py314   # the same, on the Python 3.14 CI matrix version
make e2e-test               # Playwright, app and browser both containerised
make container-shell        # an interactive shell in the container
```

`make e2e-test` reaches the app by container ip over plain http, which is
deliberately **not** a secure context. Browser APIs gated on one
(`crypto.randomUUID`, clipboard, service workers) are unavailable there, so a
feature that depends on one fails in the suite rather than in front of a user
running the dev server on a LAN address. Pass a filter to narrow it:
`scripts/e2e-container.sh home`.

**Frontend unit tests** (vitest) live alongside the code in `assets/src/`, and
`make container-test` runs them.

**End-to-end tests** (Playwright) live in `e2e/` and drive the real app in a
browser. Add or update both whenever you change the UI. See
[`e2e/README.md`](e2e/README.md); CI runs the unit tests and the e2e tests via
the `frontend` and `e2e` jobs in `.github/workflows/build.yml`.

### Running the suites before pushing

The test suites are too slow for every commit, but not for every push. Three
hooks are registered on the `pre-push` stage. They are opt-in:

```shell
prek install --hook-type pre-push
```

That runs `make container-test` and `make e2e-test` before a push - the same
thing CI runs - plus `make lint` (see
[Linting the frontend](#linting-the-frontend)), which svelte-check makes too
slow for every commit alongside the near-instant hooks `make check` runs. The
first two need no host toolchain, running in the dev container; `make lint`
assumes `assets/node_modules` is already in sync rather than installing it
itself, for the same reason.

### Ruff configuration

Ruff is configured in `pyproject.toml` with a line length of 120 and
Python 3.12 target. Key rule sets: `E`, `W`, `F`, `G`, `I`, `LOG`, `N`, `RUF`,
`TID`, `TC`, `UP`.

`G` and `LOG` settle how a log line is written. The message is a format string
and its values are arguments, positionally:

```python
_LOGGER.info("filed %s document(s)", filed)   # yes
_LOGGER.info(f"filed {filed} document(s)")    # G004
```

The values reach the record as fields rather than as text already rendered into
it, and a `debug` the logger is sitting above costs nothing to skip. Use
`extra={...}` for anything a structured backend should index; the message's own
placeholders are not the place for it.

What the linter cannot see is whether the string and its arguments agree in
number. `logging` will not tell you either: it prints `--- Logging error ---`
to stderr and the call returns, so a wrong count leaves the suite green. BAT
has a test that formats every call against stand-ins (its
`tests/test_logging_calls.py`); port it when there are enough calls to need it.

And a log line never carries a document's contents or a field extracted from
one (REQUIREMENTS.md, N-3): an id and a status, not a name and an address.

The complexity and dead-code rules (`C901`, `PLR09xx`, `ERA001`, `ARG0xx`) are
deliberately *not* in that list. The quality ratchet selects them instead, and
counts them against the commit you branched from rather than failing on them.

### The quality ratchet

Complexity, dead code, duplication and coverage are measured against the same
meters read on the commit your branch would merge into. The build fails when one
gets worse. An improvement needs no recording: it is locked in as soon as it
merges, because the next branch is measured against a base that already has it.

```shell
make quality-base    # measure the merge base, once per pull
make quality         # what CI runs, against that
make quality-show    # the readings, worst first
```

CI takes that base from its own cache, written by the run on `main`;
`make quality-base` is the local equivalent and checks the merge base out into a
throwaway worktree. Nothing about it is committed, so there is no generated file
to conflict on in a rebase and no baseline to re-record.

Nothing has to be fixed to merge. [docs/quality-ratchet.md](docs/quality-ratchet.md)
explains the meters, what a key is, and how to allow a regression that is the
point of the change.

### Linting the frontend

```shell
make lint    # svelte-check, eslint
```

Prettier is checked separately (`yarn format:check`, or the `yarn-format`
hook in `make check`) because it is fast where this is not: svelte-check
checks the whole project rather than per-file. A `pre-push` `prek` hook runs
`make lint` for that reason (see
[Running the suites before pushing](#running-the-suites-before-pushing)),
rather than `make check`. CI runs it in the `frontend` job in
`.github/workflows/build.yml`, beside the vitest run that shares its
`yarn install`. The two complexity rules in `assets/eslint.config.js` are
warnings rather than errors on purpose, so `yarn lint` stays a gate on
correctness while the count of those warnings is what
the ratchet holds down.

## Useful Make targets

| Target | Description |
| ------ | ----------- |
| `make dev` | Start dev server with Vite watcher and Quart |
| `make dev-server` | Start dev server only (no asset watcher) |
| `make build` | Build frontend assets for production |
| `make check` | Run all pre-commit hooks |
| `make lint` | Run svelte-check and eslint over the frontend |
| `make test` | Run pytest and vitest (parallel; `PYTEST_WORKERS=1` for serial) |
| `make verify` | `check`, `lint`, `test` and `quality`, behind one exit code |
| `make quality` | Check the quality ratchet against the measured merge base |
| `make quality-show` | Print the current readings, worst first |
| `make quality-base` | Measure the merge base, into `state/quality-base.json` |
| `make sync` | Install frozen production dependencies |
| `make sync-dev` | Install all dependencies including dev tools |
| `make certs` | Generate SSL certificates with mkcert |
| `make container-verify` | `make verify` in the container, from the host |
| `make container-check` | Run the pre-commit hooks in the container, from the host |
| `make container-test` | Run pytest and vitest in the container, from the host |
| `make container-test-py314` | Run the tests against Python 3.14 |
| `make container-quality` | Run the quality ratchet in the container, from the host |
| `make container-quality-base` | Measure the merge base in the container, from the host |
| `make e2e-test` | Run Playwright with app and browser both containerised |
| `make e2e-test-host` | The same suite directly on the host (escape hatch) |
| `make container-shell` | Open a shell in the dev container |
| `make container-keep` | The dev server, detached, on :8080 |
| `make container-stop` | Take that container away again |

## Container Management

### Stopping the container

```shell
docker compose down     # stops and removes the container
```

### Clearing cached state

Volumes cache the Python virtualenv, node_modules, and SSL certs across
restarts. To start fresh:

```shell
docker volume rm secretary-records-venv secretary-records-node secretary-records-cert
```

A linked worktree has volumes of its own, named after it;
`scripts/worktree-done.sh` removes them with the worktree, and
`scripts/container-gc.sh` lists what removed worktrees left behind.

## Submitting Changes

1. Create a feature branch from `main`, in a worktree of its own (AGENTS.md)
2. Make your changes and ensure `make container-verify` passes
3. Run `scripts/pr-preflight.sh` for what the change obliges you to do
4. Submit a pull request against `main`

### Reading a Copilot review

```shell
scripts/pr-review.sh <pr>
```

Every round, the newest review in full, every **Suppressed comments** block from
every round, every comment on the conversation, the threads still open, and the
checks on the head commit. It exits non-zero when the newest review predates the
head commit, when a thread is still unanswered, and when a check failed, has not
finished, or never ran.

The summary line is not the review. A review that says *"generated no new
comments"* has often found something and collapsed it into a **Suppressed
comments (N)** block, which the PR page hides behind a fold and which is easy to
scroll past.

Worse, a suppressed finding opens no thread and is not one of the pull request's
review comments — it lives only in the body of the round that raised it. A clean
round straight after one therefore leaves it nowhere a reader would look, which
is why the script sweeps every round rather than the newest.

Nor is a clean round a verdict. BAT's #729 had three reviews of one commit: the first
two found nothing, and the third found a real defect in that same unchanged code.

The checks are in the same shape, which is why they are read here too. `gh pr
checks` prints nothing when there is nothing to print, and nothing is what a
branch conflicting with `main` looks like: no workflow started, no output, no tick
either way. So no checks at all is not a pass, one still running is not a pass,
and one that was skipped is not a pass — each is said in words, with the merge
state beside it so an empty list says which of the reasons it is. What it cannot
see is a workflow whose `paths:` filter took it out of the run: that leaves no
entry in the list at all, so the remaining jobs read as a full set.

And a review need not be a review at all. A comment on the conversation is not a
review, opens no thread, and is not one of the pull request's review comments — so
on BAT's #751–#753 the script reported *"THIS REVIEW DID NOT RUN"* and *"unresolved
threads: 0"*, both correctly, over three comments carrying eight findings between
them. Those are printed too, and they are the one section the exit status ignores:
nothing on a comment records whether it was answered, so it counts them and says
so rather than guessing.

It is worth opening every time. On BAT that block has held a route
that let anyone overwrite a report's own text, a change that deleted a report's
attachment from the meeting, a regex whose stated safety property was false, and
a test that asserted a 404 from an id that never existed while claiming to cover
something else. None of those were in the headline.

A review is also tied to the commit it ran against, so it does not carry forward:
after pushing, check that the newest review's commit is the branch head before
treating it as covering your change, and ask for another if it is not.
[docs/troubleshooting.md](docs/troubleshooting.md) has the commands for reading a
review in full and for requesting one.

Where you disagree with a finding, say so in the PR or in the commit that
declines it. The next reader cannot otherwise tell whether it was weighed or
missed, and a point that goes unanswered tends to come back a round or two later.

## Communication

- Mailing list: <dev@tooling.apache.org>
- Issues: <https://github.com/apache/tooling-secretary-records/issues>
