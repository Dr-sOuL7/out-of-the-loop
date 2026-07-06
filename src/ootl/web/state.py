"""(De)serialize the in-memory GameState/Round models to/from JSONB.

The webhook engine reuses the exact dataclasses from ``ootl.game.models`` so
all message-formatting code in ``ootl.game.texts`` works unchanged; this module
is just the JSON round-trip. JSON object keys are strings, so int-keyed dicts
(user ids) are converted on the way in/out.
"""
from __future__ import annotations

from ootl.game.enums import MatchState
from ootl.game.models import GameState, Player, Round


def game_to_json(game: GameState) -> dict:
    return {
        "chat_id": game.chat_id,
        "host_id": game.host_id,
        "state": game.state.value,
        "players": [
            {"user_id": p.user_id, "display_name": p.display_name, "username": p.username}
            for p in game.players.values()
        ],
        "total_rounds": game.total_rounds,
        "current_round_number": game.current_round_number,
        "match_id": game.match_id,
        "scores": {str(uid): pts for uid, pts in game.scores.items()},
        "lobby_message_id": game.lobby_message_id,
        "deadline_kind": game.deadline_kind,
        "imposter_counts": {str(uid): n for uid, n in game.imposter_counts.items()},
        "last_imposter_id": game.last_imposter_id,
        "round": _round_to_json(game.current_round),
    }


def game_from_json(data: dict) -> GameState:
    game = GameState(
        chat_id=data["chat_id"],
        host_id=data["host_id"],
        state=MatchState(data["state"]),
        total_rounds=data.get("total_rounds", 5),
        current_round_number=data.get("current_round_number", 0),
        match_id=data.get("match_id"),
        lobby_message_id=data.get("lobby_message_id"),
        deadline_kind=data.get("deadline_kind"),
    )
    for p in data.get("players", []):
        game.players[p["user_id"]] = Player(
            user_id=p["user_id"],
            display_name=p["display_name"],
            username=p.get("username"),
        )
    game.scores = {int(uid): pts for uid, pts in data.get("scores", {}).items()}
    game.imposter_counts = {
        int(uid): n for uid, n in data.get("imposter_counts", {}).items()
    }
    game.last_imposter_id = data.get("last_imposter_id")
    game.current_round = _round_from_json(data.get("round"))
    return game


def _round_to_json(rnd: Round | None) -> dict | None:
    if rnd is None:
        return None
    return {
        "round_number": rnd.round_number,
        "category": rnd.category,
        "secret_word": rnd.secret_word,
        "question": rnd.question,
        "options": rnd.options,
        "imposter_id": rnd.imposter_id,
        "round_id": rnd.round_id,
        "participant_ids": rnd.participant_ids,
        "answers": {str(uid): a for uid, a in rnd.answers.items()},
        "answer_order": rnd.answer_order,
        "votes": {str(v): t for v, t in rnd.votes.items()},
        "eliminated_id": rnd.eliminated_id,
        "imposter_caught": rnd.imposter_caught,
        "imposter_guess": rnd.imposter_guess,
        "imposter_guessed_correct": rnd.imposter_guessed_correct,
        "voting_message_id": rnd.voting_message_id,
    }


def _round_from_json(data: dict | None) -> Round | None:
    if data is None:
        return None
    rnd = Round(
        round_number=data["round_number"],
        category=data["category"],
        secret_word=data["secret_word"],
        question=data["question"],
        options=list(data.get("options", [])),
        imposter_id=data["imposter_id"],
        round_id=data.get("round_id"),
        participant_ids=list(data.get("participant_ids", [])),
    )
    rnd.answers = {int(uid): a for uid, a in data.get("answers", {}).items()}
    rnd.answer_order = list(data.get("answer_order", []))
    rnd.votes = {int(v): t for v, t in data.get("votes", {}).items()}
    rnd.eliminated_id = data.get("eliminated_id")
    rnd.imposter_caught = data.get("imposter_caught")
    rnd.imposter_guess = data.get("imposter_guess")
    rnd.imposter_guessed_correct = data.get("imposter_guessed_correct")
    rnd.voting_message_id = data.get("voting_message_id")
    return rnd
