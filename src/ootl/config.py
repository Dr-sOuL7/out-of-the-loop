"""Runtime configuration, loaded from environment variables / a .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a hard dependency in practice
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


# Project root = three levels up from this file (src/ootl/config.py -> repo root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """All tunable settings for the bot in one immutable object."""

    bot_token: str
    database_path: Path

    answer_time_seconds: int
    vote_time_seconds: int
    guess_time_seconds: int

    min_players: int
    max_players: int
    default_rounds: int

    word_history_window: int
    question_history_window: int

    log_level: str

    # --- serverless (Vercel + Supabase) mode only -----------------------------
    # Postgres connection string (Supabase "transaction pooler" URL).
    database_url: str = ""
    # Shared secret: verifies Telegram's webhook header and guards /api/tick
    # and /api/admin endpoints.
    webhook_secret: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from the environment (and a .env file if present)."""
    # Load .env from an explicit path or the project root.
    if env_file is not None:
        load_dotenv(env_file)
    else:
        load_dotenv(PROJECT_ROOT / ".env")

    db_path = os.getenv("DATABASE_PATH", "data/ootl.db")
    database_path = Path(db_path)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    return Settings(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        database_path=database_path,
        answer_time_seconds=_get_int("ANSWER_TIME_SECONDS", 150),
        vote_time_seconds=_get_int("VOTE_TIME_SECONDS", 60),
        guess_time_seconds=_get_int("GUESS_TIME_SECONDS", 45),
        min_players=_get_int("MIN_PLAYERS", 3),
        max_players=_get_int("MAX_PLAYERS", 9),
        default_rounds=_get_int("DEFAULT_ROUNDS", 5),
        word_history_window=_get_int("WORD_HISTORY_WINDOW", 40),
        question_history_window=_get_int("QUESTION_HISTORY_WINDOW", 20),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
    )
