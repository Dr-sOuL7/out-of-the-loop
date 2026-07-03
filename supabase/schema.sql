-- ===========================================================================
-- Out of the Loop -- Supabase (Postgres) schema for the Vercel deployment.
--
-- HOW TO USE: in your Supabase project, open "SQL Editor", paste this whole
-- file, and click Run. Safe to run repeatedly (IF NOT EXISTS everywhere).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Players (persistent profile + all-time stats)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id       BIGINT PRIMARY KEY,            -- Telegram user id
    username      TEXT,
    display_name  TEXT NOT NULL DEFAULT '',
    total_points  INTEGER NOT NULL DEFAULT 0,
    games_played  INTEGER NOT NULL DEFAULT 0,
    wins          INTEGER NOT NULL DEFAULT 0,
    dm_ok         BOOLEAN NOT NULL DEFAULT FALSE, -- true once the user DMs /start
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Live game state: ONE row per group chat with an open lobby or running match.
-- The whole GameState is stored as JSONB; per-chat row locking (SELECT ...
-- FOR UPDATE) serialises concurrent webhook invocations for the same game.
-- deadline_at lets the cron tick find games whose phase timer has expired.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS live_games (
    chat_id      BIGINT PRIMARY KEY,
    state        JSONB NOT NULL,
    deadline_at  TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_live_games_deadline
    ON live_games (deadline_at) WHERE deadline_at IS NOT NULL;

-- Maps a player to the chat of the match they're playing (routes private DMs).
CREATE TABLE IF NOT EXISTS player_chat (
    user_id  BIGINT PRIMARY KEY,
    chat_id  BIGINT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Match / round history (audit + stats; the live state above is the runtime)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches (
    match_id       BIGSERIAL PRIMARY KEY,
    chat_id        BIGINT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    total_rounds   INTEGER NOT NULL DEFAULT 5,
    current_round  INTEGER NOT NULL DEFAULT 0,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS rounds (
    round_id                  BIGSERIAL PRIMARY KEY,
    match_id                  BIGINT NOT NULL REFERENCES matches (match_id) ON DELETE CASCADE,
    round_number              INTEGER NOT NULL,
    category                  TEXT,
    secret_word               TEXT,
    question                  TEXT,
    imposter_id               BIGINT,
    state                     TEXT NOT NULL DEFAULT 'ASSIGNING_ROLES',
    imposter_caught           BOOLEAN,
    imposter_guess            TEXT,
    imposter_guessed_correct  BOOLEAN,
    started_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at                  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS answers (
    answer_id     BIGSERIAL PRIMARY KEY,
    round_id      BIGINT NOT NULL REFERENCES rounds (round_id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL,
    answer_text   TEXT NOT NULL,
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (round_id, user_id)
);

CREATE TABLE IF NOT EXISTS votes (
    vote_id       BIGSERIAL PRIMARY KEY,
    round_id      BIGINT NOT NULL REFERENCES rounds (round_id) ON DELETE CASCADE,
    voter_id      BIGINT NOT NULL,
    target_id     BIGINT NOT NULL,
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (round_id, voter_id)
);

CREATE TABLE IF NOT EXISTS scores (
    score_id        BIGSERIAL PRIMARY KEY,
    match_id        BIGINT NOT NULL REFERENCES matches (match_id) ON DELETE CASCADE,
    round_id        BIGINT,
    user_id         BIGINT NOT NULL,
    points_awarded  INTEGER NOT NULL,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Content usage tracking (words/questions live in JSON files shipped with the
-- app; this table only records usage for anti-repetition + stats).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS content_usage (
    kind          TEXT NOT NULL,      -- 'word' | 'question'
    item          TEXT NOT NULL,
    times_used    INTEGER NOT NULL DEFAULT 0,
    last_used_at  TIMESTAMPTZ,
    PRIMARY KEY (kind, item)
);
