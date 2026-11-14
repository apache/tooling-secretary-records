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

"""Import every module of `app` and build the application, with only what `requirements.txt` installs, and
check that every package the code imports anywhere, a function body included, is installed.

Run it in an environment made from `requirements.txt` alone, which is what a deployment installs. The test
suite cannot say whether that file is complete: it runs with the `test` group installed as well, so a
package that only the tests declare is present there and absent in production. The server imports every
route when it starts, so one such import stops the whole application. An import inside a function runs
later, so only the second check sees it.

Run as `python -I scripts/ci/production_import.py` from the repository root. It exits non-zero and names
each module that failed to import.
"""

import ast
import importlib
import importlib.util
import pathlib
import pkgutil
import shutil
import sys
import tempfile
from collections.abc import Iterator

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _packages(path: pathlib.Path) -> Iterator[tuple[int, str]]:
    """The line and top-level package of every absolute import in the file, at any depth."""
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            continue
        for name in names:
            yield node.lineno, name.partition(".")[0]


def _installed(package: str) -> bool:
    return (
        not package or package == "app" or package in sys.stdlib_module_names or bool(importlib.util.find_spec(package))
    )


def _imported_elsewhere() -> list[str]:
    """The top-level packages that `server.py` and every `.py` file under `app/` import, at any depth,
    that are not installed: what an import of the modules cannot reach - a function body, an alembic
    `env.py`, a directory without an `__init__.py`."""
    paths = [ROOT / "server.py", *sorted((ROOT / "app").rglob("*.py"))]
    return [
        f"{path.relative_to(ROOT)}:{line}: {package} is imported and not installed"
        for path in paths
        for line, package in _packages(path)
        if not _installed(package)
    ]


def check() -> int:
    sys.path.insert(0, str(ROOT))
    import app

    failed = []

    def refused(name: str) -> None:
        failed.append(f"{name}: {sys.exc_info()[0].__name__}: {sys.exc_info()[1]}")  # pyright: ignore [reportOptionalMemberAccess]

    # BaseException: a module that exits at import has not been imported, and must not end the run as a success
    for module in pkgutil.walk_packages(app.__path__, "app.", onerror=refused):
        try:
            importlib.import_module(module.name)
        except BaseException:
            refused(module.name)
    failed.extend(_imported_elsewhere())
    for line in failed:
        print(line, file=sys.stderr)
    if failed:
        return 1

    # the application as the server builds it, which registers every route
    import app.webapp

    with tempfile.TemporaryDirectory() as directory:
        shutil.copy(ROOT / "config.yaml.example", pathlib.Path(directory) / "config.yaml")
        app.webapp.create_app(app_dir=directory, test_environment=True)
    print(f"imported every module of app, and built the application, from {sys.prefix}")
    return 0


def main() -> int:
    """`check`, where an exit from inside the application's own code, whatever its status, is a failure."""
    try:
        return check()
    except BaseException as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
