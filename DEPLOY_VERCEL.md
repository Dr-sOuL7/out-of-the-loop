# Deploying on Vercel + Supabase (free tiers)

This is the **serverless** deployment of Out of the Loop:

- **Vercel** runs the bot as webhook functions (`/api/webhook`) — Telegram
  pushes every message/button-tap to it.
- **Supabase** (Postgres) holds *all* state: live games, players, scores,
  history — and its `pg_cron` extension calls the app's `/api/tick` endpoint
  every 15 seconds so phase timers fire even when players go silent.

No servers, no Docker — and both services have free tiers that comfortably fit
a party bot.

> Prefer a persistent server instead? See [DEPLOY.md](DEPLOY.md) for the
> long-polling worker (VPS / Fly.io / Railway / Render).

---

## What you'll set up (10–15 minutes)

| # | Where | What |
|---|---|---|
| 1 | Supabase | Create a project, run one SQL file |
| 2 | Supabase | Copy the **connection string** (transaction pooler) |
| 3 | Vercel | Import the GitHub repo, set 3 environment variables |
| 4 | Browser | Visit one URL to register the Telegram webhook |
| 5 | Supabase | Run one more SQL snippet to start the 15-second tick |

---

## Step 1 — Supabase project + schema

1. Go to [supabase.com](https://supabase.com) → **New project** (free plan).
   Pick any name/region; set a strong **database password** and save it.
2. When the project is ready, open **SQL Editor** (left sidebar).
3. Paste the entire contents of [`supabase/schema.sql`](supabase/schema.sql)
   and click **Run**. You should see "Success. No rows returned".

## Step 2 — Get the connection string

1. In Supabase: **Connect** button (top bar) — or Settings → Database.
2. Under **Connection string**, choose **Transaction pooler** (port **6543**;
   this is the one designed for serverless).
3. Copy the URI. It looks like:
   `postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres`
4. Replace `[YOUR-PASSWORD]` with the database password from Step 1.
   Keep this string somewhere safe — it's a secret.

## Step 3 — Vercel project

1. Generate a webhook secret — any long random string. In a terminal:
   `openssl rand -hex 16` (or just type 30+ random characters).
2. Go to [vercel.com](https://vercel.com) → **Add New… → Project** → import
   your `out-of-the-loop` GitHub repo.
3. Before/after the first deploy, open the project's
   **Settings → Environment Variables** and add these three:

   | Name | Value |
   |---|---|
   | `BOT_TOKEN` | your token from @BotFather |
   | `DATABASE_URL` | the pooler URI from Step 2 (password filled in) |
   | `WEBHOOK_SECRET` | the random string from 3.1 |

4. **Deploy** (or redeploy so the env vars take effect). Note your app's
   domain, e.g. `your-app.vercel.app`.
5. Check it's alive: open `https://your-app.vercel.app/api/health` — you
   should see `{"ok": true, ...}`.

## Step 4 — Register the Telegram webhook

Open this URL in your browser (replace both placeholders):

```
https://your-app.vercel.app/api/admin/set-webhook?secret=YOUR-WEBHOOK-SECRET
```

You should get back `{"ok": true, "webhook_url": "https://your-app.vercel.app/api/webhook", ...}`.
This also registers the bot's command menu. **Done once; repeat only if your
domain changes.**

## Step 5 — Start the timer tick

> ⚠️ Do this **inside the Supabase SQL Editor only** — never write your real
> secret into a file that gets committed to git.

1. Enable the extensions first, **each statement run on its own** (the SQL
   Editor runs a block as one transaction — if anything fails, everything in
   the block silently rolls back, extensions included). Or toggle both in
   **Dashboard → Database → Extensions**:
   ```sql
   CREATE EXTENSION IF NOT EXISTS pg_net;
   ```
   ```sql
   CREATE EXTENSION IF NOT EXISTS pg_cron;
   ```
2. Open [`supabase/cron.sql`](supabase/cron.sql), replace the app domain (if
   yours differs) and `PASTE-YOUR-SECRET-HERE` with your `WEBHOOK_SECRET`,
   and run the schedule block.
3. The final SELECT should show one row: the `ootl-tick` job, active, running
   every 15 seconds.
4. After ~30 s, verify the ticks are landing (expect `200`s):
   ```sql
   SELECT status_code, created FROM net._http_response ORDER BY id DESC LIMIT 5;
   ```
   You can also open `https://YOUR-APP.vercel.app/api/tick?secret=YOUR-SECRET`
   in a browser — `{"ok": true, "processed": 0}` proves the endpoint works.

## Verify

1. DM your bot `/start` → welcome message.
2. In a group (bot added, BotFather privacy **disabled**): `/create`, have 3+
   members `/join` (each must have DM'd `/start` first), then `/startgame`.
3. Play a round end-to-end.

---

## How the timers work here (worth knowing)

There's no long-lived process, so phase deadlines are stored in Postgres and
enforced two ways:

- **Instantly** when everyone has acted (all answers in → voting opens
  immediately; all votes in → reveal immediately). Most phases advance this way.
- **By the tick** otherwise: `pg_cron` calls `/api/tick` every 15 s, which
  advances any game whose deadline passed. So a timeout can land up to ~15 s
  late — fine for a party game.

## Free-tier notes & limits

- **Supabase free** pauses projects after ~1 week with **no activity**; the
  15-second tick keeps the database active, so this generally isn't an issue
  while the bot is deployed. If a project does get paused, restore it from the
  dashboard with one click.
- **Vercel Hobby** allows generous function invocations per month; the tick
  costs ~172k/month plus your actual gameplay traffic — comfortably within it.
- Both dashboards show logs: Vercel → your project → **Logs** (function
  errors), Supabase → **Logs** (Postgres/cron).

## Troubleshooting

| Symptom | Check |
|---|---|
| Bot doesn't react at all | `/api/admin/set-webhook` response ok? `BOT_TOKEN` right? Vercel Logs show the webhook arriving? |
| "403" opening admin/tick URLs | `secret=` must exactly match `WEBHOOK_SECRET` in Vercel env vars. |
| Bot reacts but games stall on timeouts | Step 5 cron running? In Supabase run: `SELECT * FROM cron.job;` and check `net._http_response` for errors. |
| DB errors in Vercel logs | `DATABASE_URL` must be the **transaction pooler** URI (port 6543) with the real password. |
| Players never get their secret word | Each player must DM the bot `/start` once (Telegram rule: bots can't DM first). |
