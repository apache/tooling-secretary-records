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
"""Whether the fast local stage (`scripts/ci/fast_stage.py`) would have caught what the full suite found.

The stage is kept honest by a measure taken on every pull request, not by a set of old failures replayed
against it. After CI's full pytest run, `audit` asks the stage's own planner what it would select for the
pull request's change - measured from the merge base, as the stage measures a checkout with no green run
recorded, and with no cache of what failed last - and sets that beside the tests that failed. Each is:

- unplaced: no test file is named for it (an error in a conftest or a package), so no selection can hold it
  and it is not run again;
- a flake: it passed when it was run again, once, after the suite (`failed` writes what to run);
- unconfirmed: nothing says how it did when run again, so it is neither a miss nor a flake;
- sent: the change reaches every test (`FULL_WHEN`) or its selection was past the limit, so the stage
  would have answered that it needs the whole suite - with whether the file was in what it still ran;
- caught: its file was in the selection;
- inherited: it was not, and it fails on the base as well (`missed` writes what to run there): not this
  change's to catch;
- missed: it was not, and the stage would have run its selection alone.

It is a measurement, and never fails the build. `audit` writes the job summary and selection-audit.json,
which says why when there was nothing it could read; `rollup` (selection_rollup.py) reads a week of those
and prints the body of the one tracking issue. What talks to GitHub is in the workflows, not here.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ElementTree
from typing import Final

from fast_stage import NAMED_BY_DIRECTORY, Plan, full_run_reasons, import_rule, make_plan, suite
from fast_stage_record import file_of, git, read_junit

ROOT: Final = pathlib.Path(__file__).resolve().parents[2]
VERSION: Final = 1
OUTCOMES: Final = ("caught", "missed", "inherited", "sent", "flake", "unconfirmed", "unplaced")
REAL: Final = frozenset({"caught", "missed", "sent"})
"""The outcomes of a failure of this change that failed again: the ones the stage is measured on."""

VERDICTS: Final = {
    "fails": "The fast stage would have failed: its selection holds a real failure",
    "needs-the-suite": "The fast stage would have said this change needs the whole suite, and not passed it",
    "passes-wrongly": "The fast stage would have passed a change the full suite failed: a miss",
    "unanswered": "Something failed that the audit could not place or run again: it cannot say what the"
    " fast stage would have done",
    "inherited": "What failed fails on the base as well: there was nothing of this change's to catch",
    "clean": "Nothing failed again: there was nothing for the fast stage to catch",
}


class UnauditedError(Exception):
    """What the audit needs is not there to read."""


@dataclasses.dataclass(frozen=True)
class Case:
    test: str
    """The pytest node id: what the re-run asks for, and what its result is matched by."""
    file: str | None
    failed: bool
    skipped: bool = False


@dataclasses.dataclass(frozen=True)
class Failure:
    test: str
    file: str | None
    outcome: str
    selected: bool
    """Whether the file was in what the stage would have run - after the limit, for a capped selection."""
    rules: tuple[str, ...] = ()
    link: str | None = None
    """For a miss: a link between the test and the change that no rule draws, when one is found."""


@dataclasses.dataclass(frozen=True)
class Reports:
    """The junit reports of a run: the suite's, its failures run again, and its misses run on the base."""

    suite: pathlib.Path
    rerun: pathlib.Path | None = None
    on_base: pathlib.Path | None = None


# --- the suite's report ------------------------------------------------------------------------------


def _case(root: pathlib.Path, element: ElementTree.Element) -> Case:
    classname, name = element.get("classname", ""), element.get("name", "")
    failed = element.find("failure") is not None or element.find("error") is not None
    skipped = element.find("skipped") is not None
    if not classname:  # a file that failed to collect: pytest names it, dotted, with no class
        file = file_of(root, name)
        return Case(file or name, file, failed, skipped)
    file = file_of(root, classname)
    if file is None:
        return Case(f"{classname}::{name}", None, failed, skipped)
    inside = classname.split(".")[file.count("/") + 1 :]
    return Case("::".join([file, *inside, name]), file, failed, skipped)


def read_cases(root: pathlib.Path, junit: pathlib.Path) -> list[Case]:
    tree = ElementTree.parse(junit)  # noqa: S314 - pytest's report of a run in this same job
    return [_case(root, element) for element in tree.iter("testcase")]


def read_optional(root: pathlib.Path, junit: pathlib.Path | None) -> list[Case] | None:
    """A report of a second run, or None where there is none to read: absent, broken, or with no case in
    it - which is what one id pytest cannot find leaves, since it then runs nothing."""
    try:
        cases = read_cases(root, junit) if junit is not None else []
    except OSError, ElementTree.ParseError:
        cases = []
    return cases or None


def failing(cases: list[Case]) -> list[Case]:
    """The cases that failed or errored, each once, in the order of the report."""
    return list({case.test: case for case in cases if case.failed}.values())


def failed_again(failure: Case, rerun: list[Case] | None) -> bool | None:
    """Whether the test failed when run again; None when nothing says. A file that failed to collect is
    collected again and reports its tests instead, so it failed again when one of them did."""
    if rerun is None:
        return None
    same = [case for case in rerun if case.test == failure.test and not case.skipped]
    if not same and failure.test == failure.file:
        same = [case for case in rerun if case.file == failure.file and not case.skipped]
    return any(case.failed for case in same) if same else None


# --- the stage's answer --------------------------------------------------------------------------------


def _git(root: pathlib.Path, *arguments: str) -> str:
    finished = git(root, *arguments)
    if finished.returncode != 0:
        raise UnauditedError(f"git {' '.join(arguments)} failed: {finished.stderr.strip()}")
    return finished.stdout


def changed_between(root: pathlib.Path, base: str, head: str) -> tuple[str, str, set[str]]:
    """The head commit, where it meets `base`, and what it changed since: both sides of a rename."""
    head_sha = _git(root, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    merge_base = _git(root, "merge-base", base, head_sha).strip()
    names = _git(root, "diff", "--name-only", "--no-renames", "-z", merge_base, head_sha)
    return head_sha, merge_base, {name for name in names.split("\0") if name}


def outcome(failure: Case, again: bool | None, on_base: bool | None, plan: Plan) -> str:
    """What a failure says of the stage, in this order: one no file holds is never run again, so it is
    unplaced whatever the re-run says, and one that did not fail again is not the stage's, sent or not."""
    if failure.file is None:
        return "unplaced"
    if again is not True:
        return "flake" if again is False else "unconfirmed"
    if plan.reasons:
        return "sent"
    if failure.file in plan.rules:
        return "caught"
    return "inherited" if on_base else "missed"


def link(root: pathlib.Path, test: str, changed: set[str], tests: dict[str, str]) -> str | None:
    """What ties a missed test to the change that the stage's rules do not: it imports a module that imports
    a changed one, or it names a changed file's module. The first a rule could be written for."""
    text = {test: tests.get(test, "")}
    application = {
        path.relative_to(root).as_posix(): path.read_text(errors="replace") for path in (root / "app").rglob("*.py")
    }
    for path in sorted(changed):
        for module in sorted(import_rule(root, path, application)):
            if import_rule(root, module, text):
                return f"it imports {module}, which imports {path}"
    for path in sorted(changed):
        stem = pathlib.PurePosixPath(path).name.split(".", 1)[0]
        if stem not in NAMED_BY_DIRECTORY and len(stem) > 2 and re.search(rf"\b{re.escape(stem)}\b", text[test]):
            return f"it names `{stem}`, the module of {path}"
    return None


def _unjudged(failure: Failure) -> bool:
    """A failure that could have failed the stage, for all the audit can tell: an error in no test file, which
    may be in a package the selection holds, or a selected test nothing ran again."""
    return failure.outcome == "unplaced" or (failure.outcome == "unconfirmed" and failure.selected)


def verdict(failures: list[Failure], plan: Plan) -> str:
    """What the stage would have answered. One failure in its selection fails it; an empty selection runs
    nothing and answers that the whole suite is wanted, as a change that reaches every test does. A miss is
    a pass only when nothing else could have failed the stage, and where no failure is real, a failure the
    audit could not judge is never read as nothing to catch."""
    outcomes = {failure.outcome for failure in failures}
    real = bool(outcomes & REAL)
    if real and any(failure.selected for failure in failures if failure.outcome in REAL):
        return "fails"
    if real and (plan.reasons or not plan.rules):
        return "needs-the-suite"
    if any(_unjudged(failure) for failure in failures):
        return "unanswered"
    if real:
        return "passes-wrongly"
    if "unconfirmed" in outcomes:
        return "unanswered"
    return "inherited" if "inherited" in outcomes else "clean"


def classify(cases: list[Case], seconds: tuple[list[Case] | None, list[Case] | None], plan: Plan) -> list[Failure]:
    """Each failing case's outcome, from what it did run again and what it did on the base (`seconds`)."""
    again, on_base = seconds
    failures = []
    for case in cases:
        rules = tuple(plan.rules.get(case.file or "", ()))
        found = outcome(case, failed_again(case, again), failed_again(case, on_base), plan)
        failures.append(Failure(case.test, case.file, found, bool(rules), rules))
    return failures


def _linked(root: pathlib.Path, failures: list[Failure], changed: set[str]) -> list[Failure]:
    """Each miss with what links it to the change, where something does."""
    if not any(failure.outcome == "missed" for failure in failures):
        return failures
    tests = suite(root)
    missed = {each.test: link(root, each.file or "", changed, tests) for each in failures if each.outcome == "missed"}
    return [dataclasses.replace(each, link=missed[each.test]) if each.test in missed else each for each in failures]


def audit(root: pathlib.Path, reports: Reports, base: str, head: str) -> dict:
    """The stage's selection for the change from where `base` and `head` meet, beside what failed in the
    suite's report, as the record selection-audit.json keeps. The tree at `root` is what the suite ran."""
    head_sha, merge_base, changed = changed_between(root, base, head)
    try:
        cases, seconds = read_cases(root, reports.suite), read_junit(root, reports.suite)
    except FileNotFoundError as error:
        raise UnauditedError("the suite wrote no junit report") from error
    except ElementTree.ParseError as error:
        raise UnauditedError(f"the suite's junit report does not parse: {error}") from error
    plan = make_plan(root, changed, seconds, set())
    triggers = full_run_reasons(root, changed)
    again = (read_optional(root, reports.rerun), read_optional(root, reports.on_base))
    failures = _linked(root, classify(failing(cases), again, plan), changed)
    return {
        "version": VERSION,
        "audited": True,
        "head": head_sha,
        "merge_base": merge_base,
        "changed": sorted(changed),
        "reasons": plan.reasons,
        "triggered": bool(triggers),
        "capped": len(plan.reasons) > len(triggers),
        "selection": plan.rules,
        "files": len(plan.seconds),
        "share": round(plan.share, 3),
        "verdict": verdict(failures, plan),
        "counts": {name: sum(1 for each in failures if each.outcome == name) for name in OUTCOMES},
        "failures": [dataclasses.asdict(each) for each in failures],
    }


# --- the job summary -----------------------------------------------------------------------------------

HEADING: Final = "## Would the fast stage have caught it?"
SAID: Final = {
    "inherited": "inherited: it fails on the base too",
    "unplaced": "unplaced: in no test file",
}


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _said(failure: dict) -> str:
    if failure["outcome"] == "sent":
        return "sent, in what the stage still ran" if failure["selected"] else "sent, not in what the stage ran"
    found: str = failure["outcome"]
    return SAID.get(found, found)


def summary(record: dict) -> str:
    if not record.get("audited"):
        return f"{HEADING}\n\nThis run could not be audited: {record.get('why')}.\n"
    lines = [
        HEADING,
        "",
        f"The fast stage's selection for this change ({len(record['changed'])} files changed,"
        f" {record['merge_base'][:12]}..{record['head'][:12]}), beside what the full suite found."
        " A measurement: it never fails the build. docs/troubleshooting.md says how to read it.",
        "",
        f"**{VERDICTS[record['verdict']]}.**",
        "",
        f"It selects {len(record['selection'])} of {record['files']} test files,"
        f" {record['share']:.0%} of the suite's test time.",
    ]
    if record["reasons"]:
        lines += ["", "It would send the change to the full suite, because:", ""]
        lines += [f"- {reason}" for reason in record["reasons"]]
    failures = record["failures"]
    if failures:
        lines += ["", "| Failed | Outcome | Selected by |", "| --- | --- | --- |"]
        lines += [
            f"| `{_cell(each['test'])}` | {_said(each)} | {_cell('; '.join(each['rules'])) or '-'} |"
            for each in failures
        ]
    else:
        lines += ["", "No test failed."]
    for each in (each for each in failures if each["outcome"] == "missed"):
        lines += ["", f"Missed `{each['test']}`: {each['link'] or 'no link to the change was found'}."]
    lines += ["", "<details><summary>The selection, in the order it runs</summary>", ""]
    lines += [f"- `{path}` <- {'; '.join(rules)}" for path, rules in record["selection"].items()]
    lines += ["", "</details>", ""]
    return "\n".join(lines)


# --- the command line ----------------------------------------------------------------------------------


def _reports(arguments: argparse.Namespace) -> Reports:
    return Reports(arguments.junit, arguments.rerun, getattr(arguments, "on_base", None))


def _run_audit(arguments: argparse.Namespace) -> None:
    about = {"pr": arguments.pr, "pr_head": arguments.pr_head, "run": arguments.run}
    try:
        record = audit(arguments.root, _reports(arguments), arguments.base, arguments.head) | about
    except UnauditedError as error:
        record = {"version": VERSION, "audited": False, "why": str(error)} | about
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(record, indent=1) + "\n")
    said = summary(record)
    print(said)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a") as written:
            written.write(said)


def _write_lines(path: pathlib.Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines))


def _write_failed(arguments: argparse.Namespace) -> None:
    """The node ids to run again: one no file holds is left out, since pytest cannot find it and then runs
    nothing at all."""
    tests = [case.test for case in failing(read_cases(arguments.root, arguments.junit)) if case.file]
    _write_lines(arguments.out, tests)
    print(f"{len(tests)} failed, to run again: {arguments.out}")


def _write_missed(arguments: argparse.Namespace) -> None:
    """The files of the real misses, each once, to run on the base: a test the base fails too is inherited."""
    record = audit(arguments.root, _reports(arguments), arguments.base, arguments.head)
    files = list(dict.fromkeys(each["file"] for each in record["failures"] if each["outcome"] == "missed"))
    _write_lines(arguments.out, files)
    print(f"{len(files)} files hold a miss, to run on the base: {arguments.out}")


def _write_rollup(arguments: argparse.Namespace) -> None:
    import selection_rollup  # here, not at the top: it imports this module

    print(selection_rollup.from_arguments(arguments), end="")


def _measure_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--junit", type=pathlib.Path, required=True, help="the suite's junit report")
    command.add_argument("--rerun", type=pathlib.Path, help="the junit report of running the failures again")
    command.add_argument("--base", required=True, help="the change is measured from where this meets --head")
    command.add_argument("--head", default="HEAD")
    command.add_argument("--root", type=pathlib.Path, default=ROOT, help="the tree the suite ran")
    command.add_argument("--out", type=pathlib.Path, required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    measure = commands.add_parser("audit", help="the stage's selection beside what failed")
    _measure_arguments(measure)
    measure.add_argument("--on-base", type=pathlib.Path, help="the junit report of the misses' files on the base")
    measure.add_argument("--pr", type=int)
    measure.add_argument("--pr-head", help="the pull request's own head, which a merge commit is built from")
    measure.add_argument("--run", help="the url of the run")
    again = commands.add_parser("failed", help="write the node ids of what failed, one a line, for pytest @file")
    again.add_argument("--junit", type=pathlib.Path, required=True)
    again.add_argument("--root", type=pathlib.Path, default=ROOT)
    again.add_argument("--out", type=pathlib.Path, required=True)
    _measure_arguments(commands.add_parser("missed", help="write the files of the misses, to run on the base"))
    week = commands.add_parser("rollup", help="print the tracking issue's body from a week of records")
    week.add_argument("directory", type=pathlib.Path, help="where each run's record is, as <run id>/<file>")
    week.add_argument("--runs", type=pathlib.Path, required=True, help="the week's runs: id, head sha, repository")
    week.add_argument("--repo", required=True, help="the repository whose runs are read: others are forks")
    week.add_argument("--previous", type=pathlib.Path, help="the issue's body now, whose earlier weeks are kept")
    week.add_argument("--until", default=datetime.datetime.now(datetime.UTC).date().isoformat())
    arguments = parser.parse_args(argv)
    commands_run = {"audit": _run_audit, "failed": _write_failed, "missed": _write_missed, "rollup": _write_rollup}
    commands_run[arguments.command](arguments)
    return 0


if __name__ == "__main__":
    sys.exit(main())
