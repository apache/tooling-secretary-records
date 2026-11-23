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
"""`make check`: the pre-commit hooks over what this checkout changed, or over everything where a
change reaches files it does not name.

The files are those that differ from the merge base with `--ref` - committed, staged, modified or
untracked, so a new module is checked before it is added. A hook given some of them runs on those,
and one given none skips itself; that is what makes a commit that touched no script skip the
script's self-test.

It checks every file instead where the list cannot say what the change reaches:

- a file deleted or renamed, since what breaks is the code that still names it, and that is not in
  the list;
- a change to a file at the top of the repository or of assets/ - pyproject.toml, the
  .pre-commit-config.yaml, .importlinter, the lockfiles, eslint's and stylelint's configs, the
  Makefile - since that is where the tools read their settings from, and a setting reaches every file
  the tool reads. Markdown there is prose and checks only itself;
- no merge base with `--ref`, where nothing says what changed.

`make check-all` (`--all`) checks every file always, untracked ones among them, and `make verify` runs that
before a push.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# The directories whose own files are configuration rather than code. A file directly in one of
# these, not in a directory below it, is read by a tool as its settings.
_CONFIG_HOMES = ("", "assets")


def _git(*arguments: str) -> tuple[int, str]:
    finished = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True)
    return finished.returncode, finished.stdout


# What untracked files count: those no .gitignore hides, as the gate's key counts them (verify.py, tree()).
# A file hidden through .git/info/exclude or a global excludes file is still one a hook should read.
def _listing(*arguments: str) -> str:
    """What git lists, or the end of the run: an empty answer from a git that failed is not "nothing changed",
    which would let `make check` succeed without running a hook."""
    status, output = _git(*arguments)
    if status != 0:
        sys.exit(f"check: git {' '.join(arguments)} failed with status {status}, so no list of files can be trusted")
    return output


def _is_config(path: str) -> bool:
    home, _, name = path.rpartition("/")
    return home in _CONFIG_HOMES and not name.endswith(".md")


def selection(ref: str) -> tuple[list[str] | None, str]:
    """The files to check, or None for every file - and what to say about it."""
    status, base = _git("merge-base", ref, "HEAD")
    if status != 0:
        return None, f"no merge base with {ref}, so every file"
    base = base.strip()
    listed = _listing("diff", "--name-status", "--no-renames", "-z", base)
    fields = [f for f in listed.split("\0") if f]
    changes = list(zip(fields[::2], fields[1::2], strict=True))
    untracked = _listing("ls-files", "-z", "--others", "--exclude-per-directory=.gitignore")
    files = sorted({path for _, path in changes} | {p for p in untracked.split("\0") if p})

    if deleted := sorted(path for kind, path in changes if kind == "D"):
        return None, f"{deleted[0]} is deleted or renamed, and what still names it is not listed: every file"
    if config := [path for path in files if _is_config(path)]:
        return None, f"{config[0]} is configuration, which reaches every file: every file"
    return files, f"the {len(files)} files changed since {ref} ({base[:12]}); make check-all for every file"


def everything() -> list[str]:
    """Every file, untracked ones among them. Not `prek run --all-files`, which is the tracked files
    only: a new module that brought a deletion with it would go unchecked by the very run that
    checks every file."""
    listed = _listing("ls-files", "-z", "--cached", "--others", "--exclude-per-directory=.gitignore")
    return sorted({p for p in listed.split("\0") if p and (ROOT / p).is_file()})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--all", action="store_true", help="every file, untracked ones among them: `make check-all`")
    args = parser.parse_args()
    if args.all:
        print("check: every file", flush=True)
        return subprocess.run(["prek", "run", "--files", *everything()], cwd=ROOT).returncode
    files, said = selection(args.ref)
    print(f"check: {said}", flush=True)
    if files == []:
        return 0
    return subprocess.run(["prek", "run", "--files", *(everything() if files is None else files)], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
