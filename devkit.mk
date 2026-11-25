# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
#
# The make targets that call the kit's scripts, shared by every service. A service's own Makefile
# includes this file and keeps only what is its own - init, build, certs, dev, run, sync, and any
# target of its own:
#
#   include $(dir $(firstword $(MAKEFILE_LIST)))devkit.mk
#
# Anchored on the Makefile rather than on the directory make runs in, so that `make -f` from elsewhere
# finds this file, and the checkout it names below is the one the Makefile is in. The first word of
# MAKEFILE_LIST rather than the last: the last is whatever was read most recently, which after another
# include is that file.
#
# Where the include goes does not change the default goal: a plain `make` runs the first target of the
# service's own Makefile, as it did before. A service sets these, if at all, in its Makefile before the
# include, since make reads each where this file uses it:
#
#   PYTEST_WORKERS_MAX   the cap on pytest workers (6); the environment does not set it
#   ASSETS_MANIFEST      the built frontend, which `e2e-test-host` builds first where it is missing
#
# A target defined here is not defined again in the service's Makefile: make would warn, and run the
# recipe it read last.

devkit_mk_goal := $(.DEFAULT_GOAL)

.PHONY: check check-all lint test test-python test-frontend test-fast test-coverage pytest verify verify-fresh \
	quality quality-show quality-base snapshot snapshot-base snapshot-full \
	e2e-test e2e-test-host e2e-quick e2e-typecheck \
	container-check container-check-all container-test container-test-fast container-test-coverage container-test-py314 \
	container-pytest container-verify container-verify-fresh container-quality container-quality-base \
	container-snapshot container-snapshot-base container-snapshot-full container-shell container-keep container-stop

# The hooks over what this checkout changed since it left CHECK_REF - committed, staged,
# modified or untracked - which is what a commit needs. Every file instead where the list
# cannot say what a change reaches - a deletion, a rename, a config file, no merge base:
# scripts/ci/changed.py. `check-all` runs every hook over every file: `verify` uses it before a
# push. Untracked files are among them, which `prek run --all-files` leaves out: a gate whose key counts
# them would otherwise pass without a hook having read one. CI's analyze job runs prek on a clean checkout.
CHECK_REF ?= origin/main

check:
	@uv run scripts/ci/changed.py --ref $(CHECK_REF)

check-all:
	uv run scripts/ci/changed.py --all

# svelte-check and eslint - prettier's own check runs separately (`check`'s yarn-format hook)
# because it is fast where this is not. Kept out of `check` for that reason, but it does run
# as a `pre-push` prek hook; see CONTRIBUTING.md. CI runs it in the frontend job, beside the
# vitest run that shares its yarn install.
lint:
	cd assets && yarn lint

# pytest runs across worker processes (pytest-xdist). Each worker pays the import of the
# application and its own setup before it runs a test, so past about six of them that
# outgrows what the extra parallelism saves, which is why this caps rather than using
# `-n auto`. Run `make test PYTEST_WORKERS=1` for a serial run - the one to reach for when a
# failure is easier to read without interleaved output, or a test hangs.
#
# From the host, the container-* targets that run the whole suite choose the count
# themselves, up to the same cap, from the memory the machine has free:
# DEVKIT_SIZE_WORKERS, read by scripts/container-size.sh. `DEVKIT_SIZE_WORKERS=0` puts the
# fixed settings back.
# The service's value where its Makefile set one, and 6 otherwise - not one from the environment, which
# would reach the $(shell) below unquoted. A value on the command line still beats both.
ifneq ($(origin PYTEST_WORKERS_MAX),file)
PYTEST_WORKERS_MAX := 6
endif
PYTEST_WORKERS ?= $(shell n=$$(nproc 2>/dev/null || echo 1); if [ "$$n" -gt $(PYTEST_WORKERS_MAX) ]; then echo $(PYTEST_WORKERS_MAX); else echo "$$n"; fi)
DEVKIT_SIZE_WORKERS ?= $(PYTEST_WORKERS_MAX)
# Recursive rather than immediate, so that the target-specific override on `pytest` below
# is the one that counts when that target expands it.
PYTEST_PARALLEL = $(if $(filter-out 0 1,$(PYTEST_WORKERS)),-n $(PYTEST_WORKERS))

# A full run of each half leaves what the fast stage below reads (state/fast-stage/,
# scripts/ci/fast_stage_record.py): pytest what each test file took, and both halves a record of the
# commit and tree they ran on, which `passed` marks when the half passed. When both are marked, on
# the same commit and a clean tree, that commit is the green base - from `make test`, or from
# `verify`'s two stages, which run at the same time and may each be skipped as unchanged.
#
# The record of the last run goes first, with a line whose failure stops the recipe: a `begin` that
# never ran - `uv run` can fail before Python starts - must leave nothing for `passed` to take as
# this run's. `begin`, `record` and `passed` themselves never decide the status: the first and last
# may fail without stopping it, and the pytest line answers with pytest's.
FAST_STAGE = uv run python scripts/ci/fast_stage.py

test: test-python test-frontend

test-python:
	rm -f state/fast-stage/python.json
	-$(FAST_STAGE) begin python
	uv run pytest $(PYTEST_PARALLEL) --junitxml=state/fast-stage/junit.xml; status=$$?; \
		$(FAST_STAGE) record --junit state/fast-stage/junit.xml --status $$status; exit $$status
	-$(FAST_STAGE) passed python

test-frontend:
	rm -f state/fast-stage/frontend.json
	-$(FAST_STAGE) begin frontend
	cd assets && yarn test
	-$(FAST_STAGE) passed frontend

# The fast stage, for while you iterate: the tests most likely to catch a mistake in this change,
# stopped at the first failure - not the gate, which is `verify`. What it selects and why is in
# scripts/ci/fast_stage.py, with what the service adds in scripts/local/fast_stage.toml, and it prints
# both before it runs. FAST_BASE measures the change from where that commit and HEAD meet, rather than
# from the last green full run here (or the merge base with origin/main). A change that needs the whole
# suite still runs the selection, then the script exits 10 and names `make container-test`.
# make exits 2 for any failed recipe, so the 10 shows as make's last line, `Error 10` - which is
# why `container-test-fast` runs the script itself rather than a make inside the container, whose
# `Error 10` the outer make would follow with an `Error 2`.
FAST_STAGE_RUN = $(FAST_STAGE) run $(if $(FAST_BASE),--base $(FAST_BASE)) -- $(PYTEST_PARALLEL)

test-fast:
	$(FAST_STAGE_RUN)

# One question, asked as narrowly as you can ask it:
#
#   make pytest ARGS='tests/test_webapp.py -k login -q'
#
# Serial by default, which is the opposite of `test` and deliberate: each xdist worker pays
# its own setup before running anything, so for a handful of tests the setup costs more than
# the parallelism saves - and the output of a narrow run is worth reading, which interleaved
# output is not. `make pytest PYTEST_WORKERS=6 ARGS=tests/api` puts the workers back for a
# large selection, because a variable set on the command line beats the one set here.
pytest: PYTEST_WORKERS = 1
pytest:
	uv run pytest $(PYTEST_PARALLEL) $(ARGS)

# Everything that gates a push, behind one exit code. Five commands is four chances to
# run only some of them, and the one skipped is the one that fails in CI.
#
# The stages - check-all, lint, test-python, test-frontend, snapshot, quality - run at the
# same time, each into a log under state/verify/, and a stage that passed is not run again
# until something it reads changes: scripts/ci/verify.py, with the stages in
# scripts/local/verify.toml. `verify-fresh` runs every one.
#
# The end-to-end suite is deliberately not in here: it takes 8 to 15 minutes against the
# seconds these take, and it answers a different question. Run `make e2e-test` when the
# change touches what a browser sees.
verify:
	QUALITY_BASE=$(QUALITY_BASE) uv run scripts/ci/verify.py

verify-fresh:
	QUALITY_BASE=$(QUALITY_BASE) uv run scripts/ci/verify.py --fresh

# Coverage instrumentation slows the run down noticeably, so it is kept out of
# the default `test` target and run separately on demand. Serial: coverage
# combines fine across xdist workers, but the numbers are what this target is
# for, so it stays on the one code path that produces them unconditionally.
test-coverage:
	uv run pytest --cov=app --cov-report=term-missing --cov-report=html --cov-report=json

# --- The quality ratchet -------------------------------------------------
#
# Complexity, dead code, duplication and coverage, each measured against the same meters
# read on the commit this branch would merge into. `quality` fails when one gets worse.
# Nothing is committed and nothing needs re-recording: an improvement is locked in the
# moment it merges, because the next branch is measured against a base that has it. See
# docs/quality-ratchet.md.
#
# `quality-base` is what measures that commit, into a file under state/, which is not
# committed. It checks the merge base out into a throwaway worktree, so it costs a
# checkout and a second pass of the linters - run it again after pulling. CI does not use
# it: there the same file comes from the actions cache, written by the run on main.
#
# The two coverage meters read a report rather than running a suite: they are skipped
# unless `make test-coverage` (Python) or `yarn test --coverage` (frontend) has left one.
# `quality-base` cannot measure them at all, since that would mean running both suites
# against another commit, so locally the ratchet covers the seven meters that read source.
QUALITY_BASE ?= state/quality-base.json
QUALITY_REF ?= origin/main

quality:
	uv run scripts/ci/quality.py check --base $(QUALITY_BASE)

quality-show:
	uv run scripts/ci/quality.py show

# A worktree on a merge base another one has measured copies that reading rather than taking its
# own (scripts/ci/bases.py): the reading is keyed on the commit and on this checkout's meters.
quality-base:
	uv run scripts/ci/bases.py quality --ref $(QUALITY_REF) --out $(QUALITY_BASE)

# --- What the app answers ----------------------------------------------------
#
# Every GET route as each reader is answered, and what a fixed sequence of writes changes,
# recorded with this checkout and with the commit it would merge into, and compared file by
# file. Nothing is committed: `snapshot-base` records the merge base into state/, in a
# throwaway worktree with that commit's own tests/behaviour, and `snapshot` records this
# checkout and compares. A difference fails unless one of this branch's own commits carries a
# `Behaviour-Allow: <path or glob>` trailer, and a change to the harness itself a
# `Behaviour-Harness: <why>` one. `test` does not run it - it needs the hash seed fixed before
# the interpreter starts, which scripts/ci/behaviour.py sees to. Run the base again after a
# rebase. `snapshot` records only the parts of the record the change can reach;
# `snapshot-full` records everything. See scripts/ci/behaviour.py.
SNAPSHOT_REF ?= origin/main

snapshot:
	uv run scripts/ci/behaviour.py check --ref $(SNAPSHOT_REF)

# Recorded once for each merge base and shared by every worktree (scripts/ci/bases.py): the base
# records itself, so the commit is all that it depends on.
snapshot-base:
	uv run scripts/ci/bases.py snapshot --ref $(SNAPSHOT_REF)

snapshot-full:
	uv run scripts/ci/behaviour.py base --ref $(SNAPSHOT_REF)
	uv run scripts/ci/behaviour.py check --full --ref $(SNAPSHOT_REF)

# Runs the Playwright end-to-end tests in containers (see e2e/README.md).
e2e-test:
	scripts/e2e-container.sh

# Type-checks the specs without running them. Playwright transpiles a spec rather than
# checking it, so running the suite says nothing about its types, and this is the only
# thing that does. Seconds, and no application container: run it after editing a spec.
# CI runs it in the e2e job, between `npm ci` and the browser install.
e2e-typecheck:
	scripts/e2e-typecheck.sh

# One spec, against an app container left running from last time:
#
#   make e2e-quick FILTER=home
#
# Starting the app costs about seventy seconds, which dwarfs a narrow run; DEVKIT_KEEP keeps
# it for the next one and takes a three-test run from 108s to 40s. The e2e/ directory is
# bind-mounted, so spec edits are picked up live.
#
# The trap is that nothing under app/ or assets/ is: those are in the container this
# reuses, so after changing either, one plain `make e2e-test` (or removing the server
# container) is what makes the next answer mean anything. Run the whole suite that way
# too - a kept container has served whatever the last run left behind.
e2e-quick:
	DEVKIT_KEEP=1 scripts/e2e-container.sh $(FILTER)

# The same suite run directly on the host. This needs node, the Playwright browsers and what
# the app needs installed locally, which the container-only rule in AGENTS.md otherwise rules
# out - it is kept as an escape hatch for debugging the harness itself, and for environments
# without a container runtime. Prefer `make e2e-test`.
#
# The app keeps its state under state-e2e/, which this throws away first, so a run
# starts clean and leaves the checkout untouched.
e2e-test-host: $(ASSETS_MANIFEST)
	rm -rf state-e2e
	cd e2e && npm install && npx playwright install chromium && npx playwright test

# --- Running the checks from the host ------------------------------------
#
# The targets above assume you are already inside the dev container (via the
# devcontainer, or `docker compose exec` into it). These wrap them so they can be
# run from a plain host shell instead: same Dockerfile, same commands, working
# tree bind-mounted. Nothing is installed on the host.

container-check:
	scripts/container.sh make check CHECK_REF=$(CHECK_REF)

container-check-all:
	scripts/container.sh make check-all

# The whole suite, sized to the machine (DEVKIT_SIZE_WORKERS, above): container-pytest is
# not, since a narrow serial run holds a fraction of what the whole suite does, and
# waiting behind a gate would cost it more than it could save.
container-test:
	DEVKIT_SIZE_WORKERS=$(DEVKIT_SIZE_WORKERS) scripts/container.sh make test

# The narrow run and the one-signal run, each in a single container start:
#
#   make container-pytest ARGS='tests/test_webapp.py -k login -q'
#   make container-verify
#
# container-verify is four commands inside one container rather than four container
# starts, which is most of what makes it worth having as a target of its own.
container-pytest:
	scripts/container.sh make pytest ARGS="$(ARGS)"

container-verify:
	DEVKIT_SIZE_WORKERS=$(DEVKIT_SIZE_WORKERS) scripts/container.sh make verify

container-verify-fresh:
	DEVKIT_SIZE_WORKERS=$(DEVKIT_SIZE_WORKERS) scripts/container.sh make verify-fresh

# Unsized, and it takes no gate slot (scripts/container-size.sh): a selection is a fraction of the
# suite, and waiting behind a gate would cost it more than it saves. container.sh hands the script's
# own status back, so its 10 and 5 reach the caller.
container-test-fast:
	scripts/container.sh $(FAST_STAGE_RUN)

# serial, so sized for one worker: the whole suite in one process still holds most of what it
# holds across six
container-test-coverage:
	DEVKIT_SIZE_WORKERS=$(if $(filter 0,$(DEVKIT_SIZE_WORKERS)),0,1) scripts/container.sh make test-coverage

container-quality:
	scripts/container.sh make quality

container-quality-base:
	scripts/container.sh make quality-base

container-snapshot:
	scripts/container.sh make snapshot SNAPSHOT_REF=$(SNAPSHOT_REF)

container-snapshot-base:
	scripts/container.sh make snapshot-base SNAPSHOT_REF=$(SNAPSHOT_REF)

# The whole record, whatever the change reaches - the comparison to run on the commit about to be
# promoted, against what production runs: `make container-snapshot-full SNAPSHOT_REF=origin/production`.
container-snapshot-full:
	scripts/container.sh make snapshot-full SNAPSHOT_REF=$(SNAPSHOT_REF)

# The tests in a venv of their own on Python 3.14, as `DEVKIT_PYTHON=3.14` makes it. 3.14 is also the
# project's default now, so this repeats container-test; give DEVKIT_PYTHON another version to try it.
container-test-py314:
	DEVKIT_PYTHON=3.14 DEVKIT_SIZE_WORKERS=$(DEVKIT_SIZE_WORKERS) scripts/container.sh make test

# No deadline: an interactive shell outlives its command by design.
container-shell:
	DEVKIT_TIMEOUT=0 scripts/container.sh sh

# A container that outlives the command which started it, for browsing the app by hand -
# and so that neither a person nor an agent assembles a `podman run` of its own, which is
# how a machine came to be holding eleven of them under names nobody could attribute.
#
#   make container-keep                          the app on the e2e fixtures, detached, on :8080
#   make container-keep KEEP_CMD='sh -c "..."'   something else, same treatment
#   make container-keep KEEP_PORT=127.0.0.1:18080:8080   a second one, from another worktree
#   make container-stop                          takes it away
#
# The name is per checkout, because a fixed one is a container `make container-stop` in one
# worktree takes away from another - the mistake scripts/checkout-id.sh exists to record.
# The host port cannot be: two worktrees both keeping one need `KEEP_PORT` set on the
# second, or the second fails to bind and podman leaves the name held by a `Created`
# container. Anchored on this file rather than $PWD, so that `make -f` from elsewhere
# does not silently produce the primary checkout's empty suffix and name its container.
# abspath rather than dir alone: a plain `make` here gives `./`, whose basename is `.`,
# and the suffix would be `-.` for every checkout.
MAKEFILE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
# checkout-id.sh reads devkit.conf from the directory of the script that sources it: script_dir.
SERVICE := $(shell sed -n 's/^DEVKIT_SERVICE=//p' $(MAKEFILE_DIR)/devkit.conf | head -n 1)
CHECKOUT_SUFFIX := $(shell script_dir=$(MAKEFILE_DIR)/scripts; . $(MAKEFILE_DIR)/scripts/checkout-id.sh && \
	checkout_suffix "$(MAKEFILE_DIR)")
KEEP_NAME ?= $(SERVICE)-keep$(CHECKOUT_SUFFIX)
KEEP_CMD ?= scripts/run-preview.sh
# on the loopback address alone: the preview signs anybody in, as anybody, with /e2e/login
KEEP_PORT ?= 127.0.0.1:8080:8080

# The preview, scripts/run-preview.sh: the e2e fixtures, with /e2e/login/<uid>?roles=... for a session,
# on 0.0.0.0. It needs no config.yaml - the one CONTRIBUTING.md has you make is for run-dev.sh on the
# host, whose localhost no published port reaches from inside a container.
container-keep:
	DEVKIT_DETACH=1 DEVKIT_CONTAINER_NAME=$(KEEP_NAME) DEVKIT_PORT=$(KEEP_PORT) \
		scripts/container.sh $(KEEP_CMD)

# The runtime the same way container.sh chooses it, or this reports "was not running"
# about a container docker is running perfectly well - and says so loudly when it cannot
# ask at all, rather than reporting the absence it failed to check. The existence check
# before the removal, because `rm -f` succeeds against a name that was never there;
# `--type container` because a bare `inspect` matches images and volumes too.
container-stop:
	@runtime=$${DEVKIT_RUNTIME:-}; \
	if [ -z "$$runtime" ]; then \
		if command -v podman >/dev/null 2>&1; then runtime=podman; \
		elif command -v docker >/dev/null 2>&1; then runtime=docker; fi; \
	fi; \
	if [ -z "$$runtime" ] || ! command -v "$$runtime" >/dev/null 2>&1; then \
		echo "no container runtime to ask: $${runtime:-none found}" >&2; exit 1; \
	fi; \
	if $$runtime inspect --type container $(KEEP_NAME) >/dev/null 2>&1; then \
		$$runtime rm -f $(KEEP_NAME) >/dev/null && echo "stopped $(KEEP_NAME)"; \
	else \
		echo "$(KEEP_NAME) was not running"; \
	fi

# The first target above must not become the service's default goal: an include before the
# service's own targets would otherwise turn a plain `make` into `make check`. Empty restarts make's
# choice at the next target it reads.
.DEFAULT_GOAL := $(devkit_mk_goal)
