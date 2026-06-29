"""Lobby handlers: /create, /join, /leave, /players, /startgame, /abort + buttons."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ootl.game import texts
from ootl.game.models import GameState
from ootl.handlers.common import get_engine, get_repos, player_from_user


def _lobby_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Join ✅", callback_data="join"),
                InlineKeyboardButton("Leave 🚪", callback_data="leave"),
            ],
            [InlineKeyboardButton("Start ▶️", callback_data="start")],
        ]
    )


def _is_group(update: Update) -> bool:
    return update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def _group_only_guard(update: Update) -> bool:
    if not _is_group(update):
        await update.effective_message.reply_text(
            texts.private_only_hint(), parse_mode=ParseMode.HTML
        )
        return False
    return True


async def _refresh_lobby(context: ContextTypes.DEFAULT_TYPE, game: GameState) -> None:
    """Edit the stored lobby message to show the current roster."""
    if game.lobby_message_id is None:
        return
    settings = context.bot_data["settings"]
    try:
        await context.bot.edit_message_text(
            chat_id=game.chat_id,
            message_id=game.lobby_message_id,
            text=texts.player_list(game, settings.min_players, settings.max_players),
            parse_mode=ParseMode.HTML,
            reply_markup=_lobby_keyboard(),
        )
    except TelegramError:
        pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
async def create_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _group_only_guard(update):
        return
    user = update.effective_user
    repos = get_repos(context)
    await repos.users.upsert(user.id, user.username, user.full_name or "")

    engine = get_engine(context)
    game, message = await engine.create_room(
        update.effective_chat.id, player_from_user(user)
    )
    if game is None:
        await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)
        return
    sent = await update.effective_message.reply_text(
        message, parse_mode=ParseMode.HTML, reply_markup=_lobby_keyboard()
    )
    game.lobby_message_id = sent.message_id


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _group_only_guard(update):
        return
    user = update.effective_user
    repos = get_repos(context)
    await repos.users.upsert(user.id, user.username, user.full_name or "")

    engine = get_engine(context)
    ok, message = await engine.join(update.effective_chat.id, player_from_user(user))
    await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)
    if ok:
        game = engine.get_game(update.effective_chat.id)
        if game:
            await _refresh_lobby(context, game)


async def leave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _group_only_guard(update):
        return
    engine = get_engine(context)
    ok, message = await engine.leave(
        context, update.effective_chat.id, update.effective_user.id
    )
    await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)
    if ok:
        game = engine.get_game(update.effective_chat.id)
        if game:
            await _refresh_lobby(context, game)


async def players_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _group_only_guard(update):
        return
    engine = get_engine(context)
    settings = context.bot_data["settings"]
    game = engine.get_game(update.effective_chat.id)
    if game is None:
        await update.effective_message.reply_text(
            texts.not_in_room(), parse_mode=ParseMode.HTML
        )
        return
    await update.effective_message.reply_text(
        texts.player_list(game, settings.min_players, settings.max_players),
        parse_mode=ParseMode.HTML,
    )


def _parse_rounds(args: list[str]) -> int | None:
    if args:
        try:
            return int(args[0])
        except (ValueError, IndexError):
            return None
    return None


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _group_only_guard(update):
        return
    engine = get_engine(context)
    rounds = _parse_rounds(context.args or [])
    ok, message = await engine.start_match(
        context, update.effective_chat.id, update.effective_user.id, rounds
    )
    if not ok:
        await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)
        return
    # Freeze the lobby buttons now the match has begun.
    game = engine.get_game(update.effective_chat.id)
    if game and game.lobby_message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=game.chat_id, message_id=game.lobby_message_id
            )
        except TelegramError:
            pass


async def abort_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _group_only_guard(update):
        return
    engine = get_engine(context)
    ok, message = await engine.abort(
        context, update.effective_chat.id, update.effective_user.id
    )
    await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Inline button callbacks
# ---------------------------------------------------------------------------
async def join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    repos = get_repos(context)
    await repos.users.upsert(user.id, user.username, user.full_name or "")

    engine = get_engine(context)
    ok, message = await engine.join(query.message.chat_id, player_from_user(user))
    await query.answer(_plain(message))
    if ok:
        game = engine.get_game(query.message.chat_id)
        if game:
            await _refresh_lobby(context, game)


async def leave_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    engine = get_engine(context)
    ok, message = await engine.leave(
        context, query.message.chat_id, query.from_user.id
    )
    await query.answer(_plain(message))
    if ok:
        game = engine.get_game(query.message.chat_id)
        if game:
            await _refresh_lobby(context, game)


async def start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    engine = get_engine(context)
    ok, message = await engine.start_match(
        context, query.message.chat_id, query.from_user.id, None
    )
    if not ok:
        await query.answer(_plain(message), show_alert=True)
        return
    await query.answer("Starting…")
    game = engine.get_game(query.message.chat_id)
    if game and game.lobby_message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=game.chat_id, message_id=game.lobby_message_id
            )
        except TelegramError:
            pass


def _plain(html_text: str) -> str:
    """Crude tag-strip so HTML message text is readable in a callback toast."""
    import re

    text = re.sub(r"<[^>]+>", "", html_text)
    return text[:190]
