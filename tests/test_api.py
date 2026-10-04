"""Offline protocol tests using synthetic data, never real credentials."""
import asyncio
import hashlib
import importlib.util
from pathlib import Path
import unittest

from aiohttp import web

spec = importlib.util.spec_from_file_location("gateway_api", Path(__file__).resolve().parents[1] / "custom_components/verizon_5g/api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def fixture(**changes):
    values = {
        "hardware_model": "ASK-NCM1100", "router_version": "3.6.0.5",
        "hardware_version": "6", "cgi_wan_mac": "02:00:00:00:00:01",
        "get_wan4_ip": "192.0.2.10", "wan_dhcp4_addr": "192.0.2.10",
        "wan_state": "CELLULARWAN", "modem_wan_enable": "1",
        "cellur_wan_ipv4": "1", "cellur_wan_ipv6": "1",
        "wan_phy_status": [["ETHWAN", "Down"], ["MOCAWAN", "Down"]],
        "wwan_status_list": ["PRIVATE", "PRIVATE", "PRIVATE", "modem-1", "5G", "0.0", "-101.2", "Valid", ""],
        "uptime": "1234",
    }
    values.update(changes)
    cfg = {"wan_dhcp4_addr", "modem_wan_enable", "cellur_wan_ipv4", "cellur_wan_ipv6", "wan_disable"}
    result = []
    for k,v in values.items():
        if k in cfg:
            result.append(f'addCfg("{k}", "unused-config-token", {v!r});')
        else:
            result.append(f'addROD("{k}", {v!r});')
    result.append('//addCfg("wan_disable", "ignored", "1");')
    return '\n'.join(result)


class ParsingTests(unittest.TestCase):
    def test_ascii_hash_formula(self):
        text = "synthetic-username"
        expected = hashlib.sha512(hashlib.md5(text.encode("ascii")).hexdigest().encode("ascii")).hexdigest()
        self.assertEqual(api.arc_hash(text), expected)

    def test_low_utf16_byte_hash(self):
        raw = b'\xe9\x3d\x00'  # e-acute and surrogate pair for U+1F600
        expected = hashlib.sha512(hashlib.md5(raw).hexdigest().encode()).hexdigest()
        self.assertEqual(api.arc_hash('\u00e9\U0001f600'), expected)

    def test_snapshot_and_privacy(self):
        result = api.make_snapshot(api.parse_values(fixture()))
        self.assertEqual(result['ipv4_status'], 'Connected')
        self.assertEqual(result['signal_5g'], -101.2)
        self.assertIsNone(result['signal_4g'])
        self.assertEqual(result['sim_status'], 'Ready')
        self.assertNotIn('PRIVATE', str(result))

    def test_status_transitions_and_stale_address(self):
        cases = [
            ({"wan_disable": "1"}, "Disabled"),
            ({"modem_wan_enable": "0"}, "Disabled"),
            ({"wan_dhcp4_addr": "0.0.0.0"}, "Connecting..."),
            ({"wan_state": "WANDOWN"}, "Connecting..."),
            ({"wan_state": "WANDOWN", "cellur_wan_ipv4": "0", "cellur_wan_ipv6": "0"}, "Disconnected"),
            ({"wan_dhcp4_addr": "-", "cellur_wan_ipv4": "0", "cellur_wan_ipv6": "0"}, "Disconnected"),
            ({"wan_state": "WANDOWN", "cellur_wan_ipv4": "0", "cellur_wan_ipv6": "0", "wan_phy_status": [["ETHWAN", "Up"]]}, "Connecting..."),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertEqual(api.make_snapshot(api.parse_values(fixture(**changes)))['ipv4_status'], expected)

    def test_no_code_execution(self):
        with self.assertRaises(api.GatewayError):
            api.parse_values('addROD("get_wan4_ip", __import__("os").getcwd());')

    def test_missing_field_and_wrong_model(self):
        with self.assertRaises(api.GatewayError):
            api.make_snapshot({})
        with self.assertRaises(api.UnsupportedGatewayError):
            api.make_snapshot(api.parse_values(fixture(hardware_model='ASK-NCM1100E')))

    def test_signal_missing(self):
        for value in ('', '-', '(null)', '0.0', 'nan', 'inf', None):
            self.assertIsNone(api.signal(value))

    def test_host_validation(self):
        self.assertEqual(api.normalize_host('https://192.0.2.1/'), '192.0.2.1')
        self.assertEqual(api.normalize_host('::1'), '[::1]')
        for host in ('https://user:password@host', 'host/path', 'host:1234', ''):
            with self.assertRaises(ValueError):
                api.normalize_host(host)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.posts = 0
        self.logouts = 0
        self.reject = False
        self.busy = False
        self.fail_telemetry = False
        self.generation = 0
        self.token = 'a' * 32
        app = web.Application()
        app.router.add_get('/loginStatus.cgi', self.challenge)
        app.router.add_post('/login.cgi', self.login)
        app.router.add_post('/logout.cgi', self.logout)
        app.router.add_get('/cgi/cgi_basic.js', self.basic)
        app.router.add_get('/cgi/cgi_status.js', self.status)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.client = api.GatewayClient('127.0.0.1', 'synthetic-password')
        self.client.base_url = f'http://127.0.0.1:{port}'  # Only the test server uses HTTP.

    async def asyncTearDown(self):
        await self.client.async_close()
        await self.runner.cleanup()

    def authenticated(self, request):
        return request.cookies.get('sysauth') == str(self.generation) and self.generation > 0

    async def challenge(self, request):
        return web.json_response({'loginToken': self.token, 'token': 'logout-token' if self.authenticated(request) else '', 'islogin': '1' if self.authenticated(request) else '0'})

    async def login(self, request):
        self.posts += 1
        form = await request.post()
        # Independent formula, rather than calling the function under test.
        digest = lambda s: hashlib.sha512(s.encode()).hexdigest()
        arc = lambda s: digest(hashlib.md5(s.encode()).hexdigest())
        expected = digest(self.token + arc('synthetic-password'))
        self.assertEqual(form['luci_username'], arc('admin'))
        self.assertEqual(form['luci_password'], expected)
        if self.busy:
            return web.json_response({'flag': 7, 'timeout': 600}, status=403)
        if self.reject:
            return web.json_response({'flag': 1, 'timeout': 0}, status=403)
        self.generation += 1
        response = web.Response(status=302, headers={'Location': '/'})
        response.set_cookie('sysauth', str(self.generation))
        return response

    async def basic(self, request):
        if not self.authenticated(request):
            return web.json_response({'flag': 0, 'islogin': '0'})
        return web.Response(text='addCfg("wan_disable", "unused", "0");')

    async def status(self, request):
        if self.fail_telemetry:
            return web.Response(status=503)
        if not self.authenticated(request):
            return web.json_response({'flag': 0, 'islogin': '0'})
        return web.Response(text=fixture())

    async def logout(self, request):
        if self.authenticated(request):
            self.assertEqual((await request.post())['token'], 'logout-token')
            self.logouts += 1
        return web.Response(text='OK')

    async def test_login_poll_reuse_expiry_and_logout(self):
        first = await self.client.async_fetch()
        second = await self.client.async_fetch()
        self.assertEqual(first, second)
        self.assertEqual(self.posts, 1)
        self.generation += 1  # Simulated expiry with HTTP-200 login JSON.
        await self.client.async_fetch()
        self.assertEqual(self.posts, 2)
        await self.client.async_close()
        self.assertEqual(self.logouts, 1)

    async def test_rejected_password_is_not_retried(self):
        self.reject = True
        for _ in range(3):
            with self.assertRaises(api.GatewayAuthError):
                await self.client.async_fetch()
        self.assertEqual(self.posts, 1)

    async def test_lockout_cooldown(self):
        self.busy = True
        for _ in range(3):
            with self.assertRaises(api.GatewayBusyError):
                await self.client.async_fetch()
        self.assertEqual(self.posts, 1)

    async def test_outage_does_not_trigger_new_login(self):
        await self.client.async_fetch()
        self.fail_telemetry = True
        with self.assertRaises(api.GatewayError):
            await self.client.async_fetch()
        self.fail_telemetry = False
        await self.client.async_fetch()
        self.assertEqual(self.posts, 1)

    async def test_concurrent_polls_share_session(self):
        await asyncio.gather(self.client.async_fetch(), self.client.async_fetch())
        self.assertEqual(self.posts, 1)


if __name__ == '__main__':
    unittest.main()
