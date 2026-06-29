#!/usr/bin/env python3
"""Convenience entry point so the bot can be started with `python run.py`.

This simply adds ``src`` to the import path and delegates to ``ootl.__main__``.
Installing the package (``pip install -e .``) and running ``ootl`` works too.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ootl.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
