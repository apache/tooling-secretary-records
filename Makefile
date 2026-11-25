.PHONY: init build certs dev dev-server run sync sync-dev

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

# The targets every service shares: the gate, the tests and the fast stage, the quality ratchet, the
# behaviour record, and the e2e and container wrappers (devkit.mk, from apache/tooling-devkit).
include $(dir $(firstword $(MAKEFILE_LIST)))devkit.mk
