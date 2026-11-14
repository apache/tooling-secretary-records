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

"""What a branch may ask of the behaviour comparison (`scripts/ci/behaviour.py`), read from git.

The comparison runs the branch's own code, so whatever the branch says about itself is checked against
what git says instead. Whether the base has a harness is read from the base commit's tree, not from a
path the branch's script names. Which commits argue for a change is read from the branch's own line,
not from a merge's message or a branch merged in. And a branch that changes what the record is made
with says so in a trailer of its own, and changes nothing of the app in the same breath: a harness change
and an app change together would compare a new app, recorded by a new harness, with an old one, and a
difference could come from either.
"""

from __future__ import annotations

import fnmatch
import subprocess
from typing import TYPE_CHECKING, Final

import trailers

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Iterable

ALLOW: Final = "Behaviour-Allow"
HARNESS: Final = "Behaviour-Harness"

HARNESS_FILES: Final = (
    "tests/behaviour/*",
    "tests/conftest.py",
    "tests/data/*",
    "scripts/ci/behaviour*.py",
    "scripts/ci/trailers.py",
)
"""What the record is made with: a change here changes what is recorded without the app changing."""

HARNESS_MARKS: Final = ("scripts/ci/behaviour.py", "tests/behaviour")
"""What a commit holds where it can record itself."""

SUMMARY_BYTES: Final = 512 * 1024
"""GitHub refuses a step summary over 1 MiB; a diff stops well short of that, and the artifact has it all."""


def has_harness(commit: str, cwd: pathlib.Path) -> bool:
    """Whether `commit`'s own tree holds the harness."""
    return all(
        subprocess.run(["git", "cat-file", "-e", f"{commit}:{mark}"], cwd=cwd, capture_output=True).returncode == 0
        for mark in HARNESS_MARKS
    )


def branch_tip(base: str, cwd: pathlib.Path) -> str:
    """The branch's own last commit: HEAD, or - where HEAD is the merge CI makes of the branch into its
    base - the branch's side of it."""
    parents = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.split()
    based = subprocess.run(["git", "rev-parse", base], cwd=cwd, capture_output=True, text=True).stdout.strip()
    return parents[2] if len(parents) == 3 and parents[1] == based else "HEAD"


def claims(key: str, base: str, cwd: pathlib.Path) -> list[tuple[str, str]]:
    """`(value, subject)` for every `key:` trailer on the branch's own commits since `base`."""
    head = branch_tip(base, cwd)
    return [(value, subject) for subject, value in trailers.values(key, base, cwd, head=head, own=True)]


def too_broad(glob: str) -> bool:
    """A glob that names no part of a path, and so allows every difference there could be."""
    return not glob.strip("*?/")


def allows_everything(glob: str, recorded: Iterable[str] = ()) -> bool:
    """Whether `glob` allows every difference there could be: it names no part of a path, or it matches every
    path the base record holds - `*.txt` does, since every file of the record ends in `.txt`. A path named
    outright is never everything, even in a record of one file."""
    paths = list(recorded)
    wild = any(character in glob for character in "*?[")
    return too_broad(glob) or (wild and bool(paths) and all(fnmatch.fnmatchcase(path, glob) for path in paths))


def touched(changed: list[str], globs: tuple[str, ...]) -> list[str]:
    return [path for path in changed if any(fnmatch.fnmatchcase(path, glob) for glob in globs)]


def refusals(
    changed: list[str],
    allowances: list[tuple[str, str]],
    harness: list[tuple[str, str]],
    *,
    recorded: Iterable[str] = (),
) -> list[str]:
    """Why the branch may not be compared at all, or nothing. `recorded` is every path of the base record."""
    paths = list(recorded)
    reasons = [
        f"`{ALLOW}: {glob}` ('{subject}') allows every difference; name the paths that change"
        if too_broad(glob)
        else f"`{ALLOW}: {glob}` ('{subject}') matches every file the base record holds, and so allows every "
        "difference; name the paths that change"
        for glob, subject in allowances
        if allows_everything(glob, paths)
    ]
    tools = touched(changed, HARNESS_FILES)
    if not tools:
        return reasons
    named = ", ".join(tools[:5]) + (f" and {len(tools) - 5} more" if len(tools) > 5 else "")
    app = [path for path in changed if path.startswith("app/")]
    if app:
        reasons.append(
            f"this branch changes what the record is made with ({named}) and the app itself ({', '.join(app[:5])}): "
            "split it - the harness first, recorded against the app it has; then the change to the app, recorded "
            "by the harness the first one left"
        )
    if not harness:
        reasons.append(f"this branch changes what the record is made with ({named}); say why with `{HARNESS}: <why>`")
    return reasons


def summary(heading: str, lines: list[str], diff: list[str], limit: int = SUMMARY_BYTES) -> str:
    """The step summary, `limit` bytes at most: the heading, then each line, then the diff, until it is
    full, and where it was cut how much more the artifact holds."""
    parts = [f"### What the app answers\n\n{heading}\n\n"]
    used = len(parts[0].encode())
    closing = "\n```\n"
    rest = [f"- {line}\n" for line in lines] + (["\n```diff\n"] + [f"{line}\n" for line in diff] if diff else [])
    for index, part in enumerate(rest):
        size = len(part.encode())
        if used + size + len(closing) + 200 > limit:  # room for the closing fence and the note
            if diff and index > len(lines):
                parts.append(closing)
            parts.append(f"\n{len(rest) - index} more lines in the `behaviour` artifact.\n")
            return "".join(parts)
        parts.append(part)
        used += size
    if diff:
        parts.append(closing)
    return "".join(parts)
