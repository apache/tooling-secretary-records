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

"""The write scenarios: a fixed sequence of real writes through the API, each made as the reader who
would make it, and everything each one changed.

After each step `Recorder.changes` writes what changed: every database table, row by row, and the GET
answers among `WATCHED` that changed. Each step's file also has the answers to the requests the step
itself made. Each step happens at a moment of its own, and draws from `random` as if it were the first,
so the record is the same however the run is split between workers.

**There are no steps yet**, because the app has no route that writes. The first change that adds one
adds a step for it to `STEPS`, in the same pull request - with a `Behaviour-Harness:` trailer, as for
any change to the harness - and a step for each reader the write must refuse.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
from typing import TYPE_CHECKING, Any

import time_machine

from tests.behaviour import executed, normalise, routes, world

if TYPE_CHECKING:
    import datetime
    from collections.abc import Awaitable, Callable

WATCHED: tuple[str, ...] = ("/", "/api/me")
"""The GET answers asked again after every step, as every reader is answered: the pages the scenario's
writes reach. The whole walk after each step would cost a walk a step."""


async def _rows(app: Any) -> str:
    """Every table of the app's database, every row, in a fixed order: what a write leaves behind."""
    import sqlalchemy

    from app import db
    from app.db.models import DBBase

    parts = []
    async with app.app_context(), db.session() as session:
        for table in DBBase.metadata.sorted_tables:
            rows = await session.execute(sqlalchemy.select(table).order_by(*table.primary_key.columns))
            parts.append(f"--- {table.name}\n")
            parts += [json.dumps(dict(row._mapping), sort_keys=True, default=str) + "\n" for row in rows]
    return "".join(parts)


def _diff(name: str, before: str, after: str) -> list[str]:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), f"{name} before", f"{name} after", n=2
    )
    written = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    return [written] if written else []


@dataclasses.dataclass
class Recorder:
    clients: world.Clients
    _tables: str = ""
    _answers: dict[str, str] = dataclasses.field(default_factory=dict)

    async def start(self) -> None:
        self._tables = await _rows(self.clients.app)
        self._answers = {url: await routes.ask_everyone(self.clients, url) for url in WATCHED}

    async def changes(self) -> list[str]:
        tables = await _rows(self.clients.app)
        sections = ["=== database", *_diff("database", self._tables, tables)]
        self._tables = tables
        sections.append("=== GET answers that changed")
        for url in WATCHED:
            answer = await routes.ask_everyone(self.clients, url)
            sections += _diff(url, self._answers[url], answer)
            self._answers[url] = answer
        return sections


@dataclasses.dataclass(frozen=True)
class Step:
    name: str
    at: datetime.datetime
    """The moment it happens: the wall clock reads it."""
    act: Callable[[Scenario], Awaitable[list[str]]]


async def asked(client: Any, method: str, url: str, body: Any = None) -> str:
    """One request a step makes, and its answer, as recorded."""
    if body is None:
        response = await client.open(url, method=method)
    else:
        # the bytes a browser sends: the test client camelizes the keys of `json=`, and a uid is not a key
        # it may rewrite
        response = await client.open(
            url, method=method, data=json.dumps(body), headers={"Content-Type": "application/json"}
        )
    data = await response.get_data()
    return f"{method} {url}\n" + normalise.normalised(
        f"status: {response.status_code}\n\n" + routes.body(response.content_type or "", data)
    )


STEPS: tuple[Step, ...] = ()
"""The writes, in the order they are made, each standing on the ones before it. None yet: see above."""


@dataclasses.dataclass
class Scenario:
    clients: world.Clients
    recorder: Recorder

    def as_reader(self, name: str) -> Any:
        return self.clients.by_reader[name]


async def run(scenario: Scenario) -> dict[str, bytes]:
    produced: dict[str, bytes] = {}
    if not STEPS:
        return produced
    await scenario.recorder.start()
    for step in STEPS:
        with time_machine.travel(step.at, tick=False):
            await world.sign_in_again(scenario.clients)
            # every step draws from `random` as if it were the first: what ran before it in the worker, or
            # in the scenario, does not move what it draws
            world.seed_the_shuffle()
            with executed.part(f"scenario {step.name}"):
                requests = await step.act(scenario)
                sections = ["=== what the step asked", *requests, *await scenario.recorder.changes()]
            await world.assert_everyone_is_who_they_signed_in_as(scenario.clients)
        produced[f"scenarios/{step.name}.txt"] = normalise.normalised("\n".join(sections) + "\n").encode()
    await world.sign_in_again(scenario.clients)
    return produced
