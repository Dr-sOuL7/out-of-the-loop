"""Vercel entry point: exposes the FastAPI ASGI app for all /api/* routes.

If the real app fails to import (missing dependency, bad env, path issue),
a minimal fallback ASGI app is exposed instead that reports the import error
on every route — a readable diagnosis beats Vercel's blank 500 page.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from ootl.web.routes import app  # noqa: F401
except Exception:  # pragma: no cover - only triggers on broken deployments
    _error = traceback.format_exc()

    async def app(scope, receive, send):  # type: ignore[misc]
        if scope["type"] != "http":
            return
        body = (
            "Out of the Loop: the application failed to start.\n\n"
            "Import error:\n\n" + _error
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": body})
