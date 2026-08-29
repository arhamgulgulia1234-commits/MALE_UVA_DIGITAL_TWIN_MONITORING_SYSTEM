"""Liveness endpoint.

Answers both `GET` and `HEAD`, and the `HEAD` is not incidental.

FastAPI, unlike a plain Starlette `Route`, does **not** add `HEAD` to a `GET` route
automatically — `APIRoute` takes the declared methods verbatim. So a `@router.get`
route answers `HEAD` with **405 Method Not Allowed**.

That matters because external uptime monitors (UptimeRobot's HTTP(s) monitor among them)
send `HEAD` first to avoid pulling a body, and read the 405 as the service being down.
The endpoint would have looked healthy in a browser and dead to the monitor.

Keeping one handler for both methods rather than a second `@router.head` route means the
two can never drift apart — there is only one definition of what "healthy" means here.
Starlette discards the body for a `HEAD` response, so the JSON below costs nothing on
that path.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict:
    return {"status": "ok"}
