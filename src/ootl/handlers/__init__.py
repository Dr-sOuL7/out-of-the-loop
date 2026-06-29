"""Telegram handler registration."""
from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from ootl.handlers import common, lobby, stats, voting


def register_handlers(application: Application) -> None:
    """Wire every command, callback and message handler onto the application."""
    # --- basic / private ---
    application.add_handler(CommandHandler("start", common.start_cmd))
    application.add_handler(CommandHandler("help", common.help_cmd))
    application.add_handler(CommandHandler("rules", common.rules_cmd))

    # --- lobby (group) ---
    application.add_handler(CommandHandler("create", lobby.create_cmd))
    application.add_handler(CommandHandler("join", lobby.join_cmd))
    application.add_handler(CommandHandler("leave", lobby.leave_cmd))
    application.add_handler(CommandHandler("players", lobby.players_cmd))
    application.add_handler(CommandHandler("startgame", lobby.start_cmd))
    application.add_handler(CommandHandler("abort", lobby.abort_cmd))

    # --- stats ---
    application.add_handler(CommandHandler("score", stats.score_cmd))
    application.add_handler(CommandHandler("leaderboard", stats.leaderboard_cmd))

    # --- inline buttons ---
    application.add_handler(CallbackQueryHandler(lobby.join_cb, pattern=r"^join$"))
    application.add_handler(CallbackQueryHandler(lobby.leave_cb, pattern=r"^leave$"))
    application.add_handler(CallbackQueryHandler(lobby.start_cb, pattern=r"^start$"))
    application.add_handler(CallbackQueryHandler(voting.vote_cb, pattern=r"^vote:"))

    # --- private text = answers / imposter guesses ---
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            common.private_text,
        )
    )

    application.add_error_handler(common.error_handler)
