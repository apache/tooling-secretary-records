# End-to-end tests (Playwright)

Browser-level tests that drive the real app, signed in as whoever a spec needs.

## How it works

- **`serve.py`** launches the app over plain HTTP (`127.0.0.1:8099`) with
  `e2e/e2e-config.yaml`, after throwing away `state-e2e/` so the run starts
  with an empty database. It sets `SR_E2E=1`, which enables the dev-only
  `/e2e/login/<uid>` route in `app/client/routes.py`. That route mints an
  asfquart session cookie for any uid without going through ASF OAuth, and
  returns 404 unless `SR_E2E=1`, so it is never reachable in production.

  `?roles=secretary,admin` grants those roles (`app/models/role.py`) to that
  session alone. They are kept in the session's own metadata, so a role one
  spec grants cannot leak into another - which BAT's test login, granting onto
  a shared roster, did. A name that is not a role is ignored.
- **One server serves the whole run**, so a spec that changes state leaves it
  changed for the specs after it. Give each test a uid of its own. BAT grew a
  supervisor that restarts the server for each spec file and a pool of servers
  for parallel workers (its `e2e/supervisor.py`); port it when a spec first
  needs that isolation.
- **`tests/support/fixtures.ts`** is what every spec imports `test` and
  `expect` from. It fails a test on an exception the page threw or anything the
  Content Security Policy refused, neither of which shows on screen.
- **`playwright.config.ts`** launches `serve.py` via its `webServer` option.
  Set `SR_E2E_URL` (or `SR_E2E_EXTERNAL_SERVER=1`) to run against a server you
  started yourself; `scripts/e2e-container.sh` does.

## Running

From the repository root:

```shell
make e2e-test
```

This runs everything in containers (`scripts/e2e-container.sh`): the app in the
project's dev container, Playwright in the official image, on a shared network.
It builds the frontend assets, waits for the app to come up, and cleans up
afterwards. Nothing is installed on the host. The last line says `e2e: PASSED`
or `e2e: FAILED`, because an exit status does not survive a pipe.

Pass a filter to run a subset, and keep the app container between runs while
you iterate on a spec:

```shell
make e2e-quick FILTER=home
```

`e2e/` is bind-mounted, so a spec edit needs no restart; a change under `app/`
or `assets/` does - drop `SR_KEEP` (or remove the server container) after one,
or the suite tests the build from before it.

The browser reaches the app by container ip over plain HTTP, which is
deliberately **not** a secure context - unlike `127.0.0.1`, which is. Browser
APIs gated on a secure context (`crypto.randomUUID`, clipboard, service
workers) are therefore unavailable, so a feature depending on one fails here
rather than in front of someone running the dev server on a LAN address. This is
not an accident of the setup; don't "fix" it by switching to localhost.

`make e2e-test-host` runs the same suite directly on the host instead. That
needs node, the Playwright browsers and Python installed locally, which the
container-only rule in [AGENTS.md](../AGENTS.md) otherwise rules out; it is kept
for debugging the harness itself and for machines without a container runtime.

CI runs the suite against `127.0.0.1` - see the `e2e` job in
`.github/workflows/build.yml`.

## Screenshots

A spec that exercises a user-facing feature captures it with
`page.screenshot({ path: 'screenshots/<name>.png' })`. The files land in
`e2e/screenshots/` on the host, CI keeps the directory as the `e2e-screenshots`
artifact, and `scripts/pr-screenshots.sh` publishes them for a pull request.

## Type-checking

Running the suite says nothing about the types in it. Playwright transpiles a
spec and runs it, so a spec whose types are wrong runs anyway, and passes for as
long as the wrongness does not happen to throw.

```shell
make e2e-typecheck
```

Seconds, and no application container: it runs `tsc --noEmit` in the Playwright
image against the same `node_modules` volume the suite installs into. CI runs it
in the `e2e` job between `npm ci` and the browser install.

`tsconfig.json` is `strict`, plus `noUnusedLocals`, `noUnusedParameters` and
`noUncheckedIndexedAccess`. What each is for is a way a spec can pass while
measuring nothing:

| Caught | Why it matters here |
| ------ | ------------------- |
| `TS7006` an untyped parameter | an untyped `page` unchecks every call made on it, which is most of what a helper does |
| `TS2820` a name misspelt in a typed position | a wrong property name reads `undefined`, and `undefined === undefined` passes |
| `TS18048` an index that may be `undefined` | the same comparison, reached by `levels[i]` instead |
| `TS6133` a binding nothing reads | usually a check somebody meant to make and did not |

What it does **not** catch is a deliberate `as unknown as`: a double assertion
beats any checker. A pre-commit hook refuses one; write the type instead.
