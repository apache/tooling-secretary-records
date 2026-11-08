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


"""What the ratchet measures, and how each meter reads it.

Split from quality.py, which is the other half: what is done with a reading once it has
been taken - compared against a base, allowed, judged, printed. Nothing here knows about
any of that. The division is what a meter *is* against what a verdict *is*, and it keeps
either half readable on its own.

Every meter reads `TREE`, and every key it reports is relative to it. `TREE` is not
necessarily this checkout: comparing a change against the commit it would merge into
means measuring that commit too, in a worktree of its own, and the same key has to come
out of both readings for them to be comparable.

`ROOT` is where the tools are, and stays put. A worktree of another commit has no
`yarn install` and no virtual environment of its own, and giving it one would both cost
minutes and measure that commit's toolchain rather than this one's.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import shlex
import subprocess
import typing

if typing.TYPE_CHECKING:
    from collections.abc import Callable

# The checkout this script lives in. It is where the toolchain is installed, which is not
# necessarily what is being measured.
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# The tree under measurement. `--tree` moves it; everything a meter reads is under here,
# and every key a meter reports is relative to it, so a reading of another commit is
# comparable with a reading of this one.
TREE = ROOT

# Node's binaries, from this checkout rather than from the tree being measured: a
# throwaway worktree of another commit has no `yarn install` of its own, and running one
# there would both cost minutes and measure that commit's toolchain instead of this one.
NODE_BIN = ROOT / "assets" / "node_modules" / ".bin"

# The bars the codebase is held to, the same as BAT's (apache/tooling-agenda), whose
# ratchet this is. They are what the readings count violations *of*, and lowering one
# would quietly forgive code rather than fix it. Raising one is a real decision and
# belongs in a commit of its own. The frontend equivalents live in assets/eslint.config.js,
# which has to hold them for the editor to show them too.
MAX_CYCLOMATIC = 12
MAX_COGNITIVE = 15

# How long a file may be before it is on this meter. Taken from BAT, where it was roughly
# where that tree already divided rather than a round number imposed on it.
MAX_LINES = 500

# What a file carved out of another costs in lines whatever is in it: the license header, a
# module docstring, and the imports it no longer shares with the file it left. Measured on
# BAT as the lines before the first definition, which came to between 49 and 73. Eighty
# covers them, and is too small to hide a function in.
SPLIT_PREAMBLE = 80

# Coverage is not reproducible to the last decimal: the same suite measured under a
# different number of xdist workers moves by a few hundredths, and CI's worker count is
# whatever the runner has. Measured on BAT at 66.46 serial and 66.43 under `-n auto` on the
# same tree - so a percentage has to move by more than this before it counts either way, or
# the first CI run would fail on arithmetic rather than on anything anybody wrote.
#
# It applies only to the two `direction="up"` meters. Counts are exact, and get none.
PERCENT_SLACK = 0.1


class MeterError(Exception):
    """A meter could not be read at all - as opposed to reading badly."""


@dataclasses.dataclass(frozen=True)
class Meter:
    """One measurable property of the codebase.

    `collect` returns a mapping of key to reading. What a key means is the meter's own
    business - a file and a rule, a function, a language - and it is what shows up in the
    baseline diff, so it must survive an edit elsewhere in the file. Line numbers must
    not appear in one.
    """

    name: str
    summary: str
    collect: Callable[[], dict[str, float]]
    # "down" for a count of problems, "up" for a percentage of something good.
    direction: str = "down"
    # Coverage meters need a report that a test run produces; the rest need nothing.
    needs_report: str | None = None
    # How much a key that is new may read, per new key, on top of what the same meter freed
    # elsewhere, before it counts as a regression. Set only where a reading is the same unit
    # wherever it sits, so that lines leaving one file and arriving in another are the same
    # lines. None on the rest, where a key with no base reading is code that arrived and has
    # to meet the bar on its own.
    move_allowance: float | None = None

    @property
    def slack(self) -> float:
        return 0.0 if self.direction == "down" else PERCENT_SLACK

    def is_worse(self, now: float, was: float) -> bool:
        if self.direction == "down":
            return now > was
        return now < was - self.slack

    def is_better(self, now: float, was: float) -> bool:
        if self.direction == "down":
            return now < was
        return now > was + self.slack


def _run(command: list[str], *, cwd: pathlib.Path | None = None, allow_fail: bool = True) -> str:
    """Run a tool and hand back its stdout.

    Linters exit non-zero precisely when they have something to say, so a non-zero exit
    is the normal case here and only an empty stdout means something actually broke.
    """

    proc = subprocess.run(command, cwd=cwd or ROOT, capture_output=True, text=True)
    if proc.returncode != 0 and (not allow_fail or not proc.stdout.strip()):
        raise MeterError(
            f"`{shlex.join(command)}` exited {proc.returncode} with no usable output:\n{proc.stderr.strip()}"
        )
    return proc.stdout


def _assets() -> pathlib.Path:
    return TREE / "assets"


def _sources(*names: str) -> list[str]:
    """The measured tree's top-level source paths, absolute.

    Absolute because the python tools run from this checkout - that is where the virtual
    environment they were installed into is - while the code they read may be somewhere
    else entirely.

    Only the ones that exist. A tree with none of them is a tree with nothing to find - the
    commit this repository started from held two markdown files - and handing a tool a path
    that is not there makes it fail rather than report nothing.
    """

    return [str(TREE / name) for name in names if (TREE / name).exists()]


def _relative(path: str) -> str:
    """Tool output carries absolute paths; a reading must not.

    Relative to the tree that was measured rather than to this checkout, so that the same
    file in two commits produces the same key and the two readings can be compared.
    """

    try:
        return str(pathlib.Path(path).resolve().relative_to(TREE.resolve()))
    except ValueError:
        return path


def _ruff(codes: list[str], *, targets: list[str], config: list[str] | None = None) -> dict[str, float]:
    """Count ruff findings per file and rule.

    Keyed by file and code rather than by line, so that inserting a line above a finding
    is not a baseline change. The cost is that two findings of one rule in one file are
    indistinguishable - which is the right trade here, because the count is the thing.
    """

    if not targets:
        return {}
    command = ["uv", "run", "ruff", "check", "--no-cache", "--select", ",".join(codes), "--output-format", "json"]
    for setting in config or []:
        command += ["--config", setting]
    output = _run([*command, *targets])
    readings: dict[str, float] = {}
    for finding in json.loads(output or "[]"):
        key = f"{_relative(finding['filename'])}::{finding['code']}"
        readings[key] = readings.get(key, 0) + 1
    return readings


def python_cyclomatic() -> dict[str, float]:
    return _ruff(
        ["C901", "PLR0911", "PLR0912", "PLR0913", "PLR0915"],
        targets=_sources("app", "scripts", "server.py"),
        config=[f"lint.mccabe.max-complexity={MAX_CYCLOMATIC}"],
    )


def python_cognitive() -> dict[str, float]:
    """Cognitive complexity per function, as complexipy scores it.

    Keyed by function and holding the score itself rather than a count, because
    complexipy names functions and the name outlives an edit. That makes this the
    strictest meter of the set: a grandfathered function may be left alone or improved,
    but it may not get one point worse.
    """

    sources = _sources("app", "scripts", "server.py")
    if not sources:
        return {}
    output = _run(
        [
            "uv",
            "run",
            "complexipy",
            *sources,
            "--quiet",
            "--snapshot-ignore",
            "--output-format",
            "json",
            "--output",
            "/dev/stdout",
        ],
        allow_fail=True,
    )
    # complexipy prints its summary table alongside the json when told to write to a
    # stream, so take the document and ignore whatever framed it.
    start = output.find("[")
    if start < 0:
        raise MeterError(f"complexipy produced no json:\n{output[:400]}")
    functions = json.loads(output[start : output.rindex("]") + 1])
    return {
        f"{_relative(f['path'])}::{f['function_name']}": f["complexity"]
        for f in functions
        if f["complexity"] > MAX_COGNITIVE
    }


def python_dead_code() -> dict[str, float]:
    """Code that costs reading and earns nothing: commented-out code and unused arguments."""

    return _ruff(["ERA001", "ARG001", "ARG002", "ARG003"], targets=_sources("app", "scripts", "server.py"))


def frontend_complexity() -> dict[str, float]:
    """Both complexity rules from assets/eslint.config.js, counted per file.

    They are warnings there so that `yarn lint` stays a gate on correctness and this
    stays the thing that ratchets - see the comment beside them.
    """

    # A tree with no frontend has nothing for eslint to read: the base of the first change
    # here was one. The same holds for the knip and jscpd meters below.
    if not (_assets() / "eslint.config.js").exists():
        return {}
    output = _run([str(NODE_BIN / "eslint"), "src", "-f", "json"], cwd=_assets())
    start = output.find("[")
    if start < 0:
        raise MeterError(f"eslint produced no json:\n{output[:400]}")
    readings: dict[str, float] = {}
    for result in json.loads(output[start:]):
        for message in result["messages"]:
            if message.get("ruleId") in {"complexity", "sonarjs/cognitive-complexity"}:
                key = f"{_relative(result['filePath'])}::{message['ruleId']}"
                readings[key] = readings.get(key, 0) + 1
    return readings


def frontend_dead_code() -> dict[str, float]:
    """knip's tally of unused files, exports, types and dependencies.

    Keyed by knip's own category rather than by the offending name, so that deleting one
    unused export and adding another nets out visibly instead of silently.
    """

    if not (_assets() / "knip.json").exists():
        return {}
    output = _run([str(NODE_BIN / "knip"), "--no-progress", "--reporter", "json"], cwd=_assets())
    start = output.find("{")
    if start < 0:
        raise MeterError(f"knip produced no json:\n{output[:400]}")
    document = json.loads(output[start : output.rindex("}") + 1])
    readings: dict[str, float] = {}
    for issue in document.get("issues", []):
        for category, found in issue.items():
            if isinstance(found, list) and found:
                readings[category] = readings.get(category, 0) + len(found)
    return readings


def duplication() -> dict[str, float]:
    """Copy-pasted blocks, per language, as jscpd counts clones.

    Clone identity is not stable - editing either copy moves both ends - so this counts
    them per format rather than naming them. `.jscpd.json` says what counts as one.
    """

    if not (TREE / ".jscpd.json").exists():
        return {}
    # state/ is gitignored and is where the app keeps its own scratch, but a CI checkout
    # has neither it nor the run that would create it.
    output = TREE / "state" / "jscpd"
    output.mkdir(parents=True, exist_ok=True)
    _run(
        [str(NODE_BIN / "jscpd"), "--reporters", "json", "--output", str(output), "--silent"],
        cwd=TREE,
        allow_fail=True,
    )
    report = output / "jscpd-report.json"
    if not report.exists():
        raise MeterError("jscpd wrote no report; run it by hand to see why")
    statistics = json.loads(report.read_text())["statistics"]["formats"]
    return {language: numbers["clones"] for language, numbers in statistics.items() if numbers["clones"]}


def _source_files() -> list[pathlib.Path]:
    """The files this codebase is written in, as the other meters scope themselves.

    Migrations are generated and vendored code is not ours, matching the exclusions in
    .pre-commit-config.yaml; tests are left out because the other meters leave them out.
    """

    found = [TREE / "server.py"] if (TREE / "server.py").exists() else []
    for directory, patterns in (
        (TREE / "app", ("*.py",)),
        (TREE / "scripts", ("*.py",)),
        (_assets() / "src", ("*.ts", "*.svelte")),
    ):
        for pattern in patterns:
            found += sorted(directory.rglob(pattern))
    return [path for path in found if "migrations" not in path.parts and not path.name.endswith(".test.ts")]


def file_length() -> dict[str, float]:
    """Lines per file, for the files long enough that opening one is a decision.

    Keyed by path and holding the length itself rather than a count, like the cognitive
    meter: a file already past the bar may shrink or hold, but it may not grow. Length is
    what anyone - or anything - has to read before it can change that file safely, and the
    longest files here are where a change is likeliest to break something invisible. It is
    also the one meter whose reading you can improve without touching a line of logic.
    """

    readings: dict[str, float] = {}
    for path in _source_files():
        lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if lines > MAX_LINES:
            readings[_relative(str(path))] = lines
    return readings


def python_coverage() -> dict[str, float]:
    """Line coverage of app/, from the report `pytest --cov-report=json` leaves behind."""

    report = TREE / "coverage.json"
    if not report.exists():
        raise MeterError(
            "no coverage.json - produce one with:\n  scripts/container.sh uv run pytest --cov=app --cov-report=json"
        )
    totals = json.loads(report.read_text())["totals"]
    return {"line-percent": round(totals["percent_covered"], 2)}


def frontend_coverage() -> dict[str, float]:
    """Line coverage of assets/src/js/libs, from vitest's json-summary reporter."""

    report = _assets() / "coverage" / "coverage-summary.json"
    if not report.exists():
        raise MeterError(
            "no coverage-summary.json - produce one with:\n"
            "  scripts/container.sh sh -c 'cd assets && yarn test --coverage'"
        )
    return {"line-percent": round(json.loads(report.read_text())["total"]["lines"]["pct"], 2)}


METERS = [
    Meter(
        "python-cyclomatic",
        f"functions past {MAX_CYCLOMATIC} branches, too many arguments, returns or statements",
        python_cyclomatic,
    ),
    Meter("python-cognitive", f"functions scoring past {MAX_COGNITIVE} on cognitive complexity", python_cognitive),
    Meter("python-dead-code", "commented-out code and unused arguments", python_dead_code),
    Meter("frontend-complexity", "eslint's branch count and sonarjs's cognitive complexity", frontend_complexity),
    Meter("frontend-dead-code", "unused files, exports, types and dependencies, per knip", frontend_dead_code),
    Meter("duplication", "copy-pasted blocks, per language, per jscpd", duplication),
    Meter(
        "file-length",
        f"files past {MAX_LINES} lines, by how long they are",
        file_length,
        move_allowance=SPLIT_PREAMBLE,
    ),
    Meter(
        "python-coverage",
        "line coverage of app/",
        python_coverage,
        direction="up",
        needs_report="pytest --cov-report=json",
    ),
    Meter(
        "frontend-coverage",
        "line coverage of assets/src/js/libs",
        frontend_coverage,
        direction="up",
        needs_report="yarn test --coverage",
    ),
]

BY_NAME = {meter.name: meter for meter in METERS}
