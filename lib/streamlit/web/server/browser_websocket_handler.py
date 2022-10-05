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

import base64
import binascii
import json
from typing import Any, Awaitable, Dict, List, Optional, Union

import tornado.concurrent
import tornado.locks
import tornado.netutil
import tornado.web
import tornado.websocket
from tornado.websocket import WebSocketHandler
from typing_extensions import Final

from streamlit import config
from streamlit.logger import get_logger
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from streamlit.runtime import Runtime, SessionClient, SessionClientDisconnectedError
from streamlit.runtime.runtime_util import serialize_forward_msg
from streamlit.web.server.server_util import is_url_from_allowed_origins

_LOGGER: Final = get_logger(__name__)


class BrowserWebSocketHandler(WebSocketHandler, SessionClient):
    """Handles a WebSocket connection from the browser"""

    def initialize(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._session_id: Optional[str] = None
        # The XSRF cookie is normally set when xsrf_form_html is used, but in a
        # pure-Javascript application that does not use any regular forms we just
        # need to read the self.xsrf_token manually to set the cookie as a side
        # effect. See https://www.tornadoweb.org/en/stable/guide/security.html#cross-site-request-forgery-protection
        # for more details.
        if config.get_option("server.enableXsrfProtection"):
            _ = self.xsrf_token

    def check_origin(self, origin: str) -> bool:
        """Set up CORS."""
        return super().check_origin(origin) or is_url_from_allowed_origins(origin)

    def write_forward_msg(self, msg: ForwardMsg) -> None:
        """Send a ForwardMsg to the browser."""
        try:
            self.write_message(serialize_forward_msg(msg), binary=True)
        except tornado.websocket.WebSocketClosedError as e:
            raise SessionClientDisconnectedError from e

    def select_subprotocol(self, subprotocols: List[str]) -> Optional[str]:
        """Return the first subprotocol in the given list.

        This method is used by Tornado to select a protocol when the
        Sec-WebSocket-Protocol header is set in an HTTP Upgrade request.

        NOTE: We repurpose the Sec-WebSocket-Protocol header here in a slightly
        unfortunate (but necessary) way. The browser WebSocket API doesn't allow us to
        set arbitrary HTTP headers, and this header is the only one where we have the
        ability to set it to arbitrary values, so we use it to pass an auth token from
        client to server as the *second* value in the list.

        The reason why the auth token is set as the second value is that, when
        Sec-WebSocket-Protocol is set, many clients expect the server to respond with a
        selected subprotocol to use. We don't want that reply to be the auth token, so
        we just hard-code it to "streamlit".
        """
        if subprotocols:
            return subprotocols[0]

        return None

    def open(self, *args, **kwargs) -> Optional[Awaitable[None]]:
        # Extract user info from the X-Streamlit-User header
        is_public_cloud_app = False

        try:
            header_content = self.request.headers["X-Streamlit-User"]
            payload = base64.b64decode(header_content)
            user_obj = json.loads(payload)
            email = user_obj["email"]
            is_public_cloud_app = user_obj["isPublicCloudApp"]
        except (KeyError, binascii.Error, json.decoder.JSONDecodeError):
            email = "test@localhost.com"

        user_info: Dict[str, Optional[str]] = dict()
        if is_public_cloud_app:
            user_info["email"] = None
        else:
            user_info["email"] = email

        self._session_id = self._runtime.create_session(self, user_info)
        return None

    def on_close(self) -> None:
        if not self._session_id:
            return
        self._runtime.close_session(self._session_id)
        self._session_id = None

    def get_compression_options(self) -> Optional[Dict[Any, Any]]:
        """Enable WebSocket compression.

        Returning an empty dict enables websocket compression. Returning
        None disables it.

        (See the docstring in the parent class.)
        """
        if config.get_option("server.enableWebsocketCompression"):
            return {}
        return None

    def on_message(self, payload: Union[str, bytes]) -> None:
        if not self._session_id:
            return

        try:
            if isinstance(payload, str):
                # Sanity check. (The frontend should only be sending us bytes;
                # Protobuf.ParseFromString does not accept str input.)
                raise RuntimeError(
                    "WebSocket received an unexpected `str` message. "
                    "(We expect `bytes` only.)"
                )

            msg = BackMsg()
            msg.ParseFromString(payload)
            _LOGGER.debug("Received the following back message:\n%s", msg)

        except Exception as ex:
            _LOGGER.error(ex)
            self._runtime.handle_backmsg_deserialization_exception(self._session_id, ex)
            return

        # "debug_disconnect_websocket" and "debug_shutdown_runtime" are special
        # developmentMode-only messages used in e2e tests to test reconnect handling and
        # disabling widgets.
        if msg.WhichOneof("type") == "debug_disconnect_websocket":
            if config.get_option("global.developmentMode"):
                self.close()
            else:
                _LOGGER.warning(
                    "Client tried to disconnect websocket when not in development mode."
                )
        elif msg.WhichOneof("type") == "debug_shutdown_runtime":
            if config.get_option("global.developmentMode"):
                self._runtime.stop()
            else:
                _LOGGER.warning(
                    "Client tried to shut down runtime when not in development mode."
                )
        else:
            # AppSession handles all other BackMsg types.
            self._runtime.handle_backmsg(self._session_id, msg)

    async def get(self, subpath, *args: Any, **kwargs: Any) -> None:
        if not subpath:
            _LOGGER.warning(
                "Endpoint /stream is deprecated. Please use /_stcore/stream instead."
            )

        return await super().get(*args, **kwargs)
