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

"""The JSON API the Svelte islands talk to. Every route is under /api and needs a session."""

import quart

from app import access

API = quart.Blueprint("api", __name__, url_prefix="/api")


@API.before_request
async def check_authentication() -> None:
    # Signed in, at least; each route then says which roles it wants with `require_role`.
    await access.user_or_abort()


# This blueprint grants no cross-origin access, and needs none: the app's own frontend is served
# from this origin. A session reaches it by cookie, which a browser does not send to a response
# granting `*` anyway.

from . import handlers
from .routes import *  # noqa: F403
