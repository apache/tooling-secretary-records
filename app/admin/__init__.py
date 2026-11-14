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

"""The admin pages: server status and the data browser, for the Admin role alone."""

import quart

from app import access
from app.models.role import Role

ADMIN = quart.Blueprint("admin", __name__, template_folder="templates", url_prefix="/admin")


@ADMIN.before_request
async def check_authorization() -> None:
    user = await access.user_or_abort()
    if not user.has_role(Role.ADMIN):
        quart.abort(403, description=access.role_refusal([Role.ADMIN], "see the admin pages"))


from . import routes
