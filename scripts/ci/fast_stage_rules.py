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
"""What a repository adds to the fast stage, read from `scripts/local/fast_stage.toml`.

The kit knows what reaches every test in any repository (`fast_stage.FULL_WHEN`). A repository adds what is
its own, where it has it:

    broad_packages = ["api", "models"]          # packages under app/ whose tests are too many for a change
                                                # to one module to select them all
    [full_when]
    "app/webapp.py" = "startup"                 # a path that reaches every test, and why

The file is data, read with tomllib, and adds to the kit's globs and never takes from them. A glob the kit
has already, a key that is not here, and a package name with a `/` in it are refused, each with every other
reason at once. A repository without the file has no broad package and the globs of the kit. Needs Python
3.11 or newer for tomllib, where the file exists.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Mapping

LOCAL: Final = "scripts/local/fast_stage.toml"
_KEYS: Final = frozenset({"broad_packages", "full_when"})


class LocalError(Exception):
    """`scripts/local/fast_stage.toml` says something that cannot be used."""


@dataclasses.dataclass(frozen=True)
class Rules:
    """The kit's globs, and what this repository adds to them."""

    full_when: Mapping[str, str]
    broad_packages: frozenset[str] = frozenset()


def _read(path: pathlib.Path) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python before 3.11
        raise LocalError(f"reading {LOCAL} needs Python 3.11 or newer (tomllib)") from None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LocalError(f"{LOCAL} cannot be read: {error}") from error


def _package_problems(packages: Any) -> list[str]:
    if not isinstance(packages, list) or not all(isinstance(name, str) and name.strip() for name in packages):
        return [f"{LOCAL}: broad_packages is a list of non-empty strings"]
    return [
        f"{LOCAL}: broad_packages {name!r} is a directory name under app/, with no `/`"
        for name in packages
        if "/" in name
    ]


def _glob_problems(full_when: Any, kit: Mapping[str, str]) -> list[str]:
    if not isinstance(full_when, dict):
        return [f"{LOCAL}: full_when is a table of `glob = why`"]
    problems = []
    for glob, why in full_when.items():
        if not glob.strip():
            problems.append(f"{LOCAL}: a glob in full_when is empty")
        if not isinstance(why, str) or not why.strip():
            problems.append(f"{LOCAL}: full_when.{glob!r} says why, in a non-empty string")
        if glob in kit:
            problems.append(f"{LOCAL}: full_when.{glob!r} is the kit's already")
    return problems


def _problems(data: dict[str, Any], kit: Mapping[str, str]) -> list[str]:
    unknown = [f"{LOCAL}: {key!r} is not a key of the file" for key in sorted(set(data) - _KEYS)]
    return [
        *unknown,
        *_package_problems(data.get("broad_packages", [])),
        *_glob_problems(data.get("full_when", {}), kit),
    ]


def load_rules(root: pathlib.Path, kit: Mapping[str, str]) -> Rules:
    """The kit's globs with those of `scripts/local/fast_stage.toml` under `root` added, where there is one. A
    file that cannot be used is refused with every reason at once."""
    path = root / LOCAL
    if not path.is_file():
        return Rules(dict(kit))
    data = _read(path)
    if problems := _problems(data, kit):
        raise LocalError("\n".join(problems))
    return Rules({**kit, **data.get("full_when", {})}, frozenset(data.get("broad_packages", [])))
