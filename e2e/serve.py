#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Launch the app for end-to-end (Playwright) tests.

This serves the app over plain HTTP with e2e/e2e-config.yaml, whose state directory is the
gitignored state-e2e/, thrown away at every start so a run begins clean. It sets SR_E2E=1 so
the dev-only /e2e/login/<uid> route is enabled (see app/client/routes.py), which lets
Playwright sign in as any uid with any role without going through ASF OAuth.

Run from the repository root:  python e2e/serve.py
"""

import os
import pathlib
import shutil
import sys

import uvloop

# Enable the dev-only test-login route before the app is constructed.
os.environ["SR_E2E"] = "1"

# When launched as `python e2e/serve.py` (e.g. Playwright's webServer in CI), sys.path[0] is
# the e2e/ directory, so the top-level `app` package is not importable. Put the repository
# root on the path before importing it.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import app.config  # noqa: E402
import app.webapp  # noqa: E402  (must follow the SR_E2E env var and sys.path setup)


def main(config_path: str = "e2e/e2e-config.yaml") -> None:
    uvloop.install()

    app_config = app.config.load_app_config(REPO_ROOT / config_path)
    # A database a previous run left behind would carry its rows into this one. Only a
    # directory named for this harness: whatever else a config names is not ours to delete.
    state_dir = REPO_ROOT / app_config.base.state_dir
    if not state_dir.name.startswith("state-e2e"):
        raise SystemExit(f"refusing to clear {state_dir}: the e2e state directory is state-e2e")
    shutil.rmtree(state_dir, ignore_errors=True)

    application = app.webapp.create_app(str(REPO_ROOT), config_path, development=True)
    # The e2e server runs over plain HTTP, so the session cookie must not be
    # marked Secure (a Secure cookie is never sent back over HTTP).
    application.config["SESSION_COOKIE_SECURE"] = False

    server_config = app.config.get().server
    application.run(
        host=server_config.host,
        port=server_config.port,
        use_reloader=False,
        debug=True,
    )


if __name__ == "__main__":
    # a config of its own, as scripts/run-preview.sh gives one
    main(*sys.argv[1:2])
