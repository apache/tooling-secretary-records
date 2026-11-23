---
description: Run the Playwright end-to-end suite in containers, narrowed to a filter
argument-hint: "[filter] (a spec name or test title; omit to run everything)"
---
<!-- Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy. -->

# Run the end-to-end suite

Run it with `scripts/e2e-container.sh $ARGUMENTS`, in the background, and read
[e2e/README.md](../../e2e/README.md) if anything about the harness is unclear.

Three things decide whether the run tells you anything:

- Starting the app container costs about seventy seconds, which dwarfs a narrow run.
  `DEVKIT_KEEP=1` leaves it up for the next one, taking a three-test run from 108s to 40s.
  Use it while iterating on a spec - `e2e/` is bind-mounted, so a spec edit needs no
  restart.
- A change under `app/` or `assets/` does need one. Drop `DEVKIT_KEEP` after one, or the
  suite is testing the build from before the change and its answer means nothing.
- Run the whole suite without `DEVKIT_KEEP`, and expect eight to fifteen minutes: other
  sessions are working in this repository at the same time and the machine is shared. A
  timing-sensitive assertion can fail under that load, so re-run a lone failure before
  believing it - and say plainly which it turned out to be.

The suite reaches the app by container ip over plain http, which is deliberately not a
secure context. A feature that depends on one fails here rather than in front of a user.

Screenshots the specs capture land in `e2e/screenshots/` on the host. If this run was to
produce them for a pull request, publish them with `scripts/pr-screenshots.sh`.
