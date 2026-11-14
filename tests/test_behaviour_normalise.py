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

"""What each rule of tests/behaviour/normalise.py rewrites, and what it must leave alone.

A rule hides whatever it matches from the behaviour record, a change to it included, so it is held to
examples on both sides: a rule that stops matching what it is for fails here before it fails a
comparison, and one widened past it fails here before it hides something."""

from __future__ import annotations

import pytest

from tests.behaviour import normalise

STATE_DIR = "/tmp/tmpabcd1234"


@pytest.fixture(autouse=True)
def _directories():
    normalise.name_the_directories(state_dir=STATE_DIR)
    yield
    normalise.take_counts()


@pytest.mark.parametrize("rule", normalise.RULES, ids=lambda rule: rule.name)
def test_every_rule_has_examples_both_ways(rule: normalise.Rule) -> None:
    assert rule.rewrites, f"{rule.name} says nothing of what it is for"
    assert rule.leaves, f"{rule.name} says nothing of what it must leave alone"


@pytest.mark.parametrize(
    ("given", "expected"),
    [(given, expected) for rule in normalise.RULES for given, expected in rule.rewrites],
)
def test_a_rule_rewrites_what_it_is_for(given: str, expected: str) -> None:
    assert normalise.normalised(given) == expected


@pytest.mark.parametrize("given", [given for rule in normalise.RULES for given in rule.leaves])
def test_a_rule_leaves_its_neighbours_alone(given: str) -> None:
    assert normalise.normalised(given) == given


def test_the_state_directory_is_named() -> None:
    page = f"<td>{STATE_DIR}/secretary-records.db</td>"

    assert normalise.normalised(page) == "<td>[state]/secretary-records.db</td>"


def test_a_directory_like_the_run_s_own_is_left_alone() -> None:
    page = "<td>/tmp/tmpzzzz9999/secretary-records.db</td>"

    assert normalise.normalised(page) == page


def test_every_rewrite_is_counted() -> None:
    normalise.take_counts()

    normalise.normalised(f'{STATE_DIR} {STATE_DIR} <script nonce="Ab3_x-Ab3_x-Ab3_x-Ab3_">')

    assert normalise.take_counts() == {"state-dir": 2, "csp-nonce": 1} | {
        name: 0 for name in normalise.NAMES if name not in ("state-dir", "csp-nonce")
    }
