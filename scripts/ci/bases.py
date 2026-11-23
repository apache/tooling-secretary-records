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
"""The two bases a branch is compared against, recorded once for each merge base and shared by
every worktree of this repository.

`make quality-base` and `make snapshot-base` record the commit the branch would merge into, under
state/, and most worktrees leave main at the same commit - so a recording is kept in the
repository's common git directory, which every worktree reads (and the container sees at the same
path, scripts/container.sh), and a second worktree on the same merge base copies it rather than
recording it again.

What a recording may be reused for is its key:

- the behaviour base is the merge base recording itself, with its own script, harness and - where
  its lockfile differs - its own environment (`_record_with_its_own` in behaviour.py), so the commit
  is the whole of what it depends on;
- the quality base is that commit measured with *this* checkout's meters and toolchain
  (quality.py, `base`), so its key is the commit and this checkout's quality scripts and lockfiles.
  A branch that changes a meter measures the base again rather than borrowing one read by the old
  meter.

    uv run scripts/ci/bases.py quality --ref origin/main --out state/quality-base.json
    uv run scripts/ci/bases.py snapshot --ref origin/main --fresh    # record it again regardless
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# How many recordings of each kind are kept. A merge base is current until main moves, so this is a
# few days of main for the sessions working beside one another, not a history.
KEEP = 6


@dataclasses.dataclass(frozen=True)
class Kind:
    name: str
    script: str
    """What records it, under scripts/ci/."""
    toolchain: tuple[str, ...]
    """This checkout's files the recording depends on, besides the merge base."""
    sidecars: tuple[str, ...] = ()
    """Files written beside `--out`, named `<out>.<sidecar>`."""


KINDS = {
    "quality": Kind(
        "quality",
        "quality.py",
        toolchain=(
            "scripts/ci/quality.py",
            "scripts/ci/quality_meters.py",
            "scripts/ci/quality_allowances.py",
            "scripts/ci/quality_moves.py",
            "pyproject.toml",
            "uv.lock",
            "assets/yarn.lock",
        ),
    ),
    # `_meta` and `_beside` in behaviour.py: the record's commit, and the map a selection reads
    "snapshot": Kind("snapshot", "behaviour.py", toolchain=(), sidecars=("behaviour-record.json", "executed.json")),
}

DEFAULT_OUT = {"quality": "state/quality-base.json", "snapshot": "state/behaviour-base"}


def _git(*arguments: str) -> str:
    finished = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True)
    if finished.returncode != 0:
        raise SystemExit(f"git {' '.join(arguments)}: {finished.stderr.strip()}")
    return finished.stdout.strip()


def cache() -> pathlib.Path:
    return pathlib.Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "devkit-bases"


def key(kind: Kind, sha: str) -> str:
    digest = hashlib.sha256(f"{kind.name}\0{sha}\0".encode())
    for relative in kind.toolchain:
        path = ROOT / relative
        digest.update(relative.encode() + b"\0" + (path.read_bytes() if path.is_file() else b"absent") + b"\0")
    return f"{sha[:12]}-{digest.hexdigest()[:16]}"


def outputs(kind: Kind, out: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """Each file the recorder writes, and the name it is kept under: one of its own rather than the
    file's, so that a recording kept for one `--out` is found again for another."""
    return [("record", out), *((f"record.{s}", out.parent / f"{out.name}.{s}") for s in kind.sidecars)]


def _remove(path: pathlib.Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _copy(source: pathlib.Path, target: pathlib.Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def restore(entry: pathlib.Path, kind: Kind, out: pathlib.Path) -> None:
    """Put a kept recording where the recorder would have written it. Whatever was there goes first,
    as the recorder itself does, so that what is left is this recording and nothing of an older one."""
    for name, path in outputs(kind, out):
        _remove(path)
        kept = entry / name
        if kept.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            _copy(kept, path)


def store(entry: pathlib.Path, kind: Kind, out: pathlib.Path, sha: str) -> None:
    """Keep a recording, whole or not at all: it is assembled beside the entry and renamed into
    place, so a session reading the cache never sees half of one, and two sessions that recorded the
    same base at once keep whichever finished first."""
    entry.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".staging-", dir=entry.parent))
    try:
        for name, path in outputs(kind, out):
            if path.exists():
                _copy(path, staging / name)
        recorded = {"commit": sha, "by": str(ROOT), "at": datetime.datetime.now(tz=datetime.UTC).isoformat()}
        (staging / "recorded.json").write_text(json.dumps(recorded) + "\n", encoding="utf-8")
        os.rename(staging, entry)
    except OSError:
        if not entry.is_dir():
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def prune(directory: pathlib.Path) -> None:
    entries = sorted((p for p in directory.iterdir() if not p.name.startswith(".")), key=lambda p: p.stat().st_mtime)
    for old in entries[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def record(kind: Kind, ref: str, out: pathlib.Path, *, fresh: bool) -> int:
    sha = _git("merge-base", ref, "HEAD")
    entry = cache() / kind.name / key(kind, sha)
    # an entry without the record itself - a base with nothing to record, say - is recorded again,
    # which costs what it costs, rather than reported as copied
    if (entry / "record").exists() and not fresh:
        restore(entry, kind, out)
        os.utime(entry)
        by = json.loads((entry / "recorded.json").read_text(encoding="utf-8"))
        print(f"{kind.name} base for {sha[:12]}: recorded by {by['by']} at {by['at']}, copied -> {out}")
        return 0
    command = [sys.executable, str(ROOT / "scripts" / "ci" / kind.script), "base", "--ref", ref, "--out", str(out)]
    finished = subprocess.run(command, cwd=ROOT)
    if finished.returncode != 0:
        return finished.returncode
    if entry.is_dir():
        shutil.rmtree(entry)
    store(entry, kind, out, sha)
    prune(entry.parent)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=sorted(KINDS))
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--fresh", action="store_true", help="record it, whether a recording is kept or not")
    args = parser.parse_args()
    out = args.out or ROOT / DEFAULT_OUT[args.kind]
    return record(KINDS[args.kind], args.ref, out.resolve(), fresh=args.fresh)


if __name__ == "__main__":
    sys.exit(main())
