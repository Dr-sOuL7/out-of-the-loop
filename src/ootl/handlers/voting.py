"""Voting + imposter-guess button callbacks."""
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


async def guess_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The imposter tapped one of the word options in their DM."""
    query = update.callback_query
    engine = get_engine(context)
    try:
        _, round_no, idx = (query.data or "").split(":")
        round_no, idx = int(round_no), int(idx)
    except (ValueError, IndexError):
        await query.answer("Invalid choice.")
        return

    result = await engine.cast_guess(
        context, query.from_user.id, round_no, idx, query.message
    )
    await query.answer(result)
