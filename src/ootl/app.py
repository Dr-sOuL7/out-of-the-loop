"""Build and run the Telegram bot application."""
from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import Application, ContextTypes

from ootl.config import Settings, load_settings
from ootl.content.manager import ContentManager
from ootl.db.database import Database
from ootl.game.engine import GameEngine
from ootl.handlers import register_handlers
from ootl.logging_config import configure_logging

logger = logging.getLogger(__name__)

_COMMANDS = [
    BotCommand("create", "Create a new game room (group)"),
    BotCommand("join", "Join the open room (group)"),
    BotCommand("leave", "Leave the room (group)"),
    BotCommand("players", "Show the lobby (group)"),
    BotCommand("startgame", "Start the match (host)"),
    BotCommand("score", "Show the current scoreboard"),
    BotCommand("leaderboard", "All-time player stats"),
    BotCommand("abort", "Abort the current match (host)"),
    BotCommand("rules", "Show the rules"),
    BotCommand("help", "How to play"),
    BotCommand("start", "Register so I can DM you (private)"),
]

MISSING_TOKEN_MESSAGE = """\
========================================================================
  BOT_TOKEN is not set.

  1. In Telegram, message @BotFather and send /newbot
  2. Follow the prompts to get a token like 123456789:ABCdef...
  3. Copy .env.example to .env and paste the token into BOT_TOKEN=
  4. Run again.
========================================================================"""


async def _post_init(application: Application) -> None:
    db: Database = application.bot_data["db"]
    content: ContentManager = application.bot_data["content"]
    await db.connect()

    counts = await content.counts()
    if counts["words"] == 0:
        logger.info("No content found -- importing from JSON…")
        from ootl.content.importer import import_content

        await import_content(db)
        counts = await content.counts()
    logger.info(
        "Content ready: %s words, %s questions, %s categories",
        counts["words"],
        counts["questions"],
        counts["categories"],
    )

    # If a webhook was ever configured for this bot, long-polling would fail
    # with a conflict. Clear it so `run_polling` works on any host.
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Could not delete webhook: %s", exc)

    try:
        await application.bot.set_my_commands(_COMMANDS)
    except Exception as exc:  # pragma: no cover - cosmetic
        logger.warning("Could not set bot commands: %s", exc)

    me = await application.bot.get_me()
    logger.info("Bot @%s is up and polling.", me.username)


async def _post_shutdown(application: Application) -> None:
    db: Database = application.bot_data["db"]
    await db.close()


def build_application(settings: Settings) -> Application:
    db = Database(settings.database_path)
    content = ContentManager(
        db,
        word_history_window=settings.word_history_window,
        question_history_window=settings.question_history_window,
    )
    from ootl.db.repositories import Repositories

    repos = Repositories.build(db)
    engine = GameEngine(settings, content, repos)

    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data.update(
        {
            "settings": settings,
            "db": db,
            "content": content,
            "repos": repos,
            "engine": engine,
        }
    )
    register_handlers(application)
    return application


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    if not settings.is_configured:
        print(MISSING_TOKEN_MESSAGE)
        raise SystemExit(1)

    application = build_application(settings)
    logger.info("Starting Out of the Loop bot…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
