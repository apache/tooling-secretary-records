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

"""The app every reading is taken from, built inside the pins of `world.py`.

These tests write a record rather than assert one, so they run only when asked to write it somewhere:
`scripts/ci/behaviour.py` sets `SR_BEHAVIOUR_OUT`. Without it nothing here is collected - `make test`
and the ordinary suite pass over them - and `pytest tests/behaviour` on its own collects nothing and
exits 5, which is a failure rather than a pass.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest

from tests import conftest
from tests.behaviour import executed, leaving, normalise, store, world

if TYPE_CHECKING:
    import pathlib

collect_ignore_glob = [] if os.environ.get(store.OUT_ENV) else ["test_*.py"]


@pytest.fixture(autouse=True)
def nothing_leaves(monkeypatch: pytest.MonkeyPatch) -> Any:
    """No connection off the machine from anything a test runs; one that tries fails the test, whatever
    the app made of the error it was given (`leaving.py`)."""
    leaving.REFUSED.attempts.clear()
    leaving.guard(monkeypatch)
    yield
    if leaving.REFUSED.attempts:
        pytest.fail("the recording tried to leave the machine:\n" + "\n".join(leaving.REFUSED.attempts))


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The clock and the assets fixed before anything reads them - and the hash seed, which only the
    environment the interpreter starts in can fix, required: without it a record differs from run to
    run, so a run without it fails rather than records."""
    if os.environ.get("PYTHONHASHSEED") != world.HASH_SEED:
        pytest.fail(f"PYTHONHASHSEED must be {world.HASH_SEED} from the start: record with scripts/ci/behaviour.py")
    with world.pin_the_clock():
        world.pin_the_assets(monkeypatch)
        world.seed_the_shuffle()
        yield


@pytest.fixture
def app(pinned: None, app: Any) -> Any:
    """The suite's app, built after the pins rather than before them. What its startup runs - the
    migration check, the readers signing in - is noted from here as `startup`: building the app only
    imports it, which says nothing about what a part stands on."""
    executed.begin("startup")
    return app


@pytest.fixture
def _migrated_db_template(pinned: None, tmp_path: pathlib.Path) -> pathlib.Path:
    """A database of this test's own, migrated under the pinned clock: the suite shares one per session,
    and a row a migration seeds would carry the moment whichever test came first built it."""
    return conftest.build_migrated_db(tmp_path)


@pytest.fixture
async def clients(running_app: Any, monkeypatch: pytest.MonkeyPatch) -> world.Clients:
    from app import config

    world.pin_the_server_status(monkeypatch)
    normalise.name_the_directories(state_dir=str(config.get().base.state_dir_path))
    normalise.take_counts()
    clients = await world.log_in_everyone(running_app)
    # The test login is the only thing that needs the end-to-end switch. Off again before anybody reads
    # anything, so that what is read is what production answers - the OAuth redirect for nobody signed
    # in, rather than the suite's stand-in for it, and a 404 from every /e2e route.
    monkeypatch.delenv("SR_E2E")
    executed.end("startup")
    return clients
