# Out of the Loop 🕵️

A fast, social-deduction **party game for Telegram groups**.

One player is the **imposter** — they don't know the secret word. Everyone else
does. Players answer a prompt *indirectly* (without giving the word away), then
vote on who seems suspicious. If the imposter survives the vote, they get one
final chance to guess the word.

Short rounds, high tension, low friction, strong replay value.

---

## How a round plays

1. Host creates a room in a group chat: `/create`
2. Players join: `/join` (or tap the **Join** button)
3. Host starts: `/startgame`
4. The bot privately DMs the **secret word** to everyone except the imposter,
   who is told they're *"Out of the Loop."*
5. The bot posts a **question** to the group (e.g. *"How would you use it?"*).
6. Everyone **DMs their answer** to the bot. Answers are revealed together.
7. The bot opens **voting** — one tap, one vote, hidden until the deadline.
8. The bot **reveals** the imposter and the vote tally.
9. If the imposter survived, they get **one private guess** at the word.
10. **Scores** are awarded and the next round begins.

After the configured number of rounds, the bot shows the final leaderboard.

### Scoring (v1)

| Situation | Points |
| --- | --- |
| Imposter voted out → each clue-holder who voted correctly | **+1** |
| Imposter survives the vote → imposter | **+2** |
| Imposter survives **and** guesses the word | **+1** bonus |

No negative points — casual-friendly by design.

---

## Commands

| Command | Where | Who | Description |
| --- | --- | --- | --- |
| `/start` | DM | anyone | Register with the bot (required so it can DM you). |
| `/help` | anywhere | anyone | Show the rules and commands. |
| `/create` | group | anyone | Create a room; you become the host. |
| `/join` | group | anyone | Join the open room. |
| `/leave` | group | player | Leave the room. |
| `/players` | group | anyone | Show the current lobby. |
| `/startgame [rounds]` | group | host | Start the match (optional round count). |
| `/score` | group | anyone | Show the current match scoreboard. |
| `/leaderboard` | group | anyone | Show all-time stats for this group. |
| `/abort` | group | host | Abort the current match. |

Players answer the prompt and make the final guess by **DMing the bot** while
the relevant phase is open. Voting is done with inline buttons in the group.

---

## Architecture

The codebase follows the design doc's "content pipeline, not a static list"
recommendation:

```
src/ootl/
├── config.py            # env-driven settings
├── app.py               # builds the bot, registers handlers, startup hooks
├── __main__.py          # `python -m ootl` entry point
├── db/                  # SQLite: schema + async-wrapped access + repositories
├── content/             # JSON source-of-truth + importer + ContentManager
│   └── data/            # words.json, questions.json, categories.json, banned.json
├── game/                # the actual game engine
│   ├── enums.py         # MatchState machine + transitions
│   ├── models.py        # Room / Match / Round / Player / Vote runtime state
│   ├── scoring.py       # pure scoring logic
│   ├── tally.py         # pure vote counting + tie detection
│   ├── engine.py        # phase orchestration via the JobQueue timers
│   └── texts.py         # all user-facing message copy
├── handlers/            # Telegram command / callback / message handlers
└── utils/               # text normalisation & guess matching
```

**Content flow:** JSON files are the editable source of truth → imported into
SQLite → the `ContentManager` selects words/prompts, avoids repetition, filters
by category/difficulty, enforces banned terms, and tracks usage. The bot never
touches the content tables directly.

**State machine:** every match is driven by a strict state machine
(`LOBBY → STARTING → ASSIGNING_ROLES → QUESTION_PHASE → ANSWER_COLLECTION →
VOTING_PHASE → REVEAL_PHASE → IMPOSTER_GUESS_PHASE → SCORING_PHASE → ROUND_END →
MATCH_END`) so late votes, double starts and duplicate answers are impossible.

---

## Setup & running

### 1. Get a bot token

In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts.
You'll receive a token like `123456789:ABCdef...`.

> **Important:** so the imposter/word can be delivered privately, the bot must be
> able to DM players. Also message **@BotFather → `/setprivacy` → Disable** for
> your bot so it can read commands in groups, and add the bot to your group.

### 2. Configure

```bash
cp .env.example .env
# edit .env and paste your token into BOT_TOKEN=
```

### 3. Install & run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Import the JSON content into SQLite (also runs automatically on first start):
python -m ootl.content.importer

# Start the bot (long-polling):
python run.py
```

### Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

### Deploying / hosting

The bot is a persistent long-polling **worker** (no web port). To host it on a
VPS, Fly.io, Railway or Render, see **[DEPLOY.md](DEPLOY.md)** — it includes a
`Dockerfile`, `docker-compose.yml`, a Render blueprint, and an explanation of
why serverless platforms (e.g. Vercel) need a rewrite for this kind of bot.

```bash
# Quickest self-host, on any machine with Docker:
cp .env.example .env   # add your BOT_TOKEN
docker compose up -d --build
```

---

## Configuration reference

All settings live in `.env` (see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `BOT_TOKEN` | — | **Required.** Token from @BotFather. |
| `DATABASE_PATH` | `data/ootl.db` | SQLite file location. |
| `ANSWER_TIME_SECONDS` | `90` | Answer-collection window. |
| `VOTE_TIME_SECONDS` | `60` | Voting window. |
| `GUESS_TIME_SECONDS` | `45` | Imposter final-guess window. |
| `MIN_PLAYERS` | `3` | Minimum players to start. |
| `MAX_PLAYERS` | `9` | Maximum players in a room. |
| `DEFAULT_ROUNDS` | `5` | Rounds per match if the host doesn't specify. |
| `WORD_HISTORY_WINDOW` | `40` | Recently-used words to avoid repeating. |
| `QUESTION_HISTORY_WINDOW` | `20` | Recently-used questions to avoid repeating. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. |

---

## Editing game content

`src/ootl/content/data/` holds the editable source of truth:

- **`categories.json`** — category definitions.
- **`words.json`** — words with `category` and `difficulty` (1–3).
- **`questions.json`** — prompt templates, optionally category-specific.
- **`banned.json`** — terms that must never be selected.

After editing, re-import:

```bash
python -m ootl.content.importer --reset
```

(`--reset` rebuilds the content tables from the JSON; usage stats are preserved
where possible by word/question text.)

---

## Design decisions locked for v1

- **Match length:** host-set fixed rounds (default 5); winner = top scorer.
- **Tie vote:** *no elimination* — the imposter survives and gets to guess.
- **Answers:** collected privately via DM, revealed together.
- **One** question, **one** imposter, **one** vote and **one** guess per round.

See the design document for the full v2 roadmap (multiple questions, themed
packs, ranked rooms, tournaments, achievements, multilingual content).
