# Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. (2022)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import tempfile
from unittest.mock import MagicMock

import tornado.httpserver
import tornado.testing
import tornado.web
import tornado.websocket

from streamlit import config
from streamlit.logger import get_logger
from streamlit.runtime.forward_msg_cache import ForwardMsgCache, populate_hash_if_needed
from streamlit.runtime.runtime_util import serialize_forward_msg
from streamlit.web.server.routes import ALLOWED_MESSAGE_ORIGINS
from streamlit.web.server.server import (
    ALLOWED_MESSAGE_ORIGIN_ENDPOINT,
    HEALTH_ENDPOINT,
    MESSAGE_ENDPOINT,
    AllowedMessageOriginsHandler,
    HealthHandler,
    MessageCacheHandler,
    StaticFileHandler,
)
from tests.streamlit.message_mocks import create_dataframe_msg
from tests.testutil import patch_config_options


class HealthHandlerTest(tornado.testing.AsyncHTTPTestCase):
    """Tests the _stcore/health endpoint"""

    def setUp(self):
        super(HealthHandlerTest, self).setUp()
        self._is_healthy = True

    async def is_healthy(self):
        return self._is_healthy, "ok"

    def get_app(self):
        return tornado.web.Application(
            [(rf"/{HEALTH_ENDPOINT}", HealthHandler, dict(callback=self.is_healthy))]
        )

    def test_health(self):
        response = self.fetch("/_stcore/health")
        self.assertEqual(200, response.code)
        self.assertEqual(b"ok", response.body)

        self._is_healthy = False
        response = self.fetch("/_stcore/health")
        self.assertEqual(503, response.code)

    @patch_config_options({"server.enableXsrfProtection": False})
    def test_health_without_csrf(self):
        response = self.fetch("/_stcore/health")
        self.assertEqual(200, response.code)
        self.assertEqual(b"ok", response.body)
        self.assertNotIn("Set-Cookie", response.headers)

    @patch_config_options({"server.enableXsrfProtection": True})
    def test_health_with_csrf(self):
        response = self.fetch("/_stcore/health")
        self.assertEqual(200, response.code)
        self.assertEqual(b"ok", response.body)
        self.assertIn("Set-Cookie", response.headers)

    def test_health_deprecated(self):
        with self.assertLogs("streamlit.web.server.routes") as logs:
            self.fetch("/healthz")
        self.assertEqual(
            logs.records[0].getMessage(),
            "Endpoint /healtz is deprecated. Please use /_stcore/health instead.",
        )


class MessageCacheHandlerTest(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self._cache = ForwardMsgCache()
        return tornado.web.Application(
            [(rf"/{MESSAGE_ENDPOINT}", MessageCacheHandler, dict(cache=self._cache))]
        )

    def test_message_cache(self):
        # Create a new ForwardMsg and cache it
        msg = create_dataframe_msg([1, 2, 3])
        msg_hash = populate_hash_if_needed(msg)
        self._cache.add_message(msg, MagicMock(), 0)

        # Cache hit
        response = self.fetch("/_stcore/message?hash=%s" % msg_hash)
        self.assertEqual(200, response.code)
        self.assertEqual(serialize_forward_msg(msg), response.body)

        # Cache misses
        self.assertEqual(404, self.fetch("/_stcore/message").code)
        self.assertEqual(404, self.fetch("/_stcore/message?id=non_existent").code)


class StaticFileHandlerTest(tornado.testing.AsyncHTTPTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmpfile = tempfile.NamedTemporaryFile(dir=self._tmpdir.name, delete=False)
        self._filename = os.path.basename(self._tmpfile.name)

        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()

        self._tmpdir.cleanup()

    def get_pages(self):
        return {"page1": "page_info1", "page2": "page_info2"}

    def get_app(self):
        return tornado.web.Application(
            [
                (
                    r"/(.*)",
                    StaticFileHandler,
                    {
                        "path": self._tmpdir.name,
                        "default_filename": self._filename,
                        "get_pages": self.get_pages,
                    },
                )
            ]
        )

    def test_parse_url_path_200(self):
        responses = [
            self.fetch("/"),
            self.fetch(f"/{self._filename}"),
            self.fetch("/page1/"),
            self.fetch(f"/page1/{self._filename}"),
            self.fetch("/page2/"),
            self.fetch(f"/page2/{self._filename}"),
        ]

        for r in responses:
            assert r.code == 200

    def test_parse_url_path_404(self):
        responses = [
            self.fetch("/nonexistent"),
            self.fetch("/page2/nonexistent"),
            self.fetch(f"/page3/{self._filename}"),
        ]

        for r in responses:
            assert r.code == 404


class AllowedMessageOriginsHandlerTest(tornado.testing.AsyncHTTPTestCase):
    def setUp(self):
        super(AllowedMessageOriginsHandlerTest, self).setUp()
        self._is_healthy = True

    async def is_healthy(self):
        return self._is_healthy, "ok"

    def get_app(self):
        return tornado.web.Application(
            [
                (
                    rf"/{ALLOWED_MESSAGE_ORIGIN_ENDPOINT}",
                    AllowedMessageOriginsHandler,
                    dict(callback=self.is_healthy),
                )
            ]
        )

    def test_allowed_message_origins(self):
        response = self.fetch("/_stcore/allowed-message-origins")
        self.assertEqual(200, response.code)
        self.assertEqual(
            {"allowedOrigins": ALLOWED_MESSAGE_ORIGINS}, json.loads(response.body)
        )

    # NOTE: Temporary tests to verify this endpoint can also act as a healthcheck
    # endpoint while we need it to. These tests are more or less copy-paste from the
    # HealthHandlerTest class above.
    def test_healthcheck_responsibilities(self):
        response = self.fetch("/_stcore/allowed-message-origins")
        self.assertEqual(200, response.code)

        self._is_healthy = False
        response = self.fetch("/_stcore/allowed-message-origins")
        self.assertEqual(503, response.code)

    @patch_config_options({"server.enableXsrfProtection": False})
    def test_healthcheck_responsibilities_without_csrf(self):
        response = self.fetch("/_stcore/allowed-message-origins")
        self.assertEqual(200, response.code)
        self.assertNotIn("Set-Cookie", response.headers)

    @patch_config_options({"server.enableXsrfProtection": True})
    def test_healthcheck_responsibilities_with_csrf(self):
        response = self.fetch("/_stcore/allowed-message-origins")
        self.assertEqual(200, response.code)
        self.assertIn("Set-Cookie", response.headers)
