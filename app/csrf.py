"""
CSRF protection for a server-rendered app with no tokens in its forms.

Every POST/PUT/PATCH/DELETE must carry an Origin header (or, if the browser
sent none, a Referer) whose origin exactly matches ALLOWED_ORIGINS. A request
with neither header is rejected. SameSite=Lax alone is not enough: other ports
on the same IP count as the same site. Pure ASGI so it never touches the body.
"""

from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


# Machine endpoints that authenticate with an explicit bearer token and read no cookie. A browser cannot be tricked into sending an
# Authorization header cross-site, so the Origin check protects nothing there (and a non-browser client sends no Origin).
BEARER_PREFIXES = ("/api/frazer-helper/",)


class OriginCheckMiddleware:
    def __init__(self, app, allowed_origins):
        self.app = app
        self.allowed = {o.rstrip("/") for o in allowed_origins}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in _UNSAFE_METHODS:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            origin = headers.get("origin") or _origin_of(headers.get("referer", ""))
            bearer = scope["path"].startswith(BEARER_PREFIXES) and headers.get("authorization", "").lower().startswith("bearer ")
            if origin not in self.allowed and not bearer:
                response = PlainTextResponse("Cross-origin request refused.", status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
