"""Score / leaderboard handlers."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ootl.game import texts
from ootl.handlers.common import get_engine, get_repos


async def score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine = get_engine(context)
    game = engine.get_game(update.effective_chat.id)
    if game is None or not game.scores:
        await update.effective_message.reply_text(
            "No active match here. Send /create to start one!"
        )
        return
    await update.effective_message.reply_text(
        texts.scoreboard(game), parse_mode=ParseMode.HTML
    )


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repos = get_repos(context)
    rows = await repos.users.top_all_time(limit=10)
    await update.effective_message.reply_text(
        texts.all_time_leaderboard(rows), parse_mode=ParseMode.HTML
    )
