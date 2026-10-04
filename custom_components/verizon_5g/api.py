"""Local ASK-NCM1100 API. No gateway JavaScript is executed."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import ipaddress
import json
import math
import re
from time import monotonic

import aiohttp


class GatewayError(Exception):
    """Safe-to-log gateway error (never includes response bodies)."""


class GatewayAuthError(GatewayError):
    """Rejected credentials or challenge response."""


class GatewayBusyError(GatewayError):
    """Temporary lockout or exhausted sessions."""


class UnsupportedGatewayError(GatewayError):
    """Unverified model."""


class SessionExpired(GatewayError):
    """An authenticated endpoint returned session metadata."""


def normalize_host(value: str) -> str:
    """Accept a host/IP only; never credentials, paths or query strings."""
    value = value.strip().lower()
    if value.startswith("https://"):
        value = value[8:].rstrip("/")
    if not value or any(char in value for char in "/@?#\\ \t\n\r"):
        raise ValueError("Enter a hostname or IP address")
    try:
        address = ipaddress.ip_address(value.strip("[]"))
        return f"[{address}]" if address.version == 6 else str(address)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value):
            raise ValueError("Invalid hostname") from None
        return value


def arc_hash(text: str) -> str:
    """Firmware ArcMD5: SHA512 of lower-case MD5 hex, low UTF-16 bytes."""
    raw = text.encode("utf-16-le", errors="surrogatepass")[::2]
    md5hex = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    return hashlib.sha512(md5hex.encode("ascii")).hexdigest()


def login_fields(password: str, token: str) -> dict[str, str]:
    return {
        "luci_username": arc_hash("admin"),
        "luci_password": hashlib.sha512((token + arc_hash(password)).encode("ascii")).hexdigest(),
        "luci_token": token,
        "luci_view": "Desktop",
        "luci_keep_login": "0",
    }


_CALL = re.compile(r"^[ \t]*add(ROD|Cfg)\((.*?)\);", re.M | re.S)
_WANTED = {
    "hardware_model", "router_version", "hardware_version", "cgi_wan_mac",
    "get_wan4_ip", "cgi_wan_ip6_addr", "uptime", "wan_state", "led_info",
    "wwan_status_list", "wan_phy_status", "wan_disable", "modem_wan_enable",
    "cellur_wan_ipv4", "cellur_wan_ipv6", "wan_dhcp4_addr",
}


def parse_values(body: str) -> dict:
    """Read whitelisted literal calls only; skip commented and unrelated fields."""
    values = {}
    for match in _CALL.finditer(body):
        # Check key before parsing to avoid retaining identifiers or configuration secrets.
        key = re.match(r'\s*["\']([^"\']+)["\']\s*,', match[2])
        if not key or key[1] not in _WANTED:
            continue
        try:
            args = ast.literal_eval("(" + match[2] + ")")
        except (ValueError, SyntaxError, TypeError, RecursionError):
            raise GatewayError("Unexpected gateway data format") from None
        index = 1 if match[1] == "ROD" else 2
        if not isinstance(args, tuple) or len(args) != index + 1:
            raise GatewayError("Unexpected gateway field format")
        values[args[0]] = args[index]
    return values


def ipv4_status(v: dict) -> str:
    """Mirror supplied firmware's non-dual-WAN IPv4 status logic."""
    if str(v.get("wan_disable", "")) == "1" or str(v["modem_wan_enable"]) == "0":
        return "Disabled"
    physical_up = any(isinstance(row, (list, tuple)) and len(row) > 1 and row[1] == "Up"
                      for row in v["wan_phy_status"])
    link_present = physical_up or v["cellur_wan_ipv4"] == "1" or v["cellur_wan_ipv6"] == "1"
    if v["wan_state"] != "WANDOWN" and v["wan_dhcp4_addr"] not in ("", "0.0.0.0", "-"):
        return "Connected"
    return "Connecting..." if link_present else "Disconnected"


def signal(value) -> float | None:
    """Zero or placeholder values are not usable radio measurements."""
    try:
        number = float(value)
        return number if math.isfinite(number) and number != 0 else None
    except (TypeError, ValueError):
        return None


def make_snapshot(v: dict) -> dict:
    required = {"hardware_model", "cgi_wan_mac", "get_wan4_ip", "wan_state",
                "modem_wan_enable", "cellur_wan_ipv4", "cellur_wan_ipv6",
                "wan_dhcp4_addr", "wan_phy_status", "wwan_status_list"}
    if not required.issubset(v):
        raise GatewayError("Required gateway fields are missing")
    if v["hardware_model"] != "ASK-NCM1100":
        raise UnsupportedGatewayError("Only ASK-NCM1100 firmware has been verified")
    modem = v["wwan_status_list"]
    if not isinstance(modem, list) or len(modem) < 9 or not isinstance(v["wan_phy_status"], list):
        raise GatewayError("Unexpected modem or link status format")
    mac = str(v["cgi_wan_mac"]).lower()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
        raise GatewayError("Gateway identity is missing")
    clean = lambda x: None if x in (None, "", "-", "(null)") else str(x)
    try:
        uptime = int(v["uptime"]) if "uptime" in v else None
    except (ValueError, TypeError):
        uptime = None
    return {
        "mac": mac, "model": v["hardware_model"],
        "firmware": clean(v.get("router_version")), "hardware": clean(v.get("hardware_version")),
        "ipv4_status": ipv4_status(v), "wan_ipv4": clean(v["get_wan4_ip"]),
        "wan_ipv6": clean(v.get("cgi_wan_ip6_addr")), "wan_type": clean(v["wan_state"]),
        "technology": clean(modem[4]), "signal_4g": signal(modem[5]), "signal_5g": signal(modem[6]),
        "sim_status": {"Valid": "Ready", "None": "Absent", "PIN required": "PIN Required",
                       "PUK required": "PUK Required"}.get(modem[7], "Unknown"),
        "roaming": clean(modem[8]), "modem_firmware": clean(modem[3]),
        "uptime": uptime, "led_status": clean(v.get("led_info")),
    }


class GatewayClient:
    """One private cookie jar per gateway, serialized polling and bounded login."""

    def __init__(self, host: str, password: str, verify_ssl: bool = False):
        self.base_url = "https://" + normalize_host(host)
        self._password = password
        self._verify_ssl = verify_ssl
        self._session = None
        self._lock = asyncio.Lock()
        self._logged_in = False
        self._auth_failed = False
        self._retry_after = 0.0

    async def _request(self, path: str, data: dict | None = None) -> tuple[int, str]:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True),  # Gateway is reached by IP.
                timeout=aiohttp.ClientTimeout(total=15), trust_env=False,
                headers={"Referer": self.base_url + "/", "Accept": "application/json, text/plain, */*"},
            )
        try:
            async with self._session.request(
                "GET" if data is None else "POST", self.base_url + path,
                data=data, ssl=self._verify_ssl, allow_redirects=False,
            ) as response:
                raw = bytearray()
                async for part in response.content.iter_chunked(16384):
                    raw.extend(part)
                    if len(raw) > 1024 * 1024:
                        raise GatewayError("Gateway response exceeded size limit")
                return response.status, raw.decode("utf-8", errors="replace")
        except (aiohttp.ClientError, TimeoutError):
            raise GatewayError("Cannot communicate with the local gateway") from None

    @staticmethod
    def _json(body: str) -> dict:
        try:
            value = json.loads(body)
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    async def _login(self):
        if self._auth_failed:
            raise GatewayAuthError("Authentication rejected; re-enter credentials")
        if monotonic() < self._retry_after:
            raise GatewayBusyError("Waiting before retrying gateway login")
        status, body = await self._request("/loginStatus.cgi")
        challenge = self._json(body)
        token = challenge.get("loginToken")
        if status != 200 or not isinstance(token, str) or not re.fullmatch(r"[0-9a-fA-F]{16,256}", token):
            raise GatewayError("Gateway did not return a supported login challenge")
        # Set before POST: even a timeout must not lead to repeated rapid login attempts.
        self._retry_after = monotonic() + 60
        status, body = await self._request("/login.cgi", login_fields(self._password, token))
        if status not in (200, 302, 303):
            failure = self._json(body)
            if failure.get("flag") in (2, 7, "2", "7") or str(failure.get("timeout", "0")) != "0":
                try:
                    wait = max(300, int(failure.get("timeout", 0)))
                except (ValueError, TypeError):
                    wait = 300
                self._retry_after = monotonic() + wait
                raise GatewayBusyError("Gateway login is temporarily locked or sessions are full")
            if status == 403:
                self._auth_failed = True
                raise GatewayAuthError("Gateway rejected the login")
            raise GatewayError("Gateway login request failed")
        self._logged_in = True

    async def _read_values(self, path: str) -> dict:
        status, body = await self._request(path)
        if status in (401, 403, 302, 303) or (
            body.lstrip().startswith("{") and any(k in self._json(body) for k in ("flag", "islogin"))
        ):
            raise SessionExpired("Gateway session expired")
        if status != 200:
            raise GatewayError("Gateway telemetry request failed")
        values = parse_values(body)
        if not values and path != "/cgi/cgi_basic.js":
            raise GatewayError("Gateway did not return supported telemetry")
        if path == "/cgi/cgi_basic.js" and not re.search(r"^[ \t]*add(?:Cfg|ROD)\(", body, re.M):
            raise GatewayError("Gateway did not return basic configuration data")
        return values

    async def async_fetch(self) -> dict:
        async with self._lock:
            if self._auth_failed:
                raise GatewayAuthError("Authentication rejected; re-enter credentials")
            logged_in_now = False
            if not self._logged_in:
                await self._login()
                logged_in_now = True
            for attempt in range(2):
                try:
                    # Basic provides WAN enable settings omitted/commented in status on some firmware.
                    values = await self._read_values("/cgi/cgi_basic.js")
                    values.update(await self._read_values("/cgi/cgi_status.js"))
                    snapshot = make_snapshot(values)
                    self._retry_after = 0.0
                    return snapshot
                except SessionExpired:
                    self._logged_in = False
                    if logged_in_now or attempt:
                        self._auth_failed = True
                        raise GatewayAuthError("Login did not establish an authenticated telemetry session") from None
                    if self._session:
                        self._session.cookie_jar.clear()
                    await self._login()
                    logged_in_now = True
            raise GatewayError("Unable to refresh telemetry")

    async def async_close(self):
        """Best-effort logout of only our own session, then release sockets."""
        if self._session is None:
            return
        try:
            if self._logged_in:
                async with asyncio.timeout(5):
                    code, body = await self._request("/loginStatus.cgi")
                    token = self._json(body).get("token")
                    if code == 200 and isinstance(token, str) and token:
                        await self._request("/logout.cgi", {"token": token})
        except (GatewayError, TimeoutError):
            pass
        finally:
            await self._session.close()
            self._session = None
            self._logged_in = False
