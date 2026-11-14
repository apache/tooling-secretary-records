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

"""What the app answers, written out for `scripts/ci/behaviour.py` to compare with the commit it would
merge into: every GET route as each reader is answered, and what a fixed sequence of writes changes.
Each test writes one part of the record (`store.write`)."""

from __future__ import annotations

import pytest

from tests.behaviour import executed, routes, scenarios, store, world


async def _finish(clients: world.Clients, test: str, produced: dict[str, bytes], files: dict[str, list[str]]) -> None:
    """Write one test's part of the record - once every reader is still who the record files their answers
    under - and which modules each of its parts ran, with the files each wrote (`executed.py`)."""
    await world.assert_everyone_is_who_they_signed_in_as(clients)
    store.write(test, produced)
    executed.write(test, files)


async def _discover(clients: world.Clients) -> routes.Values:
    """What the fixture holds, asked as part of starting up: every part stands on it."""
    with executed.part("startup"):
        return await routes.discover(clients)


async def test_every_get_route_is_walked(clients: world.Clients) -> None:
    """Each GET rule the app routes is asked, or excluded with a reason: none is left out unseen. Run in
    every recording, selected or not: what the walk would ask is what a selection is made from."""
    _, report = routes.plan(clients.app, await _discover(clients))

    unfilled = [line for line in report if line.startswith("UNFILLED")]
    assert not unfilled, "a GET rule takes a parameter tests/behaviour/routes.py cannot fill:\n" + "\n".join(unfilled)
    await _finish(clients, "routes", {"routes.txt": ("\n".join(report) + "\n").encode()}, {})


@pytest.mark.parametrize("shard", range(routes.SHARDS))
async def test_every_get_route(shard: int, clients: world.Clients) -> None:
    """Every url, as each reader is answered. The rules are spread over `routes.SHARDS` tests, each with an
    app of its own, so that the suite's workers share them; a selected recording asks only the rules
    selected."""
    planned, _ = routes.plan(clients.app, await _discover(clients))

    produced: dict[str, bytes] = {}
    files: dict[str, list[str]] = {}
    for rule, urls in planned.items():
        part = f"route {rule}"
        if routes.shard_of_rule(rule) != shard or not executed.wanted(part):
            continue
        for url in urls:
            name = routes.file_name(url)
            assert name not in produced, f"two urls record into {name}"
            produced[name] = (await routes.ask_everyone(clients, url, part)).encode()
            for reader in world.READERS:
                files.setdefault(f"{part} @ {reader.name}", []).append(name)

    await _finish(clients, f"http-{shard}", produced, files)


async def test_write_scenarios(clients: world.Clients) -> None:
    """A fixed sequence of writes through the API, and everything each changed: see `scenarios.py`. Each step
    stands on the ones before it, so a selection runs the sequence whole or not at all."""
    if not executed.any_wanted("scenario "):
        pytest.skip("this change reaches nothing the write scenarios ran")
    recorder = scenarios.Recorder(clients)
    produced = await scenarios.run(scenarios.Scenario(clients, recorder))
    files = {f"scenario {step.name}": [f"scenarios/{step.name}.txt"] for step in scenarios.STEPS}
    await _finish(clients, "scenarios", produced, files)
