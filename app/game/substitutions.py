"""Deterministic automatic substitutions for bot-controlled matches.

Architecture adapted from the like-for-like, injury/fatigue-triggered auto-sub
approach documented by Global Soccer Manager and the same-position bench model
used by cmanager. No engine state is mutated here; the caller supplies the
current active XI, bench and fatigue/injury/card state.
"""

MAX_SUBSTITUTIONS = 5


def _value(player, key, default=None):
    if isinstance(player, dict):
        return player.get(key, default)
    return getattr(player, key, default)


def _id(player):
    return int(_value(player, "id"))


def _position(player):
    return str(_value(player, "position", ""))


def _score(player):
    return (
        int(_value(player, "stamina", 0) or 0),
        int(_value(player, "fitness", 0) or 0),
        int(_value(player, "potential", 0) or 0),
    )


def _choose_replacement(player_out, bench, used_in):
    position = _position(player_out)
    candidates = [p for p in bench if _id(p) not in used_in and _position(p) == position]
    return max(candidates, key=_score, default=None)


def auto_substitution(
    home_players, away_players,
    home_fatigue=None, away_fatigue=None,
    tactics=None, board=None,
    home_bench=None, away_bench=None,
    current_minute=60,
    home_injuries=None, away_injuries=None,
    home_red_cards=None, away_red_cards=None,
    home_substitutions_used=0, away_substitutions_used=0,
    max_substitutions=MAX_SUBSTITUTIONS,
):
    """Return at most one eligible automatic substitution per bot side.

    Injury has priority over fatigue. A red-carded player is never selected.
    Replacements are strictly like-for-like by position and the per-team limit
    is five. The returned records are scheduling records, not DB writes.
    """
    if int(current_minute) < 46 or int(current_minute) > 90:
        return []

    out = []
    specs = (
        ("HOME", home_players, home_bench or [], home_fatigue or {}, home_injuries or set(), home_red_cards or set(), home_substitutions_used),
        ("AWAY", away_players, away_bench or [], away_fatigue or {}, away_injuries or set(), away_red_cards or set(), away_substitutions_used),
    )
    for team, players, bench, fatigue, injuries, red_cards, used in specs:
        if int(used) >= int(max_substitutions):
            continue
        active_ids = {_id(p) for p in players}
        used_in = set(active_ids)
        injured = [p for p in players if _id(p) in {int(x) for x in injuries} and _id(p) not in {int(x) for x in red_cards}]
        candidate = injured[0] if injured else None
        reason = "травма" if candidate is not None else None
        if candidate is None:
            tired = [p for p in players if _id(p) not in {int(x) for x in red_cards} and float(fatigue.get(_id(p), 0.0)) > 80.0]
            if tired:
                candidate = max(tired, key=lambda p: float(fatigue.get(_id(p), 0.0)))
                reason = "усталость > 80"
        if candidate is None:
            continue
        replacement = _choose_replacement(candidate, bench, used_in)
        if replacement is None:
            continue
        out.append({
            "team": team,
            "minute": int(current_minute),
            "player_out": candidate,
            "player_in": replacement,
            "reason": reason,
        })
    return out
