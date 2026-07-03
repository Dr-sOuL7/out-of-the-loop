-- ===========================================================================
-- Out of the Loop -- phase-timer tick via Supabase pg_cron + pg_net.
--
-- The Vercel deployment is serverless, so nothing runs between requests.
-- Phase deadlines are stored as timestamps; this cron job calls the app's
-- /api/tick endpoint every 15 seconds, which advances any game whose deadline
-- has passed. (Phases usually advance instantly when everyone has acted --
-- the tick is the fallback for players who go silent.)
--
-- ⚠️  NEVER commit your real WEBHOOK_SECRET to git. Replace the placeholder
--     below only inside the Supabase SQL Editor, not in this file.
--
-- STEP 1 -- enable the extensions FIRST, each run on its own (the SQL Editor
-- runs a block as one transaction: if anything in a big block fails,
-- EVERYTHING in it silently rolls back, extensions included).
-- Alternatively enable both via Dashboard -> Database -> Extensions.
--
--     CREATE EXTENSION IF NOT EXISTS pg_net;
--     CREATE EXTENSION IF NOT EXISTS pg_cron;
--
-- Confirm with:
--     SELECT extname FROM pg_extension WHERE extname IN ('pg_cron','pg_net');
--
-- STEP 2 -- replace PASTE-YOUR-SECRET-HERE below with your WEBHOOK_SECRET
-- (same value as in Vercel's environment variables), then run this block.
-- Safe to re-run: it unschedules any previous copy of the job first.
-- ===========================================================================

DO $$
BEGIN
    PERFORM cron.unschedule('ootl-tick');
EXCEPTION WHEN OTHERS THEN
    NULL;
END $$;

SELECT cron.schedule(
    'ootl-tick',
    '15 seconds',
    $$
    SELECT net.http_get(
        url := 'https://out-of-the-loop-pearl.vercel.app/api/tick?secret=PASTE-YOUR-SECRET-HERE'
    );
    $$
);

SELECT jobid, jobname, schedule, active FROM cron.job WHERE jobname = 'ootl-tick';

-- STEP 3 -- verify after ~30 seconds: every row should be status_code 200.
--     SELECT status_code, created FROM net._http_response ORDER BY id DESC LIMIT 5;
