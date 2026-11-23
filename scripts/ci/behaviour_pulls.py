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
"""What the pull requests merged as main's commits allowed, for a full comparison to read.

    GH_TOKEN=... uv run scripts/ci/behaviour_pulls.py --ref <base>
    gh auth token | scripts/container.sh uv run scripts/ci/behaviour_pulls.py --ref <base> --token-stdin
    uv run scripts/ci/behaviour.py check --full --ref <base> --pulls state/behaviour-pulls.json

A pull request's own comparison reads the trailers on its own commits. A full comparison reads main's,
and a squash merge made with GitHub's "commit messages" default joins every commit's message into one:
each `Behaviour-Allow:` line lands mid-message, where git does not read it as a trailer. So main's
history allows nothing the pull request allowed, and a nightly comparison fails on the records of any change that
needed an allowance.

For each commit on HEAD's first-parent line since the merge base of `--ref` and HEAD, GitHub says which
pull request was merged as it (`commits/{sha}/pulls`). One counts only where it was merged into main and
its `merge_commit_sha` is this commit - the endpoint lists others too. Its head is fetched from
`refs/pull/N/head`, which GitHub keeps after the branch is deleted, and its trailers are read by the
same rule as its own comparison read them: `behaviour_gate.claims`, its first-parent line since the
commit's parent, without merges. One of those commits that GitHub names as another pull request's,
merged into main before this one, is left out: a pull request stacked on another carries the other's
commits, and they allowed the other's change. A commit no pull request was merged as - a push to main -
has its own trailers, which `behaviour.py` reads anyway; so does a rebase merge, whose last commit is
read here too. GitHub may list no pull request for a merge it has just made: a subject ending in `(#N)`
says which to ask for by number, and one GitHub does not yet say is merged fails the run rather than
reads as a push. Who committed a commit says nothing - GitHub commits an edit made on its web page to
main as it commits a merge, and main has many.

What it finds goes to `--out`, with the range it covers: `behaviour.py check --pulls` compares only
the range it was read for.

Nothing read is not nothing allowed. Where GitHub cannot be asked - no token, a rate limit, the network
- or a pull request's commits cannot be fetched, this fails and writes nothing, rather than letting the
comparison run as though the pull requests allowed nothing (which fails on what they allowed, and
names the wrong cause) or anything (which passes what nobody allowed).
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Final, Protocol

import behaviour_gate as gate

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT: Final = pathlib.Path(__file__).resolve().parent.parent.parent
OUT: Final = ROOT / "state" / "behaviour-pulls.json"

MAIN: Final = "main"
KEYS: Final = (gate.ALLOW, gate.HARNESS)


def _conf(key: str, valid: re.Pattern[str]) -> str:
    """What `devkit.conf` gives `key`, if it is `valid`: read as lines of `KEY=value` and never run, the
    first one that names the key, as the shell scripts read it."""
    try:
        lines = (ROOT / "devkit.conf").read_text().split("\n")
    except OSError as error:
        raise SystemExit(f"devkit.conf cannot be read: {error}") from error
    for line in lines:
        name, _, value = line.partition("=")
        if name == key:
            if valid.fullmatch(value) is None:
                raise SystemExit(f"devkit.conf has no usable {key}: {value!r}")
            return value
    raise SystemExit(f"devkit.conf has no {key}")


REPOSITORY: Final = os.environ.get("GITHUB_REPOSITORY") or _conf("DEVKIT_REPOSITORY", re.compile(r"[\w.-]+/[\w.-]+"))
API: Final = os.environ.get("GITHUB_API_URL", "https://api.github.com")
SERVER: Final = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
MERGED_AS: Final = re.compile(r"\(#(\d+)\)$")
"""The subject GitHub gives a squash merge, and the title of a pull request it merges: `... (#123)`."""


class UnreadableError(Exception):
    """GitHub, or the git it serves, could not say what a commit's pull request allowed. `again` where the
    same run can pass later: GitHub not caught up with a merge yet, a rate limit, the network."""

    def __init__(self, message: str, *, again: bool = False) -> None:
        super().__init__(message)
        self.again = again


class GitHub(Protocol):
    def pulls_of(self, sha: str) -> list[dict[str, Any]]:
        """The pull requests GitHub lists for a commit."""
        ...

    def pull(self, number: int) -> dict[str, Any] | None:
        """Pull request `number`, or None where there is none."""
        ...


@dataclasses.dataclass(frozen=True)
class Reading:
    since: str
    head: str
    pulls: dict[str, int | None]
    """Each commit read, and the pull request merged as it, or None."""
    claims: dict[str, list[tuple[str, str]]]
    """`(value, subject)` for each trailer key, as `behaviour_gate.claims` gives them."""


def _git(cwd: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True)


def _out(cwd: pathlib.Path, *arguments: str) -> str:
    finished = _git(cwd, *arguments)
    if finished.returncode != 0:
        raise UnreadableError(f"git {' '.join(arguments)}: {finished.stderr.strip()}")
    return finished.stdout.strip()


def _into_main(pull: dict[str, Any]) -> bool:
    return bool(pull.get("merged_at")) and (pull.get("base") or {}).get("ref") == MAIN


def merged_as(sha: str, github: GitHub, cwd: pathlib.Path) -> dict[str, Any] | None:
    """The pull request merged into main as `sha`, or None for a commit no pull request was merged as.

    GitHub lists it for the commit (`commits/{sha}/pulls`) once it has linked the two. Until then only the
    subject says so: a squash merge's ends in `(#N)`, and pull request N, asked by number, is merged as
    this commit - or not merged yet, which fails until GitHub catches up. Who committed it says nothing:
    GitHub commits an edit made in its web page to main just as it commits a merge."""
    found = [pull for pull in github.pulls_of(sha) if _into_main(pull) and pull.get("merge_commit_sha") == sha]
    if len(found) > 1:
        numbers = ", ".join(f"#{pull['number']}" for pull in found)
        raise UnreadableError(f"{sha[:12]} is the merge of {numbers}, and a commit is the merge of one at most")
    if found:
        return found[0]
    subject = _out(cwd, "log", "-1", "--format=%s", sha)
    named = MERGED_AS.search(subject)
    pull = github.pull(int(named[1])) if named else None
    if pull is None:
        return None
    if _into_main(pull) and pull.get("merge_commit_sha") == sha:
        return pull
    if not pull.get("merged_at"):
        raise UnreadableError(
            f"{sha[:12]} ('{subject}') reads as the merge of #{pull['number']}, which GitHub does not say is merged "
            "yet: it has not caught up with the merge",
            again=True,
        )
    # merged elsewhere, or as another commit: this one names it and is not its merge
    return None


def read(ref: str, cwd: pathlib.Path, github: GitHub, fetch: Callable[[list[int]], None]) -> Reading:
    """What each pull request merged as a commit since the merge base of `ref` and HEAD allowed."""
    head = _out(cwd, "rev-parse", "HEAD")
    since = _out(cwd, "merge-base", ref, head)
    commits = _out(cwd, "rev-list", "--first-parent", "--reverse", f"{since}..{head}").split()
    merged = {sha: merged_as(sha, github, cwd) for sha in commits}
    found = {sha: pull for sha, pull in merged.items() if pull is not None}
    if found:
        fetch(sorted({pull["number"] for pull in found.values()}))
    claims: dict[str, list[tuple[str, str]]] = {key: [] for key in KEYS}
    for sha, pull in found.items():
        for key, carried in _carried(sha, pull, cwd, github).items():
            claims[key] += carried
    for sha, pull in merged.items():
        subject = _out(cwd, "log", "-1", "--format=%s", sha)
        print(f"{sha[:12]} {subject}: " + (f"#{pull['number']}" if pull else "no pull request was merged as it"))
    return Reading(since, head, {sha: pull["number"] if pull else None for sha, pull in merged.items()}, claims)


def _carried(sha: str, pull: dict[str, Any], cwd: pathlib.Path, github: GitHub) -> dict[str, list[tuple[str, str]]]:
    """The trailers on `pull`'s own commits, read as its own comparison read them - each commit by itself,
    so that one a pull request merged before this one is left to that one. Nothing where its commits are
    main's own: main's trailers are read anyway, and its own comparison read none."""
    number, tip = pull["number"], pull["head"]["sha"]
    if _git(cwd, "cat-file", "-e", f"{tip}^{{commit}}").returncode != 0:
        raise UnreadableError(f"#{number}'s head {tip[:12]} is not here after fetching refs/pull/{number}/head")
    # the walk `gate.claims` makes, through `_out`, which fails where `git log` would answer nothing
    own = _out(cwd, "rev-list", "--no-merges", "--first-parent", f"{sha}^..{tip}").split()
    claims: dict[str, list[tuple[str, str]]] = {key: [] for key in KEYS}
    for commit in own:
        said = {key: gate.claims(key, f"{commit}^", cwd, head=commit) for key in KEYS}
        if not any(said.values()):
            continue
        if earlier := _merged_before(commit, pull, github):
            print(f"  {commit[:12]} is #{earlier}'s, merged before #{number}: read as #{earlier}'s, not here")
            continue
        for key, found in said.items():
            claims[key] += [(value, f"{subject} (#{number})") for value, subject in found]
    return claims


def _merged_before(commit: str, pull: dict[str, Any], github: GitHub) -> int | None:
    """Another pull request merged into main before `pull` with `commit` in it: one stacked on it brings
    its commits along, and they allowed its change, not this one's."""
    for other in github.pulls_of(commit):
        if _into_main(other) and other["number"] != pull["number"] and other["merged_at"] < pull["merged_at"]:
            return other["number"]
    return None


def _next_page(link: str) -> str | None:
    match = re.search(r'<([^>]+)>;\s*rel="next"', link)
    return match.group(1) if match else None


@dataclasses.dataclass(frozen=True)
class Api:
    """GitHub's REST API, asked with `token`."""

    token: str

    def pulls_of(self, sha: str) -> list[dict[str, Any]]:
        url: str | None = f"{API}/repos/{REPOSITORY}/commits/{sha}/pulls?per_page=100"
        listed: list[dict[str, Any]] = []
        while url:
            if not url.startswith(f"{API}/"):
                raise UnreadableError(f"GitHub paged {sha[:12]}'s pull requests to {url}, which is not {API}")
            page, url = self._get(url)
            listed += page
        return listed

    def pull(self, number: int) -> dict[str, Any] | None:
        # a 404 is a subject naming an issue rather than a pull request
        return self._get(f"{API}/repos/{REPOSITORY}/pulls/{number}", missing=True)[0]

    def _get(self, url: str, *, missing: bool = False) -> tuple[Any, str | None]:
        """What `url` answers, and the next page's url; nothing for a 404 where one is expected (`missing`)."""
        request = urllib.request.Request(  # noqa: S310 - the scheme is the API's: every url starts with it
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "behaviour-pulls",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - as above
                return json.load(response), _next_page(response.headers.get("Link") or "")
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None, None
            said = error.read().decode("utf-8", errors="replace")[:300]
            # GitHub's own trouble and a rate limit pass on a later run; a refusal of the token does not
            again = error.code >= 500 or error.code == 429 or "rate limit" in said.lower()
            raise UnreadableError(f"GET {url}: {error.code} {error.reason}: {said}", again=again) from error
        except (OSError, ValueError) as error:
            raise UnreadableError(f"GET {url}: {error}", again=isinstance(error, OSError)) from error


def fetcher(remote: str, token: str | None, cwd: pathlib.Path) -> Callable[[list[int]], None]:
    """Fetch each pull request's head from `remote`, the token given to this git alone and kept nowhere."""

    def fetch(numbers: list[int]) -> None:
        env = os.environ | {"GIT_TERMINAL_PROMPT": "0"}
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env |= {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"http.{SERVER}/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
            }
        refspecs = [f"refs/pull/{number}/head" for number in numbers]
        finished = subprocess.run(
            ["git", "fetch", "--no-tags", "--quiet", remote, *refspecs],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        if finished.returncode != 0:
            # the repository is private: without a token, git is refused before it finds anything
            unaided = "" if token else " (no token was given to git)"
            raise UnreadableError(f"git fetch {' '.join(refspecs)}: {finished.stderr.strip()}{unaided}")

    return fetch


def write(reading: Reading, out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataclasses.asdict(reading), indent=1) + "\n")


def load(path: pathlib.Path) -> Reading:
    data = json.loads(path.read_text())
    claims = {key: [(value, subject) for value, subject in data["claims"].get(key, [])] for key in KEYS}
    return Reading(data["since"], data["head"], data["pulls"], claims)


def carried(path: pathlib.Path, since: str, head: str) -> Reading:
    """The reading at `path`, where it was read over `since..head`: one of another range allows nothing."""
    if not path.exists():
        raise UnreadableError(f"no {path}: run scripts/ci/behaviour_pulls.py first")
    reading = load(path)
    if (reading.since, reading.head) != (since, head):
        raise UnreadableError(
            f"{path} was read over {reading.since[:12]}..{reading.head[:12]}, and this compares "
            f"{since[:12]}..{head[:12]}: read it again"
        )
    return reading


def _token(from_stdin: bool) -> str:
    token = sys.stdin.readline().strip() if from_stdin else os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise UnreadableError("no token: set GH_TOKEN, or pipe one in with --token-stdin")
    return token


def _output(name: str, value: str) -> None:
    """A step output, for the issue a failed night writes (`.github/workflows/behaviour-full.yml`)."""
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as written:
            written.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="origin/main", help="read the commits since the merge base of this and HEAD")
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    parser.add_argument("--token-stdin", action="store_true", help="read the token from the first line of stdin")
    args = parser.parse_args()
    args.out.unlink(missing_ok=True)
    try:
        token = _token(args.token_stdin)
        reading = read(args.ref, ROOT, Api(token), fetcher(f"{SERVER}/{REPOSITORY}.git", token, ROOT))
    except UnreadableError as error:
        print(
            f"::error::Cannot read what the pull requests merged as these commits allowed: {error}\n"
            "Not compared. With nothing read, a squash merge's allowances would be missing and the comparison "
            "would fail on what its pull request allowed; nothing is assumed allowed either. "
            + ("This can pass on a later run." if error.again else "A later run fails the same way until it is fixed."),
            file=sys.stderr,
        )
        _output("again", "true" if error.again else "false")
        return 1
    write(reading, args.out)
    for key in KEYS:
        for value, subject in reading.claims[key]:
            print(f"  {key}: {value} ('{subject}')")
    count = sum(1 for number in reading.pulls.values() if number is not None)
    print(f"{len(reading.pulls)} commits over {reading.since[:12]}..{reading.head[:12]}, {count} from a pull request")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
