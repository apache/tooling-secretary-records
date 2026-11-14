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

"""Whether this change alters what the app answers, and whether it says it means to.

    scripts/container.sh uv run scripts/ci/behaviour.py base     # record with the merge base
    scripts/container.sh uv run scripts/ci/behaviour.py check    # record with this checkout, compare

What is recorded is `tests/behaviour`: every GET route as each reader is answered, and what a fixed
sequence of writes through the API changes, all from the test fixture on a pinned clock. Nothing of
it is committed. `base` checks the commit this branch would merge into out into a throwaway worktree
and has *that* commit record itself, with its own `scripts/ci/behaviour.py` and its own
`tests/behaviour`; `check` records this checkout and compares the two records file by file. A
refactor that moves a module the harness reaches into moves the harness's import with it, and the
two still have to record the same thing.

A file that differs fails the check unless one of this branch's own commits says it should, in the
trailer block of its message:

    Behaviour-Allow: http/admin/*.txt

The value is a path in the record, or a glob over paths (`fnmatch`, where `*` crosses `/`); one that
names no part of a path, `*` or `**`, is refused. The reason is that commit's subject, as for
`Quality-Allow:`. A trailer that allows nothing that changed is reported, so that one left behind
after the change it argued for was reverted does not go on excusing whatever moves there next. Only
the branch's own commits count: not a merge, nor a branch merged in.

A branch that changes the harness itself - `tests/behaviour`, the suite's conftest and fixture data,
these scripts - says so with `Behaviour-Harness: <why>`, and changes nothing under `app/`
(`behaviour_gate.py`).

What differs is written to `state/behaviour.diff`, which is what a reviewer reads. In CI it also
goes to the step summary, and the job uploads it.

The base must be the merge base of `--ref` and HEAD, recorded by `base`: an older record, or one a
failed `base` left behind, is refused rather than compared with. Only while `--ref` itself has no
harness - the change that brings it - is there nothing to compare with; a base without one after
that is an error.

**Only what a change can reach.** A recording also writes, beside the record, which of the app's
modules each part of it ran (`tests/behaviour/executed.py`): each route as each reader asks it, each
scenario step, and `startup`. `check` reads that map from the base and records only the parts whose
modules the change touches, or that import one it touches - one step out, as a constant is read
without running anything of the module holding it. It records everything when the change touches
what no function call shows: a template, a static file, data under app/ that is not Python, a
migration, the fixture, the test setup, this harness, the dependencies, the Makefile, CI - or a
module that runs at startup or is imported by one, a module added or removed, and one no part runs
and no module imports (`behaviour_selection.py`). A route the base never recorded is always asked,
and the route walk is always recorded, even for a change no part runs. `--full` records everything
regardless; the nightly workflow uses it, and so does `make container-snapshot-full`.

    scripts/container.sh uv run scripts/ci/behaviour.py measure --map state/behaviour-base.executed.json

says what the selection would have run for each of main's recent commits.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import fnmatch
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, Any, Final

import behaviour_gate as gate
from behaviour_selection import compared, measure, select

if TYPE_CHECKING:
    from behaviour_selection import Selection

ROOT: Final = pathlib.Path(__file__).resolve().parent.parent.parent
STATE: Final = ROOT / "state"

HASH_SEED: Final = "0"
"""What `tests/behaviour` requires of PYTHONHASHSEED: a set of strings iterates in an order it decides."""

META: Final = "behaviour-record.json"
"""Beside a record rather than in it: which commit it was recorded from, which nothing compares."""

DIFFS_PRINTED: Final = 5


@dataclasses.dataclass(frozen=True)
class Record:
    commit: str
    harness: bool
    files: dict[str, bytes]


def _git(*arguments: str, cwd: pathlib.Path | None = None) -> str:
    finished = subprocess.run(["git", *arguments], cwd=cwd or ROOT, capture_output=True, text=True)
    if finished.returncode != 0:
        raise SystemExit(f"git {' '.join(arguments)} failed: {finished.stderr.strip()}")
    return finished.stdout.strip()


def _workers() -> str:
    """Three unless told: a recording is several apps at once, and a machine shared by several sessions
    has been taken down by fewer. CI, with a runner to itself, asks for four."""
    return os.environ.get("BEHAVIOUR_WORKERS") or str(min(3, os.cpu_count() or 1))


def _meta(out: pathlib.Path) -> pathlib.Path:
    return _beside(out, META)


def _beside(out: pathlib.Path, suffix: str) -> pathlib.Path:
    return out.parent / f"{out.name}.{suffix}"


def run_harness(out: pathlib.Path, selection: dict[str, list[str]] | None = None) -> None:
    """Record with this checkout's `tests/behaviour` into `out` - all of it, or the parts `selection`
    names - and which modules each part ran, beside it (`executed.json`)."""
    for path in (out, _beside(out, "executed")):
        if path.exists():
            shutil.rmtree(path)
    # a map an earlier recording left would be read as this one's, where this one writes none
    _beside(out, "executed.json").unlink(missing_ok=True)
    out.mkdir(parents=True)
    command = [sys.executable, "-m", "pytest", "-n", _workers(), "-q", "-p", "no:cacheprovider", "tests/behaviour"]
    env = os.environ | {
        "PYTHONHASHSEED": HASH_SEED,
        "SR_BEHAVIOUR_OUT": str(out),
        "SR_BEHAVIOUR_EXECUTED": str(_beside(out, "executed")),
    }
    if selection is not None:
        _beside(out, "select.json").write_text(json.dumps(selection) + "\n")
        env |= {"SR_BEHAVIOUR_SELECT": str(_beside(out, "select.json"))}
    finished = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    if finished.returncode != 0:
        raise SystemExit(f"recording failed ({finished.returncode}):\n{finished.stdout[-6000:]}")
    if not any(out.rglob("*")):
        raise SystemExit(f"recording wrote nothing to {out}")
    _merge_executed(out)
    print(next((line for line in reversed(finished.stdout.splitlines()) if " passed" in line), "recorded"))


def _merge_executed(out: pathlib.Path) -> None:
    """Each test's map into one, by part: a route's readers under the route, each reader's modules kept."""
    written = _beside(out, "executed")
    if not written.is_dir():
        return
    merged: dict[str, dict[str, Any]] = {}
    for path in sorted(written.glob("*.json")):
        for name, ran in json.loads(path.read_text()).items():
            part, _, reader = name.partition(" @ ")
            entry = merged.setdefault(part, {"modules": [], "files": [], "by_reader": {}})
            entry["modules"] = sorted(set(entry["modules"]) | set(ran["modules"]))
            entry["files"] = sorted(set(entry["files"]) | set(ran["files"]))
            if reader:
                entry["by_reader"][reader] = ran["modules"]
    _beside(out, "executed.json").write_text(json.dumps(merged, indent=1, sort_keys=True) + "\n")
    shutil.rmtree(written)


def load_executed(out: pathlib.Path) -> dict[str, dict[str, Any]] | None:
    path = _beside(out, "executed.json")
    return json.loads(path.read_text()) if path.exists() else None


def _write_meta(out: pathlib.Path, commit: str, *, harness: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    _meta(out).write_text(json.dumps({"commit": commit, "harness": harness}) + "\n")


def load(out: pathlib.Path) -> Record:
    meta = json.loads(_meta(out).read_text())
    files = (
        {path.relative_to(out).as_posix(): path.read_bytes() for path in sorted(out.rglob("*")) if path.is_file()}
        if out.is_dir()
        else {}
    )
    return Record(meta["commit"], meta["harness"], files)


def record(out: pathlib.Path) -> int:
    """Record with this checkout: as the run on main does for the branches after it, and as a base
    records itself for a branch."""
    _meta(out).unlink(missing_ok=True)
    run_harness(out)
    _write_meta(out, _git("rev-parse", "HEAD"), harness=True)
    return 0


def _dependencies_differ(sha: str) -> bool:
    return subprocess.run(["git", "diff", "--quiet", sha, "--", "uv.lock", "pyproject.toml"], cwd=ROOT).returncode != 0


def _record_with_its_own(sha: str, out: pathlib.Path) -> None:
    """Have `sha` record itself into `out`, with its own script and harness, in a throwaway worktree - and
    its own environment where its dependencies are not this checkout's."""
    with tempfile.TemporaryDirectory(prefix="secretary-records-behaviour-base-") as scratch:
        checkout = pathlib.Path(scratch) / "base"
        script = ["scripts/ci/behaviour.py", "record", "--out", str(out.resolve())]
        env = dict(os.environ)
        if _dependencies_differ(sha):
            command = ["uv", "run", "--frozen", "--no-dev", "--project", str(checkout), "python", *script]
            env["UV_PROJECT_ENVIRONMENT"] = str(pathlib.Path(scratch) / "venv")
        else:
            command = [sys.executable, *script]
        _git("worktree", "add", "--detach", "--quiet", str(checkout), sha)
        try:
            finished = subprocess.run(command, cwd=checkout, env=env)
        finally:
            _git("worktree", "remove", "--force", str(checkout))
    if finished.returncode != 0:
        raise SystemExit(f"{sha[:12]} could not record itself ({finished.returncode})")


def base(ref: str, out: pathlib.Path) -> int:
    """Record the merge base of `ref` and HEAD, by itself. Whatever an earlier run left is gone first, so
    that a run that fails leaves no record to be compared with."""
    if out.exists():
        shutil.rmtree(out)
    _meta(out).unlink(missing_ok=True)
    _beside(out, "executed.json").unlink(missing_ok=True)
    sha = _git("merge-base", ref, "HEAD")
    if not gate.has_harness(sha, ROOT):
        if gate.has_harness(ref, ROOT):
            print(f"{sha[:12]}, where this branch leaves {ref}, has no harness and {ref} has: rebase", file=sys.stderr)
            return 2
        _write_meta(out, sha, harness=False)
        print(f"{sha[:12]} has no tests/behaviour: there is no base to record")
        return 0
    _record_with_its_own(sha, out)
    if load(out).commit != sha:
        raise SystemExit(f"{sha[:12]} recorded itself as {load(out).commit[:12]}")
    print(f"recorded with {sha[:12]} -> {out}")
    return 0


@dataclasses.dataclass(frozen=True)
class Comparison:
    changed: list[str]
    added: list[str]
    removed: list[str]

    @property
    def paths(self) -> list[str]:
        return [*self.changed, *self.added, *self.removed]


def compare(before: dict[str, bytes], after: dict[str, bytes]) -> Comparison:
    return Comparison(
        changed=sorted(path for path in before.keys() & after.keys() if before[path] != after[path]),
        added=sorted(after.keys() - before.keys()),
        removed=sorted(before.keys() - after.keys()),
    )


def verdict(found: Comparison, allowances: list[tuple[str, str]]) -> tuple[list[str], bool]:
    """What to say about each difference and each allowance, and whether the check passes."""
    lines, passed = [], True
    kinds = {
        **dict.fromkeys(found.changed, "changed"),
        **dict.fromkeys(found.added, "new"),
        **dict.fromkeys(found.removed, "no longer recorded"),
    }
    for path in found.paths:
        reasons = [reason for glob, reason in allowances if fnmatch.fnmatchcase(path, glob)]
        if reasons:
            lines.append(f"{path}: {kinds[path]}, allowed by '{reasons[0]}'")
        else:
            lines.append(f"{path}: {kinds[path]}, and nothing says it should")
            passed = False
    for glob, reason in allowances:
        if not any(fnmatch.fnmatchcase(path, glob) for path in found.paths):
            lines.append(f"{glob}: '{reason}' allows a change here, and nothing here changed - drop the trailer")
    return lines, passed


def _diff(path: str, before: bytes | None, after: bytes | None) -> list[str]:
    old = [] if before is None else before.decode("utf-8", errors="replace").splitlines()
    new = [] if after is None else after.decode("utf-8", errors="replace").splitlines()
    return list(difflib.unified_diff(old, new, f"base/{path}", f"head/{path}", lineterm=""))


def _changed_since(commit: str) -> list[str]:
    """What differs from `commit` in the working tree, which is what is recorded - untracked files too."""
    return [
        *_git("diff", "--name-only", "--no-renames", commit).splitlines(),
        *_git("ls-files", "--others", "--exclude-standard").splitlines(),
    ]


def _added_or_removed(commit: str) -> list[str]:
    """What is new in the working tree since `commit`, or gone from it - an untracked file being new."""
    listed = [line.split("\t", 1) for line in _git("diff", "--name-status", "--no-renames", commit).splitlines()]
    new_or_gone = [path for status, path in listed if status in ("A", "D")]
    return new_or_gone + _git("ls-files", "--others", "--exclude-standard").splitlines()


def _usable_base(base_out: pathlib.Path, ref: str) -> Record | None:
    """The base record, where it is the merge base's own: not older, and not left by a run that failed."""
    if not _meta(base_out).exists():
        print(f"no base record at {base_out}; run `make container-snapshot-base` first", file=sys.stderr)
        return None
    recorded = load(base_out)
    merge_base = _git("merge-base", ref, "HEAD")
    if recorded.commit != merge_base:
        print(
            f"the base record is of {recorded.commit[:12]}, and this branch leaves {ref} at {merge_base[:12]}: "
            "record the base again",
            file=sys.stderr,
        )
        return None
    if not recorded.harness and gate.has_harness(ref, ROOT):
        print(f"the base record says {ref} has no harness, and it has one: record the base again", file=sys.stderr)
        return None
    return recorded


def _report(
    recorded: Record, head: Record, allowances: list[tuple[str, str]], notes: list[str], diff_file: pathlib.Path
) -> int:
    found = compare(recorded.files, head.files)
    lines, passed = verdict(found, allowances)
    lines = notes + lines
    diff = [line for path in found.paths for line in _diff(path, recorded.files.get(path), head.files.get(path))]
    diff_file.write_text("".join(line + "\n" for line in diff), encoding="utf-8")
    span = f"{recorded.commit[:12]}..{head.commit[:12]}"
    heading = (
        f"The app answers as it did, over {span}: {len(head.files)} files, all the same."
        if not found.paths
        else f"The app answers differently over {span}: {len(found.changed)} changed, "
        f"{len(found.added)} new, {len(found.removed)} no longer recorded, of {len(head.files)}."
    )
    print(heading)
    for path in found.paths[:DIFFS_PRINTED]:
        print("\n".join(_diff(path, recorded.files.get(path), head.files.get(path))[:60]))
    print("\n".join(f"  {line}" for line in lines))
    if found.paths:
        print(f"\nThe whole diff is in {diff_file}.")
    if not passed:
        print(
            f"A change meant to alter what the app answers says so with `{gate.ALLOW}: <path or glob>` in the trailer "
            "block of one of its commits, and the pull request says why. Read the diff before adding one."
        )
    _summarise(heading, lines, diff)
    return 0 if passed else 1


def _harness_notes(changed: list[str], harness: list[tuple[str, str]]) -> list[str]:
    if not harness:
        return []
    if not gate.touched(changed, gate.HARNESS_FILES):
        return [
            f"{gate.HARNESS}: '{subject}' says the harness changes, and it does not - drop the trailer"
            for _, subject in harness
        ]
    return [f"the harness changes: {why} ('{subject}')" for why, subject in harness]


def _choose(changed: list[str], moved: list[str], executed: dict[str, dict[str, Any]] | None) -> Selection | None:
    """What to record: None for everything, or the parts `changed` can reach - none, for a change to
    nothing a part runs, in which case the route walk is still recorded."""
    if executed is None:
        return None
    selection = select(ROOT, changed, executed, added_or_removed=moved)
    if selection.everything:
        print("Recording everything:\n" + "\n".join(f"  {reason}" for reason in selection.reasons))
        return None
    print(
        f"Recording {len(selection.parts)} of {len(executed) - 1} parts, which run "
        f"{', '.join(selection.reasons) or 'nothing this change touches'}, and the route walk."
    )
    return selection


def _record_selected(head_out: pathlib.Path, selection: Selection | None, executed: dict[str, Any] | None) -> Record:
    """This checkout recorded - the selected parts, or everything - as the record says."""
    _meta(head_out).unlink(missing_ok=True)
    wanted = {"parts": selection.parts, "known": sorted(executed or {})} if selection else None
    run_harness(head_out, selection=wanted)
    _write_meta(head_out, _git("rev-parse", "HEAD"), harness=True)
    head = load(head_out)
    if selection is None:
        return head
    # the match counts of a shard that asked some of its routes count what it asked
    asked = {path: content for path, content in head.files.items() if not path.startswith("normalisation/http-")}
    return Record(head.commit, True, asked)


def check(
    base_out: pathlib.Path, head_out: pathlib.Path, diff_file: pathlib.Path, ref: str, *, full: bool = False
) -> int:
    """Record with this checkout and compare with the base. 1 on a difference nothing allows, or a branch
    the gate refuses; 2 on a base that is not this branch's."""
    recorded = _usable_base(base_out, ref)
    if recorded is None:
        return 2
    diff_file.parent.mkdir(parents=True, exist_ok=True)
    diff_file.write_text("")
    if not recorded.harness:
        record(head_out)
        print(f"{recorded.commit[:12]} has no tests/behaviour, so there is nothing to compare this record with.")
        return 0

    changed = _changed_since(recorded.commit)
    allowances = gate.claims(gate.ALLOW, recorded.commit, ROOT)
    harness = gate.claims(gate.HARNESS, recorded.commit, ROOT)
    refused = gate.refusals(changed, allowances, harness, recorded=recorded.files)
    # a full run goes on past a refusal, and an allowance of everything must not then allow everything
    allowances = [(glob, why) for glob, why in allowances if not gate.allows_everything(glob, recorded.files)]
    if refused and not full:
        heading = "This branch is not compared:"
        print(heading + "\n" + "\n".join(f"  {reason}" for reason in refused))
        _summarise(heading, refused, [])
        return 1
    # a full run compares commits already merged, which may hold a harness change and a change to the app
    # that landed apart: what would refuse one branch is said, and the comparison made all the same
    notes = [f"in these commits: {reason}" for reason in refused] + _harness_notes(changed, harness)

    executed = load_executed(base_out)
    selection = None if full else _choose(changed, _added_or_removed(recorded.commit), executed)
    head = _record_selected(head_out, selection, executed)
    if selection is not None:
        recorded = Record(recorded.commit, True, compared(recorded.files, executed or {}, selection))
    return _report(recorded, head, allowances, notes, diff_file)


def _measure(executed_file: pathlib.Path, count: int, ref: str) -> int:
    commits = []
    for sha in _git("rev-list", "--first-parent", f"-{count}", ref).splitlines():
        listed = [
            line.split("\t", 1) for line in _git("diff", "--name-status", "--no-renames", f"{sha}^", sha).splitlines()
        ]
        moved = [path for status, path in listed if status in ("A", "D")]
        commits.append((sha, _git("log", "-1", "--format=%s", sha), [path for _, path in listed], moved))
    measure(ROOT, json.loads(executed_file.read_text()), commits)
    return 0


def _summarise(heading: str, lines: list[str], diff: list[str]) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as written:
            written.write(gate.summary(heading, lines, diff))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    recorded = commands.add_parser("record", help="record with this checkout, as main's run does")
    recorded.add_argument("--out", type=pathlib.Path, default=STATE / "behaviour-base")
    based = commands.add_parser("base", help="record with the commit this branch would merge into")
    based.add_argument("--ref", default="origin/main", help="the branch this one would merge into")
    based.add_argument("--out", type=pathlib.Path, default=STATE / "behaviour-base")
    checked = commands.add_parser("check", help="record with this checkout and compare")
    checked.add_argument("--ref", default="origin/main", help="the branch this one would merge into")
    checked.add_argument("--base", type=pathlib.Path, default=STATE / "behaviour-base")
    checked.add_argument("--out", type=pathlib.Path, default=STATE / "behaviour-head")
    checked.add_argument("--diff", type=pathlib.Path, default=STATE / "behaviour.diff")
    checked.add_argument("--full", action="store_true", help="record everything, whatever the change reaches")
    measured = commands.add_parser("measure", help="what the selection would record for recent commits")
    measured.add_argument("--map", type=pathlib.Path, default=STATE / "behaviour-base.executed.json")
    measured.add_argument("--commits", type=int, default=40)
    measured.add_argument("--ref", default="origin/main")
    args = parser.parse_args()

    if args.command == "record":
        return record(args.out)
    if args.command == "base":
        return base(args.ref, args.out)
    if args.command == "measure":
        return _measure(args.map, args.commits, args.ref)
    return check(args.base, args.out, args.diff, args.ref, full=args.full)


if __name__ == "__main__":
    raise SystemExit(main())
