import asyncio
import io
import unittest
from http.cookies import SimpleCookie

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from proxy import MAX_BODY, Config, create_app
from yarl import URL

USER = "0" * 32
ORIGIN = "https://ha.example.com"
INGRESS = "/api/hassio_ingress/synthetic-route"
OPTIONS = {
    "backend_url": "https://tv.example.com",
    "browser_origins": [ORIGIN],
    "allowed_user_ids": [USER],
}


class ConfigurationTests(unittest.TestCase):
    def test_exact_https_origin_and_explicit_user_allowlist(self):
        config = Config.from_options(OPTIONS)
        self.assertEqual(str(config.backend), "https://tv.example.com")
        self.assertEqual(config.allowed_user_ids, frozenset([USER]))

    def test_invalid_backend_or_browser_origins_fail_closed(self):
        for field in ["backend_url", "browser_origins"]:
            for value in ["http://tv.example.com", "https://*.example.com",
                          "https://user:secret@tv.example.com", "https://tv.example.com/path",
                          "https://tv.example.com?query=yes", "https://tv.example.com#fragment",
                          "https://tv.example.com:bad", "https://tv.example.com\\evil"]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    Config.from_options(OPTIONS | {
                        field: [value] if field == "browser_origins" else value})
        for value in [[], "", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config.from_options(OPTIONS | {"browser_origins": value})

    def test_no_default_user_and_no_username_or_wildcard_authorization(self):
        for value in [[], None, [""], ["admin"], ["*"], [USER.upper().replace("0", "A")]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config.from_options(OPTIONS | {"allowed_user_ids": value})


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.release_stream = asyncio.Event()
        upstream = web.Application(client_max_size=MAX_BODY * 2)

        async def controller(request):
            body = await request.read()
            self.requests.append({
                "path": request.path,
                "query": list(request.query.items()),
                "origin": request.headers.get("Origin"),
                "csrf": request.headers.get("X-CSRF-Token"),
                "cookie": request.headers.get("Cookie"),
                "authorization": request.headers.get("Authorization"),
                "forwarded": request.headers.get("X-Forwarded-For"),
                "user": request.headers.get("X-Remote-User-Id"),
                "body_size": len(body),
            })
            if request.path == "/cookies":
                response = web.json_response({"ok": True})
                response.set_cookie("tvcontrol_session", "synthetic-browser-cookie",
                                    httponly=True, secure=True, samesite="Strict", path="/")
                response.set_cookie("unrelated", "must-not-be-forwarded")
                return response
            if request.path == "/events":
                response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
                await response.prepare(request)
                await response.write(b"data: first\n\n")
                await self.release_stream.wait()
                await response.write(b"data: second\n\n")
                await response.write_eof()
                return response
            if request.path == "/interrupted-events":
                response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
                await response.prepare(request)
                await response.write(b"data: first\n\n")
                request.transport.close()
                return response
            if request.path == "/redirect":
                raise web.HTTPFound(location="/api/status?view=test")
            if request.path == "/external":
                raise web.HTTPFound(location="https://untrusted.example/")
            if request.path == "/nas-denied":
                return web.Response(status=403, text="Not found", content_type="text/html")
            if request.path == "/csrf-denied":
                return web.json_response({"detail": "Missing or invalid request token"}, status=403)
            return web.json_response(self.requests[-1])

        upstream.router.add_route("*", "/{path:.*}", controller)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        # Synthetic HTTP upstream and peer substitution exist only in this isolated test process.
        config = Config(URL(str(self.upstream.make_url("/")).rstrip("/")),
                        frozenset([ORIGIN]), frozenset([USER]))
        gateway = create_app(config)

        @web.middleware
        async def synthetic_peer(request, handler):
            return await handler(request.clone(
                remote=request.headers.get("X-Test-Peer", "172.30.32.2")))

        gateway.middlewares.append(synthetic_peer)
        self.client = TestClient(TestServer(gateway), cookie_jar=aiohttp.DummyCookieJar())
        await self.client.start_server()
        self.headers = {"X-Remote-User-Id": USER, "X-Ingress-Path": INGRESS}

    async def asyncTearDown(self):
        self.release_stream.set()
        await self.client.close()
        await self.upstream.close()

    async def test_only_supervisor_peer_is_accepted(self):
        for peer in ["127.0.0.1", "172.30.32.3", "192.0.2.1"]:
            with self.subTest(peer=peer):
                response = await self.client.get("/", headers=self.headers | {"X-Test-Peer": peer})
                self.assertEqual(response.status, 403)
        self.assertFalse(self.requests)

    async def test_unknown_or_missing_users_and_ingress_paths_are_denied(self):
        cases = [
            {"X-Remote-User-Id": "1" * 32},
            {"X-Remote-User-Id": ""},
            {"X-Ingress-Path": ""},
            {"X-Ingress-Path": "/api/hassio_ingress/../other"},
            {"X-Ingress-Path": "//untrusted.example"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                response = await self.client.get("/", headers=self.headers | changes)
                self.assertEqual(response.status, 403)
        self.assertFalse(self.requests)

    async def test_real_origin_csrf_and_payload_are_preserved(self):
        response = await self.client.put("/api/rule?view=one&view=two", data=b'{"minutes":45}',
                                         headers=self.headers | {
                                             "Origin": ORIGIN, "X-CSRF-Token": "synthetic-csrf",
                                             "Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual(result["origin"], ORIGIN)
        self.assertEqual(result["csrf"], "synthetic-csrf")
        self.assertEqual(result["path"], "/api/rule")
        self.assertEqual(result["query"], [["view", "one"], ["view", "two"]])
        self.assertEqual(result["body_size"], 14)

    async def test_untrusted_origin_cannot_reach_the_controller(self):
        response = await self.client.post("/api/rule", data=b"{}",
                                          headers=self.headers | {"Origin": "https://attacker.example"})
        self.assertEqual(response.status, 403)
        self.assertFalse(self.requests)

    async def test_ha_credentials_and_identity_headers_never_reach_the_backend(self):
        response = await self.client.get("/", headers=self.headers | {
            "Cookie": "ingress_session=synthetic-ha-session; tvcontrol_session=synthetic-tv-session",
            "Authorization": "Bearer synthetic-must-not-leave-ha",
            "X-Forwarded-For": "198.51.100.1",
        })
        result = await response.json()
        self.assertEqual(result["cookie"], "tvcontrol_session=synthetic-tv-session")
        for field in ["authorization", "forwarded", "user"]:
            self.assertIsNone(result[field], field)

    async def test_session_cookies_are_scoped_and_not_shared_between_browsers(self):
        response = await self.client.get("/cookies", headers=self.headers)
        self.assertEqual(response.status, 200)
        cookies = SimpleCookie()
        for value in response.headers.getall("Set-Cookie"):
            cookies.load(value)
        self.assertEqual(set(cookies), {"tvcontrol_session"})
        cookie = cookies["tvcontrol_session"]
        self.assertEqual(cookie["path"], f"{INGRESS}/")
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        response = await self.client.get("/", headers=self.headers)
        self.assertIsNone((await response.json())["cookie"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_streaming_events_arrive_before_the_upstream_finishes(self):
        response = await self.client.get("/events", headers=self.headers)
        self.assertEqual(response.status, 200)
        first = await asyncio.wait_for(response.content.readuntil(b"\n\n"), timeout=2)
        self.assertEqual(first, b"data: first\n\n")
        self.assertFalse(self.release_stream.is_set())
        self.release_stream.set()
        self.assertEqual(await response.content.read(), b"data: second\n\n")

    async def test_only_same_controller_redirects_are_rewritten(self):
        response = await self.client.get("/redirect", headers=self.headers, allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], f"{INGRESS}/api/status?view=test")
        response = await self.client.get("/external", headers=self.headers, allow_redirects=False)
        self.assertEqual(response.status, 502)
        self.assertIn("external redirect", await response.text())

    async def test_interrupted_stream_never_appends_a_second_http_response(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.client.server.port)
        try:
            request = (f"GET /interrupted-events HTTP/1.1\r\nHost: localhost\r\n"
                       f"X-Remote-User-Id: {USER}\r\nX-Ingress-Path: {INGRESS}\r\n"
                       "Connection: close\r\n\r\n")
            writer.write(request.encode())
            await writer.drain()
            wire = await asyncio.wait_for(reader.read(), timeout=3)
            self.assertIn(b"data: first\n\n", wire)
            self.assertEqual(wire.count(b"HTTP/1.1 "), 1, wire)
            self.assertNotIn(b"not reachable", wire)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_nas_deny_is_explained_without_masking_real_csrf_errors(self):
        response = await self.client.get("/nas-denied", headers=self.headers)
        self.assertEqual(response.status, 502)
        self.assertIn("public access has not been enabled", await response.text())
        response = await self.client.get("/csrf-denied", headers=self.headers)
        self.assertEqual(response.status, 403)
        self.assertEqual((await response.json())["detail"], "Missing or invalid request token")

    async def test_connection_failure_before_headers_is_an_explicit_gateway_error(self):
        await self.upstream.close()
        response = await self.client.get("/", headers=self.headers)
        self.assertEqual(response.status, 502)
        self.assertIn("not reachable", await response.text())

    async def test_oversized_upload_is_rejected_before_forwarding(self):
        response = await self.client.post("/api/assets/wallpaper",
                                          data=io.BytesIO(b"x" * (MAX_BODY + 1)),
                                          headers=self.headers)
        self.assertEqual(response.status, 413)
        self.assertFalse(self.requests)

    async def test_chunked_upload_has_the_same_explicit_size_error(self):
        async def oversized_body():
            yield b"x" * (MAX_BODY // 2)
            yield b"x" * (MAX_BODY // 2 + 1)

        response = await self.client.post("/api/assets/wallpaper", data=oversized_body(),
                                          headers=self.headers)
        self.assertEqual(response.status, 413)
        self.assertFalse(self.requests)

    async def test_prepared_eight_mib_image_still_fits(self):
        size = 8 * 1024 * 1024
        response = await self.client.post("/api/assets/wallpaper", data=io.BytesIO(b"x" * size),
                                          headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["body_size"], size)


if __name__ == "__main__":
    unittest.main()
