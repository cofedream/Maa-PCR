"""Bounded SDK transport; redirects and arbitrary hosts are prohibited."""
import asyncio
from urllib.parse import urlsplit
import requests

SDK_ROOT = "https://line1-sdk-center-login-sh.biligame.net/"
SDK_PATHS = {"/api/client/rsa", "/api/client/login"}


class AsyncResponse:
    def __init__(self, response):
        self.raw_response = response

    @property
    async def content(self):
        if len(self.raw_response.content) > 2 * 1024 * 1024:
            raise RuntimeError("oversize_sdk_response")
        return self.raw_response.content


async def post(url, data=None, headers=None, **kwargs):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "line1-sdk-center-login-sh.biligame.net" or parsed.path not in SDK_PATHS or parsed.query:
        raise RuntimeError("sdk_request_rejected")
    response = await asyncio.to_thread(requests.post, url, data=data, headers=headers, timeout=(5, 10), allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError("sdk_request_failed")
    return AsyncResponse(response)
