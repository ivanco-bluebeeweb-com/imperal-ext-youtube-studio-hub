"""Small deterministic doubles for YouTube Studio Hub tests -- same
QueueHTTP/MockContext pattern as Google Drive Connector's tests/conftest.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from imperal_sdk.testing import MockContext, MockSecretStore


class Response:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self.body = {} if body is None else body
        self.headers = headers or {}

    def json(self):
        return self.body


class QueueHTTP:
    def __init__(self):
        self.responses = []
        self.calls = []

    def push(self, body=None, status=200, headers=None):
        self.responses.append(Response(status, body, headers))
        return self

    async def get(self, url, **kwargs):
        return await self._call("GET", url, kwargs)

    async def post(self, url, **kwargs):
        return await self._call("POST", url, kwargs)

    async def put(self, url, **kwargs):
        return await self._call("PUT", url, kwargs)

    async def patch(self, url, **kwargs):
        return await self._call("PATCH", url, kwargs)

    async def delete(self, url, **kwargs):
        return await self._call("DELETE", url, kwargs)

    async def request(self, method, url, **kwargs):
        return await self._call(method, url, kwargs)

    async def _call(self, method, url, kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        assert self.responses, f"Unexpected HTTP call: {method} {url}"
        return self.responses.pop(0)


@pytest.fixture
def ctx():
    context = MockContext(extension_id="youtube-studio-hub")
    context.http = QueueHTTP()
    context.secrets = MockSecretStore({
        "google_client_id": "client-id",
        "google_client_secret": "client-secret",
    })
    return context


@pytest.fixture
async def account(ctx):
    return await ctx.store.create("youtube_accounts", {
        "email": "creator@example.com",
        "display_name": "Creator",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "token_expires_at": "2099-01-01T00:00:00+00:00",
        "channel_ids": ["UCabc123"],
    })
