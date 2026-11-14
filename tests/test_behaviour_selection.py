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

"""What a change can reach, and so what a pull request records (`scripts/ci/behaviour_selection.py`,
`tests/behaviour/executed.py`).

A selection that records too little passes a change it never looked at, which is the failure that
matters; one that records too much only costs time. So each way the map can fail to see a change is
pinned to "everything", against a hand-written map and a small package of its own, whose imports grimp
reads as it reads the app's.
"""

import fnmatch
import importlib.util
import itertools
import json
import pathlib
import sys
import typing

import pytest

_CI = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "ci"
spec = importlib.util.spec_from_file_location("behaviour_selection", _CI / "behaviour_selection.py")
assert spec and spec.loader
selection = importlib.util.module_from_spec(spec)
sys.modules["behaviour_selection"] = selection
spec.loader.exec_module(selection)

from tests.behaviour import executed  # noqa: E402 - after the selection, which it does not need

EXAMPLES = {
    "app/**/templates/**": "app/client/templates/page.html",
    "app/**/static/**": "app/client/static/assets/x.js",
    "app/*/migrations/**": "app/db/migrations/versions/0042_x.py",
    "app/**/alembic.ini": "app/db/alembic.ini",
    "app/__init__.py": "app/__init__.py",
    "app/**/__init__.py": "app/admin/__init__.py",
    "tests/data/**": "tests/data/submissions/icla.pdf",
    "tests/conftest.py": "tests/conftest.py",
    "tests/behaviour/**": "tests/behaviour/world.py",
    "scripts/ci/behaviour.py": "scripts/ci/behaviour.py",
    "scripts/ci/behaviour_selection.py": "scripts/ci/behaviour_selection.py",
    "scripts/ci/behaviour_gate.py": "scripts/ci/behaviour_gate.py",
    "scripts/ci/trailers.py": "scripts/ci/trailers.py",
    "config.yaml.example": "config.yaml.example",
    "uv.lock": "uv.lock",
    "pyproject.toml": "pyproject.toml",
    "assets/yarn.lock": "assets/yarn.lock",
    "assets/package.json": "assets/package.json",
    "Makefile": "Makefile",
    ".github/workflows/**": ".github/workflows/build.yml",
    ".github/actions/**": ".github/actions/apt-install/action.yml",
}
"""A path each glob of `EVERYTHING_WHEN` is there for."""

_PACKAGES = itertools.count()


@pytest.fixture
def package(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """A package of its own, by a name no other test has used: `a` imports `b`; `s` runs at startup and
    imports `u`; `e` imports `f`, and nothing runs either; `c` stands alone."""
    name = f"selpkg{next(_PACKAGES)}"
    folder = tmp_path / name
    folder.mkdir()
    for module, body in {
        "__init__": "",
        "a": f"from {name} import b\n\ndef f():\n    return b.X\n",
        "b": "X = 1\n",
        "c": "def g():\n    return 2\n",
        "s": f"from {name} import u\n",
        "u": "Y = 3\n",
        "e": f"from {name} import f\n",
        "f": "Z = 4\n",
        "d": "def h():\n    return 5\n",
    }.items():
        (folder / f"{module}.py").write_text(body)
    return tmp_path, name


def mapped(name: str) -> dict[str, dict[str, list[str]]]:
    """What a recording would have noted: startup ran `s`, one route ran `a`, another `d`."""
    return {
        "startup": {"modules": [f"{name}/s.py"], "files": []},
        "route /a": {"modules": [f"{name}/a.py"], "files": ["http/a.txt"]},
        "route /d": {"modules": [f"{name}/d.py"], "files": ["http/d.txt"]},
    }


def chosen(package: tuple[pathlib.Path, str], *touched: str, moved: tuple[str, ...] = ()) -> object:
    root, name = package
    return selection.select(
        root,
        [f"{name}/{module}.py" for module in touched],
        mapped(name),
        added_or_removed=[f"{name}/{module}.py" for module in moved],
        package=name,
    )


class TestEverythingWhen:
    def test_every_glob_has_an_example(self) -> None:
        assert set(EXAMPLES) == set(selection.EVERYTHING_WHEN)

    @pytest.mark.parametrize(("glob", "path"), sorted(EXAMPLES.items()))
    def test_each_records_everything(self, glob: str, path: str, tmp_path: pathlib.Path) -> None:
        assert fnmatch.fnmatchcase(path, glob)
        assert selection.select(tmp_path, [path], {"startup": {"modules": []}}).everything

    def test_any_file_under_app_that_is_not_python(self, tmp_path: pathlib.Path) -> None:
        assert selection.select(tmp_path, ["app/data/names.csv"], {"startup": {"modules": []}}).everything


class TestReach:
    def test_a_module_a_part_ran_selects_that_part(self, package: tuple[pathlib.Path, str]) -> None:
        found = chosen(package, "a")
        assert (found.everything, found.parts) == (False, ["route /a"])

    def test_a_module_imported_by_one_a_part_ran_selects_that_part(self, package: tuple[pathlib.Path, str]) -> None:
        found = chosen(package, "b")
        assert (found.everything, found.parts) == (False, ["route /a"])

    def test_a_module_startup_runs_is_everything(self, package: tuple[pathlib.Path, str]) -> None:
        found = chosen(package, "s")
        assert found.everything
        assert found.reasons == [f"{package[1]}/s.py runs at startup, or imports what does"]

    def test_a_module_startup_imports_is_everything(self, package: tuple[pathlib.Path, str]) -> None:
        found = chosen(package, "u")
        assert found.everything
        assert "runs at startup, or imports what does" in found.reasons[0]

    def test_a_module_nothing_runs_or_imports_is_everything(self, package: tuple[pathlib.Path, str]) -> None:
        found = chosen(package, "c")
        assert found.everything
        assert "run by no part and imported by no module" in found.reasons[0]

    def test_it_is_everything_beside_a_module_a_part_ran(self, package: tuple[pathlib.Path, str]) -> None:
        assert chosen(package, "a", "c").everything

    def test_a_module_reaching_no_part_is_everything(self, package: tuple[pathlib.Path, str]) -> None:
        found = chosen(package, "f")
        assert found.everything
        assert "reaches no part" in found.reasons[0]

    def test_a_module_added_or_removed_is_everything(self, package: tuple[pathlib.Path, str]) -> None:
        assert chosen(package, "a", moved=("gone",)).everything

    def test_nothing_under_the_package_selects_no_part(self, package: tuple[pathlib.Path, str]) -> None:
        root, name = package
        found = selection.select(root, ["docs/x.md"], mapped(name), package=name)
        assert (found.everything, found.parts) == (False, [])


class TestCompared:
    files: typing.ClassVar[dict[str, bytes]] = {
        "http/a.txt": b"a",
        "http/d.txt": b"d",
        "routes.txt": b"r",
        "normalisation/routes.txt": b"n",
        "scenarios/01.txt": b"s",
        "normalisation/scenarios.txt": b"ns",
        "other/x.txt": b"t",
    }

    def test_only_the_parts_recorded_and_the_walk(self) -> None:
        executed_map = {"route /a": {"modules": [], "files": ["http/a.txt"]}}
        kept = selection.compared(self.files, executed_map, selection.Selection(False, [], ["route /a"]))
        assert set(kept) == {"http/a.txt", "routes.txt", "normalisation/routes.txt"}

    def test_a_test_that_runs_whole_is_kept_whole(self) -> None:
        kept = selection.compared(self.files, {}, selection.Selection(False, [], ["scenario 01"]))
        assert set(kept) == {
            "scenarios/01.txt",
            "normalisation/scenarios.txt",
            "routes.txt",
            "normalisation/routes.txt",
        }


class TestWanted:
    @pytest.fixture(autouse=True)
    def told(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        told = tmp_path / "select.json"
        told.write_text(
            json.dumps({"parts": ["route /a", "scenario 01"], "known": ["route /a", "route /b", "scenario 01"]})
        )
        monkeypatch.setenv(executed.SELECT_ENV, str(told))
        monkeypatch.setattr(executed, "_selected", None)
        monkeypatch.setattr(executed, "_known", set())

    def test_a_selected_part_is_wanted(self) -> None:
        assert executed.wanted("route /a")

    def test_a_known_part_not_selected_is_not(self) -> None:
        assert not executed.wanted("route /b")

    def test_a_part_the_base_never_recorded_is(self) -> None:
        assert executed.wanted("route /new")

    def test_a_whole_test_is_wanted_where_any_of_its_parts_is(self) -> None:
        assert executed.any_wanted("scenario ")
        assert not executed.any_wanted("other ")

    def test_everything_is_wanted_where_nothing_was_said(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(executed.SELECT_ENV)
        assert executed.wanted("route /b")
        assert executed.any_wanted("other ")
