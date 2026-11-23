# AGENTS.md

Operating rules for AI coding agents working in this repository (Secretary
Records, the replacement for the Whimsy Secretary Workbench). Claude Code loads
this file via `CLAUDE.md`; other agents read it directly.

**Read [AGENTS.shared.md](AGENTS.shared.md) first.** It holds the rules that every
Tooling repository shares - the container wrappers, worktrees, the quality ratchet,
what BAT answers, the pull request and review rules - and it comes from
[apache/tooling-devkit](https://github.com/apache/tooling-devkit). This file adds
the project shape and the rules that are only this repository's, and cannot make a
shared rule weaker. An issue or pull request number cited in the shared file is the
kit's or tooling-agenda's, not this repository's.

For full environment setup see [CONTRIBUTING.md](CONTRIBUTING.md), and for the
end-to-end harness see [e2e/README.md](e2e/README.md). When something behaves
strangely - a pull request with no checks, a suite that fails a different spec each
run - look in [docs/troubleshooting.md](docs/troubleshooting.md) before
investigating it fresh. If any instruction here conflicts with an explicit request
from the user, ask before overriding it.

What the tool must do is [REQUIREMENTS.md](REQUIREMENTS.md), and the tool it
replaces is [WORKBENCH.md](WORKBENCH.md). Both are agreed with the Secretary:
change them by agreement, never as a side effect of a feature.

## Non-negotiables of this repository

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

The shared non-negotiables are in AGENTS.shared.md. Where it names a `make` target this repository does not
have yet (`make container-test-fast`: the fast stage of `scripts/ci/fast_stage.py` is in the kit, and nothing
here runs it), use `make container-pytest` for the question you have.

## Project shape

| Layer | Technology | Location |
| ----- | ---------- | -------- |
| Backend | Python 3.14+ / Quart (async) via `asfquart` | `app/` |
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
