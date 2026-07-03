"""Serverless (Vercel + Supabase) deployment of Out of the Loop.

Same game, different runtime: Telegram calls /api/webhook per update, all live
state lives in Postgres JSONB rows locked per chat, and a Supabase pg_cron job
calls /api/tick to fire expired phase deadlines. The pure game logic
(enums/scoring/tally/texts/models) is shared with the long-polling worker.
"""
