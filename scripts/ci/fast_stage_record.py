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

# Comes from apache/tooling-devkit (devkit.files): change it there, not in a copy.
"""What a pytest run leaves for the next fast stage, in state/fast-stage/ (`scripts/ci/fast_stage.py`).

A run that passes records what each test file took. A full run of each half of the suite - `test-python`
and `test-frontend`, from `make test` or `make verify` - notes the commit and the tree before it starts
(`begin`), so a commit made while it runs is not taken as tested, and marks the note when it passed
(`passed`). Both halves passed on one commit and a clean tree make that commit the green base, which the
fast stage measures the next change from.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import xml.etree.ElementTree as ElementTree
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import pathlib

STATE: Final = "state/fast-stage"
GREEN: Final = f"{STATE}/green.json"
DURATIONS: Final = f"{STATE}/durations.json"
HALVES: Final = ("python", "frontend")
"""The halves of a full run, each noted in state/fast-stage/<half>.json - which the Makefile removes before it
runs the half, by name."""


def git(root: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)


def read_json(path: pathlib.Path) -> object:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError, json.JSONDecodeError:
        return None


def _write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n")


def file_of(root: pathlib.Path, classname: str) -> str | None:
    """The test file a junit `classname` (`tests.api.test_x.TestY`) belongs to."""
    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        if (root / f"{'/'.join(parts[:end])}.py").is_file():
            return f"{'/'.join(parts[:end])}.py"
    return None


def read_junit(root: pathlib.Path, path: pathlib.Path) -> dict[str, float]:
    """Seconds per test file, from pytest's junit report."""
    seconds: dict[str, float] = {}
    tree = ElementTree.parse(path)  # noqa: S314 - pytest's own report of this run, written a moment ago
    for case in tree.iter("testcase"):
        if file := file_of(root, case.get("classname", "")):
            seconds[file] = seconds.get(file, 0.0) + float(case.get("time") or 0)
    return seconds


def _half(root: pathlib.Path, half: str) -> pathlib.Path:
    if half not in HALVES:
        raise SystemExit(f"no half {half}: the halves are {', '.join(HALVES)}")
    return root / STATE / f"{half}.json"


def begin(root: pathlib.Path, half: str) -> None:
    """Note what a full run of one half is about to test, before it starts. A tree git cannot read is not
    clean."""
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    status = git(root, "status", "--porcelain")
    clean = status.returncode == 0 and status.stdout == ""
    when = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    _write_json(_half(root, half), {"head": head, "clean": clean, "when": when})


def record(root: pathlib.Path, junit: pathlib.Path, status: int, *, full: bool) -> None:
    """Keep what each file of a run that passed took. A full run replaces what was kept."""
    if status != 0:
        return
    known = read_json(root / DURATIONS)
    durations = {} if full or not isinstance(known, dict) else known
    seconds = read_junit(root, junit)
    _write_json(root / DURATIONS, {**durations, **{path: round(time, 2) for path, time in seconds.items()}})


def passed(root: pathlib.Path, half: str) -> None:
    """One half passed. When both have, on the same commit and a clean tree, that commit is the green base.

    `verify` runs the halves at the same time, and skips one whose files have not changed since it passed;
    its record is then the one that run left, and counts only if it is for the same commit. A dirty tree is
    no base: what was uncommitted may be reverted since, and a revert would read as no change."""
    noted = read_json(_half(root, half))
    if isinstance(noted, dict):
        _write_json(_half(root, half), {**noted, "passed": True})
    records = [each for each in (read_json(_half(root, half)) for half in HALVES) if isinstance(each, dict)]
    if len(records) == len(HALVES) and all(
        each.get("passed") is True and each.get("clean") is True for each in records
    ):
        heads = {each["head"] for each in records}
        if len(heads) == 1:
            _write_json(root / GREEN, {"head": heads.pop(), "when": max(str(each.get("when")) for each in records)})
    # two halves finishing at once may each read the other's note before it is marked, and leave no green:
    # the next change is then measured from further back, which costs time and misses nothing
