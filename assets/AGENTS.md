# assets/

Rules for the frontend. The repository-wide contract is in the root
[AGENTS.md](../AGENTS.md), and what the design system has already decided about
how it looks is in [docs/design-book.html](../docs/design-book.html) — BAT's
book, which this tool follows. Read the chapter covering what you are touching
before you touch it, together with the `frontend-design` skill.

## Where things go

Components live in `src/js/components/`, pure logic in `src/js/libs/`, and each
page's island is an entry in `src/js/pages/` that is also listed in the `input`
of `vite.config.js`. Use Svelte 5 runes (`$state`, `$derived`, `$props`).

The colour, type and space tokens in `src/css/app.css` are the design book's,
under its own `--bat-*` names. Use a token rather than a literal; a new token
needs an issue and a change to the book first (CONTRIBUTING.md, Design changes).

## The Documentation page is prose

The in-app Documentation page is markdown, one file per section, in
`app/md/documentation/`. `client.documentation` composes them and marks the
sections whose role the reader holds.

Change what a user can do and change that page in the same PR — it is the only
account of the workflow they ever read. Read
[CONTRIBUTING.md](../CONTRIBUTING.md#workflow-changes) for which changes need it
and what keeps it honest.

## Tests, three suites

A UI change needs vitest tests alongside the code here *and* Playwright coverage
in `e2e/tests/` for the user-facing flow. CI runs them as the `frontend` and
`e2e` jobs; a UI change without matching test updates is incomplete.

The vitest half is two suites, told apart by the filename:

| File | Suite | What it is for |
| ---- | ----- | -------------- |
| `*.test.ts` | `logic` | pure modules, no DOM |
| `*.component.test.ts` | `components` | a component mounted in happy-dom and driven |

**A component that fetches belongs in the second one.** Playwright owns the flow
a user walks through; what a component does when an answer is late is a question
that suite cannot afford to ask - it needs the application container and takes
tens of seconds a case, so those cases do not get written. In BAT, what went
unwritten was exactly where the defects were: a stale answer painting over a
newer one, a request landing after its form had closed, a control committing
what the list was no longer showing.

Write them so a failure is reachable. Four of BAT's first asserted a state that
was already true before the thing under test happened, so removing the trigger
left them all passing. Waiting a fixed amount and then asserting that nothing
changed passes both when an answer was dropped and when it has not been looked
at yet; ask for something *after* releasing the held answer and wait for that to
be drawn instead.

The cases worth writing for one are the ones about time. For every fetch: an
answer arriving late, after a newer one, after the surface closes, after an
error, after a success. And what the component draws when its *first* read
fails - a signed-in reader without a role is answered 403.

`vitest.setup.ts` calls `vi.unstubAllGlobals()` after each test, so stub a global
with `vi.stubGlobal` and it goes back on its own. An assignment
(`globalThis.fetch = ...`) is **not** undone by that and is left for the next test
to find, so use the stub.

Everything below runs in the dev container, like everything else here — `yarn` on
the host is refused, and rightly:

```sh
make container-test          # vitest, alongside pytest
make e2e-test                # Playwright, app and browser both containerised
scripts/container.sh make lint    # svelte-check, eslint
```

`lint` runs in the `frontend` job too, along with the coverage floor for
`src/js/libs`. The components are deliberately not on that floor: a meter of
their own is a separate decision from having somewhere to test them. Run it
before you push; a formatting-only failure is the easiest CI red to avoid.

## Traps BAT has already hit

- **No secure-context-only APIs.** `crypto.randomUUID`, the clipboard and service
  workers are undefined when the app is reached over plain http on anything but
  localhost, which is exactly how the e2e suite reaches it. A pre-commit hook
  forbids the raw `crypto.randomUUID` call; wrap it with a fallback (BAT's
  `utils.uniqueId()`) if you need one.
- **Every screen size.** Phone and tablet matter as much as the desktop, and the
  tablet width is where a layout is tightest.
- **Prettier breaks a line between an element and the punctuation after it**, and
  Svelte draws that line break as a space: `<strong>{uid}</strong>, with` comes
  out as `ada , with`. Build such a sentence as one string
  (`components/RoleSummary.svelte` does).
- **Splitting a module can make `file-length` worse, in the files you did not
  touch.** Moving types or helpers out of a module and pointing every consumer at
  the new one adds an import line to each of them, and a file sitting just under
  the threshold crosses it. Re-export from the module that already had them
  instead, so only the files that want nothing else from it reach past.
