# Deploying Out of the Loop

## What kind of process this is (read first)

The bot runs as a **persistent worker** using Telegram **long-polling**:

- It keeps a long-lived process alive and holds each active match's state in
  memory.
- It runs background **timers** (answer / vote / guess deadlines, next-round
  delay) via the job queue.
- It talks to Telegram over **outbound** HTTPS only — it does **not** listen on
  any port and needs no public URL.

➡️ **Host it on anything that runs a long-lived process/container:** a VPS,
Fly.io, Railway, Render (background worker), etc.

➡️ For serverless hosting there is now a **dedicated Vercel + Supabase
runtime** — see **[DEPLOY_VERCEL.md](DEPLOY_VERCEL.md)**. The notes below about
serverless limits explain why that mode is architected differently
(webhooks + Postgres state + cron tick) from this worker.

---

## Secrets & configuration

Set these as **environment variables / secrets in your host's dashboard** — do
**not** commit them.

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `BOT_TOKEN` | ✅ | — | From @BotFather. |
| `DATABASE_PATH` | | `/data/ootl.db` (in Docker) | Point at a mounted volume to persist stats. |
| `ANSWER_TIME_SECONDS` | | `90` | |
| `VOTE_TIME_SECONDS` | | `60` | |
| `GUESS_TIME_SECONDS` | | `45` | |
| `MIN_PLAYERS` / `MAX_PLAYERS` | | `3` / `9` | |
| `DEFAULT_ROUNDS` | | `5` | |
| `LOG_LEVEL` | | `INFO` | |

The app reads real environment variables first; a local `.env` is only used for
development. So on a host you just set `BOT_TOKEN` as a secret — no `.env` needed.

## Persistence

The container writes its SQLite DB to `DATABASE_PATH` (`/data/ootl.db`). The
**word/question content re-imports automatically** from the bundled JSON on every
start, so content is never lost. To keep **player stats / all-time leaderboard**
across restarts and redeploys, mount a **persistent volume at `/data`** (each
option below shows how). Without a volume, stats reset on restart — the game
itself still works fine.

---

## Option A — Any VPS / server with Docker (also covers a "Stackhost" VPS)

```bash
git clone <your-repo-url> && cd out-of-the-loop
cp .env.example .env
# edit .env and paste your token into BOT_TOKEN=
docker compose up -d --build
docker compose logs -f          # watch it come up ("Bot @yourbot is up and polling.")
```

The named volume `ootl-data` persists your stats. To update after pushing code:
`git pull && docker compose up -d --build`.

No firewall/port changes are needed — the bot only makes outbound connections.

## Option B — Fly.io (great fit: cheap, persistent, has volumes)

```bash
fly launch --no-deploy            # detects the Dockerfile; pick a name/region
fly volumes create ootl_data --size 1        # persistent disk
```

Add the volume mount to the generated `fly.toml`:

```toml
[[mounts]]
  source = "ootl_data"
  destination = "/data"
```

Then:

```bash
fly secrets set BOT_TOKEN=123456:your-token-here
fly deploy
fly logs
```

(There is no `[http_service]` block — this app serves no HTTP.)

## Option C — Railway

1. **New Project → Deploy from GitHub repo** (Railway builds the `Dockerfile`).
2. In **Variables**, add `BOT_TOKEN` (and optionally `DATABASE_PATH=/data/ootl.db`).
3. Add a **Volume** mounted at `/data` for persistent stats.
4. Ensure the service runs the worker (the `Procfile`/`CMD` already does
   `python run.py`). Railway needs no public port for this service.

## Option D — Render (Blueprint included)

This repo ships a `render.yaml` that defines a **Background Worker** with a 1 GB
disk at `/data`.

1. In Render: **New → Blueprint**, pick this repo.
2. When prompted, set **`BOT_TOKEN`** (kept as a secret, `sync: false`).
3. Deploy. Watch the logs for `Bot @yourbot is up and polling.`

> Use a **Background Worker**, not a Web Service — a Web Service expects an open
> HTTP port and its health checks would fail.

---

## Why not Vercel (or other serverless)?

Vercel runs **stateless, short-lived functions** triggered per HTTP request.
This bot needs the opposite of that:

| This bot needs… | Serverless/Vercel gives… |
| --- | --- |
| A process that stays alive between messages | Functions that spin up per request and die |
| In-memory match state across a whole round | No shared memory between invocations |
| Background timers (answer/vote/guess deadlines) | No background execution after a response |
| Long-polling (outbound `getUpdates` loop) | Only inbound HTTP handlers |

Running on Vercel would require a **re-architecture**:

1. Switch from long-polling to a **webhook** (an HTTP function per update).
2. Move all live game state to an **external store** (Redis or Postgres) since
   memory doesn't persist between invocations.
3. Replace the job-queue timers with an **external scheduler** (e.g. a cron /
   scheduled function, or QStash-style delayed messages) to fire phase
   deadlines.

That's a meaningful v2 project with more moving parts and slower phase handling.
For v1, a persistent worker (Options A–D) is simpler, cheaper, and snappier.
If you decide you specifically need the webhook/serverless model, that can be
built as a follow-up.

---

## After deploying — verify it works

1. Open a DM with your bot and send `/start` → you should get the welcome.
2. Add the bot to a group (and confirm **@BotFather → /setprivacy → Disabled**
   so it can read group commands).
3. In the group: `/create`, have 3+ members `/join` (each must have pressed
   **Start** in the bot's DM first), then `/startgame`.
4. Play a round — you'll get your secret role in DM, answer via DM, vote with the
   buttons, and see the reveal + scores.

## Operating notes

- **Single instance only.** Run exactly one copy of the bot per token —
  two pollers on the same token conflict. (Scale by concurrency within the one
  process, not multiple instances.)
- **Restarts drop in-progress rounds.** Completed rounds/stats are persisted, but
  a match that is mid-round when the process restarts is lost (players just
  `/create` again). Durable mid-round recovery is a planned enhancement.
- **Logs** are your friend: `LOG_LEVEL=DEBUG` for verbose troubleshooting.
