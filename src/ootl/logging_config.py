"""Centralised logging setup."""
from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with a concise, readable format."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs every Telegram API call at INFO -- too noisy. Quiet it down.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
