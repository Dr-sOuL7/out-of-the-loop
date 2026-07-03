"""Vercel entry point: exposes the FastAPI ASGI app for all /api/* routes.

IMPORTANT: Vercel statically scans this file for a top-level ``app`` binding to
recognise it as an ASGI function -- ``app`` must be assigned at module level
(not inside try/except), which is why the import guard lives in a helper.

If the real app fails to import (missing dependency, bad env, path issue), a
minimal fallback ASGI app is served instead that reports the import traceback
on every route -- a readable diagnosis beats Vercel's blank 500 page.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _load_app():
    try:
        from ootl.web.routes import app as real_app

        return real_app
    except Exception:  # pragma: no cover - only triggers on broken deployments
        error = traceback.format_exc()

        async def fallback(scope, receive, send):
            if scope["type"] != "http":
                return
            body = (
                "Out of the Loop: the application failed to start.\n\n"
                "Import error:\n\n" + error
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                }
            )
            await send({"type": "http.response.body", "body": body})

        return fallback


app = _load_app()
