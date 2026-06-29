"""Voting button callback."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from ootl.handlers.common import get_engine


async def vote_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    engine = get_engine(context)
    try:
        target_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Invalid vote.")
        return

    result = await engine.cast_vote(
        context, query.message.chat_id, query.from_user.id, target_id
    )
    await query.answer(result)
