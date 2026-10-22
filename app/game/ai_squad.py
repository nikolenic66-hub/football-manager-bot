"""AI-assisted squad analysis with compact prompts and a deterministic fallback."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from app.game.ai_service import ask_ai
from app.game.ratings import player_rating

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300
_RATE_LIMIT_SECONDS = 60
_CACHE: dict[int, tuple[float, str | None, dict[str, Any]]] = {}
_LAST_CALL: dict[int, float] = {}
_CACHE_LOCK = threading.Lock()

_SYSTEM_PROMPT = (
    "Ты опытный футбольный спортивный директор. Проанализируй состав на русском языке. "
    "Дай ровно 3–5 коротких предложений: сильные стороны, слабые позиции, физическое "
    "состояние и 1–2 конкретных приоритета усиления. Используй только приведённые цифры; "
    "не выдумывай игроков, травмы или характеристики. Не используй Markdown-заголовки."
)


async def _load_summary(session, club_id: int) -> dict[str, Any]:
    """Load real schema fields and aggregate them in Python (overall is derived by game rules)."""
    result = await session.execute(
        text(
            """SELECT p.id AS id, p.first_name, p.last_name, p.position,
                      p.potential, p.rarity,
                      p.pace, p.shooting, p.passing, p.dribbling,
                      p.defending, p.physical, p.stamina, p.mental,
                      cp.fitness, cp.form, cp.morale, cp.is_injured,
                      cp.injury_matches
               FROM club_players cp
               JOIN players p ON p.id = cp.player_id
               WHERE cp.club_id = :club_id
               ORDER BY p.position, p.id"""
        ),
        {"club_id": int(club_id)},
    )
    rows = result.mappings().all()
    counts: dict[str, int] = defaultdict(int)
    rating_sums: dict[str, float] = defaultdict(float)
    potential_sums: dict[str, float] = defaultdict(float)
    injured_count = 0
    injured_matches = 0
    rarity_counts: dict[str, int] = defaultdict(int)
    all_ratings: list[float] = []

    for row in rows:
        data = dict(row)
        position = str(data.get("position") or "?")
        data["fitness"] = int(data.get("fitness") if data.get("fitness") is not None else 100)
        data["form"] = int(data.get("form") or 0)
        data["morale"] = int(data.get("morale") if data.get("morale") is not None else 70)
        player = SimpleNamespace(**data)
        overall = float(player_rating(player))
        counts[position] += 1
        rating_sums[position] += overall
        potential_sums[position] += float(data.get("potential") or overall)
        all_ratings.append(overall)
        rarity_counts[str(data.get("rarity") or "BASE").upper()] += 1
        if bool(data.get("is_injured")):
            injured_count += 1
            injured_matches += max(0, int(data.get("injury_matches") or 0))

    positions = {
        position: {
            "count": count,
            "avg_overall": round(rating_sums[position] / count, 1),
            "avg_potential": round(potential_sums[position] / count, 1),
        }
        for position, count in sorted(counts.items())
    }
    weak_positions = [
        position
        for position, values in sorted(
            positions.items(), key=lambda item: (item[1]["avg_overall"], item[1]["count"])
        )
        if values["count"] < 2 or values["avg_overall"] < 60
    ]
    return {
        "player_count": len(rows),
        "positions": positions,
        "weak_positions": weak_positions[:4],
        "injured_count": injured_count,
        "injured_matches": injured_matches,
        "avg_overall": round(sum(all_ratings) / len(all_ratings), 1) if all_ratings else 0.0,
        "rarity_counts": dict(sorted(rarity_counts.items())),
    }


def _build_prompt(summary: dict[str, Any]) -> str:
    position_text = "; ".join(
        f"{position}: {values['count']} игрок(а/ов), средний OVR {values['avg_overall']}, "
        f"потенциал {values['avg_potential']}"
        for position, values in summary["positions"].items()
    ) or "игроков нет"
    weak_text = ", ".join(summary["weak_positions"]) or "явно слабых позиций по порогам нет"
    rarity_text = ", ".join(
        f"{rarity} {count}" for rarity, count in summary["rarity_counts"].items()
    ) or "нет данных"
    return (
        f"Состав: {summary['player_count']} игроков; средний OVR команды {summary['avg_overall']}. "
        f"Позиции (число, средний OVR, средний потенциал): {position_text}. "
        f"Самые проблемные позиции: {weak_text}. "
        f"Травмированы: {summary['injured_count']}; суммарно оставшихся матчей травм: "
        f"{summary['injured_matches']}. Редкость карточек: {rarity_text}. "
        "Сформулируй 3–5 предложений и не повторяй таблицу целиком."
    )


def _fallback_from_summary(summary: dict[str, Any]) -> str:
    if summary["player_count"] == 0:
        return "Состав пуст. Подпишите игроков, чтобы получить анализ команды."

    problems: list[str] = []
    if summary["weak_positions"]:
        problems.append("не хватает глубины или качества на позициях " + ", ".join(summary["weak_positions"]))
    if summary["injured_count"]:
        problems.append(f"травмированы {summary['injured_count']} игрок(а/ов)")
    if summary["avg_overall"] < 60:
        problems.append(f"средний рейтинг команды низкий ({summary['avg_overall']})")

    if not problems:
        return (
            f"Состав в порядке: средний рейтинг команды {summary['avg_overall']}, "
            f"в распоряжении {summary['player_count']} игроков. Критических слабых мест "
            "по текущим данным не обнаружено; следите за физическим состоянием и глубиной скамейки."
        )
    return "Есть проблемы: " + "; ".join(problems) + ". В первую очередь усилите самые слабые позиции и проверьте доступность игроков."


async def analyze_squad(session, club_id: int) -> str | None:
    """Return AI analysis or None; UI can use ``build_fallback_analysis`` for fallback text."""
    try:
        club_id = int(club_id)
    except (TypeError, ValueError):
        logger.warning("ai_squad: fallback, reason=invalid_club_id")
        return None

    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(club_id)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            logger.info("ai_squad: cache_hit club_id=%s", club_id)
            return cached[1]
        last_call = _LAST_CALL.get(club_id, 0.0)
        if now - last_call < _RATE_LIMIT_SECONDS:
            logger.warning("ai_squad: fallback, reason=rate_limited club_id=%s", club_id)
            return None
        _LAST_CALL[club_id] = now

    try:
        summary = await _load_summary(session, club_id)
        if summary["player_count"] == 0:
            with _CACHE_LOCK:
                _CACHE[club_id] = (time.monotonic(), None, summary)
            logger.warning("ai_squad: fallback, reason=empty_squad club_id=%s", club_id)
            return None
        response = await ask_ai(
            _build_prompt(summary),
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=220,
            temperature=0.65,
        )
        if response is not None and len(response.strip()) < 20:
            logger.warning("ai_squad: fallback, reason=response_too_short club_id=%s", club_id)
            response = None
        with _CACHE_LOCK:
            _CACHE[club_id] = (time.monotonic(), response.strip() if response else None, summary)
        if response:
            logger.info("ai_squad: response club_id=%s chars=%d", club_id, len(response.strip()))
        return response.strip() if response else None
    except Exception as exc:
        logger.warning(
            "ai_squad: fallback, reason=analysis_error club_id=%s error=%s",
            club_id,
            type(exc).__name__,
        )
        return None


async def build_fallback_analysis(session, club_id: int) -> str:
    """Build deterministic fallback text from the same aggregate used for the AI prompt."""
    try:
        club_id = int(club_id)
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get(club_id)
            summary = cached[2] if cached and now - cached[0] < _CACHE_TTL_SECONDS else None
        if summary is None:
            summary = await _load_summary(session, club_id)
        return _fallback_from_summary(summary)
    except Exception as exc:
        logger.warning(
            "ai_squad: fallback, reason=fallback_build_error club_id=%s error=%s",
            club_id,
            type(exc).__name__,
        )
        return "Не удалось прочитать данные состава. Попробуйте обновить анализ позже."
