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

"""The one world every reading of the app is taken in: the fixture, a fixed clock, and the same readers.

Everything here is a pin rather than a normalisation - something that would vary between two runs,
fixed before it can, so that nothing the app says has to be rewritten after it said it. What cannot be
pinned is in `normalise.py`, each with the reason.
"""

from __future__ import annotations

import dataclasses
import datetime
import os
import pathlib
import sys
from typing import Any

import time_machine

NOW = datetime.datetime(2025, 4, 1, 12, 0, tzinfo=datetime.UTC)
"""The wall clock, for everything that reads it."""

HASH_SEED = "0"
"""What `PYTHONHASHSEED` must be. A set of strings iterates in an order the hash seed decides, which is
drawn at random for each process unless the environment fixes it - and a set serialised as it iterates
would then be another answer on every run."""

PYTHON_VERSION = "3.x"
"""What the server status page says the interpreter is: the record is of the app, and two runs of the
same commit on two machines, or on one machine a patch release apart, must record the same thing."""

STATIC_ASSETS = pathlib.Path(__file__).resolve().parent / "static-assets"
"""A built bundle's stand-in: a manifest and the files it names, so that a page links the same names
whether or not `make build` has run in the checkout - a build names its bundles by content hash."""


@dataclasses.dataclass(frozen=True)
class Reader:
    """Somebody reading the app, as the session OAuth would give them."""

    name: str
    uid: str | None
    pmcs: tuple[str, ...] = ()
    """The session's committees, as LDAP would give them to OAuth. `tooling` is what makes an admin."""
    is_member: bool = True
    is_root: bool = False


READERS: tuple[Reader, ...] = (
    Reader("anonymous", None),
    # signed in, and holding no role here
    Reader("signed-in", "dan"),
    # the three secretarial roles, by the suite's configuration (tests/conftest.py, TEST_ROLES)
    Reader("secretary", "ada"),
    Reader("assistant-secretary", "bea"),
    Reader("secretarial-team", "cy"),
    # on the Tooling PMC by the session, which is what makes an admin
    Reader("admin", "eve", pmcs=("tooling",)),
    # infrastructure root and nothing else - not a member, on no committee - so that what `isRoot` alone
    # opens is answered to somebody
    Reader("root", "rootonly", is_member=False, is_root=True),
)


@dataclasses.dataclass
class Clients:
    """A test client per reader, every one signed in before any of them reads anything."""

    app: Any
    by_reader: dict[str, Any]
    signed_in: dict[str, tuple[Any, ...]] = dataclasses.field(default_factory=dict)
    """What each reader was when signed in: the roles configured for the uid, and the session's flags."""


def _standing(reader: Reader) -> Any:
    from app import config

    roles = config.get().roles
    return sorted(
        name for name in ("secretary", "assistant_secretary", "secretarial_team") if reader.uid in getattr(roles, name)
    )


async def _session(app: Any, client: Any) -> Any:
    async with client.session_transaction() as session:
        stored = session.get(app.app_id) or {}
        return {key: stored.get(key) for key in ("uid", "isMember", "isChair", "isRoot", "pmcs")}


async def sign_in_as(app: Any, client: Any, reader: Reader) -> None:
    """The test login for `reader`, switched on for the one request where the run has it off - the readers
    are read as production answers them, which has no test login."""
    was = os.environ.get("SR_E2E")
    os.environ["SR_E2E"] = "1"
    try:
        response = await client.get(f"/e2e/login/{reader.uid}")
    finally:
        if was is None:
            del os.environ["SR_E2E"]
    assert response.status_code in (200, 302), f"test login of {reader.uid}: {response.status_code}"
    # the test login says everyone is a member and nobody is root or on a committee: say what OAuth would
    async with client.session_transaction() as session:
        stored = dict(session[app.app_id])
        stored.update(isMember=reader.is_member, isRoot=reader.is_root, pmcs=list(reader.pmcs))
        session[app.app_id] = stored


async def log_in_everyone(app: Any) -> Clients:
    clients = Clients(app, {})
    for reader in READERS:
        client = app.test_client()
        if reader.uid is not None:
            await sign_in_as(app, client, reader)
        clients.by_reader[reader.name] = client
    for reader in READERS:
        if reader.uid is not None:
            client = clients.by_reader[reader.name]
            clients.signed_in[reader.name] = (_standing(reader), await _session(app, client))
    return clients


async def who_changed(clients: Clients) -> list[str]:
    """Each reader whose configured roles or session are no longer what sign-in gave them."""
    changed = []
    for reader in READERS:
        if reader.uid is None:
            continue
        client = clients.by_reader[reader.name]
        now = (_standing(reader), await _session(clients.app, client))
        if now != clients.signed_in[reader.name]:
            changed.append(f"{reader.name}: signed in as {clients.signed_in[reader.name]}, now {now}")
    return changed


async def sign_in_again(clients: Clients) -> None:
    """Every reader signed in as before, after something signed one of them out."""
    for reader in READERS:
        if reader.uid is not None:
            await sign_in_as(clients.app, clients.by_reader[reader.name], reader)


async def assert_everyone_is_who_they_signed_in_as(clients: Clients) -> None:
    """Every reader's roles and session as sign-in left them.

    A request that signs a reader out, or changes who holds a role, would have every answer after it
    recorded for somebody other than the reader it is filed under. Asked at the end of every part, so
    that one does not pass as a difference in behaviour, or as none."""
    changed = await who_changed(clients)
    assert not changed, "a reader is no longer who they signed in as:\n" + "\n".join(changed)


def pin_the_clock() -> Any:
    """Every reading of the wall clock, `datetime.now()` and `time.time()` alike, answers `NOW` and does
    not move. Monotonic time is left alone, so a timeout still runs out."""
    return time_machine.travel(NOW, tick=False)


def pin_the_assets(monkeypatch: Any) -> None:
    """The fixture's asset manifest and files, whether or not a real build sits in the checkout: a build
    names its bundles by content hash, and which build a page links to is not the app's behaviour.

    The manifest is pinned in whichever module holds its path, not by that module's own path, so that
    moving the page shell's helpers moves nothing here."""
    import app.client as client

    monkeypatch.setattr(_the_module_holding("_MANIFEST_PATH"), "_MANIFEST_PATH", str(STATIC_ASSETS / "manifest.json"))
    monkeypatch.setattr(client.CLIENT, "static_folder", str(STATIC_ASSETS))


class _Stopped:
    """A `time` module whose monotonic clock does not move."""

    @staticmethod
    def monotonic() -> float:
        return 1000.0


class _Instead:
    """A module, with some of its functions answered otherwise."""

    def __init__(self, module: Any, **instead: Any) -> None:
        self._module, self._instead = module, instead

    def __getattr__(self, name: str) -> Any:
        return self._instead[name] if name in self._instead else getattr(self._module, name)


def _the_module_holding(name: str) -> Any:
    """The one module of app/ that holds `name`, wherever it has moved to - found by what it holds rather
    than imported by its path, so that moving it into a package moves nothing here. Loud where none or
    several do: a pin that finds nothing to pin pins nothing."""
    holders = [
        module
        for key, module in sorted(sys.modules.items())
        if key.startswith("app.") and module is not None and name in vars(module)
    ]
    if len(holders) != 1:
        raise RuntimeError(
            f"{len(holders)} modules of app/ hold {name} ({[m.__name__ for m in holders]}); the pin wants one"
        )
    return holders[0]


def pin_the_server_status(monkeypatch: Any) -> None:
    """The server status page's start, its uptime and the interpreter it names. The start is read when the
    module is imported, which in a worker is whenever the first test built an app; the uptime is read from
    the monotonic clock, which the wall-clock pin leaves running; and the interpreter is the machine's.
    Pinned once the app has imported its routes."""
    import platform

    status = _the_module_holding("_STARTED_AT")
    monkeypatch.setattr(status, "_STARTED_AT", NOW)
    monkeypatch.setattr(status, "_STARTED", _Stopped.monotonic())
    monkeypatch.setattr(status, "time", _Stopped)
    monkeypatch.setattr(status, "platform", _Instead(platform, python_version=lambda: PYTHON_VERSION))


SEED = 0


def seed_the_shuffle() -> None:
    """Python's own `random`, seeded at the start of every part and of every write scenario's step, so that
    each draws the same whatever ran before it in the same process."""
    import random

    random.seed(SEED)
