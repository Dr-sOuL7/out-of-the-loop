-- ===========================================================================
-- Out of the Loop -- phase-timer tick via Supabase pg_cron + pg_net.
--
-- The Vercel deployment is serverless, so nothing runs between requests.
-- Phase deadlines (answer/vote/guess timers) are stored as timestamps; this
-- cron job calls the app's /api/tick endpoint every 15 seconds, which
-- advances any game whose deadline has passed. (During active play phases
-- usually advance instantly when everyone has acted -- the tick is the
-- fallback for players who go silent.)
--
-- HOW TO USE:
--   1. Replace the two placeholders below:
--        YOUR-APP.vercel.app   -> your real Vercel domain
--        YOUR-WEBHOOK-SECRET   -> the same value you set as WEBHOOK_SECRET
--                                 in Vercel's environment variables
--   2. Paste the whole file into the Supabase SQL Editor and Run.
--
-- Safe to re-run: it unschedules any previous copy of the job first.
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;

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
        url := 'https://out-of-the-loop-pearl.vercel.app/api/tick?secret=01248163264128256512102420484096'
    );
    $$
);

SELECT jobid, jobname, schedule, active FROM cron.job WHERE jobname = 'ootl-tick';
