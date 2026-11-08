.PHONY: init build certs check dev dev-server e2e-test e2e-test-host e2e-quick e2e-typecheck lint run sync \
	sync-dev test test-coverage pytest verify quality quality-show quality-base \
	container-check container-test container-test-coverage container-test-py314 \
	container-pytest container-verify container-quality container-quality-base \
	container-shell container-keep container-stop

HOME ?= /home/$(USER)
YARN ?= $(HOME)/.local/bin/yarn
ASSETS_MANIFEST := app/client/static/assets/manifest.json

state/%.pem:
	# generate a certificate signed by mkcert root CA
	mkdir -p state
	mkcert -cert-file state/cert.pem -key-file state/key.pem localhost.apache.org localhost 127.0.0.1 ::1

init:
	cd assets && corepack enable --install-directory $(HOME)/.local/bin && corepack prepare yarn@stable --activate

# This target is used by Puppet
build:
	cd assets && $(YARN) install --immutable && $(YARN) run build

# Only (re)builds the assets if they haven't been built yet.
$(ASSETS_MANIFEST):
	$(MAKE) build

certs: state/cert.pem state/key.pem

check:
	uv run prek run --all-files

# svelte-check and eslint - prettier's own check runs separately (`check`'s yarn-format hook)
# because it is fast where this is not. Kept out of `check` for that reason, but it does run
# as a `pre-push` prek hook; see CONTRIBUTING.md. CI runs it in the frontend job, beside the
# vitest run that shares its yarn install.
lint:
	cd assets && yarn lint

dev: certs
	scripts/run-dev.sh

dev-server: certs
	scripts/run-dev.sh -s

run:
	scripts/run.sh

sync:
	uv sync --frozen --no-default-groups

sync-dev:
	uv sync --frozen

# pytest runs across worker processes (pytest-xdist). Each worker pays the import of the
# application and its own setup before it runs a test, so past about six of them that
# outgrows what the extra parallelism saves, which is why this caps rather than using
# `-n auto`. Run `make test PYTEST_WORKERS=1` for a serial run - the one to reach for when a
# failure is easier to read without interleaved output, or a test hangs.
PYTEST_WORKERS ?= $(shell n=$$(nproc 2>/dev/null || echo 1); if [ "$$n" -gt 6 ]; then echo 6; else echo "$$n"; fi)
# Recursive rather than immediate, so that the target-specific override on `pytest` below
# is the one that counts when that target expands it.
PYTEST_PARALLEL = $(if $(filter-out 0 1,$(PYTEST_WORKERS)),-n $(PYTEST_WORKERS))

test:
	uv run pytest $(PYTEST_PARALLEL)
	cd assets && yarn test

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

# Everything that gates a push, behind one exit code. Four commands is three chances to
# run only some of them, and the one skipped is the one that fails in CI.
#
# The end-to-end suite is deliberately not in here: it takes 8 to 15 minutes against the
# seconds these take, and it answers a different question. Run `make e2e-test` when the
# change touches what a browser sees.
verify: check lint test quality

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

quality-base:
	uv run scripts/ci/quality.py base --ref $(QUALITY_REF) --out $(QUALITY_BASE)

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
# Starting the app costs about seventy seconds, which dwarfs a narrow run; SR_KEEP keeps
# it for the next one and takes a three-test run from 108s to 40s. The e2e/ directory is
# bind-mounted, so spec edits are picked up live.
#
# The trap is that nothing under app/ or assets/ is: those are in the container this
# reuses, so after changing either, one plain `make e2e-test` (or removing the server
# container) is what makes the next answer mean anything. Run the whole suite that way
# too - a kept container has served whatever the last run left behind.
e2e-quick:
	SR_KEEP=1 scripts/e2e-container.sh $(FILTER)

# The same suite run directly on the host. This needs node, the Playwright
# browsers and Python installed locally, which the container-only rule in
# AGENTS.md otherwise rules out - it is kept as an escape hatch for debugging
# the harness itself, and for environments without a container runtime. Prefer
# `make e2e-test`.
#
# The app keeps its state under state-e2e/, which this throws away first, so a run
# starts clean and leaves the checkout untouched.
e2e-test-host: $(ASSETS_MANIFEST)
	rm -rf state-e2e
	cd e2e && npm install && npx playwright install chromium && npx playwright test

# --- Running the checks from the host ------------------------------------
#
# The targets above assume you are already inside the dev container (via the
# devcontainer, or `docker compose exec secretary-records`). These wrap them so they can be
# run from a plain host shell instead: same Dockerfile, same commands, working
# tree bind-mounted. Nothing is installed on the host.

container-check:
	scripts/container.sh make check

container-test:
	scripts/container.sh make test

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
	scripts/container.sh make verify

container-test-coverage:
	scripts/container.sh make test-coverage

container-quality:
	scripts/container.sh make quality

container-quality-base:
	scripts/container.sh make quality-base


# Reproduce a CI matrix version locally. SQLAlchemy's async engine only works on
# 3.14 because we depend on greenlet directly, so this is worth being able to run.
container-test-py314:
	SR_PYTHON=3.14 scripts/container.sh make test

# No deadline: an interactive shell outlives its command by design.
container-shell:
	SR_TIMEOUT=0 scripts/container.sh sh

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
# container. Anchored on this makefile rather than $PWD, so that `make -f` from elsewhere
# does not silently produce the primary checkout's empty suffix and name its container.
# abspath rather than dir alone: a plain `make` here gives `./`, whose basename is `.`,
# and the suffix would be `-.` for every checkout.
MAKEFILE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
CHECKOUT_SUFFIX := $(shell . $(MAKEFILE_DIR)/scripts/checkout-id.sh && \
	checkout_suffix "$(MAKEFILE_DIR)")
KEEP_NAME ?= secretary-records-keep$(CHECKOUT_SUFFIX)
KEEP_CMD ?= scripts/run-preview.sh
# on the loopback address alone: the preview signs anybody in, as anybody, with /e2e/login
KEEP_PORT ?= 127.0.0.1:8080:8080

# The preview, scripts/run-preview.sh: the e2e fixtures, with /e2e/login/<uid>?roles=... for a session,
# on 0.0.0.0. It needs no config.yaml - the one CONTRIBUTING.md has you make is for run-dev.sh on the
# host, whose localhost no published port reaches from inside a container.
container-keep:
	SR_DETACH=1 SR_CONTAINER_NAME=$(KEEP_NAME) SR_PORT=$(KEEP_PORT) \
		scripts/container.sh $(KEEP_CMD)

# The runtime the same way container.sh chooses it, or this reports "was not running"
# about a container docker is running perfectly well - and says so loudly when it cannot
# ask at all, rather than reporting the absence it failed to check. The existence check
# before the removal, because `rm -f` succeeds against a name that was never there;
# `--type container` because a bare `inspect` matches images and volumes too.
container-stop:
	@runtime=$${SR_RUNTIME:-}; \
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
