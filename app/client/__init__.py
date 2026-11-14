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

"""The pages: server-rendered Jinja templates with Svelte islands, and the assets they load."""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

import quart

from app import admin

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_LOGGER = logging.getLogger(__name__)

CLIENT = quart.Blueprint(
    "client",
    __name__,
    static_folder="static/assets",
    static_url_path="/assets",
    template_folder="templates",
    url_prefix="/",
)

CLIENT.register_blueprint(admin.ADMIN)

# Vite writes every entry/chunk/asset it fingerprints into this subdirectory (see
# assetsDir/entryFileNames/chunkFileNames in assets/vite.config.js) - a build never reuses
# a filename for different content, so these can be cached effectively forever.
_HASHED_ASSET_PREFIX = f"{CLIENT.static_url_path}/bundled/"
_HASHED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"

# Everything else under /assets (vendor/* and manifest.json) is copied in verbatim, without a
# content hash in the filename, so a dependency bump serves the same URL with new content.
_VENDOR_ASSET_CACHE_CONTROL = "public, max-age=1800"

_MANIFEST: Mapping[str, Any] = {}
_MANIFEST_PATH = os.path.join(CLIENT.static_folder or "", "manifest.json")


def _load_manifest() -> None:
    global _MANIFEST

    try:
        with open(_MANIFEST_PATH) as content:
            _MANIFEST = json.load(content)
    except OSError:
        _LOGGER.warning("No asset manifest at '%s': run `make build`", _MANIFEST_PATH)


@CLIENT.after_request
async def _set_asset_cache_control(response: quart.Response) -> quart.Response:
    if quart.request.path.startswith(_HASHED_ASSET_PREFIX):
        response.headers["Cache-Control"] = _HASHED_ASSET_CACHE_CONTROL
    elif quart.request.path.startswith(f"{CLIENT.static_url_path}/"):
        response.headers["Cache-Control"] = _VENDOR_ASSET_CACHE_CONTROL
    return response


@CLIENT.before_app_serving
async def before_serving() -> None:
    _load_manifest()


@CLIENT.before_request
async def _reload_manifest_in_development() -> None:
    # `make dev` rebuilds the bundle as files change, under new hashed names. Re-reading a small
    # file per page is cheaper than a watcher, and only happens in development.
    if quart.current_app.config.get("DEBUG", False):
        _load_manifest()


@CLIENT.context_processor
def context_processor() -> dict[str, Any]:
    def asset(file_path: str) -> str:
        try:
            return f"/assets/{_MANIFEST[file_path]['file']}"
        # The two ways the manifest can fail to name a file: no entry at all, or an entry
        # that is not the shape vite writes.
        except (KeyError, TypeError):
            _LOGGER.debug("did not find asset '%s'", file_path)
            return f"/assets/{file_path}"

    def css_for_svelte_page(page: str) -> Iterator[str]:
        """Every stylesheet this page's bundle needs, each named once."""
        seen: set[str] = set()
        for css in _get_css_assets(f"js/pages/{page}.ts", set()):
            if css not in seen:
                seen.add(css)
                yield css

    def _get_css_assets(file_path: str, visited: set[str]) -> Iterator[str]:
        # a chunk reached twice is a walk that need not end: the graph may hold a cycle
        if file_path in visited:
            return
        visited.add(file_path)

        asset_info = _MANIFEST.get(file_path)
        if asset_info is not None:
            yield from asset_info.get("css", [])
            for imported_file in asset_info.get("imports", []):
                yield from _get_css_assets(imported_file, visited)

    return {"asset": asset, "css_for_svelte_page": css_for_svelte_page}


from . import handlers, icla, routes
