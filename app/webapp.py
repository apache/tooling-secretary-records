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

"""The web application: what `create_app` builds, and everything it needs to build it."""

import logging
import logging.handlers
import os
import pathlib
from typing import Any

import asfquart
import asfquart.base as base
import asfquart.generics
import quart_schema

from app import access, basic_auth, db, security_headers
from app.config import AppConfig

# The plain oauth endpoints, not the OIDC ones asfquart now defaults to, as BAT does. Once the
# board approves it, these become the OIDC capable endpoints, which support 2FA: the same hosts
# and query strings, with the paths auth-oidc and token-oidc in place of auth and token.
asfquart.generics.OAUTH_URL_INIT = "https://oauth.apache.org/auth?state=%s&redirect_uri=%s"
asfquart.generics.OAUTH_URL_CALLBACK = "https://oauth.apache.org/token?code=%s"


def _ensure_state_dir(app_config: AppConfig) -> None:
    state_dir_path = app_config.base.state_dir_path
    if not state_dir_path.exists():
        state_dir_path.mkdir(parents=True)
    elif not state_dir_path.is_dir():
        raise NotADirectoryError(f"State directory '{state_dir_path}' is not a directory")


def _register_routes(quart_app: base.QuartApp) -> None:
    from app import api, client

    quart_schema.QuartSchema(
        quart_app,
        openapi_path=None,
        swagger_ui_path=None,
        convert_casing=True,
    )

    quart_app.register_blueprint(api.API)
    quart_app.register_blueprint(client.CLIENT)


def _setup_context(quart_app: base.QuartApp, app_config: AppConfig) -> None:
    @quart_app.context_processor
    async def app_context() -> dict[str, Any]:
        return {
            "current_user": await access.current_user(),
            "dev_mode": app_config.base.dev_mode,
        }


def _setup_logging(quart_app: base.QuartApp, app_config: AppConfig, test_environment: bool) -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(process)d] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    log_file = app_config.logging.file_path
    if log_file is not None and log_file.parent.exists():
        file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=5)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Not under pytest: the handler goes on the root logger and the app is built once per test,
    # so attaching one each time makes every record print once per app built so far. The logging
    # plugin captures records itself and reports them for a failing test.
    if not test_environment:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    debug = app_config.logging.debug
    if isinstance(debug, list):
        # a name starting with '.' is relative to the app package
        for module in debug:
            module_name = f"app{module}" if module.startswith(".") else module
            quart_app.logger.info("Set debug logging for module '%s'", module_name)
            logging.getLogger(module_name).setLevel(logging.DEBUG)
    elif debug:
        logging.getLogger("app").setLevel(logging.DEBUG)


def create_app(
    app_dir: str | None = None, cfg_file: str | None = None, development: bool = False, test_environment: bool = False
) -> base.QuartApp:
    from app import config

    # load the application config first to determine the state directory
    app_path = pathlib.Path(app_dir or os.getcwd())
    app_config = config.load_app_config(app_path / (cfg_file or asfquart.base.CONFIG_FNAME))

    if test_environment:
        # when running tests, no need to store the app secret
        token_file = None
    else:
        _ensure_state_dir(app_config)
        token_file = str((app_config.base.state_dir_path / "apptoken.txt").absolute())

    quart_app = asfquart.construct("secretary_records", app_dir, cfg_file, token_file)
    basic_auth.install()

    # asfquart defaults the session cookie to SameSite=Strict, which makes the browser withhold
    # an existing, valid session cookie whenever a link into the app is followed from another
    # site - webmail, most often, which is where a link to a work item will come from - and the
    # reader is sent through OAuth again. Lax still withholds it on a cross-site POST, which is
    # the CSRF risk, and allows it on a top-level GET navigation like this one.
    quart_app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    if development:
        quart_app.config["DEBUG"] = True
        quart_app.config["TEMPLATES_AUTO_RELOAD"] = True

    config.setup_app_config(quart_app, app_config)
    _setup_logging(quart_app, app_config, test_environment)
    db.init_database(quart_app)
    security_headers.setup_security_headers(quart_app)
    _register_routes(quart_app)
    _setup_context(quart_app, app_config)

    @quart_app.after_serving
    async def shutdown() -> None:
        quart_app.logger.info("Shutting down app...")
        quart_app.background_tasks.clear()

    return quart_app
