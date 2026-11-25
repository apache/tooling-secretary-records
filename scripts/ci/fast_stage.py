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
"""The fast local stage: the tests most likely to catch a mistake, run first and stopped at the first
failure (`make test-fast`). It is not the gate - `make container-verify` is, and CI runs everything.

In order, with `--failed-first --maxfail=1`:

1. the files pytest's own cache says failed last;
2. tier 0: cheap tests that read a whole list - every module, table, route, input or snapshot - and so
   fail wherever one is added, which no path can say. Found by what such a test cannot avoid writing
   (`TIER0_PATTERN`), not by a list, so a new one joins by itself;
3. the path rule (`RULES`): by a changed file's name and place, then the tests that name the file, then
   the tests that import it.

Changes are measured from the last green full `make test` on a clean tree here (fast_stage_record.py), or
the merge base with origin/main, plus what is uncommitted: a base that moved under the branch is an
untested change.

It never runs the whole suite. A change that reaches every test (`FULL_WHEN`, what tests/conftest.py
imports), or a selection past `SHARE_LIMIT`, still runs what it selected, up to the limit, and then answers
`NEEDS_THE_SUITE`, never a pass: the whole suite wants what `make container-test` sizes. Nothing is retried.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import pathlib
import re
import subprocess
import sys
from typing import Final

import fast_stage_rules
from fast_stage_record import DURATIONS, GREEN, STATE, begin, git, passed, read_json, record

ROOT: Final = pathlib.Path(__file__).resolve().parents[2]
LASTFAILED: Final = ".pytest_cache/v/cache/lastfailed"
CONFTEST: Final = "tests/conftest.py"
BEHAVIOUR: Final = "tests/behaviour/"

NEEDS_THE_SUITE: Final = 10
"""The exit status for a change the stage cannot answer: no pytest status (0 to 5), so no test failure."""
NOTHING_SELECTED: Final = 5  # pytest's own status for no tests: an empty selection is refused, not run whole

SHARE_LIMIT: Final = 0.40
"""Past this share of the suite's test time the selection saves too little to be worth its blind spots."""

TIER0_PATTERN: Final = re.compile(
    r"snapshot|url_map|glob\(|iterdir\(|metadata\.tables|sorted_tables|grimp|importlinter"
)
"""What a test that checks a whole list reads: a snapshot, every route, a directory, every table, the imports."""

TIER0_MAX_SECONDS: Final = 15.0
"""Tier 0 is for the cheap ones; a snapshot test is kept whatever it costs, since nothing else reaches it."""

SECONDS_PER_TEST: Final = 0.7
"""What a test is taken to cost before any run has recorded one: about the suite's mean."""

NAMED_BY_DIRECTORY: Final = frozenset({"__init__", "routes", "models", "service", "tables"})

FULL_WHEN: Final = {
    "pyproject.toml": "the dependencies and pytest's configuration",
    "uv.lock": "the dependencies",
    "requirements.txt": "the dependencies",
    ".python-version": "the interpreter",
    "tests/__init__.py": "the test package",
    CONFTEST: "the fixtures every test stands on",
    "tests/data/**": "the fixture data",
    "tests/behaviour/**": "the behaviour harness",
    "app/__init__.py": "startup",
}
"""What reaches every test in any repository, as do the modules tests/conftest.py imports
(`_conftest_imports`). A repository adds its own in `scripts/local/fast_stage.toml` (`fast_stage_rules`)."""

NOT_THE_GATE: Final = (
    "This was the fast stage, not the gate: run `make container-verify` before you push. "
    "CI runs the whole suite on every push."
)
NEEDS_THE_SUITE_SAYS: Final = (
    "Run the whole suite with `make container-test` (`make test` in the container); when it passes on a"
    " clean tree it records a new base, and the next fast run is narrow again. Until then"
    " `FAST_BASE=HEAD` measures only what is uncommitted."
)


@dataclasses.dataclass(frozen=True)
class Base:
    sha: str
    why: str


@dataclasses.dataclass
class Plan:
    reasons: list[str]
    """Why the change needs the whole suite, which the stage does not run; empty when it does not."""
    rules: dict[str, list[str]]
    """Each selected test file, in the order it runs, and the rules that chose it."""
    seconds: dict[str, float]
    """What each test file of the suite is expected to cost; `estimated` of them have no recorded duration."""
    estimated: int

    @property
    def selected_seconds(self) -> float:
        return sum(self.seconds.get(path, 0.0) for path in self.rules)

    @property
    def share(self) -> float:
        total = sum(self.seconds.values())
        return self.selected_seconds / total if total else 1.0


def _paths(root: pathlib.Path, *arguments: str) -> set[str]:
    finished = git(root, *arguments, "-z")
    if finished.returncode != 0:
        raise SystemExit(f"git {' '.join(arguments)} failed: {finished.stderr.strip()}")
    return {field for field in finished.stdout.split("\0") if field}


def find_base(root: pathlib.Path, given: str | None = None) -> Base:
    """Where the change is measured from: where `given` and HEAD meet, the last green full run here, or
    the merge base with origin/main."""
    if given:
        finished = git(root, "merge-base", given, "HEAD")
        if finished.returncode != 0:
            raise SystemExit(f"{given} names no commit here that shares history with HEAD")
        return Base(finished.stdout.strip(), f"the merge base of {given}, as given, and HEAD")
    green = read_json(root / GREEN)
    if isinstance(green, dict) and git(root, "cat-file", "-e", f"{green.get('head')}^{{commit}}").returncode == 0:
        return Base(str(green["head"]), f"the last green full `make test` in this checkout ({green.get('when')})")
    finished = git(root, "merge-base", "origin/main", "HEAD")
    if finished.returncode != 0:
        raise SystemExit("no green full run is recorded here, and there is no merge base with origin/main")
    return Base(finished.stdout.strip(), "the merge base with origin/main: no green full pytest run is recorded here")


def changed_since(root: pathlib.Path, base: str) -> tuple[set[str], set[str]]:
    """What the commits since `base` changed, and what is uncommitted - both sides of a rename."""
    committed = _paths(root, "diff", "--name-only", "--no-renames", base, "HEAD")
    uncommitted = _paths(root, "diff", "--name-only", "--no-renames", "HEAD")
    uncommitted |= _paths(root, "ls-files", "--others", "--exclude-standard")
    return committed, uncommitted


def _conftest_imports(root: pathlib.Path) -> set[str]:
    """The files of the modules tests/conftest.py imports from app/: every test stands on them."""
    conftest = root / CONFTEST
    text = conftest.read_text(errors="replace") if conftest.is_file() else ""
    found = set()
    for module, names in re.findall(r"^\s*from (app(?:\.\w+)*) import ([\w ,]+)", text, re.MULTILINE):
        for name in (part.split(" as ")[0].strip() for part in names.split(",")):
            found.add(f"{module}.{name}")
            found.add(module)
    candidates = {f"{name.replace('.', '/')}{suffix}" for name in found for suffix in (".py", "/__init__.py")}
    return {path for path in candidates if (root / path).is_file()}


def full_run_reasons(root: pathlib.Path, changed: set[str]) -> list[str]:
    """Why a change needs the whole suite: it reaches every test."""
    imported = _conftest_imports(root)
    full_when = fast_stage_rules.load_rules(root, FULL_WHEN).full_when
    reasons = []
    for path in sorted(changed):
        if why := next((why for glob, why in full_when.items() if fnmatch.fnmatchcase(path, glob)), None):
            reasons.append(f"{path}: {why}")
        elif path in imported:
            reasons.append(f"{path}: imported by {CONFTEST}, which every test stands on")
    return reasons


def suite(root: pathlib.Path) -> dict[str, str]:
    """Each test file, and what it says - read once, since every rule below reads it. Not the behaviour
    record's: it runs only under scripts/ci/behaviour.py, and its conftest ignores it in a plain run but not
    when it is named on the command line, where it errors."""
    paths = sorted(path.relative_to(root).as_posix() for path in (root / "tests").rglob("test_*.py"))
    return {path: (root / path).read_text(errors="replace") for path in paths if not path.startswith(BEHAVIOUR)}


def _stem(name: str) -> str:
    return name.split(".", 1)[0].lstrip("_").replace("-", "_")


def _named_for(stem: str, tests: dict[str, str]) -> set[str]:
    """The test files named for `stem`: `test_<stem>*`, or `_<stem>` anywhere in the name."""
    if not stem:
        return set()
    return {
        path for path in tests if (name := path.rsplit("/", 1)[-1]).startswith(f"test_{stem}") or f"_{stem}" in name
    }


def _mentioning(pattern: str, tests: dict[str, str]) -> set[str]:
    compiled = re.compile(pattern, re.DOTALL)
    return {path for path, text in tests.items() if compiled.search(text)}


def _reexported(root: pathlib.Path, path: str) -> list[str]:
    """The names the package of `path` re-exports from it: `from .y import A` in app/x/__init__.py."""
    package = root / path.rsplit("/", 1)[0] / "__init__.py"
    text = package.read_text(errors="replace") if package.is_file() else ""
    module = rf"(?:\.{re.escape(path.rsplit('/', 1)[-1].removesuffix('.py'))}|{re.escape(path[:-3].replace('/', '.'))})"
    imported = re.findall(rf"^\s*from {module} import (?:\(([^)]*)\)|([^\n]*))", text, re.MULTILINE)
    names = ",".join(listed or line for listed, line in imported).split(",")
    return [part.split(" as ")[-1].strip() for part in names if part.strip()]


def _importers(root: pathlib.Path, path: str, tests: dict[str, str]) -> set[str]:
    """The tests that import the module at `path`: `app.x.y`, or from `app.x` either `y` or a name the
    package re-exports from it."""
    dotted = path.removesuffix(".py").replace("/", ".")
    package, _, name = dotted.rpartition(".")
    names = "|".join(re.escape(each) for each in [name, *_reexported(root, path)])
    return _mentioning(
        rf"\b{re.escape(dotted)}\b|from {re.escape(package)} import (?:\([^)]*|[^\n]*)\b(?:{names})\b", tests
    )


def _naming(path: str, tests: dict[str, str]) -> set[str]:
    """The tests that name the file; for a frontend file also those that name its directory as a path
    segment, as a test reading every image does."""
    parts = path.split("/")
    pattern = re.escape(parts[-1])
    if parts[0] == "assets" and len(parts) > 2:
        pattern += rf"|[\"']{re.escape(parts[-2])}[\"']"
    return _mentioning(pattern, tests)


def _module_rule(path: str, tests: dict[str, str], broad_packages: frozenset[str]) -> set[str]:
    """A file under app/: the tests named for it and, outside the repository's broad packages
    (`scripts/local/fast_stage.toml`), those of its package. A module is named by its directory when its own
    name says only its role."""
    parts = path.split("/")
    named_by_directory = parts[-1].split(".", 1)[0] in NAMED_BY_DIRECTORY and len(parts) > 2
    stems = {_stem(parts[-2] if named_by_directory else parts[-1])}
    if not path.endswith(".py") and len(parts) > 2:
        stems.add(_stem(parts[-2]))
    selected = set().union(*(_named_for(stem, tests) for stem in stems))
    if len(parts) > 2 and parts[1] not in broad_packages:
        package = parts[1]
        selected |= {test for test in tests if test.startswith(f"tests/{package}/")}
        selected |= {test for test in tests if test.rsplit("/", 1)[-1].startswith(f"test_{package}")}
    return selected


def _test_rule(path: str, tests: dict[str, str]) -> set[str]:
    """A file under tests/: a test selects itself and the tests that import from it; a helper its
    directory's tests, or at the top level the tests that import it."""
    directory, name = path.rsplit("/", 1)
    importers = _mentioning(rf"\b{re.escape(path.removesuffix('.py').replace('/', '.'))}\b", tests)
    if fnmatch.fnmatchcase(name, "test_*.py"):
        return ({path} & set(tests)) | importers
    return {test for test in tests if test.startswith(f"{directory}/")} if directory != "tests" else importers


def path_rule(root: pathlib.Path, path: str, tests: dict[str, str]) -> set[str]:
    """The test files a changed path selects by its name and place."""
    if path.startswith("tests/"):
        return _test_rule(path, tests)
    if path.startswith("app/"):
        return _module_rule(path, tests, fast_stage_rules.load_rules(root, FULL_WHEN).broad_packages)
    if path.startswith("scripts/") and path.endswith(".py"):
        return _named_for(_stem(path.rsplit("/", 1)[-1]), tests)
    return set()


def naming_rule(_root: pathlib.Path, path: str, tests: dict[str, str]) -> set[str]:
    """The tests that name a changed file outside tests/."""
    return set() if path.startswith("tests/") or path.endswith("/__init__.py") else _naming(path, tests)


def import_rule(root: pathlib.Path, path: str, tests: dict[str, str]) -> set[str]:
    """The tests that import a changed module of app/ - not a package, whose importers are everybody."""
    is_module = path.startswith("app/") and path.endswith(".py") and not path.endswith("/__init__.py")
    return _importers(root, path, tests) if is_module else set()


RULES: Final = (("from", path_rule), ("names", naming_rule), ("imports", import_rule))
"""The path rule's three readings, in the order they are trusted: the limit drops the last first."""


def tier0(tests: dict[str, str], seconds: dict[str, float]) -> list[str]:
    return [
        path
        for path, text in tests.items()
        if TIER0_PATTERN.search(text)
        and (seconds.get(path, 0.0) <= TIER0_MAX_SECONDS or "snapshot" in path.rsplit("/", 1)[-1])
    ]


def failed_before(root: pathlib.Path) -> set[str]:
    """The files pytest's cache says failed last: `--failed-first` orders them first only if they run."""
    lastfailed = read_json(root / LASTFAILED)
    return {nodeid.split("::", 1)[0] for nodeid in lastfailed} if isinstance(lastfailed, dict) else set()


def expected_seconds(tests: dict[str, str], durations: dict[str, float]) -> dict[str, float]:
    """What each file costs: as last recorded, or its test count at the recorded mean where it has none."""
    defined = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)
    counts = {path: max(1, len(defined.findall(text))) for path, text in tests.items()}
    known = {path: float(durations[path]) for path in tests if path in durations}
    known_tests = sum(counts[path] for path in known)
    per_test = sum(known.values()) / known_tests if known_tests else SECONDS_PER_TEST
    return {path: known.get(path, counts[path] * per_test) for path in tests}


def _within(plan: Plan, limit: float) -> None:
    """Drop the last-chosen files until the selection is within `limit`, and say so."""
    share = plan.share
    while plan.rules and plan.share > limit:
        plan.rules.popitem()
    if share > limit:
        plan.reasons.append(f"the selection is {share:.0%} of the suite, past {limit:.0%}: it runs {plan.share:.0%}")


def make_plan(
    root: pathlib.Path, changed: set[str], durations: dict[str, float], failed: set[str], limit: float = SHARE_LIMIT
) -> Plan:
    tests = suite(root)
    seconds = expected_seconds(tests, durations)
    rules: dict[str, list[str]] = {}
    for path in sorted(failed & set(tests)):
        rules.setdefault(path, []).append("failed last")
    for path in tier0(tests, seconds):
        rules.setdefault(path, []).append("tier 0")
    for label, rule in RULES:
        for changed_path in sorted(changed):
            for path in sorted(rule(root, changed_path, tests)):
                rules.setdefault(path, []).append("changed" if path == changed_path else f"{label} {changed_path}")
    plan = Plan(full_run_reasons(root, changed), rules, seconds, sum(1 for path in tests if path not in durations))
    _within(plan, limit)
    return plan


def vitest_command(root: pathlib.Path, changed: set[str]) -> list[str] | None:
    """`vitest related` for the changed frontend sources; the whole of vitest for its configuration, or for a
    source deleted or renamed, whose tests `related` can no longer find."""
    assets = sorted(path.removeprefix("assets/") for path in changed if path.startswith("assets/"))
    configuration = ("package.json", "yarn.lock", "*.config.*", "vitest.*", "tsconfig*.json")
    if any("/" not in path and fnmatch.fnmatchcase(path, glob) for path in assets for glob in configuration):
        return ["yarn", "test"]
    sources = [path for path in assets if path.startswith("src/") and path.endswith((".ts", ".js", ".svelte"))]
    if any(not (root / "assets" / path).is_file() for path in sources):
        return ["yarn", "test"]
    return ["yarn", "vitest", "related", "--run", *sources] if sources else None


def describe(base: Base, committed: set[str], uncommitted: set[str], plan: Plan) -> str:
    changed = committed | uncommitted
    lines = [f"Base: {base.sha[:12]}, {base.why}"]
    lines.append(f"Changed: {len(changed)} files, {len(uncommitted)} of them uncommitted")
    lines += [f"  {path}{'  (uncommitted)' if path in uncommitted else ''}" for path in sorted(changed)]
    if plan.reasons:
        lines.append("This change needs the whole suite, which the fast stage does not run, because:")
        lines += [f"  {reason}" for reason in plan.reasons]
    lines.append(
        f"Selected {len(plan.rules)} of {len(plan.seconds)} test files, about {plan.selected_seconds:.0f} s"
        f" of the suite's {sum(plan.seconds.values()):.0f} s ({plan.share:.0%}), in this order:"
    )
    lines += [f"  {path}  <- {'; '.join(rules)}" for path, rules in plan.rules.items()]
    if plan.estimated:
        lines.append(f"({plan.estimated} test files have no recorded duration: estimated from their test count.)")
    return "\n".join(lines)


def _verdict(status: int, plan: Plan) -> tuple[int, str]:
    if status != 0:
        return status, "Fast stage FAILED."
    if plan.reasons:
        return (
            NEEDS_THE_SUITE,
            f"Fast stage ran its selection; this change needs the whole suite. {NEEDS_THE_SUITE_SAYS}",
        )
    return 0, "Fast stage passed."


def run(root: pathlib.Path, given_base: str | None, pytest_arguments: list[str]) -> int:
    try:
        fast_stage_rules.load_rules(root, FULL_WHEN)
    except fast_stage_rules.LocalError as error:
        raise SystemExit(str(error)) from error
    base = find_base(root, given_base)
    committed, uncommitted = changed_since(root, base.sha)
    changed = committed | uncommitted
    known = read_json(root / DURATIONS)
    plan = make_plan(root, changed, known if isinstance(known, dict) else {}, failed_before(root))
    print(describe(base, committed, uncommitted, plan), flush=True)
    if not plan.rules:
        status = NEEDS_THE_SUITE if plan.reasons else NOTHING_SELECTED
        print(f"\nThe fast stage selected no test file, and runs none. {NEEDS_THE_SUITE_SAYS} {NOT_THE_GATE}")
        return status
    junit = root / STATE / "junit-fast.xml"
    pytest = [str(pathlib.Path(sys.executable).with_name("pytest")), "--failed-first", "--maxfail=1"]
    status = subprocess.run([*pytest, f"--junitxml={junit}", *pytest_arguments, *plan.rules], cwd=root).returncode
    record(root, junit, status, full=False)
    vitest = vitest_command(root, changed)
    if status == 0 and vitest:
        print(f"$ {' '.join(vitest)}", flush=True)
        status = subprocess.run(vitest, cwd=root / "assets").returncode
    status, said = _verdict(status, plan)
    print(f"\n{said} {NOT_THE_GATE}")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("run", help="the fast stage; arguments after -- go to pytest")
    stage.add_argument("--base", help="measure the change from where this commit and HEAD meet instead")
    stage.add_argument("pytest_arguments", nargs="*")
    commands.add_parser("begin", help="note HEAD and the tree before a full run of a half").add_argument("half")
    done = commands.add_parser("record", help="keep what a full run's junit report says")
    done.add_argument("--junit", type=pathlib.Path, required=True)
    done.add_argument("--status", type=int, required=True)
    commands.add_parser("passed", help="a half passed; both on one clean commit make it green").add_argument("half")
    arguments = parser.parse_args()
    if arguments.command == "run":
        return run(ROOT, arguments.base, arguments.pytest_arguments)
    if arguments.command == "record":
        record(ROOT, arguments.junit, arguments.status, full=True)
    else:
        (begin if arguments.command == "begin" else passed)(ROOT, arguments.half)
    return 0


if __name__ == "__main__":
    sys.exit(main())
