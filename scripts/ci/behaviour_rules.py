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
"""What a repository adds to the behaviour comparison, read from `scripts/local/behaviour.toml`.

The kit knows the globs that hold in every repository (`behaviour_selection.EVERYTHING_WHEN`,
`behaviour_gate.HARNESS_FILES`) and the tests that run whole (`behaviour_selection.WHOLE`). A repository
adds its own, where it has them:

    everything_when = ["schema/**"]             # a change here records everything: what no function call shows
    harness_files = ["tests/data/wc/*"]         # what the record is made with, beside the kit's own
    [whole]
    "task " = "tasks"                           # a test whose parts run as one: the prefix of its parts, and
                                                # the directory of its files in the record

The file is data, read with tomllib, and adds to the kit's lists and never takes from them. A glob the kit
has already, a key that is not here, and a prefix the kit has already are refused, each with every other
reason at once. A repository without the file has the lists of the kit. Needs Python 3.11 or newer for
tomllib, where the file exists.
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING, Any, Final

import behaviour_gate
import behaviour_selection

if TYPE_CHECKING:
    import pathlib

LOCAL: Final = "scripts/local/behaviour.toml"
_GLOB_KEYS: Final = {
    "everything_when": behaviour_selection.EVERYTHING_WHEN,
    "harness_files": behaviour_gate.HARNESS_FILES,
}


class LocalError(Exception):
    """`scripts/local/behaviour.toml` says something that cannot be used."""


@dataclasses.dataclass(frozen=True)
class Rules:
    """The kit's lists, and what this repository adds to them."""

    everything_when: tuple[str, ...] = behaviour_selection.EVERYTHING_WHEN
    harness_files: tuple[str, ...] = behaviour_gate.HARNESS_FILES
    whole: dict[str, str] = dataclasses.field(default_factory=lambda: dict(behaviour_selection.WHOLE))


def _read(path: pathlib.Path) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python before 3.11
        raise LocalError(f"reading {LOCAL} needs Python 3.11 or newer (tomllib)") from None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LocalError(f"{LOCAL} cannot be read: {error}") from error


def _problems(data: dict[str, Any]) -> list[str]:
    problems = [f"{LOCAL}: {key!r} is not a key of the file" for key in sorted(set(data) - {*_GLOB_KEYS, "whole"})]
    for key, kit in _GLOB_KEYS.items():
        globs = data.get(key, [])
        if not isinstance(globs, list) or not all(isinstance(glob, str) and glob.strip() for glob in globs):
            problems.append(f"{LOCAL}: {key} is a list of non-empty strings")
        elif repeated := sorted(set(globs) & set(kit)):
            problems.append(f"{LOCAL}: {key} repeats what the kit has: {', '.join(repeated)}")
    whole = data.get("whole", {})
    if not isinstance(whole, dict):
        return [*problems, f"{LOCAL}: whole is a table of `prefix = directory`"]
    for prefix, name in whole.items():
        if not prefix.strip():
            problems.append(f"{LOCAL}: a prefix in whole is empty")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            problems.append(f"{LOCAL}: whole.{prefix!r} names a directory: letters, digits, `_` and `-`")
        elif prefix in behaviour_selection.WHOLE:
            problems.append(f"{LOCAL}: whole.{prefix!r} is the kit's already")
    return problems


def load_rules(root: pathlib.Path) -> Rules:
    """The kit's lists with those of `scripts/local/behaviour.toml` under `root` added, where there is one. A
    file that cannot be used is refused with every reason at once."""
    path = root / LOCAL
    if not path.is_file():
        return Rules()
    data = _read(path)
    if problems := _problems(data):
        raise LocalError("\n".join(problems))
    return Rules(
        (*behaviour_selection.EVERYTHING_WHEN, *data.get("everything_when", [])),
        (*behaviour_gate.HARNESS_FILES, *data.get("harness_files", [])),
        {**behaviour_selection.WHOLE, **data.get("whole", {})},
    )
