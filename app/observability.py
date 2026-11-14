"""Safe, structured request observability."""
from __future__ import annotations

import json
import logging
from time import perf_counter
from uuid import uuid4

from fastapi.responses import JSONResponse
from starlette.routing import Match

logger = logging.getLogger("sentra.requests")
if not any(getattr(handler, "_sentra_request_handler", False) for handler in logger.handlers):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._sentra_request_handler = True
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def _route_template(request):
    # Never log literal user-controlled path segments, including unmatched URLs.
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    for candidate in request.app.routes:
        match, _ = candidate.matches(request.scope)
        if match == Match.FULL and getattr(candidate, "path", None):
            return candidate.path
    return "<unmatched>"


async def observe_request(request, call_next):
    request_id = str(uuid4())
    started = perf_counter()
    error_type = None
    try:
        response = await call_next(request)
    except Exception as exc:
        error_type = type(exc).__name__
        response = JSONResponse({"detail": "internal server error", "request_id": request_id}, status_code=500)
    response.headers["X-Request-ID"] = request_id
    logger.info(json.dumps({
        "request_id": request_id,
        "method": request.method,
        "path": _route_template(request),
        "status": response.status_code,
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "error_type": error_type,
    }))
    return response
