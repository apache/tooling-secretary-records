#!/usr/bin/env python3
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

"""Secretary Records for the Apache Software Foundation"""

import app.webapp

application = None


if __name__ == "__main__":
    import uvloop

    import app.config

    # install uvloop to avoid issue with SSL + hypercorn
    # see: https://github.com/pgjones/hypercorn/issues/261
    uvloop.install()

    application = app.webapp.create_app(development=True)
    server_config = app.config.get().server

    application.run(
        host=server_config.host,
        port=server_config.port,
        certfile=server_config.certfile,
        keyfile=server_config.keyfile,
        use_reloader=True,
        debug=True,
    )
else:
    application = app.webapp.create_app()
