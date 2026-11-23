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
"""The rules a repository adds to its pre-PR checklist, read from `scripts/local/preflight.toml`.

`scripts/pr-preflight.sh` runs on a host with nothing but `sh` and `git`, and reads this helper's output
and not the file: one line to a fact, `kind<TAB>value`, in the order of the file. A repository without the
file has the rules of the kit and no others.

    [[rules]]
    title = "the interface"                     # the heading of the block, printed when the rule applies
    touches = ["^assets/", "^app/templates/"]   # extended regular expressions, matched against the paths
                                                # the branch changes; any one is enough. A string is one.
    adds = "\\.py$"                              # or against the paths it adds, instead of `touches`
    reads = "(fetch\\(|await )"                  # with `touches`: and a line the branch adds there matches
    reads_in = "^assets/src/js/components/"     # the paths whose added lines are read; `touches` by default
    say = ["Read the design book.", "Add tests."]   # the lines of the block

The file is data, read with tomllib. A file that cannot be used is refused with every reason at once.
Needs Python 3.11 or newer for tomllib.

    python3 -I scripts/ci/preflight_rules.py scripts/local/preflight.toml
"""

from __future__ import annotations

import pathlib
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python before 3.11
    sys.exit("preflight: reading scripts/local/preflight.toml needs Python 3.11 or newer (tomllib)")

_KEYS = {"title", "touches", "adds", "reads", "reads_in", "say"}


class RulesError(Exception):
    """The file says something that cannot be run."""


def _text(value: object, where: str, problems: list[str]) -> str:
    """One piece of text the shell reads as a single field of a single line."""
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where} is a non-empty string")
        return ""
    if any(character in value for character in "\t\n\r\0"):
        problems.append(f"{where} holds a tab or a line break, which the checklist cannot carry")
        return ""
    return value


def _patterns(value: object, where: str, problems: list[str]) -> list[str]:
    """A pattern, or a list of them, as a list."""
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        problems.append(f"{where} is a pattern, or a list of them")
        return []
    return [text for item in items if (text := _text(item, where, problems))]


def _rule(number: int, entry: object, problems: list[str]) -> list[tuple[str, str]]:
    """The facts of one rule, in the order the shell reads them."""
    title = entry.get("title") if isinstance(entry, dict) else None
    where = f"rule {number}" + (f" ({title})" if isinstance(title, str) and title.strip() else "")
    if not isinstance(entry, dict):
        problems.append(f"{where} is a table")
        return []
    problems += [f"{where} has no {key}" for key in sorted(set(entry) - _KEYS)]
    facts = [("rule", _text(title, f"{where}: title", problems))]
    if ("touches" in entry) == ("adds" in entry):
        problems.append(f"{where} has `touches` or `adds`, and not both")
    for key in ("touches", "adds"):
        if key in entry:
            facts += [(key, pattern) for pattern in _patterns(entry[key], f"{where}: {key}", problems)]
    if "reads" in entry:
        if "touches" not in entry:
            problems.append(f"{where}: reads goes with `touches`")
        facts.append(("reads", _text(entry["reads"], f"{where}: reads", problems)))
    if "reads_in" in entry:
        if "reads" not in entry:
            problems.append(f"{where}: reads_in goes with `reads`")
        facts += [("reads_in", pattern) for pattern in _patterns(entry["reads_in"], f"{where}: reads_in", problems)]
    lines = entry.get("say")
    if not isinstance(lines, list) or not lines:
        problems.append(f"{where}: say is a list of lines")
    else:
        facts += [("say", _text(line, f"{where}: say", problems)) for line in lines]
    return facts


def load(path: pathlib.Path) -> list[tuple[str, str]]:
    """The rules of the file as facts, or every reason it cannot be used."""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RulesError(f"{path} cannot be read ({error.strerror})") from error
    except tomllib.TOMLDecodeError as error:
        raise RulesError(f"{path} is not TOML: {error}") from error
    problems = [f"{key} is not a setting" for key in sorted(set(document) - {"rules"})]
    entries = document.get("rules", [])
    if not isinstance(entries, list):
        problems.append("rules is a list of [[rules]] tables")
        entries = []
    facts: list[tuple[str, str]] = []
    for number, entry in enumerate(entries, 1):
        facts += _rule(number, entry, problems)
    if problems:
        raise RulesError(f"{path}:\n" + "\n".join(f"  {problem}" for problem in problems))
    return facts


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: preflight_rules.py <scripts/local/preflight.toml>", file=sys.stderr)
        return 2
    try:
        facts = load(pathlib.Path(argv[0]))
    except RulesError as error:
        print(f"preflight: {error}", file=sys.stderr)
        return 1
    for kind, value in facts:
        print(f"{kind}\t{value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
