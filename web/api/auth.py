"""Optional bearer-token auth for every API route.

Set ``LLMCI_API_TOKEN`` to turn it on. Then every ``/api/*`` and ``/v1/*``
request needs ``Authorization: Bearer <token>``. Browsers cannot set headers
on ``EventSource``, so ``?token=`` is also accepted. ``/health`` stays open
for load balancers. With no token set, the API stays open (local demo mode)
and logs a warning at start-up.
"""

from __future__ import annotations

import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PROTECTED_PREFIXES = ("/api/", "/v1/")


def api_token() -> str:
    return os.environ.get("LLMCI_API_TOKEN", "").strip()


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = api_token()
        path = request.url.path
        if (
            token
            and request.method != "OPTIONS"
            and path.startswith(PROTECTED_PREFIXES)
        ):
            header = request.headers.get("authorization", "")
            supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
            supplied = supplied or request.query_params.get("token", "")
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse(
                    {"detail": "Missing or invalid API token"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)
