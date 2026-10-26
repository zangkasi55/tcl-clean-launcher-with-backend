import io
import json
import logging
import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import unquote

import aiohttp
from aiohttp import web
from yarl import URL

LOGGER = logging.getLogger("tcl_ingress")
MAX_BODY = 9 * 1024 * 1024
INGRESS_PATH = re.compile(r"/api/hassio_ingress/[A-Za-z0-9_-]+/?")
USER_ID = re.compile(r"[a-f0-9]{32}")
REQUEST_HEADERS = {
    "accept", "accept-language", "content-type", "content-length",
    "if-none-match", "if-modified-since", "origin", "range", "x-csrf-token",
}
RESPONSE_HEADERS = {
    "content-type", "content-length", "content-encoding", "content-disposition",
    "content-security-policy", "etag", "last-modified", "accept-ranges", "content-range",
    "x-content-type-options", "referrer-policy",
}


def https_origin(value: str) -> URL:
    if not isinstance(value, str) or any(c.isspace() or c in "'\";*\\<>" for c in value):
        raise ValueError("An exact HTTPS origin is required")
    try:
        url = URL(value)
        _ = url.port
    except ValueError as exc:
        raise ValueError("An exact HTTPS origin is required") from exc
    if (url.scheme != "https" or not url.host or url.user is not None
            or url.password is not None or url.path != "/" or url.query_string or url.fragment):
        raise ValueError("An exact HTTPS origin is required")
    return url.origin()


@dataclass(frozen=True)
class Config:
    backend: URL
    browser_origins: frozenset[str]
    allowed_user_ids: frozenset[str]

    @classmethod
    def from_options(cls, options: dict) -> "Config":
        backend = https_origin(options["backend_url"])
        origins = options.get("browser_origins")
        users = options.get("allowed_user_ids")
        if not isinstance(origins, list) or not origins:
            raise ValueError("Configure at least one exact Home Assistant HTTPS browser origin")
        if (not isinstance(users, list) or not users
                or any(not isinstance(user, str) or not USER_ID.fullmatch(user) for user in users)):
            raise ValueError("Configure the explicit Home Assistant user IDs allowed to use TCL")
        return cls(backend, frozenset(str(https_origin(value)) for value in origins),
                   frozenset(users))


def response_cookies(headers, ingress_path: str) -> list[str]:
    result = []
    for value in headers.getall("Set-Cookie", []):
        cookies = SimpleCookie()
        cookies.load(value)
        if "tvcontrol_session" not in cookies:
            continue
        cookie = cookies["tvcontrol_session"]
        cookie["path"] = f"{ingress_path.rstrip('/')}/"
        cookie["domain"] = ""
        cookie["secure"] = True
        cookie["httponly"] = True
        cookie["samesite"] = "Strict"
        result.append(cookie.OutputString())
    return result


def create_app(config: Config) -> web.Application:
    app = web.Application(client_max_size=MAX_BODY)
    client_key = web.AppKey("upstream", aiohttp.ClientSession)

    async def connection(application):
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False, trust_env=False,
            timeout=aiohttp.ClientTimeout(total=None, connect=15, sock_read=90),
        ) as client:
            application[client_key] = client
            yield

    app.cleanup_ctx.append(connection)

    async def proxy(request: web.Request) -> web.StreamResponse:
        if request.remote != "172.30.32.2":
            raise web.HTTPForbidden(text="Home Assistant ingress connection required")
        if request.headers.get("X-Remote-User-Id") not in config.allowed_user_ids:
            raise web.HTTPForbidden(text="This Home Assistant user is not allowed to use TCL controls")
        ingress_path = request.headers.get("X-Ingress-Path", "")
        if not INGRESS_PATH.fullmatch(ingress_path):
            raise web.HTTPForbidden(text="A valid Home Assistant ingress path is required")
        origin = request.headers.get("Origin")
        if origin and origin not in config.browser_origins:
            raise web.HTTPForbidden(text="Open Home Assistant using its configured HTTPS address")
        if request.method not in {"GET", "HEAD", "POST", "PUT", "DELETE"}:
            raise web.HTTPMethodNotAllowed(request.method, ["GET", "HEAD", "POST", "PUT", "DELETE"])
        decoded_path = unquote(request.rel_url.path)
        if decoded_path.startswith("//") or any(p in {".", ".."} for p in decoded_path.split("/")):
            raise web.HTTPBadRequest(text="Invalid controller path")
        if request.content_length is not None and request.content_length > MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=request.content_length)
        body = await request.read() if request.can_read_body else None
        target = config.backend.with_path(request.rel_url.path).with_query(request.rel_url.query)
        headers = {name: value for name, value in request.headers.items()
                   if name.lower() in REQUEST_HEADERS}
        cookie = request.cookies.get("tvcontrol_session")
        if cookie:
            cookies = SimpleCookie()
            cookies["tvcontrol_session"] = cookie
            headers["Cookie"] = cookies.output(header="").strip()
        response = None
        try:
            async with request.app[client_key].request(
                request.method, target, headers=headers,
                data=io.BytesIO(body) if body is not None else None,
                allow_redirects=False,
            ) as upstream:
                if upstream.status == 403 and upstream.content_type == "text/html":
                    LOGGER.warning("The private NAS controller rejected the ingress connection")
                    raise web.HTTPBadGateway(
                        text="The NAS rejected the connection from Home Assistant. "
                        "Check private LAN routing and the controller access profile; "
                        "public access has not been enabled.")
                output_headers = {name: value for name, value in upstream.headers.items()
                                  if name.lower() in RESPONSE_HEADERS}
                output_headers["Cache-Control"] = "no-store"
                location = upstream.headers.get("Location")
                if location:
                    redirect = target.join(URL(location))
                    if redirect.origin() != config.backend.origin():
                        raise web.HTTPBadGateway(text="The controller returned an external redirect")
                    output_headers["Location"] = (
                        f"{ingress_path.rstrip('/')}{redirect.raw_path_qs}")
                response = web.StreamResponse(status=upstream.status, headers=output_headers)
                for value in response_cookies(upstream.headers, ingress_path):
                    response.headers.add("Set-Cookie", value)
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (aiohttp.ClientError, TimeoutError) as exc:
            LOGGER.warning("Private controller connection failed (%s)", type(exc).__name__)
            if response is not None and response.prepared:
                response.force_close()
                if request.transport is not None:
                    request.transport.close()
                return response
            raise web.HTTPBadGateway(
                text="The private TCL controller is not reachable. "
                "Check its LAN HTTPS address and TLS certificate.") from exc

    app.router.add_route("*", "/{path:.*}", proxy)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    configuration = Config.from_options(json.loads(Path("/data/options.json").read_text("utf-8")))
    web.run_app(create_app(configuration), host="0.0.0.0", port=8099, access_log=None)
