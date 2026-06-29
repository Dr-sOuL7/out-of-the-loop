-- Out of the Loop -- SQLite schema.
-- Safe to run repeatedly: every table uses IF NOT EXISTS.

PRAGMA foreign_keys = ON;

-- ===========================================================================
-- Players (persistent profile + all-time stats)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,          -- Telegram user id
    username      TEXT,                          -- @handle (may be null)
    display_name  TEXT NOT NULL DEFAULT '',
    total_points  INTEGER NOT NULL DEFAULT 0,
    games_played  INTEGER NOT NULL DEFAULT 0,
    wins          INTEGER NOT NULL DEFAULT 0,
    dm_ok         INTEGER NOT NULL DEFAULT 0,    -- 1 once the user has DM'd /start
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ===========================================================================
-- Rooms (one open lobby per group chat at a time)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS rooms (
    room_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    host_id     INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'LOBBY',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rooms_chat ON rooms (chat_id);

-- ===========================================================================
-- Matches (a full game session = several rounds)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS matches (
    match_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id        INTEGER,
    chat_id        INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'STARTING',
    total_rounds   INTEGER NOT NULL DEFAULT 5,
    current_round  INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_matches_chat ON matches (chat_id);

-- ===========================================================================
-- Rounds (one complete cycle)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS rounds (
    round_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id                  INTEGER NOT NULL,
    round_number              INTEGER NOT NULL,
    category                  TEXT,
    secret_word               TEXT,
    question                  TEXT,
    imposter_id               INTEGER,
    state                     TEXT NOT NULL DEFAULT 'ASSIGNING_ROLES',
    imposter_caught           INTEGER,           -- 1/0/null
    imposter_guess            TEXT,
    imposter_guessed_correct  INTEGER,           -- 1/0/null
    started_at                TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at                  TEXT,
    FOREIGN KEY (match_id) REFERENCES matches (match_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rounds_match ON rounds (match_id);

-- ===========================================================================
-- Answers (one per clue-holder / imposter per round)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS answers (
    answer_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    answer_text   TEXT NOT NULL,
    submitted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (round_id, user_id),
    FOREIGN KEY (round_id) REFERENCES rounds (round_id) ON DELETE CASCADE
);

-- ===========================================================================
-- Votes (one per voter per round)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS votes (
    vote_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id      INTEGER NOT NULL,
    voter_id      INTEGER NOT NULL,
    target_id     INTEGER NOT NULL,
    submitted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (round_id, voter_id),
    FOREIGN KEY (round_id) REFERENCES rounds (round_id) ON DELETE CASCADE
);

-- ===========================================================================
-- Scores (every points award, for an audit trail)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS scores (
    score_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL,
    round_id        INTEGER,
    user_id         INTEGER NOT NULL,
    points_awarded  INTEGER NOT NULL,
    reason          TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (match_id) REFERENCES matches (match_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_scores_match ON scores (match_id);

-- ===========================================================================
-- Content tables (populated from JSON by the importer; read via ContentManager)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS content_categories (
    key          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT
);

CREATE TABLE IF NOT EXISTS content_words (
    word_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL UNIQUE,
    category      TEXT NOT NULL,
    difficulty    INTEGER NOT NULL DEFAULT 2,
    active        INTEGER NOT NULL DEFAULT 1,
    times_used    INTEGER NOT NULL DEFAULT 0,
    last_used_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_words_cat ON content_words (category);

CREATE TABLE IF NOT EXISTS content_questions (
    question_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL UNIQUE,
    category      TEXT,                          -- null = generic (any category)
    difficulty    INTEGER NOT NULL DEFAULT 2,
    active        INTEGER NOT NULL DEFAULT 1,
    times_used    INTEGER NOT NULL DEFAULT 0,
    last_used_at  TEXT
);

CREATE TABLE IF NOT EXISTS content_banned (
    term  TEXT PRIMARY KEY
);
