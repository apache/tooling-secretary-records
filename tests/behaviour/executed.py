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

"""Which of the app's own modules each part of the record ran, so a change is compared only where it can reach.

`sys.monitoring` (Python 3.12), not coverage: a `PY_START` callback notes the file of every function of
`app/` that starts, and disables itself for that function at once, so each is noted once a part and
then costs nothing. `restart_events()` between parts turns them all back on. Every thread is seen - a
request handed to a worker thread included - and nothing is measured line by line, which is what makes
it cheap enough to leave on in every run.

A part is one route as one reader asks it, or one scenario step - and `startup`, which is everything
the app runs before any part begins: building the app, migrating its database, the readers signing in,
the discovery of what the fixture holds. A module that only startup runs is one every part depends on
without running it, so a change to one runs everything.

Written where `SR_BEHAVIOUR_EXECUTED` says, beside the record and not in it: which modules a part ran
moves whenever code moves, and is not behaviour.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

ENV = "SR_BEHAVIOUR_EXECUTED"
_TOOL = 4
"""A `sys.monitoring` tool id nobody else claims: 0-2 and 5 are the debugger's, coverage's, the
profiler's and the optimiser's."""

_ROOT = str(pathlib.Path(__file__).resolve().parent.parent.parent / "app") + os.sep


class _Monitor:
    def __init__(self) -> None:
        self.active: list[str] = []
        self.parts: dict[str, set[str]] = {}
        self.installed = False

    def _started(self, code: object, _offset: int) -> object:
        filename = getattr(code, "co_filename", "")
        if getattr(code, "co_name", "") == "<module>":
            # a module being imported is not a part running it: what it defines at import is reached
            # through whoever imports it, which the selection asks one step out
            return sys.monitoring.DISABLE
        if filename.startswith(_ROOT):
            module = "app/" + filename[len(_ROOT) :].replace(os.sep, "/")
            for name in self.active:
                self.parts[name].add(module)
        return sys.monitoring.DISABLE

    def install(self) -> None:
        if self.installed:
            return
        sys.monitoring.use_tool_id(_TOOL, "secretary-records-behaviour")
        sys.monitoring.register_callback(_TOOL, sys.monitoring.events.PY_START, self._started)
        sys.monitoring.set_events(_TOOL, sys.monitoring.events.PY_START)
        self.installed = True


_MONITOR = _Monitor()


def recording() -> bool:
    return bool(os.environ.get(ENV))


def begin(name: str = "startup") -> None:
    """Start noting what runs, for `name` and every part already open around it. Every function counts
    again, the first time it starts after this."""
    if not recording():
        return
    _MONITOR.install()
    _MONITOR.active.append(name)
    _MONITOR.parts.setdefault(name, set())
    sys.monitoring.restart_events()


def end(name: str = "startup") -> None:
    """Stop noting for `name`."""
    if recording() and name in _MONITOR.active:
        _MONITOR.active.remove(name)


@contextlib.contextmanager
def part(name: str) -> Iterator[None]:
    begin(name)
    try:
        yield
    finally:
        end(name)


def write(test: str, files: dict[str, list[str]]) -> None:
    """This test's parts - the modules each ran and the record files each wrote - and start again."""
    if not recording():
        return
    out = pathlib.Path(os.environ[ENV])
    out.mkdir(parents=True, exist_ok=True)
    parts = {
        name: {"modules": sorted(modules), "files": sorted(files.get(name, []))}
        for name, modules in _MONITOR.parts.items()
    }
    (out / f"{test}.json").write_text(json.dumps(parts, indent=1, sort_keys=True) + "\n")
    _MONITOR.parts = {}


SELECT_ENV = "SR_BEHAVIOUR_SELECT"
"""A file naming the parts to record, where a change reaches only some (`scripts/ci/behaviour.py`)."""

_selected: set[str] | None = None
_known: set[str] = set()


def wanted(part: str) -> bool:
    """Whether to record `part`: every part, unless this run was told which - and then also any part the
    base never recorded, such as a route the change adds."""
    global _selected, _known
    if not os.environ.get(SELECT_ENV):
        return True
    if _selected is None:
        told = json.loads(pathlib.Path(os.environ[SELECT_ENV]).read_text())
        _selected, _known = set(told["parts"]), set(told["known"])
    return part in _selected or part not in _known


def any_wanted(prefix: str) -> bool:
    """Whether any part named under `prefix` is to be recorded - for a test whose parts run as one."""
    if not os.environ.get(SELECT_ENV):
        return True
    wanted("")
    return any(part.startswith(prefix) for part in _selected or ())
