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

"""Every GET the app routes, filled in from the fixture and asked of every reader.

The rules come from the app's own `url_map`, so a route added later is walked without anyone listing
it - or fails the walk, if it takes a parameter nothing here knows how to fill. A rule is either
filled, or excluded with the reason in `EXCLUDED`: never left out because nobody looked.

The values a parameter takes are read from the app's own answers where it gives them (`discover`), so
they follow the fixture rather than a list kept beside it.
"""

from __future__ import annotations

import dataclasses
import difflib
import itertools
import json
import re
import urllib.parse
import zlib
from typing import Any

from tests.behaviour import executed, normalise, world

SHARDS = 2
"""How many tests the routes are spread over, by a hash of the rule, so the suite's workers share them.
Each shard is an app of its own, so changing this changes what each url is asked after - and nothing
should depend on that, which a full run with another count would show."""

UNKNOWN = "zz-unknown"
"""A value for every parameter that names nothing in the fixture, so the answer to a miss is recorded."""
UNKNOWN_DATE = "2000-01-05"
UNKNOWN_INT = "999"

# Everything below is keyed by the rule - the url pattern a client asks for - and never by the endpoint
# name: moving a route into another blueprint or package renames its endpoint and answers the same.

EXCLUDED: dict[str, str] = {}
"""A GET rule that is not asked, with the reason: one that answers nothing until a client speaks, or one
that changes who the readers are for every url asked after it."""

TEST_ONLY_PREFIX = "/e2e/"
"""The suite's own routes, which answer 404 unless SR_E2E=1 - as they do here, since the readers are
signed in before it is turned off. Each is asked once, with values that name nothing, so that the 404 is
on record: what one does when it is on is a question for the suites that drive it."""

HEADERS = (
    "allow",
    "cache-control",
    "content-disposition",
    "content-security-policy",
    "content-type",
    "location",
    "retry-after",
    "set-cookie",
    "vary",
    "www-authenticate",
)
"""The headers recorded. Left out: Date and Content-Length, which follow from the clock and the body,
and the security headers every answer carries alike (`tests/test_webapp.py`)."""

QUERIES: dict[str, list[str]] = {
    # the variants a route answers differently to, beside the bare url every rule is asked
    "/admin/data": ["page_size=2&offset=1", "page_size=0"],
    "/admin/data/<table_name>": ["page_size=2&offset=1"],
    # not `logout`, which would sign the reader out for every url asked after it
    "/auth": ["login", "login=/documentation"],
}


def get_rules(app: Any) -> list[Any]:
    """Every rule a GET (or a websocket) reaches, one per url pattern, in the order of the pattern."""
    rules = {rule.rule: rule for rule in app.url_map.iter_rules() if "GET" in (rule.methods or ()) or rule.websocket}
    return [rules[pattern] for pattern in sorted(rules)]


def shard_of_rule(rule: str) -> int:
    return zlib.crc32(rule.encode()) % SHARDS


# --- what a parameter can be -------------------------------------------------------------------


@dataclasses.dataclass
class Values:
    tables: list[str]
    assets: list[str]


async def discover(clients: world.Clients) -> Values:
    """What the fixture holds, as the app reports it to an admin."""
    admin = clients.by_reader["admin"]
    data_page = await (await admin.get("/admin/data")).get_data(as_text=True)
    tables = sorted(set(re.findall(r'href="/admin/data/([A-Za-z0-9_]+)"', data_page)))
    assets = sorted(
        path.relative_to(world.STATIC_ASSETS).as_posix() for path in world.STATIC_ASSETS.rglob("*") if path.is_file()
    )
    return Values(tables=tables, assets=assets)


def _unknown(converter: str) -> str:
    return {"DateConverter": UNKNOWN_DATE, "IntegerConverter": UNKNOWN_INT}.get(converter, UNKNOWN)


def _choices(rule: str, name: str, converter: str, values: Values) -> list[str] | None:
    """The values parameter `name` is filled with - or None where nothing here knows what it takes."""
    if rule.startswith(TEST_ONLY_PREFIX):
        return [_unknown(converter)]
    if rule == "/static/<path:filename>":
        # the app-level static folder Quart makes, which holds nothing of this app's
        return ["zz-unknown.css"]
    if rule == "/assets/<path:filename>":
        return [*values.assets, "bundled/zz-unknown.js"]
    if converter == "IntegerConverter":
        return ["1", UNKNOWN_INT]
    by_name: dict[str, list[str]] = {
        "table_name": values.tables,
        # a catch-all is asked what it does with a path nothing routes
        "_path": [],
    }
    if name not in by_name:
        return None
    return [*by_name[name], UNKNOWN]


def urls_for(rule: Any, values: Values) -> list[str] | None:
    """Every url `rule` is asked at, or None where a parameter of it cannot be filled."""
    # the variables in the rule, in the order they appear: `arguments` also holds a default's, which the
    # url does not carry
    names = list(rule._converters)
    if not names:
        return [rule.rule]

    combinations: list[dict[str, str]] = [{}]
    for name in names:
        converter = type(rule._converters[name]).__name__
        expanded: list[dict[str, str]] = []
        for filled in combinations:
            choices = _choices(rule.rule, name, converter, values)
            if choices is None:
                return None
            expanded.extend({**filled, name: choice} for choice in choices)
        combinations = expanded

    urls = []
    for filled in combinations:
        path = rule.rule
        for name, value in filled.items():
            path = re.sub(rf"<(?:[^:<>]+:)?{re.escape(name)}>", value, path)
        urls.append(path)
    return urls


# --- asking --------------------------------------------------------------------------------------


def body(content_type: str, data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        import hashlib

        return f"[{len(data)} bytes, not utf-8, sha256 {hashlib.sha256(data).hexdigest()}]\n"
    if "json" in content_type:
        try:
            return json.dumps(json.loads(text), sort_keys=True, indent=1, ensure_ascii=False) + "\n"
        except ValueError:
            pass
    return text


async def answer(client: Any, url: str, headers: dict[str, str] | None = None) -> str:
    response = await client.get(url, headers=headers)
    data = await response.get_data()
    lines = [f"status: {response.status_code}"]
    for header in HEADERS:
        lines.extend(f"{header}: {value}" for value in response.headers.get_all(header))
    return normalise.normalised("\n".join(lines) + "\n\n" + body(response.content_type or "", data))


BASE_READER = "secretary"
"""Whose answer the others are written as a diff against: the reader shown the most of the app."""

DIFF_CONTEXT = 2


def _against(base: str, text: str, base_name: str, name: str) -> str | None:
    """`text` as a unified diff against `base`, where that is the shorter way to write it."""
    diff = difflib.unified_diff(
        base.splitlines(keepends=True), text.splitlines(keepends=True), base_name, name, n=DIFF_CONTEXT
    )
    # a line with no newline of its own would run into the next one in the record
    written = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff)
    return written if len(written) < len(text) else None


async def _answers(clients: world.Clients, url: str, part: str) -> list[tuple[str, str]]:
    """`(who, answer)` for each reader's session. What each reader's answer ran is noted under `part` and
    the reader (`executed.py`)."""
    answers = []
    for reader in world.READERS:
        with executed.part(f"{part} @ {reader.name}"):
            answers.append((reader.name, await answer(clients.by_reader[reader.name], url)))
    return answers


async def ask_everyone(clients: world.Clients, url: str, part: str = "watched") -> str:
    """One url as every reader is answered.

    Readers answered alike share one heading. The Secretary's answer is written whole, and every other
    one as a diff against it where that is shorter - a page differs between readers by a link or two in
    its header, and seven copies of a page say nothing six diffs do not. Nothing is lost: each answer can
    be rebuilt from the record, and the record is compared byte for byte."""
    groups: dict[str, list[str]] = {}
    for who, text in await _answers(clients, url, part):
        groups.setdefault(text, []).append(who)
    base_text, base_readers = next((text, readers) for text, readers in groups.items() if BASE_READER in readers)
    base_name = ", ".join(base_readers)

    sections = [f"GET {url}\n"]
    for text, readers in groups.items():
        name = ", ".join(readers)
        diff = None if text is base_text else _against(base_text, text, base_name, name)
        if diff is None:
            sections.append(f"=== {name}\n{text}")
        else:
            sections.append(f"=== {name}: as {base_name}, but\n{diff}")
    return "\n".join(sections)


def file_name(url: str) -> str:
    path, _, query = url.partition("?")
    stem = path.strip("/") or "index"
    if query:
        stem += "@" + urllib.parse.unquote(query)
    stem = re.sub(r"[^A-Za-z0-9._@=&/,+-]", "_", stem)
    return f"http/{stem}.txt"


def plan(app: Any, values: Values) -> tuple[dict[str, list[str]], list[str]]:
    """Every url per rule, and the coverage report: each GET rule, with what it was asked at or why not.

    The report names the rule and not its endpoint, so that a route moved into another package - a new
    endpoint name, the same answers - is not a difference."""
    urls: dict[str, list[str]] = {}
    report: list[str] = []
    for rule in get_rules(app):
        if rule.rule in EXCLUDED:
            report.append(f"excluded   {rule.rule}: {EXCLUDED[rule.rule]}")
            continue
        found = urls_for(rule, values)
        if found is None:
            report.append(f"UNFILLED   {rule.rule}")
            continue
        queries = QUERIES.get(rule.rule, [])
        found = found + [f"{url}?{query}" for url, query in itertools.product(found, queries)]
        urls[rule.rule] = found
        report.append(f"{len(found):>5} urls  {rule.rule}")
    return urls, report
