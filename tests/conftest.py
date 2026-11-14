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

import logging
import pathlib
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator

import alembic.command
import pytest
import quart.testing.app
import yaml

from app import config as app_config_module
from app import db
from app.webapp import create_app

APP_DIR = pathlib.Path(__file__).parent.parent.absolute()

# Who holds which role in the suite's configuration, so a test can sign in as one of them
# without the e2e grant. `ada` is the Secretary, `bea` the Assistant Secretary, `cy` on the
# team; anybody else holds nothing until a login grants it.
TEST_ROLES = {
    "secretary": ["ada"],
    "assistant_secretary": ["bea"],
    "secretarial_team": ["cy"],
}


@pytest.fixture
def app() -> Iterator[quart.Quart]:
    # A state dir of its own per test: it is where the sqlite database lands, and the default
    # (./state) is the developer's real one.
    with tempfile.TemporaryDirectory() as state_dir, tempfile.NamedTemporaryFile("w", suffix=".yaml") as config_file:
        yaml.safe_dump(
            {
                "base": {"state_dir": state_dir},
                "server": {"port": 3000},
                "logging": {"file": None},
                "roles": TEST_ROLES,
            },
            config_file,
        )
        config_file.flush()
        yield create_app(str(APP_DIR), config_file.name, test_environment=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def _migrated_db_template() -> Iterator[pathlib.Path]:
    """A SQLite file already migrated to head, built once for the whole session.

    `running_app` runs every migration plus an `alembic check` at startup; seeding each test's
    database from a copy of this one makes that a no-op rather than real DDL per test."""
    tmp_dir = tempfile.mkdtemp(prefix="secretary-records-test-db-")
    db_path = pathlib.Path(tmp_dir) / "template.db"
    logging.getLogger("alembic").setLevel(logging.WARNING)
    alembic.command.upgrade(db.alembic_config(db_path), "head")
    yield db_path
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
async def running_app(app, monkeypatch, _migrated_db_template) -> AsyncIterator[quart.Quart]:
    """The app with its before_serving hooks run, so the database exists and is migrated.

    The plain `app` fixture never starts, which is enough for a test that reaches into a pure
    helper. Anything that answers a request through the database needs the real startup.
    """
    # the dev-only test-login route is what the Playwright suite authenticates with; reusing it
    # here keeps the tests on the same code path as the browser
    monkeypatch.setenv("SR_E2E", "1")
    shutil.copyfile(_migrated_db_template, db.db_path())

    test_app_context = quart.testing.app.TestApp(app, startup_timeout=120, shutdown_timeout=120)
    # __aenter__ inside the try: a startup that raises leaves the app half-started, and skipping
    # __aexit__ then leaves the database engine undisposed - and aiosqlite's worker thread is
    # not a daemon, so the whole suite wedges at interpreter exit waiting to join it.
    try:
        await test_app_context.__aenter__()
        yield app
    finally:
        await test_app_context.__aexit__(None, None, None)


@pytest.fixture
async def authenticated_client(running_app):
    """A test client with a session cookie, minted the way the e2e suite does."""

    async def login(uid: str, roles: str = ""):
        test_client = running_app.test_client()
        response = await test_client.get(f"/e2e/login/{uid}?roles={roles}")
        assert response.status_code in (302, 200), f"test-login failed: {response.status_code}"
        return test_client

    return login


@pytest.fixture
def app_config(app) -> app_config_module.AppConfig:
    return app_config_module.get()
