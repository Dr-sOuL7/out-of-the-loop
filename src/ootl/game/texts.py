"""All user-facing copy in one place (HTML parse mode).

Keeping message text here makes the engine/handlers read cleanly and makes the
game's voice easy to tweak. Everything that interpolates user-supplied text
(names, answers, words) is HTML-escaped via :func:`esc`.
"""
from __future__ import annotations

from html import escape

from ootl.game.models import GameState, Player, Round

# Emoji used as a small visual anchor throughout.
E_SPY = "🕵️"


def esc(text: object) -> str:
    """HTML-escape arbitrary text for Telegram's HTML parse mode."""
    return escape(str(text), quote=False)


def mention(player: Player) -> str:
    """An inline mention that pings the user, using their display name."""
    return f'<a href="tg://user?id={player.user_id}">{esc(player.display_name)}</a>'


def mention_id(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name)}</a>'


# ---------------------------------------------------------------------------
# Static help / rules
# ---------------------------------------------------------------------------
def help_text() -> str:
    return (
        "🕵️ <b>Out of the Loop</b> — a social-deduction party game.\n\n"
        "<b>The idea:</b> everyone gets a secret word… except one <b>imposter</b>, "
        "who has no idea what it is. You all answer a question <i>indirectly</i>, "
        "then vote on who the imposter is. If the imposter survives the vote, "
        "they get one shot at guessing the word.\n\n"
        "<b>Group commands</b>\n"
        "• /create — start a new room (you become host)\n"
        "• /join — join the open room\n"
        "• /leave — leave the room\n"
        "• /players — show who's in the lobby\n"
        "• /startgame [rounds] — host starts the match\n"
        "• /score — current scoreboard\n"
        "• /leaderboard — all-time stats for this group\n"
        "• /abort — host cancels the match\n\n"
        "<b>In private chat with me</b>\n"
        "• /start — register so I can DM you your secret role\n"
        "• You'll <b>DM me your answer</b> each round, and your final guess if "
        "you're the imposter.\n\n"
        "<b>Tip:</b> every player must press <b>Start</b> in a private chat with "
        "me first, or I can't send them the secret word!"
    )


def rules_text() -> str:
    return (
        "📜 <b>Rules</b>\n\n"
        "• <b>Clue-holders</b> know the word and must answer naturally — never "
        "say, spell or obviously hint the word.\n"
        "• The <b>imposter</b> doesn't know the word and must blend in.\n"
        "• One answer, one vote and (if you're the imposter) one guess per round.\n"
        "• No editing answers after sending.\n\n"
        "<b>Scoring</b>\n"
        "• Imposter voted out → each clue-holder who voted correctly: <b>+1</b>\n"
        "• Imposter survives the vote → imposter: <b>+2</b>\n"
        "• Imposter survives <i>and</i> guesses the word → <b>+1</b> bonus\n\n"
        "On a tied vote, nobody is eliminated and the imposter survives."
    )


# ---------------------------------------------------------------------------
# Lobby
# ---------------------------------------------------------------------------
def room_created(host: Player, min_players: int) -> str:
    return (
        f"{E_SPY} <b>New room created!</b>\n\n"
        f"Host: {mention(host)}\n\n"
        f"Tap <b>Join</b> below or send /join to play.\n"
        f"You need at least <b>{min_players}</b> players. "
        f"When everyone's in, the host sends /startgame."
    )


def already_have_room() -> str:
    return (
        "There's already an open room in this chat. Send /join to play, "
        "/players to see who's in, or /abort (host) to cancel it."
    )


def player_list(game: GameState, min_players: int, max_players: int) -> str:
    lines = [f"{E_SPY} <b>Lobby</b> — {game.player_count}/{max_players} players\n"]
    for i, player in enumerate(game.players.values(), start=1):
        tag = " 👑" if game.is_host(player.user_id) else ""
        lines.append(f"{i}. {mention(player)}{tag}")
    lines.append("")
    if game.player_count < min_players:
        need = min_players - game.player_count
        lines.append(f"Need <b>{need}</b> more to start.")
    else:
        lines.append("Ready! Host can send /startgame.")
    return "\n".join(lines)


def joined(player: Player, count: int) -> str:
    return f"✅ {mention(player)} joined. ({count} in the lobby)"


def left(name: str, count: int) -> str:
    return f"👋 {esc(name)} left the room. ({count} remaining)"


def not_in_room() -> str:
    return "There's no open room here. Send /create to start one."


def already_joined() -> str:
    return "You're already in the lobby. 😊"


def room_full(max_players: int) -> str:
    return f"Sorry, the room is full ({max_players} players)."


def only_host(action: str = "do that") -> str:
    return f"Only the host can {action}."


def need_more_players(have: int, need: int) -> str:
    return (
        f"Not enough players to start — you have <b>{have}</b>, "
        f"need <b>{need}</b>. Get more friends to /join!"
    )


def dm_required(missing: list[str]) -> str:
    names = ", ".join(esc(n) for n in missing)
    return (
        "⚠️ I can't start yet — these players haven't opened a private chat "
        f"with me, so I can't DM them their secret role:\n\n<b>{names}</b>\n\n"
        "Each of them must open a chat with me and press <b>Start</b> (send "
        "/start), then the host can /startgame again."
    )


# ---------------------------------------------------------------------------
# Round flow
# ---------------------------------------------------------------------------
def match_starting(total_rounds: int) -> str:
    return (
        f"{E_SPY} <b>Game on!</b> {total_rounds} round"
        f"{'s' if total_rounds != 1 else ''} ahead. Dealing secret roles in "
        f"your DMs now…"
    )


def role_clue_holder(word: str, category: str, round_no: int, total: int) -> str:
    return (
        f"🤫 <b>Round {round_no}/{total}</b>\n\n"
        f"The secret word is:\n\n<b>{esc(word)}</b>\n"
        f"<i>(category: {esc(category)})</i>\n\n"
        "Answer the upcoming question <b>naturally</b> — give it away to fellow "
        "clue-holders, but don't make it obvious to the imposter. Don't say or "
        "spell the word!"
    )


def role_imposter(category: str, round_no: int, total: int) -> str:
    return (
        f"🎭 <b>Round {round_no}/{total}</b>\n\n"
        "You are the <b>IMPOSTER</b>. You're <i>out of the loop</i> — you don't "
        "know the secret word!\n\n"
        f"The category is <b>{esc(category)}</b>.\n\n"
        "🕵️ I'll secretly forward the other players' answers to you here as "
        "they come in — use them to work out the word and blend in. Send me "
        "your own answer any time before the timer runs out. If you survive "
        "the vote, you'll get one chance to guess the word."
    )


def answer_forward(answer: str) -> str:
    """Live intel line DM'd to the imposter as clue-holders answer."""
    return f"👀 Intercepted answer: <i>“{esc(answer)}”</i>"


OPTION_LETTERS = ["A", "B", "C", "D"]


def _options_block(options: list[str]) -> str:
    return "\n".join(
        f"{OPTION_LETTERS[i]}) {esc(opt)}" for i, opt in enumerate(options)
    )


def question_post(
    question: str,
    category: str,
    round_no: int,
    total: int,
    seconds: int,
    options: list[str] | None = None,
) -> str:
    head = (
        f"{E_SPY} <b>Round {round_no}/{total}</b> — category: <b>{esc(category)}</b>\n\n"
        f"❓ <b>{esc(question)}</b>\n\n"
    )
    if options:
        return head + (
            f"{_options_block(options)}\n\n"
            f"🔘 <b>Pick your option in my DM</b> within <b>{seconds}s</b>. "
            "Choices are hidden until the reveal!"
        )
    return head + (
        f"💬 <b>DM me your answer</b> (in our private chat) within "
        f"<b>{seconds}s</b>.\n"
        "Don't reveal the word — and don't answer here in the group!"
    )


def answer_dm_prompt(question: str) -> str:
    return (
        "✍️ Your turn! Reply here with your answer to:\n\n"
        f"❓ <b>{esc(question)}</b>\n\n"
        "Keep it short and natural — one line is perfect."
    )


def answer_dm_options(question: str) -> str:
    return (
        "🔘 Your turn! Pick the option that fits best:\n\n"
        f"❓ <b>{esc(question)}</b>\n\n"
        "There's no right answer — but your pick says a lot. Choose wisely; "
        "the first tap locks in."
    )


def answer_picked(question: str, option: str) -> str:
    return (
        f"❓ <b>{esc(question)}</b>\n\n"
        f"🔒 You picked: <b>{esc(option)}</b>\n"
        "Sit tight — all picks are revealed together."
    )


def answer_received(answer: str) -> str:
    return (
        f"✅ Got it: <i>“{esc(answer)}”</i>\n"
        "Sit tight — answers are revealed together."
    )


def answer_updated(answer: str) -> str:
    return f"✏️ Updated your answer to: <i>“{esc(answer)}”</i>"


def answer_too_late() -> str:
    return "⏰ Too late — the answer phase for that round is closed."


def not_your_turn_to_answer() -> str:
    return "There's no answer phase running for you right now."


def answer_progress(done: int, total: int) -> str:
    return f"📝 Answers in: <b>{done}/{total}</b>"


def answers_revealed(game: GameState, rnd: Round, timed_out: bool = False) -> str:
    if timed_out:
        lines = ["⏰ <b>Time's up!</b> Locking in the answers we have…\n"]
    else:
        lines = [f"{E_SPY} <b>Answers are in!</b>\n"]
    # Reveal in submission order for a little extra intrigue.
    order = rnd.answer_order or list(rnd.answers.keys())
    for i, uid in enumerate(order, start=1):
        name = game.display_name(uid)
        ans = rnd.answers.get(uid, "—")
        lines.append(f"{i}. <b>{esc(name)}</b>: <i>“{esc(ans)}”</i>")
    missing = [uid for uid in rnd.participant_ids if uid not in rnd.answers]
    for uid in missing:
        lines.append(f"• <b>{esc(game.display_name(uid))}</b>: <i>(no answer)</i>")
    lines.append("\n🗳️ Voting opens now — who's the imposter?")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Voting
# ---------------------------------------------------------------------------
def voting_open(seconds: int) -> str:
    return (
        f"🗳️ <b>Vote!</b> Tap the name of who you think is the imposter.\n"
        f"You have <b>{seconds}s</b>. Votes are hidden until the reveal — you can "
        "change your pick until time runs out."
    )


def vote_recorded(target_name: str) -> str:
    return f"🔒 Vote locked in for <b>{esc(target_name)}</b>."


def vote_changed(target_name: str) -> str:
    return f"🔁 Changed your vote to <b>{esc(target_name)}</b>."


def vote_not_participant() -> str:
    return "You're not playing in this round."


def vote_for_self() -> str:
    return "You can't vote for yourself! 🙃"


def vote_progress(done: int, total: int) -> str:
    return f"🗳️ Votes in: <b>{done}/{total}</b>"


# ---------------------------------------------------------------------------
# Reveal / guess / scoring
# ---------------------------------------------------------------------------
def reveal(
    game: GameState,
    rnd: Round,
    counts: dict[int, int],
    imposter_caught: bool,
    is_tie: bool,
    no_votes: bool,
) -> str:
    imposter_name = game.display_name(rnd.imposter_id)
    lines = [f"{E_SPY} <b>The vote is in…</b>\n"]

    # Tally lines, highest first.
    if counts:
        ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        lines.append("<b>Votes:</b>")
        for uid, c in ordered:
            lines.append(f"• {esc(game.display_name(uid))}: {c}")
        lines.append("")

    if no_votes:
        lines.append("Nobody voted! 😶 The imposter slips away…")
    elif is_tie:
        lines.append("It's a <b>tie</b> — nobody is eliminated, so the imposter survives!")

    lines.append(f"\n🎭 The imposter was: <b>{esc(imposter_name)}</b>")

    if imposter_caught:
        # Word is only revealed once the imposter is caught. If they survive,
        # it stays hidden until their final guess is resolved.
        lines.append(f"🔑 The secret word was: <b>{esc(rnd.secret_word)}</b>")
        lines.append("\n✅ <b>Caught!</b> The clue-holders win this round.")
    else:
        lines.append(
            "\n😈 <b>The imposter survived!</b> The secret word stays hidden — "
            "one final guess incoming…"
        )
    return "\n".join(lines)


def imposter_guess_prompt(category: str, seconds: int) -> str:
    return (
        "😈 You survived the vote!\n\n"
        f"One last chance: <b>guess the secret word</b> (category: "
        f"<b>{esc(category)}</b>). Reply here within <b>{seconds}s</b>. "
        "One attempt only — get it right for a bonus point."
    )


def imposter_guess_announce(name: str, seconds: int) -> str:
    return (
        f"😈 {esc(name)} survived and is making their final guess "
        f"(within {seconds}s)…"
    )


def guess_correct(game: GameState, rnd: Round) -> str:
    return (
        f"🎯 The imposter guessed <b>{esc(rnd.imposter_guess)}</b> — "
        f"<b>correct!</b> The word was <b>{esc(rnd.secret_word)}</b>. "
        "Bonus point to the imposter!"
    )


def guess_wrong(game: GameState, rnd: Round) -> str:
    guess = rnd.imposter_guess or "(no guess)"
    return (
        f"❌ The imposter guessed <b>{esc(guess)}</b> — wrong! "
        f"The word was <b>{esc(rnd.secret_word)}</b>."
    )


def guess_dm_correct(word: str) -> str:
    return f"🎯 Correct! The word was <b>{esc(word)}</b>. +1 bonus point!"


def guess_dm_wrong(word: str) -> str:
    return f"❌ Not quite — the word was <b>{esc(word)}</b>."


def round_scores(game: GameState, awards_text: list[str]) -> str:
    lines = ["📊 <b>Round scoring</b>\n"]
    if awards_text:
        lines.extend(awards_text)
    else:
        lines.append("No points awarded this round.")
    lines.append("\n<b>Match scoreboard</b>")
    lines.append(scoreboard_block(game))
    return "\n".join(lines)


def scoreboard_block(game: GameState) -> str:
    board = game.leaderboard()
    if not board:
        return "—"
    medals = ["🥇", "🥈", "🥉"]
    rows = []
    for i, (uid, pts) in enumerate(board):
        prefix = medals[i] if i < len(medals) else f"{i + 1}."
        rows.append(f"{prefix} {esc(game.display_name(uid))} — <b>{pts}</b>")
    return "\n".join(rows)


def scoreboard(game: GameState) -> str:
    return (
        f"📊 <b>Scoreboard</b> (round {game.current_round_number}/"
        f"{game.total_rounds})\n\n" + scoreboard_block(game)
    )


def next_round_in(seconds: int) -> str:
    return f"\n⏭️ Next round in {seconds}s…"


def match_over(game: GameState) -> str:
    board = game.leaderboard()
    lines = [f"🏁 <b>Match over!</b> {game.total_rounds} rounds played.\n"]
    lines.append(scoreboard_block(game))
    if board:
        top_points = board[0][1]
        winners = [uid for uid, p in board if p == top_points and top_points > 0]
        lines.append("")
        if not winners:
            lines.append("No points scored — a true mystery. 🤔")
        elif len(winners) == 1:
            lines.append(f"🏆 <b>Winner: {esc(game.display_name(winners[0]))}!</b>")
        else:
            names = ", ".join(esc(game.display_name(u)) for u in winners)
            lines.append(f"🏆 <b>It's a tie: {names}!</b>")
    lines.append("\nThanks for playing! Send /create to go again.")
    return "\n".join(lines)


def all_time_leaderboard(rows: list[dict]) -> str:
    if not rows:
        return "No games played in this bot yet. Be the first — /create!"
    lines = ["🏆 <b>All-time leaderboard</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        prefix = medals[i] if i < len(medals) else f"{i + 1}."
        name = row.get("display_name") or "Player"
        lines.append(
            f"{prefix} {esc(name)} — <b>{row['total_points']}</b> pts "
            f"({row['wins']} wins / {row['games_played']} games)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Misc / errors
# ---------------------------------------------------------------------------
def aborted(name: str) -> str:
    return f"🛑 The match was aborted by {esc(name)}. Send /create to start over."


def game_already_running() -> str:
    return "A match is already running here. Use /abort (host) to stop it first."


def dm_welcome(name: str) -> str:
    return (
        f"👋 Hi {esc(name)}! You're registered — I can now send you secret roles.\n\n"
        "Add me to a group with your friends, then send /create there to start "
        "a game. Send /help any time for the rules."
    )


def private_only_hint() -> str:
    return "Send game commands like /create and /join in your <b>group chat</b>."


def round_aborted_not_enough() -> str:
    return (
        "⚠️ Too many players left — not enough remain to continue. "
        "Match aborted. Send /create to start again."
    )
