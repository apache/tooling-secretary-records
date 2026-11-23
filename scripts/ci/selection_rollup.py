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
"""A week of selection audits (`scripts/ci/selection_audit.py`) as the body of the one tracking issue, which
`.github/workflows/selection-audit-rollup.yml` writes: `selection_audit.py rollup`.

What it reads was uploaded by pull requests' runs, which run the pull request's code. So it reads only the
runs of this repository's own branches, as the workflow lists them, and exactly one record of each - the one
under that run's id, and only when it is of that run's head commit. Every field is checked for its type,
every string from a record is set as code, where a mention notifies nobody, and a run's url is made from its
id rather than read.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import TYPE_CHECKING, Final

from selection_audit import OUTCOMES, VERDICTS, VERSION, Failure

if TYPE_CHECKING:
    import argparse
    import pathlib

MARK: Final = "<!-- selection-audit -->"
"""The first line of the tracking issue's body: the workflow edits no issue whose body does not start so."""
SHA: Final = re.compile(r"[0-9a-f]{40}", re.ASCII)
WEEK_ROW: Final = re.compile(
    r"\| [0-9]{4}-[0-9]{2}-[0-9]{2} \|(?: [0-9]+ \|){3} (?:n/a|[0-9]{1,3}%) \|(?: [0-9]+ \|){3}", re.ASCII
)
UNSAFE: Final = re.compile(r"[`\x00-\x1f\x7f]")
"""What would end a code span or a line: a backtick, a newline and the other control characters."""
KEPT_WEEKS: Final = 104
LISTED: Final = 25
"""The missed tests listed; each says up to `PER_TEST` of the pushes it failed in."""
PER_TEST: Final = 5
MAX_BODY: Final = 60_000
"""GitHub refuses an issue body past 65,536 characters."""
WRONG: Final = ("fails", "needs-the-suite", "passes-wrongly")
"""The verdicts of a run with a real failure."""


@dataclasses.dataclass(frozen=True)
class Run:
    id: int
    head: str
    repository: str


@dataclasses.dataclass(frozen=True)
class Seen:
    pr: int | None
    commit: str
    run: int
    audited: bool
    verdict: str | None
    changed: tuple[str, ...]
    failures: tuple[Failure, ...]


@dataclasses.dataclass(frozen=True)
class Week:
    repository: str
    runs: int
    """This repository's runs the workflow listed; a fork's are counted in `forks` and not read."""
    forks: int
    kept: int
    """This repository's runs that kept a record of their own head commit."""
    seen: list[Seen]
    """One record a push: the latest run's, where a push ran more than once."""


def read_runs(text: str) -> list[Run]:
    """The workflow's list of the week's runs, a line each: id, head commit, head repository."""
    runs = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[0].isascii() and fields[0].isdigit() and SHA.fullmatch(fields[1]):
            runs.append(Run(int(fields[0]), fields[1], fields[2]))
    return runs


def _listed(value: object) -> list:
    return value if isinstance(value, list) else []


def _failure(raw: object) -> Failure | None:
    if not isinstance(raw, dict) or raw.get("outcome") not in OUTCOMES or not isinstance(raw.get("test"), str):
        return None
    hint = raw.get("link")
    hint = hint if isinstance(hint, str) else None
    return Failure(raw["test"], None, raw["outcome"], raw.get("selected") is True, (), hint)


def _seen(raw: object, run: Run) -> Seen | None:
    """The run's record, when it is one, and of the run's own head commit."""
    if not isinstance(raw, dict) or raw.get("version") != VERSION or raw.get("pr_head") != run.head:
        return None
    pr, verdict = raw.get("pr"), raw.get("verdict")
    return Seen(
        pr if type(pr) is int else None,
        run.head,
        run.id,
        raw.get("audited") is True,
        verdict if verdict in VERDICTS else None,
        tuple(path for path in _listed(raw.get("changed")) if isinstance(path, str)),
        tuple(found for found in map(_failure, _listed(raw.get("failures"))) if found),
    )


def _read(path: pathlib.Path, run: Run) -> Seen | None:
    try:
        return _seen(json.loads(path.read_text()), run)
    except OSError, UnicodeDecodeError, ValueError, RecursionError:
        return None


def load(directory: pathlib.Path, runs: list[Run], repository: str) -> Week:
    """The record under each of this repository's runs' ids; a fork's run is not read."""
    ours = sorted({run.id: run for run in runs if run.repository == repository}.values(), key=lambda run: run.id)
    kept = [seen for run in ours if (seen := _read(directory / str(run.id) / "selection-audit.json", run))]
    latest = {(seen.pr, seen.commit): seen for seen in kept}
    seen = sorted(latest.values(), key=lambda each: (each.pr or 0, each.commit))
    return Week(repository, len(ours), sum(1 for run in runs if run.repository != repository), len(kept), seen)


# --- the body ------------------------------------------------------------------------------------------


def _code(value: str) -> str:
    return f"`{UNSAFE.sub('', value)[:200] or ' '}`"


def _each_failure(seen: list[Seen]) -> list[tuple[Seen, Failure]]:
    """Each push's failures, a test once: as the record first gives it."""
    pairs = []
    for each in seen:
        tests: dict[str, Failure] = {}
        for failure in each.failures:
            tests.setdefault(failure.test, failure)
        pairs += [(each, failure) for failure in tests.values()]
    return pairs


def _rate(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "n/a"


def _verdicts(week: Week) -> dict[str, int]:
    return {name: sum(1 for each in week.seen if each.audited and each.verdict == name) for name in VERDICTS}


def _tests(pairs: list[tuple[Seen, Failure]]) -> dict[str, int]:
    counts = {name: sum(1 for _, failure in pairs if failure.outcome == name) for name in OUTCOMES}
    return counts | {"held": sum(1 for _, failure in pairs if failure.outcome == "sent" and failure.selected)}


def _row(week: Week, until: str) -> str:
    verdicts, tests = _verdicts(week), _tests(_each_failure(week.seen))
    real = sum(verdicts[name] for name in WRONG)
    numbers = [len(week.seen), real, verdicts["passes-wrongly"]]
    rate = _rate(verdicts["passes-wrongly"], real)
    rest = [tests["caught"], tests["missed"], tests["flake"]]
    return f"| {until} | {' | '.join(map(str, numbers))} | {rate} | {' | '.join(map(str, rest))} |"


def _weeks(previous: str, until: str, row: str) -> list[str]:
    """The rows of the week table, newest first: this week's, and the earlier ones `previous` holds."""
    rows = {line[2:12]: line for line in previous.splitlines() if WEEK_ROW.fullmatch(line)} | {until: row}
    return [rows[week] for week in sorted(rows, reverse=True)[:KEPT_WEEKS]]


def _runs(week: Week) -> list[str]:
    verdicts = _verdicts(week)
    real = sum(verdicts[name] for name in WRONG)
    unaudited = sum(1 for each in week.seen if not each.audited)
    prs = len({each.pr for each in week.seen})
    return [
        f"{week.runs} runs of `Build and test` on this repository's pull requests; {week.forks} from forks,"
        " not read. "
        f"{week.kept} of {week.runs} runs kept an audit: {len(week.seen)} pushes of {prs} pull requests, each"
        f" read once; {unaudited} of them could not be audited.",
        "",
        f"**Runs the stage would have passed wrongly: {verdicts['passes-wrongly']} of {real} runs with a real"
        f" failure ({_rate(verdicts['passes-wrongly'], real)}).**",
        "",
        "| Runs | Count |",
        "| --- | --- |",
        f"| A real failure, which the stage would have failed on | {verdicts['fails']} |",
        f"| A real failure, which the stage would have sent to the full suite | {verdicts['needs-the-suite']} |",
        f"| A real failure, which the stage would have passed: a miss | {verdicts['passes-wrongly']} |",
        f"| Failures the audit could not place or run again | {verdicts['unanswered']} |",
        f"| Failures that fail on the base too | {verdicts['inherited']} |",
        f"| Nothing failed again | {verdicts['clean']} |",
    ]


def _failing(pairs: list[tuple[Seen, Failure]]) -> list[str]:
    tests = _tests(pairs)
    return [
        "| Failing tests | Count |",
        "| --- | --- |",
        f"| Caught: the selection held the file | {tests['caught']} |",
        f"| Missed: the selection did not hold the file | {tests['missed']} |",
        f"| Inherited: missed, and failing on the base too | {tests['inherited']} |",
        f"| Sent to the full suite: a trigger or the 40% limit | {tests['sent']}, {tests['held']} in what the"
        " stage ran |",
        f"| Flakes: passed when run again | {tests['flake']} |",
        f"| Not run again | {tests['unconfirmed']} |",
        f"| In no test file | {tests['unplaced']} |",
    ]


def _case_lines(test: str, cases: list[tuple[Seen, Failure]], repository: str) -> list[str]:
    prs = {seen.pr for seen, _ in cases}
    lines = [f"- {_code(test)}: {len(cases)} pushes of {len(prs)} pull requests"]
    for seen, _ in cases[:PER_TEST]:
        where = f"#{seen.pr}" if seen.pr is not None else f"commit {seen.commit[:12]}"
        changed = ", ".join(_code(path) for path in seen.changed[:10]) or "nothing"
        more = f" and {len(seen.changed) - 10} more" if len(seen.changed) > 10 else ""
        lines.append(f"  - {where} ([run](https://github.com/{repository}/actions/runs/{seen.run})): {changed}{more}")
    if len(cases) > PER_TEST:
        lines.append(f"  - and {len(cases) - PER_TEST} more pushes")
    hint = next((failure.link for _, failure in cases if failure.link), None)
    lines.append(f"  - what links them: {_code(hint) if hint else 'none found: a rule to write by hand'}")
    return lines


def _misses(pairs: list[tuple[Seen, Failure]], repository: str, listed: int) -> list[str]:
    """The missed tests, each once with the pushes it failed in: a test many pull requests miss is one rule."""
    by_test: dict[str, list[tuple[Seen, Failure]]] = {}
    for seen, failure in pairs:
        if failure.outcome == "missed":
            by_test.setdefault(failure.test, []).append((seen, failure))
    if not by_test:
        return ["None this week."]
    lines = ["Each is a rule to add to `scripts/ci/fast_stage.py`, with a case as its test.", ""]
    for test, cases in list(by_test.items())[:listed]:
        lines += _case_lines(test, cases, repository)
    if len(by_test) > listed:
        lines.append(f"- and {len(by_test) - listed} more tests, in the artifacts")
    return lines


def _body(week: Week, until: str, rows: list[str], listed: int) -> str:
    """The body, with `listed` missed tests in it."""
    pairs = _each_failure(week.seen)
    lines = [
        MARK,
        "What the fast local stage (`make container-test-fast`, `scripts/ci/fast_stage.py`) would have caught of"
        " what the full suite found on pull requests. `.github/workflows/selection-audit-rollup.yml` writes this"
        " body every week from the `selection-audit` artifacts of the past seven days' `Build and test` runs."
        " docs/troubleshooting.md says how to read it.",
        "",
        f"## The week to {until}",
        "",
        *_runs(week),
        "",
        *_failing(pairs),
        "",
        "### Misses",
        "",
        *_misses(pairs, week.repository, listed),
        "",
        "## Week by week",
        "",
        "| Week to | Pushes | With a real failure | Passed wrongly | Miss rate | Caught tests | Missed tests"
        " | Flakes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *rows,
    ]
    return "\n".join(lines) + "\n"


def rollup(week: Week, until: str, previous: str = "") -> str:
    """The tracking issue's body: this week's runs, tests and misses, and a row a week, the earlier rows kept
    from `previous`. The same records and the same body give the same body again. Past the limit on a body,
    missed tests go from the end of the list, and the body says how many: never a week, since a row left
    out of this body is gone from every body after it."""
    rows = _weeks(previous, until, _row(week, until))
    for listed in range(LISTED, 0, -1):
        body = _body(week, until, rows, listed)
        if len(body) <= MAX_BODY:
            return body
    return _body(week, until, rows, 0)


def from_arguments(arguments: argparse.Namespace) -> str:
    previous = arguments.previous.read_text() if arguments.previous and arguments.previous.is_file() else ""
    runs = read_runs(arguments.runs.read_text())
    return rollup(load(arguments.directory, runs, arguments.repo), arguments.until, previous)
