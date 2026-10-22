"""AI-assisted Best XI suggestions with strict validation and deterministic fallback."""
from __future__ import annotations

import json
import logging
import re
import time
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from app.game.ai_service import ask_ai
from app.game.best_xi import get_best_xi
from app.game.board import FORMATION_SLOTS
from app.game.ratings import player_rating

logger = logging.getLogger(__name__)

ALLOWED_FORMATIONS = ("4-3-3", "4-4-2", "3-5-2", "4-2-3-1", "5-3-2")
_CACHE_TTL = 600
_RATE_LIMIT = 120
_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_last_request: dict[int, float] = {}

_SYSTEM_PROMPT = (
    "Ты — футбольный аналитик. Подбери сильнейший стартовый состав строго из доступных игроков. "
    "Не выбирай травмированных или дисквалифицированных. Верни формацию и ровно 11 игроков. "
    "Используй только ID из списка; не выдумывай игроков. Краткое обоснование — 1–2 предложения. "
    "Формат: ФОРМАЦИЯ: X-X-X; ИГРОКИ: [ID: 123 | позиция: CB | имя: Имя Фамилия]; ...; ОБОСНОВАНИЕ: ..."
)


def _rating(row: dict[str, Any]) -> float:
    data = dict(row)
    data.setdefault("form", 0)
    data.setdefault("morale", 70)
    data.setdefault("fitness", 100)
    data.setdefault("suspended", bool(data.get("suspension_matches")))
    return float(player_rating(SimpleNamespace(**data)))


def _player_name(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "").strip() for key in ("first_name", "last_name")).strip() or f"Игрок {row.get('id')}"


def _formation_from_text(response: str) -> str | None:
    match = re.search(r"(?:ФОРМАЦИЯ|FORMATION)\s*[:=]\s*([0-9](?:-[0-9])+)", response, re.I)
    if match and match.group(1) in ALLOWED_FORMATIONS:
        return match.group(1)
    try:
        payload = json.loads(response)
        formation = payload.get("formation") if isinstance(payload, dict) else None
        if formation in ALLOWED_FORMATIONS:
            return formation
    except (ValueError, TypeError):
        pass
    return None


def _extract_ids(response: str) -> list[int]:
    ids: list[int] = []
    try:
        payload = json.loads(response)
        if isinstance(payload, dict):
            items = payload.get("players", [])
            for item in items:
                if isinstance(item, dict) and str(item.get("id", "")).isdigit():
                    ids.append(int(item["id"]))
            if ids:
                return ids
    except (ValueError, TypeError):
        pass
    for value in re.findall(r"(?:ID\s*[:=#]?\s*|\bid\s*[:=#]\s*)(\d+)", response, re.I):
        ids.append(int(value))
    return ids


def _compatible(player_position: str, slot_position: str) -> bool:
    position = (player_position or "").upper()
    slot = (slot_position or "").upper()
    groups = {
        "GK": {"GK"},
        "CB": {"CB", "LB", "RB", "DM"},
        "LB": {"LB", "CB", "LM", "LW"},
        "RB": {"RB", "CB", "RM", "RW"},
        "DM": {"DM", "CM", "CB"},
        "CM": {"CM", "DM", "AM", "LM", "RM"},
        "AM": {"AM", "CM", "LW", "RW"},
        "LM": {"LM", "LW", "LB", "CM"},
        "RM": {"RM", "RW", "RB", "CM"},
        "LW": {"LW", "LM", "ST", "AM"},
        "RW": {"RW", "RM", "ST", "AM"},
        "ST": {"ST", "LW", "RW", "AM"},
    }
    return position in groups.get(slot, {slot})


def _assign_slots(players: list[dict[str, Any]], formation: str) -> list[dict[str, Any]] | None:
    slots = [slot[0] for slot in FORMATION_SLOTS[formation]]
    # Assign the most restrictive slots first, preferring exact positions and higher OVR.
    pending = list(enumerate(slots))
    pending.sort(key=lambda item: sum(_compatible(str(p.get("position") or ""), item[1]) for p in players))
    unused = list(players)
    assignments: dict[int, dict[str, Any]] = {}
    for original_index, slot in pending:
        candidates = [p for p in unused if _compatible(str(p.get("position") or ""), slot)]
        if not candidates:
            return None
        chosen = max(candidates, key=lambda p: (str(p.get("position") or "").upper() == slot, p["overall"], -int(p["id"])))
        assignments[original_index] = {**chosen, "slot_position": slot}
        unused.remove(chosen)
    return [assignments[index] for index in range(11)]


def _fallback(players: list[dict[str, Any]], formation: str, reason: str) -> dict[str, Any]:
    try:
        chosen = get_best_xi(players, formation)
    except Exception:
        chosen = []
    normalized = []
    for row in chosen:
        item = dict(row)
        item["overall"] = _rating(item)
        item["name"] = _player_name(item)
        item["slot_position"] = item.get("position") or "?"
        normalized.append(item)
    return {
        "formation": formation,
        "players": normalized,
        "reason": "Состав подобран детерминированным алгоритмом игры. " + reason,
        "source": "fallback",
    }


async def _load_players(session, club_id: int) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """SELECT p.id, p.first_name, p.last_name, p.position, p.potential,
                      p.pace, p.shooting, p.passing, p.dribbling, p.defending,
                      p.physical, p.stamina, p.mental,
                      cp.fitness, cp.form, cp.morale, cp.is_injured,
                      cp.injury_matches, cp.suspension_matches
               FROM club_players cp JOIN players p ON p.id=cp.player_id
               WHERE cp.club_id=:club_id ORDER BY p.id"""
        ),
        {"club_id": int(club_id)},
    )
    players: list[dict[str, Any]] = []
    for mapping in result.mappings().all():
        row = dict(mapping)
        row["overall"] = _rating(row)
        row["name"] = _player_name(row)
        players.append(row)
    return players


async def _opponent_formation(session, club_id: int) -> str:
    row = (await session.execute(
        text(
            """SELECT CASE WHEN m.home_club_id=:club_id THEN m.away_club_id ELSE m.home_club_id END AS opponent_id
               FROM matches m
               WHERE (m.home_club_id=:club_id OR m.away_club_id=:club_id)
                 AND m.status='SCHEDULED'
               ORDER BY m.scheduled_at NULLS LAST, m.id LIMIT 1"""
        ), {"club_id": int(club_id)}
    )).mappings().first()
    if not row:
        return "неизвестна"
    formation = (await session.execute(
        text("SELECT formation FROM club_tactics WHERE club_id=:club_id"),
        {"club_id": int(row["opponent_id"])},
    )).scalar_one_or_none()
    return str(formation or "4-3-3")


async def suggest_best_xi(session, club_id: int) -> dict[str, Any] | None:
    """Suggest a validated XI; use the game's existing deterministic algorithm on failure."""
    try:
        club_id = int(club_id)
        now = time.monotonic()
        cached = _cache.get(club_id)
        if cached and now - cached[0] < _CACHE_TTL:
            logger.info("ai_best_xi: cache_hit club_id=%s", club_id)
            return cached[1]

        players = await _load_players(session, club_id)
        own_formation = (await session.execute(
            text("SELECT formation FROM club_tactics WHERE club_id=:club_id"),
            {"club_id": club_id},
        )).scalar_one_or_none() or "4-3-3"
        formation = own_formation if own_formation in ALLOWED_FORMATIONS else "4-3-3"
        available = [p for p in players if not p.get("is_injured") and int(p.get("suspension_matches") or 0) <= 0]
        last = _last_request.get(club_id, 0.0)
        if now - last < _RATE_LIMIT:
            result = _fallback(available, formation, "ИИ временно ограничен по частоте запросов.")
            _cache[club_id] = (now, result)
            return result
        _last_request[club_id] = now
        if len(available) < 11:
            result = _fallback(available, formation, "Для полного стартового состава недостаточно доступных игроков.")
            _cache[club_id] = (now, result)
            return result

        opponent = await _opponent_formation(session, club_id)
        roster = "\n".join(
            f"ID: {p['id']} | {p['position']} | {_player_name(p)} | OVR {p['overall']:.1f} | fitness {p.get('fitness', 100)} | potential {p.get('potential', 0)}"
            for p in available
        )
        prompt = (
            f"Мои игроки:\n{roster}\nСоперник играет {opponent}. "
            "Предложи оптимальных 11 игроков и одну формацию из 4-3-3, 4-4-2, 3-5-2, 4-2-3-1, 5-3-2. "
            "Ответь в формате: ФОРМАЦИЯ: X-X-X; ИГРОКИ: [ID: число | позиция: позиция | имя: имя]; ...; "
            "ОБОСНОВАНИЕ: 1–2 предложения. Каждый ID должен быть уникальным и взят из списка."
        )
        response = await ask_ai(prompt, _SYSTEM_PROMPT, max_tokens=420, temperature=0.4)
        if not response:
            result = _fallback(available, formation, "ИИ не ответил.")
        else:
            ai_formation = _formation_from_text(response)
            ids = _extract_ids(response)
            by_id = {int(p["id"]): p for p in available}
            if ai_formation not in ALLOWED_FORMATIONS or len(ids) != 11 or len(set(ids)) != 11 or any(pid not in by_id for pid in ids):
                result = _fallback(available, formation, "Ответ ИИ не прошёл проверку состава.")
            else:
                selected = [by_id[pid] for pid in ids]
                assigned = _assign_slots(selected, ai_formation)
                if not assigned or len(assigned) != 11:
                    result = _fallback(available, formation, "Позиции игроков не подходят под предложенную формацию.")
                else:
                    rationale_match = re.search(r"(?:ОБОСНОВАНИЕ|RATIONALE)\s*[:=]\s*(.+)$", response, re.I | re.S)
                    rationale = rationale_match.group(1).strip() if rationale_match else ""
                    if not rationale:
                        try:
                            parsed_payload = json.loads(response)
                            if isinstance(parsed_payload, dict):
                                rationale = str(parsed_payload.get("rationale") or "")
                        except (ValueError, TypeError):
                            pass
                    if not rationale:
                        rationale = "Выбраны сильнейшие доступные игроки с учётом баланса позиций."
                    result = {
                        "formation": ai_formation,
                        "players": [{**p, "slot_position": p["slot_position"], "name": _player_name(p)} for p in assigned],
                        "reason": rationale[:700],
                        "source": "ai",
                    }
        _cache[club_id] = (time.monotonic(), result)
        return result
    except Exception as exc:
        logger.warning("ai_best_xi: fallback, reason=analysis_error club_id=%s error=%s", club_id, type(exc).__name__)
        try:
            players = await _load_players(session, int(club_id))
            result = _fallback([p for p in players if not p.get("is_injured") and int(p.get("suspension_matches") or 0) <= 0], "4-3-3", "Не удалось получить рекомендацию ИИ.")
            _cache[int(club_id)] = (time.monotonic(), result)
            return result
        except Exception:
            return None
