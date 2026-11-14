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

"""Which parts of the behaviour record a change can reach (`scripts/ci/behaviour.py`).

A part's modules are what it ran, as `tests/behaviour/executed.py` noted them. A change reaches a part
that ran a module it touches, or a module importing one it touches - one step out, never the closure:
a constant is read without running anything of the module that holds it.

Everything is recorded, rather than a guess, where the map cannot answer:
- a change to what no function call shows (`EVERYTHING_WHEN`): templates, data, a package's
  `__init__`, the harness, the dependencies, CI;
- a module added or removed under app/, which the base's map knows nothing of, or knows only by a name
  the head no longer has - a refactor moves code, and moving it is exactly what must be compared;
- a module startup runs, or one imported by a module startup runs, since every part stands on startup;
- a module the map shows no part running, and that no module imports: a change no part could show is
  a change the map cannot see, never one that changes nothing.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import sys
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Iterable

PACKAGE: Final = "app"

EVERYTHING_WHEN: Final = (
    # what code reaches without running a function the map could see
    "app/**/templates/**",
    "app/**/static/**",
    "app/*/migrations/**",
    "app/**/alembic.ini",
    "app/__init__.py",
    "app/**/__init__.py",
    # the fixture, the test setup, and what the record is made with
    "tests/data/**",
    "tests/conftest.py",
    "tests/behaviour/**",
    "scripts/ci/behaviour.py",
    "scripts/ci/behaviour_selection.py",
    "scripts/ci/behaviour_gate.py",
    "scripts/ci/trailers.py",
    "config.yaml.example",
    # the dependencies, and what builds and runs it
    "uv.lock",
    "pyproject.toml",
    "assets/yarn.lock",
    "assets/package.json",
    "Makefile",
    ".github/workflows/**",
    ".github/actions/**",
)
"""A change to any of these records everything; so does one to any file under app/ that is not Python."""

WHOLE: Final = {"scenario ": "scenarios"}
"""The tests whose parts run as one - each on what the one before left - by the prefix of their parts and
the directory of their files: one part selected records them all."""


@dataclasses.dataclass(frozen=True)
class Selection:
    everything: bool
    reasons: list[str]
    parts: list[str]


def _module_name(path: str) -> str:
    return path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


def _module_paths(name: str) -> set[str]:
    base = name.replace(".", "/")
    return {f"{base}.py", f"{base}/__init__.py"}


def _importers(root: pathlib.Path, modules: set[str], package: str) -> dict[str, set[str]]:
    """For each of `modules`, the modules of `package` that import it directly."""
    import grimp

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    graph = grimp.build_graph(package, cache_dir=None)
    return {
        module: set(graph.find_modules_that_directly_import(module)) if module in graph.modules else set()
        for module in modules
    }


def _unseen(changed: Iterable[str], package: str) -> list[str]:
    """The changed paths no function call shows."""
    return [
        path
        for path in changed
        if any(fnmatch.fnmatchcase(path, glob) for glob in EVERYTHING_WHEN)
        or (path.startswith(f"{package}/") and not path.endswith(".py"))
    ]


def _everything_because(changed: list[str], added_or_removed: Iterable[str], package: str) -> list[str]:
    """Why the paths alone call for everything, or nothing."""
    if unseen := _unseen(changed, package):
        return [f"{path} is not seen through what a part runs" for path in unseen]
    moved = sorted(path for path in added_or_removed if path.startswith(f"{package}/") and path.endswith(".py"))
    return [f"{path} is added or removed, which the base's map cannot speak for" for path in moved]


def _beyond_the_map(
    touched: set[str], reached: set[str], importers: dict[str, set[str]], executed: dict[str, dict[str, Any]]
) -> list[str]:
    """Why what the change reaches calls for everything, or nothing."""
    startup = set(executed.get("startup", {}).get("modules", []))
    if at_startup := sorted(reached & startup):
        return [f"{path} runs at startup, or imports what does" for path in at_startup]
    ran = {module for part, entry in executed.items() if part != "startup" for module in entry["modules"]}
    unread = sorted(path for path in touched if path not in ran and not importers[_module_name(path)])
    return [f"{path} is run by no part and imported by no module: the map cannot see it" for path in unread]


def select(
    root: pathlib.Path,
    changed: list[str],
    executed: dict[str, dict[str, Any]],
    *,
    added_or_removed: Iterable[str] = (),
    package: str = PACKAGE,
) -> Selection:
    """The parts of the record a change to `changed` can reach, or everything. `added_or_removed` are the
    changed paths that are new or gone, rather than edited."""
    if reasons := _everything_because(changed, added_or_removed, package):
        return Selection(True, reasons, [])
    touched = {path for path in changed if path.startswith(f"{package}/") and path.endswith(".py")}
    if not touched:
        return Selection(False, [], [])
    importers = _importers(root, {_module_name(path) for path in touched}, package)
    reached = touched | {path for found in importers.values() for name in found for path in _module_paths(name)}
    if reasons := _beyond_the_map(touched, reached, importers, executed):
        return Selection(True, reasons, [])
    parts = sorted(part for part, entry in executed.items() if part != "startup" and reached & set(entry["modules"]))
    if not parts:
        return Selection(True, [f"{', '.join(sorted(touched))} reaches no part the map knows"], [])
    return Selection(False, sorted(touched), parts)


def compared(files: dict[str, bytes], executed: dict[str, dict[str, Any]], selection: Selection) -> dict[str, bytes]:
    """The base's files a selected recording is compared with: those of the parts it recorded, all of each
    test that runs whole, and the route walk's, which always runs. Not the match counts of a test that
    ran in part, which count what it ran."""
    kept = {path for part in selection.parts for path in executed.get(part, {}).get("files", [])}
    kept |= {"routes.txt", "normalisation/routes.txt"}
    whole = {name for prefix, name in WHOLE.items() if any(part.startswith(prefix) for part in selection.parts)}
    return {
        path: content
        for path, content in files.items()
        if path in kept or path.split("/")[0] in whole or path in {f"normalisation/{name}.txt" for name in whole}
    }


def measure(
    root: pathlib.Path, executed: dict[str, dict[str, Any]], commits: list[tuple[str, str, list[str], list[str]]]
) -> None:
    """What the selection would have recorded for each `(sha, subject, changed, added_or_removed)`, and on
    average."""
    total = len(executed) - 1
    counts = {"everything": 0, "some": 0, "nothing": 0}
    fractions = []
    for sha, subject, changed, moved in commits:
        selection = select(root, changed, executed, added_or_removed=moved)
        kind = "everything" if selection.everything else "some" if selection.parts else "nothing"
        counts[kind] += 1
        fractions.append(1.0 if selection.everything else len(selection.parts) / total)
        shown = f"{len(selection.parts):>4}/{total}  " if kind == "some" else f"{kind:<11}"
        print(f"{sha[:10]} {shown} {subject[:70]}")
        if selection.everything:
            print(f"           {selection.reasons[0]}")
    print(
        f"\n{len(commits)} commits: everything {counts['everything']}, some {counts['some']}, "
        f"nothing {counts['nothing']};"
    )
    print(f"on average {sum(fractions) / max(len(fractions), 1):.0%} of the parts a full recording runs")
