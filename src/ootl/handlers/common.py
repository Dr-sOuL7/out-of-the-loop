"""Basic handlers: /start, /help, /rules, private-text routing, errors."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from ootl.db.repositories import Repositories
from ootl.game import texts
from ootl.game.engine import GameEngine
from ootl.game.models import Player

logger = logging.getLogger(__name__)


def player_from_user(user) -> Player:
    return Player(
        user_id=user.id,
        display_name=user.full_name or (user.username or f"Player{user.id}"),
        username=user.username,
    )


def get_engine(context: ContextTypes.DEFAULT_TYPE) -> GameEngine:
    return context.bot_data["engine"]


def get_repos(context: ContextTypes.DEFAULT_TYPE) -> Repositories:
    return context.bot_data["repos"]


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    repos = get_repos(context)
    await repos.users.upsert(user.id, user.username, user.full_name or "")

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "👋 Open a private chat with me and press <b>Start</b> so I can DM you "
            "your secret role. Then play in your group!",
            parse_mode=ParseMode.HTML,
        )
        return

    # Private /start -> we now know we can DM this user.
    await repos.users.mark_dm_ok(user.id)
    await update.effective_message.reply_text(
        texts.dm_welcome(user.full_name or "there"), parse_mode=ParseMode.HTML
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        texts.help_text(), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        texts.rules_text(), parse_mode=ParseMode.HTML
    )


async def private_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route a private text message: it may be an answer or an imposter guess."""
    user = update.effective_user
    text = update.effective_message.text or ""
    engine = get_engine(context)
    consumed = await engine.record_private_answer(context, user, text)
    if not consumed:
        # Not part of any active phase -- gently nudge.
        await update.effective_message.reply_text(
            "I'm not expecting anything from you right now. Send /help for how to "
            "play, or jump into a game in your group!"
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error while processing update", exc_info=context.error)
