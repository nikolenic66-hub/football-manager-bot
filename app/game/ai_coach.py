"""Lightweight in-match AI coach for bot-controlled teams.

The decision shape is adapted from the public fm26 AI tactical-adjustment
approach: evaluate score, match minute, performance indicators and stamina,
then make threshold-based tactical changes.  We intentionally keep it small
and deterministic for this project's immutable ``Tactics`` model: no new
framework, no persistence and no database state are introduced here.
"""

from app.game.tactics import Style, Tactics


def _clamp(value):
    return max(0, min(100, int(value)))


def _avg(value):
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def adjust_tactics(tactics, match_context):
    """Return the bot's next tactics for the supplied match state.

    Adapted from fm26's threshold/urgency idea (late-game score pressure,
    performance and stamina) but reduced to the sliders and five styles used
    by this project.  The returned ``Tactics`` is a new immutable value.

    The function is deliberately idempotent for the same tactical state:
    repeated 5-minute evaluations do not keep adding +15/+10/+10 forever.
    We move toward situation-specific targets instead of accumulating deltas.
    """
    if not isinstance(tactics, Tactics):
        raise TypeError('tactics must be a Tactics instance')
    context = dict(match_context or {})
    minute = int(context.get('minute') or 0)
    if minute < 60:
        return tactics

    home_score = int(context.get('home_score') or 0)
    away_score = int(context.get('away_score') or 0)
    is_home = bool(context.get('is_home'))
    own_score = home_score if is_home else away_score
    opp_score = away_score if is_home else home_score
    losing = own_score < opp_score
    winning = own_score > opp_score
    draw = own_score == opp_score

    possession = _avg(context.get('possession', 50))
    own_sot = int(context.get('shots_on_target_home' if is_home else 'shots_on_target_away') or 0)
    opp_sot = int(context.get('shots_on_target_away' if is_home else 'shots_on_target_home') or 0)
    own_fatigue = _avg(context.get('fatigue_home' if is_home else 'fatigue_away', 0))
    opp_fatigue = _avg(context.get('fatigue_away' if is_home else 'fatigue_home', 0))

    # fm26-inspired urgency: late minutes and negative score state matter most;
    # possession/shots/stamina refine the decision instead of overriding it.
    performance_bad = possession < 45 or own_sot < opp_sot
    opponent_tired = opp_fatigue >= 55
    own_tired = own_fatigue >= 65

    style = tactics.style
    tempo, pressing, width, defensive_line, aggression = (
        tactics.tempo, tactics.pressing, tactics.width,
        tactics.defensive_line, tactics.aggression,
    )
    formation = tactics.formation

    if losing and minute >= 60:
        # Required late chase: attack + faster tempo/press/width/high line.
        # 0:3+ is intentionally less reckless: keep ATTACK but do not stack
        # another all-in formation jump; pressing can still rise.
        style = Style.ATTACK
        if tactics.style != Style.ATTACK:
            tempo = _clamp(tactics.tempo + 15)
            pressing = _clamp(tactics.pressing + 10)
            width = _clamp(tactics.width + 10)
            defensive_line = _clamp(tactics.defensive_line + 10)
            # Stamina-aware refinement inspired by fm26: a tired bot does not
            # get an extra pressing spike, while a tired opponent is fair game.
            if opponent_tired and not own_tired:
                pressing = _clamp(pressing + 5)

        if minute >= 75 and (opp_score - own_score) <= 2:
            # fm26's late losing branch adds attackers. Poor possession/shot
            # output raises the urgency for the three-forward shape; if the
            # bot is already 4-3-3, do not churn the formation unnecessarily.
            if performance_bad and tactics.formation != '4-3-3':
                formation = '3-4-3'
            else:
                formation = '4-3-3' if tactics.formation == '4-3-3' else '3-4-3'
        elif minute >= 75 and (opp_score - own_score) >= 3:
            # Down 0:3+ => keep the first pressing boost, but do not repeatedly
            # ratchet it up every five minutes.
            formation = tactics.formation
        return Tactics(formation, style, tempo, pressing, width, defensive_line, aggression)

    if winning and minute >= 70:
        # Protect the lead: apply the defensive package once, then keep it
        # stable on later 5-minute checks.
        if tactics.style != Style.DEFENSE or tactics.formation != '5-3-2':
            style = Style.DEFENSE
            tempo = _clamp(tactics.tempo - 10)
            pressing = _clamp(tactics.pressing - 10)
            defensive_line = _clamp(tactics.defensive_line - 15)
            formation = '5-3-2'
        return Tactics(formation, style, tempo, pressing, width, defensive_line, aggression)

    if draw and minute >= 80:
        # Late draw: modest positive urgency, not an all-in switch.
        if tactics.tempo < 60 or tactics.pressing < 55:
            tempo = _clamp(tactics.tempo + 10)
            pressing = _clamp(tactics.pressing + 5)
        return Tactics(formation, style, tempo, pressing, width, defensive_line, aggression)

    return tactics

# The pre-match recommendation API below is separate from adjust_tactics(),
# which remains the deterministic in-match controller used by the engine.
import json
import logging
import re
import time
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from app.game.ai_service import ask_ai
from app.game.ratings import player_rating

_ai_logger = logging.getLogger(__name__)
_AI_FORMATIONS = ("4-3-3", "4-4-2", "3-5-2", "4-2-3-1", "5-3-2")
_AI_STYLES = ("ATTACK", "BALANCED", "DEFENSE")
_AI_SLIDERS = ("tempo", "pressing", "width", "defensive_line", "aggression")
_AI_CACHE_TTL = 300
_AI_RATE_LIMIT = 120
_AI_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_AI_LAST_REQUEST: dict[str, float] = {}
_AI_MATCH_KEYS: dict[int, str] = {}

_AI_COACH_SYSTEM = (
    "Ты — профессиональный футбольный тактический аналитик. Дай реалистичную предматчевую рекомендацию. "
    "Используй только разрешённые формации 4-3-3, 4-4-2, 3-5-2, 4-2-3-1, 5-3-2; стиль ATTACK, BALANCED или DEFENSE; "
    "пять целых слайдеров от 0 до 100. Не выдумывай данные о составах. Ответ строго JSON с ключами "
    "formation, style, sliders (tempo, pressing, width, defensive_line, aggression), rationale."
)


def _ai_player_name(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(k) or "").strip() for k in ("first_name", "last_name")).strip() or f"Игрок {row.get('id')}"


def _ai_rating(row: dict[str, Any]) -> float:
    values = dict(row)
    values.setdefault("fitness", 100)
    values.setdefault("form", 0)
    values.setdefault("morale", 70)
    return float(player_rating(SimpleNamespace(**values)))


def _parse_ai_json(response: str) -> dict[str, Any] | None:
    try:
        value = json.loads(response)
        if isinstance(value, dict):
            return value
    except (ValueError, TypeError):
        pass
    start, end = response.find("{"), response.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(response[start:end + 1])
            if isinstance(value, dict):
                return value
        except (ValueError, TypeError):
            pass
    return None


async def _load_coach_squad(session, club_id: int) -> list[dict[str, Any]]:
    rows = (await session.execute(
        text(
            """SELECT p.id,p.first_name,p.last_name,p.position,p.potential,
                      p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,
                      cp.fitness,cp.form,cp.morale,cp.is_injured,cp.injury_matches,cp.suspension_matches
               FROM club_players cp JOIN players p ON p.id=cp.player_id
               WHERE cp.club_id=:club_id ORDER BY cp.is_injured ASC,cp.suspension_matches ASC,cp.fitness DESC,p.id"""
        ), {"club_id": int(club_id)}
    )).mappings().all()
    players = []
    for mapping in rows:
        row = dict(mapping)
        row["overall"] = _ai_rating(row)
        row["name"] = _ai_player_name(row)
        players.append(row)
    players.sort(key=lambda p: (bool(p.get("is_injured")), int(p.get("suspension_matches") or 0), -p["overall"]))
    return players


async def _get_coach_match_id(session, club_id: int, opponent_id: int) -> int | None:
    row = (await session.execute(
        text(
            """SELECT id FROM matches
               WHERE status='SCHEDULED'
                 AND ((home_club_id=:club_id AND away_club_id=:opponent_id)
                   OR (away_club_id=:club_id AND home_club_id=:opponent_id))
               ORDER BY scheduled_at NULLS LAST,id LIMIT 1"""
        ), {"club_id": int(club_id), "opponent_id": int(opponent_id)}
    )).mappings().first()
    return int(row["id"]) if row else None


async def suggest_tactics(session, club_id: int, opponent_id: int) -> dict[str, Any] | None:
    """Return a validated pre-match tactic proposal, or None to keep current tactics."""
    try:
        club_id, opponent_id = int(club_id), int(opponent_id)
        match_id = await _get_coach_match_id(session, club_id, opponent_id)
        cache_key = f"match:{match_id}" if match_id is not None else f"clubs:{club_id}:{opponent_id}"
        now = time.monotonic()
        cached = _AI_CACHE.get(cache_key)
        if cached and now - cached[0] < _AI_CACHE_TTL:
            _ai_logger.info("ai_coach: cache_hit key=%s", cache_key)
            return cached[1]

        own = await _load_coach_squad(session, club_id)
        opponent = await _load_coach_squad(session, opponent_id)
        current_row = (await session.execute(
            text("SELECT formation,style,tempo,pressing,width,defensive_line,aggression FROM club_tactics WHERE club_id=:club_id"),
            {"club_id": club_id},
        )).mappings().first()
        opponent_row = (await session.execute(
            text("SELECT formation,style,tempo,pressing,width,defensive_line,aggression FROM club_tactics WHERE club_id=:club_id"),
            {"club_id": opponent_id},
        )).mappings().first()
        current = dict(current_row) if current_row else {
            "formation": "4-3-3", "style": "BALANCE", "tempo": 50,
            "pressing": 50, "width": 50, "defensive_line": 50, "aggression": 50,
        }
        opponent_formation = str((opponent_row or {}).get("formation") or "4-3-3")
        key_time = _AI_LAST_REQUEST.get(cache_key, 0.0)
        if now - key_time < _AI_RATE_LIMIT:
            _AI_CACHE[cache_key] = (now, None)
            if match_id is not None:
                _AI_MATCH_KEYS[match_id] = cache_key
            return None
        _AI_LAST_REQUEST[cache_key] = now

        def squad_summary(players: list[dict[str, Any]]) -> str:
            eligible = [p for p in players if not p.get("is_injured") and int(p.get("suspension_matches") or 0) <= 0]
            return "; ".join(
                f"{p['name']} ({p['position']}, OVR {p['overall']:.1f}, fitness {p.get('fitness',100)})"
                for p in eligible[:11]
            ) or "нет доступных игроков"

        prompt = (
            f"Мой состав: {squad_summary(own)}. Соперник: {squad_summary(opponent)}, формация {opponent_formation}. "
            f"Текущая тактика: формация {current.get('formation')}, стиль {current.get('style')}, "
            f"tempo={current.get('tempo')}, pressing={current.get('pressing')}, width={current.get('width')}, "
            f"defensive_line={current.get('defensive_line')}, aggression={current.get('aggression')}. "
            "Предложи тактику на матч: формация, стиль ATTACK/BALANCED/DEFENSE, слайдеры tempo, pressing, width, "
            "defensive_line, aggression (целые 0–100), обоснование 2–3 предложения. "
            "Верни только JSON: {\"formation\":\"4-3-3\",\"style\":\"BALANCED\",\"sliders\":{\"tempo\":50,\"pressing\":50,\"width\":50,\"defensive_line\":50,\"aggression\":50},\"rationale\":\"...\"}."
        )
        response = await ask_ai(prompt, _AI_COACH_SYSTEM, max_tokens=320, temperature=0.4)
        result = None
        if response:
            data = _parse_ai_json(response)
            if isinstance(data, dict):
                formation = str(data.get("formation") or "")
                style = str(data.get("style") or "").upper()
                sliders = data.get("sliders")
                if isinstance(sliders, dict):
                    try:
                        parsed_sliders = {name: int(sliders[name]) for name in _AI_SLIDERS}
                    except (KeyError, TypeError, ValueError):
                        parsed_sliders = {}
                else:
                    parsed_sliders = {}
                if (
                    formation in _AI_FORMATIONS
                    and style in _AI_STYLES
                    and len(parsed_sliders) == len(_AI_SLIDERS)
                    and all(0 <= value <= 100 for value in parsed_sliders.values())
                ):
                    result = {
                        "formation": formation,
                        "style": style,
                        "sliders": parsed_sliders,
                        "rationale": str(data.get("rationale") or "Тактика сбалансирована под доступный состав и схему соперника.")[:700],
                        "match_id": match_id,
                        "club_id": club_id,
                        "opponent_id": opponent_id,
                        "source": "ai",
                        "current_tactics": current,
                    }
        _AI_CACHE[cache_key] = (time.monotonic(), result)
        if match_id is not None:
            _AI_MATCH_KEYS[match_id] = cache_key
        if result is None:
            _ai_logger.warning("ai_coach: fallback, reason=invalid_or_empty_response key=%s", cache_key)
        return result
    except Exception as exc:
        _ai_logger.warning("ai_coach: fallback, reason=request_error error=%s", type(exc).__name__)
        return None


def get_cached_tactics_recommendation(match_id: int) -> dict[str, Any] | None:
    """Return a still-fresh proposal for the apply button."""
    key = _AI_MATCH_KEYS.get(int(match_id))
    if not key:
        return None
    cached = _AI_CACHE.get(key)
    if not cached or time.monotonic() - cached[0] >= _AI_CACHE_TTL:
        return None
    return cached[1]
