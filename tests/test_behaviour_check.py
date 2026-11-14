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

"""The behaviour comparison's own decisions (`scripts/ci/behaviour.py`, `behaviour_gate.py`).

What is recorded is the harness's business. What this pins is what the comparison makes of it: which
differences pass, which commits may say so, which base a record may be compared with, and which
branches are not compared at all. Each is a way a change to the app could pass a check that never looked.
Every repository here is a scratch one; the recording itself is stood in for.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import typing

import pytest

_CI = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "ci"


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, _CI / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("trailers")
gate = _load("behaviour_gate")
_load("behaviour_selection")
behaviour = _load("behaviour")

HARNESS = {"scripts/ci/behaviour.py": "# the script\n", "tests/behaviour/test_x.py": "# the harness\n"}


def git(where: pathlib.Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=where, check=True, capture_output=True, text=True).stdout.strip()


def commit(where: pathlib.Path, message: str, files: dict[str, str | None]) -> str:
    """Write each file (None removes it) and commit them, with `message` whole - its trailers too."""
    for name, content in files.items():
        if content is None:
            git(where, "rm", "-q", "--", name)
            continue
        path = where / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(where, "add", "--", name)
    git(where, "commit", "-q", "--allow-empty", "-m", message)
    return git(where, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    git(tmp_path, "init", "-q", "-b", "main")
    for key, value in (("user.email", "t@example.org"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(tmp_path, "config", key, value)
    monkeypatch.setattr(behaviour, "ROOT", tmp_path)
    return tmp_path


def recording(monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> list[pathlib.Path]:
    """Stand in for the harness: each recording writes `files`. Returns where it was asked to write."""
    asked: list[pathlib.Path] = []

    def run(out: pathlib.Path) -> None:
        asked.append(out)
        for name, content in files.items():
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            (out / name).write_text(content)

    def run_fresh(out: pathlib.Path, selection: object = None) -> None:
        out.mkdir(parents=True, exist_ok=True)
        run(out)

    monkeypatch.setattr(behaviour, "run_harness", run_fresh)
    return asked


def recorded_base(where: pathlib.Path, commit_: str, files: dict[str, str], *, harness: bool = True) -> pathlib.Path:
    """A base record of `commit_`, as `base` leaves one."""
    out = where / "state" / "behaviour-base"
    for name, content in files.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(content)
    behaviour._write_meta(out, commit_, harness=harness)
    return out


def checked(where: pathlib.Path, base_out: pathlib.Path, ref: str = "main") -> int:
    return behaviour.check(base_out, where / "state" / "behaviour-head", where / "state" / "behaviour.diff", ref)


class TestCompare:
    def test_each_kind_of_difference_is_told_apart(self) -> None:
        found = behaviour.compare(
            {"same": b"1", "changed": b"1", "removed": b"1"}, {"same": b"1", "changed": b"2", "added": b"1"}
        )
        assert (found.changed, found.added, found.removed) == (["changed"], ["added"], ["removed"])

    def test_the_same_files_are_no_difference(self) -> None:
        assert behaviour.compare({"a": b"1"}, {"a": b"1"}).paths == []


class TestVerdict:
    found = behaviour.Comparison(changed=["http/a.txt"], added=["scenarios/b.txt"], removed=[])

    def test_a_difference_nothing_allows_fails(self) -> None:
        lines, passed = behaviour.verdict(self.found, [("http/*", "why")])
        assert not passed
        assert "scenarios/b.txt: new, and nothing says it should" in lines

    def test_every_difference_allowed_passes(self) -> None:
        _, passed = behaviour.verdict(self.found, [("http/*", "one"), ("scenarios/b.txt", "two")])
        assert passed

    def test_a_glob_crosses_directories(self) -> None:
        _, passed = behaviour.verdict(behaviour.Comparison(["http/a/b/c.txt"], [], []), [("http/*.txt", "why")])
        assert passed

    def test_an_allowance_nothing_needed_is_reported(self) -> None:
        lines, passed = behaviour.verdict(behaviour.Comparison([], [], []), [("http/*", "old reason")])
        assert passed
        assert lines == ["http/*: 'old reason' allows a change here, and nothing here changed - drop the trailer"]


class TestGlobs:
    @pytest.mark.parametrize("glob", ["*", "**", "*/*", "**/", "?*"])
    def test_a_glob_naming_no_part_of_a_path_is_too_broad(self, glob: str) -> None:
        assert gate.too_broad(glob)

    @pytest.mark.parametrize("glob", ["http/*", "*.txt", "scenarios/01-*"])
    def test_a_glob_naming_something_is_not(self, glob: str) -> None:
        assert not gate.too_broad(glob)

    def test_a_too_broad_allowance_is_refused(self) -> None:
        assert gate.refusals([], [("**", "everything")], []) == [
            "`Behaviour-Allow: **` ('everything') allows every difference; name the paths that change"
        ]

    RECORD: typing.ClassVar[list[str]] = [
        "routes.txt",
        "normalisation/routes.txt",
        "http/index.txt",
        "http/admin/data.txt",
        "scenarios/01-x.txt",
    ]
    """The kinds of path a record holds: every one of them ends in `.txt`."""

    @pytest.mark.parametrize("glob", ["*.txt", "*t", "*.*", "*x*", "*/x.txt", "**/foo", "h*/x.txt", "?ttp/*"])
    def test_a_glob_that_names_no_directory_is_refused(self, glob: str) -> None:
        """A glob is anchored by a first path segment written out: without one it reaches every directory of
        the record, whichever files it happens to hold today."""
        [reason] = gate.refusals([], [(glob, "why")], [], recorded=self.RECORD)
        assert reason == (
            f"`Behaviour-Allow: {glob}` ('why') names no directory, and so can allow a difference anywhere; "
            "name the directory: http/*.txt, not *.txt"
        )

    @pytest.mark.parametrize("glob", ["*.txt", "*/x.txt"])
    def test_a_glob_that_names_no_directory_is_refused_with_no_record_to_read(self, glob: str) -> None:
        assert len(gate.refusals([], [(glob, "why")], [])) == 1

    def test_a_glob_matching_every_recorded_path_is_still_refused(self) -> None:
        """A record of one directory: a glob over it names the directory, and still allows everything."""
        [reason] = gate.refusals([], [("http/*", "why")], [], recorded=["http/a.txt", "http/b/c.txt"])
        assert reason == (
            "`Behaviour-Allow: http/*` ('why') matches every file the base record holds, and so allows every "
            "difference; name the paths that change"
        )

    @pytest.mark.parametrize(
        "glob",
        ["http/*.txt", "http/admin/*", "scenarios/*", "normalisation/*", "routes.txt", "http/index.txt"],
    )
    def test_a_glob_naming_part_of_the_record_is_allowed(self, glob: str) -> None:
        assert gate.refusals([], [(glob, "why")], [], recorded=self.RECORD) == []


class TestHarnessChanges:
    def test_a_harness_change_says_so(self) -> None:
        [reason] = gate.refusals(["tests/behaviour/routes.py"], [], [])
        assert "Behaviour-Harness" in reason

    def test_a_harness_change_that_says_so_is_compared(self) -> None:
        assert gate.refusals(["tests/behaviour/routes.py", "docs/x.md"], [], [("why", "subject")]) == []

    def test_a_harness_change_with_an_app_change_is_refused_even_when_it_says_so(self) -> None:
        [reason] = gate.refusals(["tests/data/x.txt", "app/routes.py"], [], [("why", "subject")])
        assert "split it" in reason

    @pytest.mark.parametrize(
        "path",
        ["tests/conftest.py", "tests/data/x.txt", "scripts/ci/behaviour_gate.py", "scripts/ci/trailers.py"],
    )
    def test_each_part_of_the_harness_counts(self, path: str) -> None:
        assert gate.touched([path], gate.HARNESS_FILES) == [path]

    def test_an_app_change_alone_is_compared(self) -> None:
        assert gate.refusals(["app/routes.py"], [], []) == []


class TestHasHarness:
    def test_both_marks_make_a_harness(self, repo: pathlib.Path) -> None:
        sha = commit(repo, "harness", HARNESS)
        assert gate.has_harness(sha, repo)

    @pytest.mark.parametrize("missing", list(HARNESS))
    def test_one_mark_is_not_enough(self, repo: pathlib.Path, missing: str) -> None:
        sha = commit(repo, "half", {name: content for name, content in HARNESS.items() if name != missing})
        assert not gate.has_harness(sha, repo)


class TestClaims:
    """Only the branch's own commits argue for it."""

    def branch(self, repo: pathlib.Path) -> str:
        base = commit(repo, "base", {"a": "1"})
        git(repo, "checkout", "-q", "-b", "side")
        commit(repo, "side\n\nBehaviour-Allow: from-a-side-branch", {"side": "1"})
        git(repo, "checkout", "-q", "-b", "branch", base)
        commit(repo, "own\n\nBehaviour-Allow: own", {"b": "1"})
        git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge side\n\nBehaviour-Allow: on-the-merge")
        return base

    def test_a_merge_and_what_it_brings_do_not_count(self, repo: pathlib.Path) -> None:
        base = self.branch(repo)
        assert gate.claims("Behaviour-Allow", base, repo) == [("own", "own")]

    def test_the_merge_ci_makes_reads_the_branch_side(self, repo: pathlib.Path) -> None:
        base = self.branch(repo)
        git(repo, "checkout", "-q", "--detach", base)
        git(repo, "merge", "-q", "--no-ff", "branch", "-m", "Merge into base\n\nBehaviour-Allow: ci-merge")
        assert gate.claims("Behaviour-Allow", base, repo) == [("own", "own")]

    def test_a_mention_is_not_a_trailer(self, repo: pathlib.Path) -> None:
        base = commit(repo, "base", {})
        commit(repo, "say Behaviour-Allow: x in prose\n\nNot a trailer: Behaviour-Allow: y, mid-line.", {"c": "1"})
        assert gate.claims("Behaviour-Allow", base, repo) == []


class TestBase:
    def test_a_base_that_fails_leaves_no_record(self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sha = commit(repo, "harness", HARNESS)
        out = recorded_base(repo, sha, {"old.txt": "stale"}, harness=False)

        def fails(sha: str, out: pathlib.Path) -> None:
            raise SystemExit("the recording broke")

        monkeypatch.setattr(behaviour, "_record_with_its_own", fails)
        with pytest.raises(SystemExit):
            behaviour.base("main", out)
        assert not behaviour._meta(out).exists()
        assert not out.exists()

    def test_no_harness_anywhere_is_the_bootstrap(self, repo: pathlib.Path) -> None:
        sha = commit(repo, "before", {"a": "1"})
        out = repo / "state" / "behaviour-base"
        assert behaviour.base("main", out) == 0
        assert behaviour.load(out) == behaviour.Record(sha, False, {})

    def test_a_branch_from_before_the_harness_is_not_a_bootstrap(self, repo: pathlib.Path) -> None:
        commit(repo, "before", {"a": "1"})
        git(repo, "checkout", "-q", "-b", "old")
        commit(repo, "a change from before", {"app/x.py": "1"})
        git(repo, "checkout", "-q", "main")
        commit(repo, "harness", HARNESS)
        git(repo, "checkout", "-q", "old")
        out = repo / "state" / "behaviour-base"
        assert behaviour.base("main", out) == 2
        assert not behaviour._meta(out).exists()

    def test_the_harness_is_read_from_the_base_not_the_branch(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rename: a branch that moves tests/behaviour away still has a base that records."""
        sha = commit(repo, "harness", HARNESS)
        git(repo, "checkout", "-q", "-b", "rename")
        git(repo, "mv", "tests/behaviour", "tests/elsewhere")
        commit(repo, "move the harness\n\nBehaviour-Allow: http/*", {"app/x.py": "changed"})
        recorded: list[str] = []

        def records(sha: str, out: pathlib.Path) -> None:
            recorded.append(sha)
            behaviour._write_meta(out, sha, harness=True)

        monkeypatch.setattr(behaviour, "_record_with_its_own", records)
        assert behaviour.base("main", repo / "state" / "behaviour-base") == 0
        assert recorded == [sha]


class TestCheck:
    def test_a_record_of_an_older_commit_is_refused(self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        older = commit(repo, "harness", HARNESS)
        commit(repo, "main moves on", {"docs/a.md": "1"})
        asked = recording(monkeypatch, {"a.txt": "1"})
        assert checked(repo, recorded_base(repo, older, {"a.txt": "1"})) == 2
        assert asked == []

    def test_a_stale_bootstrap_is_refused(self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sha = commit(repo, "harness", HARNESS)
        asked = recording(monkeypatch, {"a.txt": "1"})
        assert checked(repo, recorded_base(repo, sha, {}, harness=False)) == 2
        assert asked == []

    def test_the_rename_is_refused(self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Move the harness and change the app in one branch, on a base the cache had: not compared."""
        sha = commit(repo, "harness", {**HARNESS, "app/x.py": "1"})
        git(repo, "checkout", "-q", "-b", "rename")
        git(repo, "mv", "tests/behaviour", "tests/elsewhere")
        commit(repo, "move the harness\n\nBehaviour-Harness: moved", {"app/x.py": "changed"})
        asked = recording(monkeypatch, {"a.txt": "different"})
        assert checked(repo, recorded_base(repo, sha, {"a.txt": "1"})) == 1
        assert asked == []

    def test_a_full_run_says_what_would_refuse_a_branch_and_compares(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A night's commits on main may hold a harness change and a change to the app, merged apart."""
        sha = commit(repo, "harness", {**HARNESS, "app/x.py": "1"})
        commit(repo, "more harness\n\nBehaviour-Harness: records more", {"tests/behaviour/test_y.py": "#"})
        commit(repo, "a change to the app", {"app/x.py": "2"})
        asked = recording(monkeypatch, {"a.txt": "1"})
        out = recorded_base(repo, sha, {"a.txt": "1"})
        head, diff = repo / "state" / "behaviour-head", repo / "state" / "behaviour.diff"
        assert behaviour.check(out, head, diff, sha, full=True) == 0
        assert len(asked) == 1
        assert "in these commits: this branch changes what the record is made with" in capsys.readouterr().out
        assert behaviour.check(out, head, diff, sha) == 1

    @staticmethod
    def mapped(out: pathlib.Path) -> None:
        """A module map beside the base record, as a recording writes one: one route ran x, one ran y."""
        executed = {
            "startup": {"modules": [], "files": []},
            "route /x": {"modules": ["app/x.py"], "files": ["http/x.txt"]},
            "route /y": {"modules": ["app/y.py"], "files": ["http/y.txt"]},
        }
        behaviour._beside(out, "executed.json").write_text(json.dumps(executed))

    @staticmethod
    def told(monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> list[object]:
        """Stand in for the harness, keeping what each recording was told to record."""
        selections: list[object] = []

        def run(out: pathlib.Path, selection: object = None) -> None:
            selections.append(selection)
            out.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (out / name).parent.mkdir(parents=True, exist_ok=True)
                (out / name).write_text(content)

        monkeypatch.setattr(behaviour, "run_harness", run)
        # what imports what is the selection's own tests' to ask (test_behaviour_selection.py): none, here
        monkeypatch.setattr(
            sys.modules["behaviour_selection"], "_importers", lambda root, modules, package: {m: set() for m in modules}
        )
        return selections

    def test_a_selected_recording_is_compared_with_what_it_recorded(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = commit(repo, "harness", {**HARNESS, "app/x.py": "1", "app/y.py": "1"})
        git(repo, "checkout", "-q", "-b", "branch")
        commit(repo, "change x", {"app/x.py": "2"})
        out = recorded_base(repo, sha, {"http/x.txt": "x", "http/y.txt": "y", "routes.txt": "r"})
        self.mapped(out)
        selections = self.told(monkeypatch, {"http/x.txt": "x", "routes.txt": "r"})
        assert checked(repo, out) == 0
        assert selections == [{"parts": ["route /x"], "known": ["route /x", "route /y", "startup"]}]

    def test_a_change_no_part_runs_still_records_the_walk(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = commit(repo, "harness", {**HARNESS, "app/x.py": "1"})
        git(repo, "checkout", "-q", "-b", "branch")
        commit(repo, "words", {"docs/x.md": "words"})
        out = recorded_base(repo, sha, {"http/x.txt": "x", "routes.txt": "r"})
        self.mapped(out)
        selections = self.told(monkeypatch, {"routes.txt": "a route more"})
        assert checked(repo, out) == 1
        assert selections == [{"parts": [], "known": ["route /x", "route /y", "startup"]}]

    def test_a_map_an_earlier_recording_left_is_not_read(self, repo: pathlib.Path) -> None:
        out = repo / "state" / "behaviour-head"
        out.mkdir(parents=True)
        behaviour._beside(out, "executed.json").write_text("{}")
        with pytest.raises(SystemExit):  # nothing of the harness here to run: it is the clearing that is asked
            behaviour.run_harness(out)
        assert not behaviour._beside(out, "executed.json").exists()

    def test_moving_the_harness_away_is_a_harness_change(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """git reads a move as a rename and names only where it went; where it came from counts too."""
        sha = commit(repo, "harness", HARNESS)
        git(repo, "checkout", "-q", "-b", "rename")
        git(repo, "mv", "tests/behaviour", "tests/elsewhere")
        commit(repo, "move the harness", {})
        asked = recording(monkeypatch, {"a.txt": "1"})
        assert checked(repo, recorded_base(repo, sha, {"a.txt": "1"})) == 1
        assert asked == []

    def test_a_harness_change_without_its_trailer_is_refused(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = commit(repo, "harness", HARNESS)
        git(repo, "checkout", "-q", "-b", "branch")
        commit(repo, "a new part", {"tests/behaviour/test_y.py": "#"})
        asked = recording(monkeypatch, {"a.txt": "1"})
        assert checked(repo, recorded_base(repo, sha, {"a.txt": "1"})) == 1
        assert asked == []

    def test_a_harness_change_with_its_trailer_is_compared(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sha = commit(repo, "harness", HARNESS)
        git(repo, "checkout", "-q", "-b", "branch")
        commit(repo, "a new part\n\nBehaviour-Harness: records more", {"tests/behaviour/test_y.py": "#"})
        recording(monkeypatch, {"a.txt": "1"})
        assert checked(repo, recorded_base(repo, sha, {"a.txt": "1"})) == 0
        assert "the harness changes: records more ('a new part')" in capsys.readouterr().out

    @pytest.mark.parametrize(("message", "expected"), [("change", 1), ("change\n\nBehaviour-Allow: a.txt", 0)])
    def test_a_difference_passes_only_where_allowed(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, message: str, expected: int
    ) -> None:
        sha = commit(repo, "harness", HARNESS)
        git(repo, "checkout", "-q", "-b", "branch")
        commit(repo, message, {"app/x.py": "2"})
        recording(monkeypatch, {"a.txt": "2"})
        assert checked(repo, recorded_base(repo, sha, {"a.txt": "1"})) == expected
        assert "+2" in (repo / "state" / "behaviour.diff").read_text()

    @pytest.mark.parametrize(("allowed", "expected"), [("*.txt", 1), ("http/*.txt", 0)])
    def test_an_allowance_matching_the_whole_record_is_refused(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, allowed: str, expected: int
    ) -> None:
        sha = commit(repo, "harness", HARNESS)
        git(repo, "checkout", "-q", "-b", "branch")
        commit(repo, f"change\n\nBehaviour-Allow: {allowed}", {"app/x.py": "2"})
        asked = recording(monkeypatch, {"http/a.txt": "2", "routes.txt": "r"})
        assert checked(repo, recorded_base(repo, sha, {"http/a.txt": "1", "routes.txt": "r"})) == expected
        assert len(asked) == (0 if expected else 1)

    def test_the_bootstrap_records_and_passes(self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sha = commit(repo, "before", {"a": "1"})
        git(repo, "checkout", "-q", "-b", "bring")
        commit(repo, "the harness", HARNESS)
        asked = recording(monkeypatch, {"a.txt": "1"})
        assert checked(repo, recorded_base(repo, sha, {}, harness=False)) == 0
        assert len(asked) == 1


class TestSummary:
    def test_a_summary_stops_short_of_the_limit_and_says_where_the_rest_is(self) -> None:
        diff = [f"+{'x' * 99}" for _ in range(20_000)]
        text = gate.summary("heading", ["a line"], diff, limit=64 * 1024)
        assert len(text.encode()) <= 64 * 1024
        assert text.rstrip().endswith("more lines in the `behaviour` artifact.")
        assert text.count("```") == 2

    def test_a_small_summary_is_whole(self) -> None:
        text = gate.summary("heading", ["a line"], ["+x"])
        assert text == "### What the app answers\n\nheading\n\n- a line\n\n```diff\n+x\n\n```\n"

    def test_wide_lines_count_by_bytes(self) -> None:
        diff = ["+" + "é" * 1000 for _ in range(200)]
        assert len(gate.summary("h", [], diff, limit=50_000).encode()) <= 50_000
