"""Fixed Bilibili login and read-only game wire protocol.

Derived from AutoPCR apiclient/sessionmgr/sdkclient. Credentials and session
headers exist only in this process. No logger, token cache, DB updater or API
business module is imported.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import secrets
from urllib.parse import urlsplit

from Crypto.Cipher import AES
import msgpack
import requests

from ..model.modelbase import Response
from ..model.sdkrequests import (SourceIniIndexRequest, SourceIniGetMaintenanceStatusRequest,
    ToolSdkLoginRequest, CheckGameStartRequest)
from ..model.requests import LoadIndexRequest, HomeIndexRequest, DailyTaskTopRequest, UnitRoleGachaIndexRequest, ShopItemListRequest
from ..sdk.bsgamesdk import login1

ALLOWED_REQUESTS = (SourceIniIndexRequest, SourceIniGetMaintenanceStatusRequest, ToolSdkLoginRequest,
    CheckGameStartRequest, LoadIndexRequest, HomeIndexRequest, DailyTaskTopRequest, UnitRoleGachaIndexRequest, ShopItemListRequest)
ALLOWED_PATHS = frozenset(cls().url for cls in ALLOWED_REQUESTS)


class SyncFailure(RuntimeError):
    """Only this fixed code is safe to display; never display upstream errors."""


class ReadOnlyClient:
    def __init__(self, username, password, *, content_version):
        self.username, self.password = username, password
        self.content_version = str(content_version)
        self.viewer_id = 0
        self.root = "https://l3-prod-all-gs-gzlj.bilibiligame.net/"
        self.headers = {"Accept-Encoding": "gzip", "User-Agent": "Dalvik/2.1.0 (Linux, U, Android 5.1.1, PCRT00 Build/LMY48Z)",
            "X-Unity-Version": "2021.3.20f1c1", "APP-VER": "11.7.2", "BATTLE-LOGIC-VERSION": "4", "BUNDLE-VER": "",
            "DEVICE": "2", "DEVICE-NAME": "OPPO PCRT00", "EXCEL-VER": "1.0.0", "GRAPHICS-DEVICE-NAME": "Adreno (TM) 640",
            "IP-ADDRESS": "10.0.2.15", "KEYCHAIN": "", "LOCALE": "CN", "PLATFORM-OS-VERSION": "Android OS 5.1.1 / API-22",
            "REGION-CODE": "", "RES-VER": "10002200", "SHORT-UDID": "0", "RES-KEY": "ab00a0a6dd915a052a2ef7fd649083e5",
            "PLATFORM": "2", "PLATFORM-ID": "2", "CHANNEL-ID": "1", "DEVICE-ID": hashlib.md5(username.encode("utf-8")).hexdigest()}

    @staticmethod
    def encrypt(data, key):
        n = 16 - len(data) % 16
        return AES.new(key, AES.MODE_CBC, b"7Fk9Lm3Np8Qr4Sv2").encrypt(data + bytes([n]) * n) + key

    @staticmethod
    def unpack(data):
        decoded = base64.b64decode(data, validate=True)
        if len(decoded) < 48 or (len(decoded) - 32) % 16:
            raise SyncFailure("invalid_response")
        plain = AES.new(decoded[-32:], AES.MODE_CBC, b"7Fk9Lm3Np8Qr4Sv2").decrypt(decoded[:-32])
        pad = plain[-1]
        if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
            raise SyncFailure("invalid_response")
        return msgpack.unpackb(plain[:-pad], strict_map_key=False)

    @staticmethod
    def no_null_key(obj):
        if isinstance(obj, dict):
            if None in obj and all(isinstance(key, int) or key is None for key in obj):
                return [ReadOnlyClient.no_null_key(value) for key, value in sorted((k, v) for k, v in obj.items() if k is not None)]
            return {key: ReadOnlyClient.no_null_key(value) for key, value in obj.items() if key is not None}
        if isinstance(obj, list):
            return [ReadOnlyClient.no_null_key(value) for value in obj]
        return obj

    @staticmethod
    def validate_root(root):
        parsed = urlsplit(root)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".bilibiligame.net") or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise SyncFailure("server_rejected")
        return root.rstrip("/") + "/"

    async def request(self, request):
        # Exact class and independently checked endpoint: no subclass or custom
        # URL can smuggle a consuming request through the read-only transport.
        if type(request) not in ALLOWED_REQUESTS or request.url not in ALLOWED_PATHS:
            raise SyncFailure("request_rejected")
        key = secrets.token_hex(16).encode("ascii")
        request.viewer_id = base64.b64encode(self.encrypt(str(self.viewer_id).encode("utf-8"), key)).decode("ascii") if request.crypted else str(self.viewer_id)
        body = self.encrypt(msgpack.packb(request.dict(by_alias=True), use_bin_type=False), key) if request.crypted else request.json(by_alias=True).encode("utf-8")
        try:
            response = await asyncio.to_thread(requests.post, self.validate_root(self.root) + request.url,
                data=body, headers=self.headers, timeout=(5, 10), allow_redirects=False)
            if response.status_code != 200 or len(response.content) > 16 * 1024 * 1024:
                raise SyncFailure("network_failed")
            raw = self.unpack(response.content) if request.crypted else json.loads(response.content)
            raw = self.no_null_key(raw)
            cls = request.__class__.__orig_bases__[0].__args__[0]
            parsed = Response[cls].parse_obj(raw)
        except SyncFailure:
            raise
        except Exception:
            raise SyncFailure("network_or_protocol_failed") from None
        header = parsed.data_headers
        if not header or not parsed.data:
            raise SyncFailure("invalid_response")
        if isinstance(request, SourceIniGetMaintenanceStatusRequest) and raw.get("data_headers", {}).get("store_url"):
            match = re.search(r"gzlj_(\d+\.\d+\.\d+)", raw["data_headers"]["store_url"])
            if not match:
                raise SyncFailure("protocol_update_required")
            self.headers["APP-VER"] = match.group(1)
            raise SyncFailure("app_version_changed")
        if parsed.data.server_error or header.result_code not in (0, 1):
            raise SyncFailure("game_request_failed")
        if header.sid:
            self.headers["SID"] = hashlib.md5((header.sid + "c!SID!n").encode("utf-8")).hexdigest()
        if header.request_id:
            self.headers["REQUEST-ID"] = header.request_id
        if header.viewer_id:
            self.viewer_id = int(header.viewer_id)
        return parsed.data

    async def collect(self):
        try:
            login_response = await login1(self.username, self.password)
            if login_response.get("code") == 200000:
                raise SyncFailure("needs_verification")
            if login_response.get("code") != 0 or not login_response.get("uid") or not login_response.get("access_key"):
                raise SyncFailure("sdk_login_failed")
            servers = await self.request(SourceIniIndexRequest())
            if not servers.server:
                raise SyncFailure("server_rejected")
            self.root = self.validate_root("https://" + servers.server[0].replace("\t", ""))
            for attempt in range(2):
                try:
                    maintenance = await self.request(SourceIniGetMaintenanceStatusRequest())
                    break
                except SyncFailure as error:
                    if str(error) != "app_version_changed" or attempt:
                        raise
            if str(maintenance.required_manifest_ver) != self.content_version:
                raise SyncFailure("content_update_required")
            self.headers["MANIFEST-VER"] = maintenance.required_manifest_ver
            sdk = await self.request(ToolSdkLoginRequest(uid=str(login_response["uid"]), access_key=login_response["access_key"], platform="2", channel_id="1"))
            login_response.clear()
            if sdk.is_risk:
                raise SyncFailure("needs_verification")
            started = await self.request(CheckGameStartRequest(apptype=0, campaign_data="", campaign_user=random.randint(0, 100000) & ~1))
            if not started.now_tutorial:
                raise SyncFailure("tutorial_incomplete")
            load = await self.request(LoadIndexRequest(carrier="OPPO"))
            home = await self.request(HomeIndexRequest(message_id=1, gold_history=0, is_first=1, tips_id_list=[]))
            if any(q.quest_id == 11008001 and q.result_type == 2 for q in (home.quest_list or [])):
                await self.request(DailyTaskTopRequest(setting_alchemy_count=1, is_check_by_term_normal_gacha=0))
            await self.request(UnitRoleGachaIndexRequest())
            shop = await self.request(ShopItemListRequest())
            return load, home, shop
        except SyncFailure:
            raise
        except Exception:
            raise SyncFailure("sync_failed") from None
        finally:
            self.password = ""
            self.username = ""
            self.headers.clear()
