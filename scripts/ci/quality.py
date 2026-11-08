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

"""The quality ratchet: measure what is hard to maintain, and let it only get better.

The codebase already exceeds every threshold worth setting, so setting them outright
would mean either a flag day or a permanently red build. Instead a change is compared
against the code it is a change *to*, and fails when a reading gets worse than the base
it started from. Nothing has to be fixed to merge; the numbers just cannot climb.

The base is measured rather than committed. A recorded baseline is a generated file that
every branch has to carry, so every rebase conflicts on it and neither side's copy
describes the tree afterwards; and an improvement has to be re-recorded by hand or the
gain sits there as headroom for the next change to spend. Measuring both sides removes
both problems: a gain is locked in the moment it merges, because the next change is
compared against a base that already has it.

    quality.py readings --out FILE   # every meter, as json - what CI publishes for main
    quality.py check --base FILE     # this tree against those readings - what CI runs
    quality.py base --ref origin/main --out FILE   # measure the merge base, for local use
    quality.py show                  # the readings, with detail

In CI the base readings come from the run on `main` that produced the commit this branch
would merge into, restored from the actions cache. That cache is a rendezvous rather than
the source of the answer: on a miss CI measures the base itself, the same way `base` does
here. A meter reports as unmeasured, and passes, only when measuring the base failed -
which is not the branch's fault, and must not turn a pull request red.

`check` and `show` also take `--json`, which says the same thing in a form something
other than a person can act on: which meter moved, which key inside it, from what to
what. Both renderings come from one evaluation, so they cannot drift apart.

A regression that is the point of the change is allowed by a `Quality-Allow:` trailer on a
commit the branch adds. Why that needs no ceiling and leaves nothing to sweep up is in
quality_allowances.py.

The two coverage meters read a report rather than running a suite, because the suites
that produce them take minutes and already run in jobs of their own. They are skipped,
with a note, when their report is absent - `check --require-coverage` turns that into a
failure, which is what CI uses in the jobs that do have the report.

Every meter reads a tree, and `--tree` says which one. It defaults to the checkout this
script lives in; `base` points it at a throwaway worktree of another commit. The tools
themselves always come from this checkout, so both sides of a comparison are measured by
one toolchain rather than by whatever each commit happened to pin.

Four modules, one concern each: quality_meters.py is what a meter is and how it reads a
tree, quality_allowances.py is what a change is allowed to make worse and how it says so,
quality_moves.py is how code that moved is told from code that arrived, and this is what
is done with a reading once it has been taken.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import typing

import quality_meters
from quality_allowances import Allowance, allowances_in_commits, git_maybe
from quality_meters import BY_NAME, METERS, Meter, MeterError
from quality_moves import rekey, renames_since, within_the_room_freed

# The three vocabularies the json contract is written in. Spelled out as literals so that
# a reader knows what can appear and a type checker refuses anything else.
#
# `allowed` is a regression a `Quality-Allow:` trailer names; it is reported like any
# other movement and simply does not count against the verdict. `moved` is a key with no
# base reading that the meter freed the room for elsewhere, which is code that changed
# address rather than code that arrived, and counts against nothing either. `unmeasured`
# is a meter with no base reading to compare against, which is a gap in what CI knows
# rather than anything about the code, and so is not a failure either.
Movement = typing.Literal["worse", "better", "allowed", "moved"]
Verdict = typing.Literal["worse", "better", "holding", "unmeasured"]
Status = typing.Literal["slipped", "holding"]


class Change(typing.TypedDict):
    """One key that moved. `was` is None for a key that is new, `now` for one that is gone."""

    key: str
    was: float | None
    now: float | None
    movement: Movement


class MeterVerdict(typing.TypedDict):
    name: str
    summary: str
    verdict: Verdict
    changes: list[Change]


class CheckResult(typing.TypedDict):
    """What `check --json` writes. The printed output says the same thing."""

    command: str
    status: Status
    regressed: list[str]
    improved: list[str]
    unmeasured: list[str]
    # what this run did not compare, which the verdict names rather than claiming
    unchecked: list[str]
    meters: list[MeterVerdict]
    skipped: list[str]
    spent: list[str]


class MeterReadings(typing.TypedDict):
    name: str
    summary: str
    direction: str
    total: float
    readings: dict[str, float]


class SurveyResult(typing.TypedDict):
    """What `show --json` writes."""

    command: str
    meters: list[MeterReadings]
    skipped: list[str]


def read_readings(path: pathlib.Path) -> dict[str, dict[str, float]]:
    """The readings in a file `readings` wrote, or nothing at all when there is no file.

    Nothing rather than an error: in CI this is a cache entry, and a miss is a fact about
    the cache rather than about the branch. `evaluate` turns the absence into an
    unmeasured verdict that says so.
    """

    if not path.exists():
        return {}
    return json.loads(path.read_text())["meters"]


def read_base_commit(path: pathlib.Path) -> str | None:
    """Which commit the base readings were taken at, when the file says.

    `check` needs it to know which commits this branch adds, and so which `Quality-Allow:`
    trailers are this change's rather than the record's. Absent from a file written before
    this was recorded, and absent when the reading came from somewhere with no commit to
    name, so the caller has to cope with None either way.
    """

    if not path.exists():
        return None
    return json.loads(path.read_text()).get("commit")


def write_readings(path: pathlib.Path, meters: dict[str, dict[str, float]], commit: str | None = None) -> None:
    document = {
        "_comment": (
            "Written by scripts/ci/quality.py - a measurement of one tree, not a file to keep. "
            "See the module docstring for what a reading is and what it is compared against."
        ),
        "commit": commit,
        "meters": {name: dict(sorted(readings.items())) for name, readings in sorted(meters.items())},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")


def _movement(meter: Meter, current: float | None, previous: float | None) -> Movement | None:
    """Which way one key moved, or None when it did not move enough to count."""

    if current is None:
        return "better"
    if previous is None:
        return "worse"
    if meter.is_worse(current, previous):
        return "worse"
    if meter.is_better(current, previous):
        return "better"
    return None


def compare(
    meter: Meter, now: dict[str, float], was: dict[str, float], allowances: list[Allowance] | None = None
) -> list[Change]:
    """Every key that moved, as data. A reading inside the meter's slack did not move.

    Returned rather than printed so that the text and the json renderings say the same
    thing by construction - the point of `--json` is that a script and a person are
    reading one answer, not two that can drift apart.

    An allowance downgrades one key's regression to `allowed`; it does not hide it. The
    change is still reported, and still shown to whoever reads the output, because a
    regression somebody argued for is exactly the one worth seeing. `moved` is reported the
    same way and for the same reason: it is a new key that the meter can account for.
    """

    allowed = {entry.key: entry for entry in allowances or [] if entry.meter == meter.name}
    moved = within_the_room_freed(meter, now, was)
    changes: list[Change] = []
    for key in sorted(set(now) | set(was)):
        current, previous = now.get(key), was.get(key)
        movement = _movement(meter, current, previous)
        if movement == "worse" and key in moved:
            movement = "moved"
        elif movement == "worse" and key in allowed and allowed[key].permits(current):
            movement = "allowed"
        if movement:
            changes.append({"key": key, "was": previous, "now": current, "movement": movement})
    return changes


def spent_allowances(meters: list[MeterVerdict], allowances: list[Allowance]) -> list[str]:
    """Allowances that permit nothing, because the key they name did not regress.

    A trailer claiming a regression the change does not make: written for one that was
    later fixed, or for the wrong key, or copied from another branch. The commit message
    then says the change makes something worse when it does not, which is worth saying out
    loud - the message is the argument, and an argument for a thing that did not happen is
    the kind of thing a reader believes.

    Not regressing covers two shapes, and both are spent: a key that did not move, and one
    that *improved*. The second reads oddly until you say it out loud - a trailer arguing
    for a regression on a key the change made better is the most wrong a trailer can be.

    Reported rather than failed. Nothing is wrong with the tree, and the fix is to amend a
    commit message, which is not something to stop a branch over.
    """

    # A key that regressed is reported as the regression it is, by the meter, and its
    # allowance is doing exactly what it says. Everything else is spent.
    still_moving = {
        (verdict["name"], change["key"])
        for verdict in meters
        for change in verdict["changes"]
        if change["movement"] in {"allowed", "worse"}
    }
    measured = {verdict["name"] for verdict in meters if verdict["verdict"] != "unmeasured"}
    return [
        f"{entry.meter}:{entry.key} - {entry.reason}"
        for entry in allowances
        if entry.meter in measured and (entry.meter, entry.key) not in still_moving
    ]


def _change_line(change: Change) -> str:
    allowed = " (allowed)" if change["movement"] == "allowed" else ""
    if change["now"] is None:
        return f"    {change['key']}: {change['was']} -> gone"
    if change["was"] is None:
        arrival = "moved here, at" if change["movement"] == "moved" else "new, at"
        return f"    {change['key']}: {arrival} {change['now']}{allowed}"
    return f"    {change['key']}: {change['was']} -> {change['now']}{allowed}"


def collect(names: list[str], *, require_reports: bool) -> tuple[dict[str, dict[str, float]], list[str]]:
    readings, skipped = {}, []
    for name in names:
        meter = BY_NAME[name]
        try:
            readings[name] = meter.collect()
        except MeterError as problem:
            if meter.needs_report and not require_reports:
                skipped.append(f"{name}: skipped, needs `{meter.needs_report}`")
                continue
            print(f"{name}: could not be read\n  {problem}", file=sys.stderr)
            raise SystemExit(2) from problem
    return readings, skipped


def _judge(
    meter: Meter, now: dict[str, float], was: dict[str, float] | None, allowances: list[Allowance] | None = None
) -> MeterVerdict:
    """One meter's verdict against the base readings."""

    if was is None:
        return {"name": meter.name, "summary": meter.summary, "verdict": "unmeasured", "changes": []}
    changes = compare(meter, now, was, allowances)
    moved = {change["movement"] for change in changes}
    verdict: Verdict = "worse" if "worse" in moved else ("better" if "better" in moved else "holding")
    return {"name": meter.name, "summary": meter.summary, "verdict": verdict, "changes": changes}


def evaluate(base: pathlib.Path, names: list[str], *, require_reports: bool) -> CheckResult:
    """What `check` found, before anybody decides how to say it."""

    was = read_readings(base)
    base_commit = read_base_commit(base)
    allowances = allowances_in_commits(base_commit)
    renames = renames_since(base_commit)
    readings, skipped = collect(names, require_reports=require_reports)
    meters = [
        _judge(BY_NAME[name], now, rekey(was[name], renames) if name in was else None, allowances)
        for name, now in readings.items()
    ]
    regressed = [meter["name"] for meter in meters if meter["verdict"] == "worse"]
    return {
        "command": "check",
        "status": "slipped" if regressed else "holding",
        "regressed": regressed,
        "improved": [meter["name"] for meter in meters if meter["verdict"] == "better"],
        "unmeasured": [meter["name"] for meter in meters if meter["verdict"] == "unmeasured"],
        # a skipped meter has no reading at all, so it is not among `meters` to be judged
        "unchecked": [m["name"] for m in meters if m["verdict"] == "unmeasured"]
        + [name for name in names if name not in readings],
        "meters": meters,
        "skipped": skipped,
        "spent": spent_allowances(meters, allowances),
    }


VERDICT_LINES = {
    "unmeasured": "{name}: no base reading to compare against",
    "worse": "{name}: worse than the base - {summary}",
    "better": "{name}: better than the base",
    "holding": "{name}: holding",
}


def _compared_nothing(result: CheckResult) -> bool:
    """Whether this run put a single meter beside a base reading."""
    return len(result["unchecked"]) == len(result["meters"]) + len(result["skipped"])


def _nothing_compared(result: CheckResult) -> str:
    """What to do about a run that compared nothing, which is not a pass and may not read as one.

    A checkout with no base readings answered "Every meter that was checked is at or better than
    the base" - true, and true of nothing - and #814 was pushed on it and failed in CI.

    Which of the two reasons it was decides the remedy: a meter with no base wants
    `quality-base`, and one whose report is missing wants the suite that produces it. Naming the
    wrong one sends the reader to a command that will change nothing.
    """
    remedies = []
    if result["unmeasured"]:
        remedies.append("there are no base readings here - run `make quality-base` first")
    if result["skipped"]:
        remedies.append("the readings these need have not been produced - see the notes above")
    return "\nNOTHING WAS COMPARED, so this says nothing about the change:\n    " + "\n    ".join(remedies)


def _print_check(result: CheckResult) -> None:
    for meter in result["meters"]:
        print(VERDICT_LINES[meter["verdict"]].format(**meter))
        for change in meter["changes"]:
            print(_change_line(change))
    for note in result["skipped"]:
        print(note)

    if result["unmeasured"]:
        print(
            f"\nNothing to compare {', '.join(result['unmeasured'])} against. In CI that is a cold cache"
            "\nand these meters went unchecked; locally, `make quality-base` measures origin/main first.",
            file=sys.stderr,
        )
    for entry in result["spent"]:
        print(
            f"\nThis Quality-Allow trailer permits nothing - the key it names did not regress."
            f"\nDrop it from the commit message, which is otherwise arguing for a regression"
            f"\nthis change does not make:\n  {entry}",
            file=sys.stderr,
        )

    if result["regressed"]:
        print(
            f"\nThe ratchet slipped on: {', '.join(result['regressed'])}."
            "\nBring the readings back to where the base has them, or - if the change genuinely"
            "\nwarrants it - say why in the commit that makes it:"
            "\n\n    Quality-Allow: <meter> <key>",
            file=sys.stderr,
        )
    elif _compared_nothing(result):
        print(_nothing_compared(result))
    elif result["unchecked"]:
        # The note above goes to stderr and is read past; this line is the answer taken away,
        # and "every meter" over seven of nine is how #785 read green locally and failed in CI.
        # Each meter says why it was not read: a missing report is one CI supplies by running
        # the suite, while a missing base reading is one CI may have skipped as well.
        print("\nEvery meter that was checked is at or better than the base.")
        for name in result["unchecked"]:
            unmeasured = name in result["unmeasured"]
            why = (
                "no base to compare against, which CI has too on a cold cache"
                if unmeasured
                else "CI runs the suite it needs"
            )
            print(f"    {name}: not checked here - {why}")
    else:
        print("\nEvery meter is at or better than the base.")


def command_check(base: pathlib.Path, names: list[str], *, require_reports: bool, as_json: bool) -> int:
    result = evaluate(base, names, require_reports=require_reports)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        _print_check(result)
    return 0 if result["status"] == "holding" else 1


def survey(names: list[str]) -> SurveyResult:
    readings, skipped = collect(names, require_reports=False)
    meters: list[MeterReadings] = [
        {
            "name": name,
            "summary": BY_NAME[name].summary,
            "direction": BY_NAME[name].direction,
            "total": round(sum(now.values()), 2),
            "readings": now,
        }
        for name, now in readings.items()
    ]
    return {"command": "show", "meters": meters, "skipped": skipped}


def command_show(names: list[str], *, as_json: bool) -> int:
    result = survey(names)
    if as_json:
        print(json.dumps(result, indent=2))
        return 0
    for meter in result["meters"]:
        unit = "%" if meter["direction"] == "up" else f" across {len(meter['readings'])} keys"
        print(f"\n{meter['name']} - {meter['summary']}")
        print(f"  now: {meter['total']}{unit}")
        for key, value in sorted(meter["readings"].items(), key=lambda pair: (-pair[1], pair[0])):
            print(f"    {value:>7} {key}")
    for note in result["skipped"]:
        print(note)
    return 0


def command_readings(out: pathlib.Path, names: list[str], *, require_reports: bool) -> int:
    readings, skipped = collect(names, require_reports=require_reports)
    # The commit these were taken at, so whoever compares against them can tell which
    # commits are theirs. Best-effort: a reading is still a reading without it.
    write_readings(out, readings, commit=git_maybe("rev-parse", "HEAD"))
    for note in skipped:
        print(f"{note} - not written")
    print(f"Wrote {', '.join(readings)} to {out}")
    return 0


def _git(*arguments: str) -> str:
    """Ask git something, and refuse to guess when it will not answer."""

    finished = subprocess.run(["git", *arguments], cwd=quality_meters.ROOT, capture_output=True, text=True)
    if finished.returncode != 0:
        raise SystemExit(f"`git {' '.join(arguments)}` failed:\n{finished.stderr.strip()}")
    return finished.stdout.strip()


def _measure_worktree(sha: str, names: list[str]) -> dict[str, dict[str, float]]:
    """Read the meters against another commit, in a worktree thrown away afterwards.

    A worktree rather than a second clone because it costs a checkout rather than a
    fetch, and `--detach` so that no branch is claimed - several sessions work in this
    repository at once and a branch taken here is one taken from somebody.
    """

    with tempfile.TemporaryDirectory(prefix="secretary-records-quality-base-") as scratch:
        checkout = pathlib.Path(scratch) / "tree"
        _git("worktree", "add", "--detach", "--quiet", str(checkout), sha)
        try:
            # eslint.config.js imports the packages it composes, and a fresh worktree has
            # none of them. Linking this checkout's is what makes the comparison one
            # toolchain reading two trees, rather than two installs that may differ.
            # A commit from before there was a frontend has no assets/ to link into.
            (checkout / "assets").mkdir(exist_ok=True)
            (checkout / "assets" / "node_modules").symlink_to(quality_meters.NODE_BIN.parent, target_is_directory=True)
            quality_meters.TREE = checkout
            readings, _skipped = collect(names, require_reports=False)
        finally:
            quality_meters.TREE = quality_meters.ROOT
            _git("worktree", "remove", "--force", str(checkout))
    return readings


def command_base(ref: str, out: pathlib.Path, names: list[str]) -> int:
    """Measure what this branch would be merging into: the point it diverged from `ref`.

    The merge base rather than the tip of `ref`, so that what the comparison reports is
    what this branch did. Against the tip, a meter somebody else improved after this
    branch was cut reads as this branch making it worse.

    `--ref` naming an ancestor of HEAD - `HEAD^1`, which is what CI passes - resolves to
    that commit itself, since the merge base of an ancestor is the ancestor.
    """

    sha = _git("merge-base", ref, "HEAD")
    measurable = [name for name in names if not BY_NAME[name].needs_report]
    if left_out := [name for name in names if name not in measurable]:
        print(f"Not measuring {', '.join(left_out)}: reading one needs a suite to have run at {sha[:8]}.")
    write_readings(out, _measure_worktree(sha, measurable), commit=sha)
    print(f"Measured {sha[:8]} into {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "show", "readings", "base"])
    parser.add_argument(
        "--meter",
        action="append",
        choices=[meter.name for meter in METERS],
        help="only this meter; repeatable. Defaults to all of them.",
    )
    parser.add_argument("--base", type=pathlib.Path, help="check: the readings to compare this tree against")
    parser.add_argument("--out", type=pathlib.Path, help="readings, base: where to write what was measured")
    parser.add_argument("--ref", default="origin/main", help="base: the branch this one would merge into")
    parser.add_argument("--tree", type=pathlib.Path, help="the checkout to measure. Defaults to this one.")
    parser.add_argument(
        "--require-coverage",
        action="store_true",
        help="fail rather than skip when a coverage report is missing",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="write the same answer as json, for something that is not a person to read",
    )
    arguments = parser.parse_args()
    names = arguments.meter or [meter.name for meter in METERS]

    if arguments.tree:
        quality_meters.TREE = arguments.tree.resolve()

    if arguments.command == "check":
        if not arguments.base:
            parser.error("check needs --base: the readings this tree is compared against")
        return command_check(
            arguments.base, names, require_reports=arguments.require_coverage, as_json=arguments.as_json
        )
    if arguments.command == "show":
        return command_show(names, as_json=arguments.as_json)
    if not arguments.out:
        parser.error(f"{arguments.command} needs --out: where to write the readings")
    if arguments.command == "readings":
        return command_readings(arguments.out, names, require_reports=arguments.require_coverage)
    return command_base(arguments.ref, arguments.out, names)


if __name__ == "__main__":
    raise SystemExit(main())
