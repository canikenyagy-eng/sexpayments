"""Shared testkit for cascade provider adapter tests.

Three adapters (LegacyCrypto, Swifty, BridgePay) all need the same fake httpx
plumbing to drive their HTTP code paths. Instead of copy-pasting ``_FakeClient``
into every test file, import these helpers:

    from tests.unit.cascade_adapter_testkit import (
        FakeRequest, FakeResponse, patch_httpx,
    )

    def test_thing():
        def responder(req: FakeRequest):
            return FakeResponse(200, {"ok": True})

        with patch_httpx(responder) as captured:
            ...
        # captured is a list of FakeClient instances; .calls has the requests.

Design choices:
  * One async ``request()`` method on the client — handles json/content/files/data
    keyword args the way base.request_signed sends them.
  * Captures bodies as bytes so adapters can assert on signed payloads.
  * Generic responder pattern: ``Callable[[FakeRequest], FakeResponse]`` lets
    each test decide what to return based on the URL/method/body.
"""
from __future__ import annotations

import json as _json_lib
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch


class FakeResponse:
    """Quacks like httpx.Response — just the bits the adapter base uses."""

    def __init__(
        self,
        status: int,
        json_data: Any = None,
        text: str = "",
    ):
        self.status_code = status
        self._json = json_data
        if text:
            self.text = text
        elif json_data is not None:
            self.text = _json_lib.dumps(json_data)
        else:
            self.text = ""

    def json(self):
        if self._json is None:
            raise ValueError("FakeResponse has no JSON body")
        return self._json


class FakeRequest:
    """Records one HTTP call the adapter made via httpx.AsyncClient.

    Inspectable from tests:
      * ``method`` / ``url`` / ``headers``
      * ``read()`` — body bytes (what request_signed will sign + transport)
      * ``form_data`` — multipart fields
      * ``files_present`` — True when the call carried files=...
    """

    def __init__(self, method: str, url: str, headers: Dict[str, str], body: bytes):
        self.method = method
        self.url = url
        self.headers = dict(headers)
        self._body = body
        self.form_data: Dict[str, Any] = {}
        self.files_present = False

    def read(self) -> bytes:
        return self._body


class FakeClient:
    """Drop-in for httpx.AsyncClient. Routes every call through ``responder``."""

    def __init__(
        self,
        responder: Callable[[FakeRequest], FakeResponse],
        base_url: str,
    ):
        self.responder = responder
        self.base_url = base_url
        self.calls: List[FakeRequest] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        json=None,
        content=None,
        headers=None,
        data=None,
        files=None,
        params=None,
    ):
        body_bytes = b""
        if content is not None:
            body_bytes = content if isinstance(content, bytes) else content.encode("utf-8")
        elif json is not None:
            body_bytes = _json_lib.dumps(json).encode("utf-8")
        elif files or data:
            body_bytes = b"<multipart>"
        req = FakeRequest(method.upper(), self._url(path), headers or {}, body_bytes)
        if data:
            req.form_data = dict(data)
        if files:
            req.files_present = True
        self.calls.append(req)
        return self.responder(req)


def make_httpx_patch(
    responder: Callable[[FakeRequest], FakeResponse],
    target: str = "app.modules.cascading.integrations.base.httpx.AsyncClient",
):
    """Lower-level API: returns ``(patcher, captured)``.

    Use this when the test wants to attach assertions BEFORE the ``with``
    block opens — same shape as the original ``_patch_httpx`` helpers
    embedded in each adapter test file.
    """
    captured: List[FakeClient] = []

    class _Factory:
        def __call__(self, *args, **kwargs):
            client = FakeClient(responder, kwargs.get("base_url", ""))
            captured.append(client)
            return client

    return patch(target, _Factory()), captured


@contextmanager
def patch_httpx(
    responder: Callable[[FakeRequest], FakeResponse],
    target: str = "app.modules.cascading.integrations.base.httpx.AsyncClient",
):
    """Patch httpx.AsyncClient with a FakeClient factory.

    The default ``target`` patches the base module — all adapters route through
    ``base.request_signed`` so we only need one patch. Yields the list of
    captured FakeClient instances; ``.calls`` on each one carries the actual
    requests in order.
    """
    patcher, captured = make_httpx_patch(responder, target=target)
    with patcher:
        yield captured
