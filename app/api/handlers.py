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

"""Error handlers for the API blueprint: a client that asked for data gets text back, not a page."""

from typing import Any

import quart
from asfquart import auth
from werkzeug.exceptions import HTTPException

from . import API


@API.errorhandler(Exception)
async def handle_any_exception(error: Exception) -> Any:
    quart.current_app.logger.exception("Unhandled exception", exc_info=error)
    return "Internal server error", 500


@API.errorhandler(HTTPException)
async def handle_http_exception(error: HTTPException) -> Any:
    """`quart.abort(403)` and the like. Without a handler of its own an HTTPException falls
    through to `handle_any_exception` above and every `abort()` here would answer 500."""
    return error.description or str(error), error.code or 500


@API.errorhandler(auth.AuthenticationFailed)
async def handle_not_authenticated(error: auth.AuthenticationFailed) -> Any:
    """401 rather than a redirect to OAuth: a fetch cannot follow a sign-in page."""
    return error.message, error.errorcode
