"""Vercel entry point: exposes the FastAPI ASGI app for all /api/* routes."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ootl.web.routes import app  # noqa: E402, F401
