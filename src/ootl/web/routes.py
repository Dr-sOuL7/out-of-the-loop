"""FastAPI app exposing the serverless endpoints.

Endpoints (all under /api via Vercel's rewrite):
* POST /api/webhook  -- Telegram sends every update here (secret header checked)
* GET  /api/tick     -- Supabase pg_cron calls this to fire expired deadlines
* GET  /api/admin/set-webhook -- one-time setup: registers the webhook + commands
* GET  /api/health   -- liveness/info
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response
from telegram import Bot, BotCommand, Update

from ootl.config import load_settings
from ootl.web import db
from ootl.web.engine import WebEngine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Out of the Loop", docs_url=None, redoc_url=None)

_settings = None
_bot: Bot | None = None

_COMMANDS = [
    BotCommand("create", "Create a new game room (group)"),
    BotCommand("join", "Join the open room (group)"),
    BotCommand("leave", "Leave the room (group)"),
    BotCommand("players", "Show the lobby (group)"),
    BotCommand("startgame", "Start the match (host)"),
    BotCommand("score", "Show the current scoreboard"),
    BotCommand("leaderboard", "All-time stats"),
    BotCommand("abort", "Abort the current match (host)"),
    BotCommand("rules", "Show the rules"),
    BotCommand("help", "How to play"),
    BotCommand("start", "Register so I can DM you (private)"),
]


def get_settings():
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


async def get_bot() -> Bot:
    """Bot client, initialised once per (warm) process."""
    global _bot
    if _bot is None:
        bot = Bot(get_settings().bot_token)
        await bot.initialize()
        _bot = bot
    return _bot


def _secret_ok(request: Request) -> bool:
    settings = get_settings()
    if not settings.webhook_secret:
        return False
    supplied = request.query_params.get("secret", "")
    header = request.headers.get("x-telegram-bot-api-secret-token", "")
    return settings.webhook_secret in (supplied, header)


@app.get("/api/health")
@app.get("/health")
async def health():
    return {"ok": True, "app": "out-of-the-loop"}


@app.post("/api/webhook")
@app.post("/webhook")
async def webhook(request: Request) -> Response:
    if not _secret_ok(request):
        return Response(status_code=403)
    settings = get_settings()
    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    bot = await get_bot()
    try:
        update = Update.de_json(payload, bot)
    except Exception:
        logger.exception("could not parse update")
        return Response(status_code=200)

    conn = await db.connect(settings.database_url)
    try:
        engine = WebEngine(settings, bot, conn)
        try:
            await engine.handle_update(update)
        except Exception:
            # Always ACK Telegram: a 5xx would make it retry the same update
            # in a loop. The error is logged for diagnosis instead.
            logger.exception("error handling update")
        try:
            await engine.process_due()
        except Exception:
            logger.exception("error processing due deadlines")
    finally:
        await conn.close()
    return Response(status_code=200)


@app.get("/api/tick")
@app.get("/tick")
@app.post("/api/tick")
@app.post("/tick")
async def tick(request: Request):
    if not _secret_ok(request):
        return Response(status_code=403)
    settings = get_settings()
    bot = await get_bot()
    conn = await db.connect(settings.database_url)
    try:
        engine = WebEngine(settings, bot, conn)
        processed = await engine.process_due()
    finally:
        await conn.close()
    return {"ok": True, "processed": processed}


@app.get("/api/admin/set-webhook")
@app.get("/admin/set-webhook")
async def set_webhook(request: Request):
    """One-time setup: point Telegram at this deployment's /api/webhook."""
    if not _secret_ok(request):
        return Response(status_code=403)
    settings = get_settings()
    host = request.headers.get("x-forwarded-host") or request.url.hostname
    base = f"https://{host}"
    url = f"{base}/api/webhook"
    bot = await get_bot()
    await bot.set_webhook(
        url=url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )
    await bot.set_my_commands(_COMMANDS)
    info = await bot.get_webhook_info()
    return {
        "ok": True,
        "webhook_url": info.url,
        "pending_updates": info.pending_update_count,
        "commands_set": True,
    }
