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

"""Which parts of the record a run is asked for (`tests/behaviour/executed.py`).

What a change can reach is the kit's to test (`scripts/ci/behaviour_selection.py`); what the harness does with
the answer is this repository's.
"""

import json
from typing import TYPE_CHECKING

import pytest

from tests.behaviour import executed

if TYPE_CHECKING:
    import pathlib


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
