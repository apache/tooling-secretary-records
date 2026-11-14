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

"""What is rewritten in an answer before it is recorded - every rule of it, in this one place.

A rule here is a concession: it hides whatever it matches from the record, including a change to it.
So each one matches only what varies between two runs of the same code, and says why it could not be
pinned instead (`world.py` holds the pins). Content a refactor could change is never matched.

Each rule carries examples of what it rewrites and of what it must leave alone, which
`tests/test_behaviour_normalise.py` holds it to; and how often each one rewrote something is part of
the record (`store.write`), so a rule that starts matching more is a difference like any other.
"""

from __future__ import annotations

import collections
import dataclasses
import re


@dataclasses.dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    replacement: str
    why: str
    rewrites: tuple[tuple[str, str], ...]
    """Examples: what the rule is for, and what it makes of it."""
    leaves: tuple[str, ...]
    """Examples of what it must not touch: the nearest things to what it is for."""


@dataclasses.dataclass(frozen=True)
class DirectoryRule:
    """A directory the run made, named by the path itself rather than a pattern for paths like it."""

    name: str
    token: str
    why: str


DIRECTORY_RULES: tuple[DirectoryRule, ...] = (
    DirectoryRule(
        "state-dir",
        "[state]",
        "The state directory the suite's `app` fixture makes for each test with `tempfile.TemporaryDirectory`"
        " (tests/conftest.py), which holds the database the server status page names.",
    ),
)

RULES: tuple[Rule, ...] = (
    Rule(
        "csp-nonce",
        re.compile(r"(nonce-|nonce=\")[A-Za-z0-9_-]{22}(?=['\"])"),
        r"\1[nonce]",
        "The Content-Security-Policy nonce, minted per request from `secrets` (app/security_headers.py), which"
        " no seed reaches: a nonce that could be predicted would not be one.",
        rewrites=(
            ("script-src 'self' 'nonce-Ab3_x-Ab3_x-Ab3_x-Ab3_'", "script-src 'self' 'nonce-[nonce]'"),
            ('<script nonce="Ab3_x-Ab3_x-Ab3_x-Ab3_">', '<script nonce="[nonce]">'),
        ),
        leaves=("script-src 'self'", '<script nonce="short">', "a nonce-Ab3_x-Ab3_x-Ab3_x-Ab3_x-Ab3 in prose"),
    ),
    Rule(
        "oauth-state",
        re.compile(r"((?:[?&]|%3F|%26)state(?:=|%3D))[0-9a-f]{32}\b"),
        r"\1[state]",
        "The OAuth state asfquart puts on the redirect to oauth.apache.org, from `secrets.token_hex`, for the"
        " same reason as the nonce.",
        rewrites=(
            ("auth?state=0123456789abcdef0123456789abcdef&x", "auth?state=[state]&x"),
            ("auth%3Fstate%3D0123456789abcdef0123456789abcdef", "auth%3Fstate%3D[state]"),
        ),
        leaves=("?state=open", "?state=0123456789abcdef", '"state": "0123456789abcdef0123456789abcdef"'),
    ),
    Rule(
        "session-cookie",
        re.compile(r"^(set-cookie: [^=;]+=)[^;\n]*", re.MULTILINE),
        r"\1[value]",
        "A cookie's value: the session is signed with a secret key the app draws at random when it is built."
        " That a cookie is set, its name and its attributes are kept.",
        rewrites=(
            ("set-cookie: session=eyJhIjoxfQ.abc; HttpOnly; Path=/", "set-cookie: session=[value]; HttpOnly; Path=/"),
        ),
        leaves=("cache-control: session=1", '"set-cookie: session=x" in a body line'),
    ),
)

NAMES: tuple[str, ...] = tuple(rule.name for rule in (*DIRECTORY_RULES, *RULES))

_directories: dict[str, str] = {}
_counts: collections.Counter[str] = collections.Counter()


def name_the_directories(*, state_dir: str) -> None:
    """The run's own directories, for the rules that rewrite them where they appear."""
    _directories.clear()
    _directories.update({"state-dir": state_dir})


def normalised(text: str) -> str:
    for rule in DIRECTORY_RULES:
        path = _directories.get(rule.name)
        if path:
            _counts[rule.name] += text.count(path)
            text = text.replace(path, rule.token)
    for rule in RULES:
        text, count = rule.pattern.subn(rule.replacement, text)
        _counts[rule.name] += count
    return text


def take_counts() -> dict[str, int]:
    """How often each rule has rewritten something since this was last asked, and start again."""
    counts = dict(_counts)
    _counts.clear()
    return counts
