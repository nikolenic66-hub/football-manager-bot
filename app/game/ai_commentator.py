import asyncio
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import Future

from app.game.ai_service import ask_ai

logger = logging.getLogger(__name__)

AI_COMMENTARY_MIN_INTERVAL_SEC = 30
_MAX_TOKENS = 200
_CACHE = {}
_LAST_CALL_TS = {}
_CACHE_LOCK = threading.Lock()
_LOOP = None
_LOOP_THREAD = None
_LOOP_READY = threading.Event()

_SYSTEM_PROMPT = (
    'Ты — эмоциональный футбольный комментатор. Пиши 1-2 предложения живого комментария на русском языке. '
    'НЕ пересказывай событие сухо. Добавляй эмоции, оценку и контекст. '
    'Никогда не меняй счёт. Счёт указан в user-сообщении явно. Если счёт 1:0 — пиши 1:0. '
    'Имена игроков транслитерируй (Weah → Веа, Barella → Барелла). Не обрывай фразу на середине. '
    'ПЛОХО: George Weah наносит удар. ХОРОШО: Джордж Веа пробует издалека — вратарь справляется, но как опасно! '
    'ПЛОХО: George Weah забивает. ХОРОШО: ГОЛ! Джордж Веа открывает счёт — Шахтёр повёл 1:0! '
    'Не добавляй неподтверждённых событий и не меняй факты из user-сообщения.'
)

def _event_key(event_type, context):
    payload = json.dumps({'event_type': event_type, 'context': context}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _ensure_loop():
    global _LOOP, _LOOP_THREAD
    with _CACHE_LOCK:
        if _LOOP is not None and _LOOP.is_running():
            return _LOOP
        _LOOP_READY.clear()
        def runner():
            global _LOOP
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _LOOP = loop
            _LOOP_READY.set()
            loop.run_forever()
        _LOOP_THREAD = threading.Thread(target=runner, name='ai-commentator', daemon=True)
        _LOOP_THREAD.start()
    _LOOP_READY.wait(timeout=1)
    return _LOOP


async def _request(prompt, system_prompt):
    return await ask_ai(
        prompt=prompt,
        system_prompt=system_prompt,
        max_tokens=_MAX_TOKENS,
        temperature=0.85,
    )

def generate_comment(event_type, context) -> str | None:
    """Generate AI commentary only for high-impact match events."""
    event_type = str(event_type).upper()
    event_type = {'RED': 'RED_CARD'}.get(event_type, event_type)
    VALID_TYPES = {'GOAL', 'RED_CARD', 'INJURY', 'PENALTY'}
    if event_type not in VALID_TYPES:
        return None
    key = _event_key(event_type, context)
    # Match engines currently pass club/opponent context rather than a database match ID.
    # Prefer a real match_id when available; otherwise scope the limit to this fixture.
    match_id = context.get('match_id') or f"{context.get('club_name', '')}::{context.get('opponent_name', '')}"
    now = time.monotonic()
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
        # Cache deduplicates identical events. A per-match 30-second throttle
        # suppresses every subsequent event in a simulated match because the
        # engine generates events in one synchronous pass; retain timestamp
        # bookkeeping but do not silence distinct event keys.
        _LAST_CALL_TS[str(match_id)] = now
    score = str(context.get('score') or f"{context.get('home_score', 0)}:{context.get('away_score', 0)}")
    player_name = context.get('player_name') or 'неизвестный игрок'
    logger.info('ai_commentator: request event_type=%s minute=%s player=%s score=%s', event_type, context.get('minute'), player_name, score)
    prompt = (
        f"Матч: {context.get('home_name') or context.get('club_name')} vs {context.get('away_name') or context.get('opponent_name')}. "
        f"Минута: {context.get('minute')}. Текущий счёт: {score} (не выдумывай другой). "
        f"Событие: {event_type}. Игрок: {player_name} ({context.get('club_name')}). "
        f"Сгенерируй живой комментарий 1-2 предложения."
    )
    try:
        future: Future = asyncio.run_coroutine_threadsafe(_request(prompt, _SYSTEM_PROMPT), _ensure_loop())
        result = future.result(timeout=12)
    except Exception:
        logger.exception('AI commentary generation failed; caller should keep template text')
        logger.warning('ai_commentator: fallback, reason=request_exception')
        return None
    if not result or len(result.strip()) < 10:
        reason = 'empty_response' if not result else 'response_too_short'
        logger.warning('ai_commentator: fallback, reason=%s', reason)
        return None
    result = result.strip()
    logger.info('ai_commentator: response len=%d text=%r', len(result), result[:120])
    with _CACHE_LOCK:
        _CACHE[key] = result
    return result
