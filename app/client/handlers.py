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

"""Error pages for the CLIENT blueprint: a browser that asked for a page gets a page back."""

from typing import Any

import quart
from werkzeug.exceptions import HTTPException

from . import CLIENT


@CLIENT.errorhandler(403)
async def handle_forbidden(error: HTTPException) -> Any:
    return await quart.render_template("error.html", status_code=403, message=error.description), 403


@CLIENT.errorhandler(404)
async def handle_not_found(error: HTTPException) -> Any:
    return await quart.render_template("error.html", status_code=404, message=error.description), 404
