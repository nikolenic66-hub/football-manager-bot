"""Explainable player transfer-interest model.

The current schema has club reputation but no dedicated player reputation or
league-tier columns.  Those two values therefore accept explicit inputs and
fall back to deterministic proxies when absent, without changing the schema.
"""
from __future__ import annotations

from math import ceil


def _v(obj, key, default=0):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _clamp(value, lo, hi):
    return max(lo, min(hi, float(value)))


def _overall(player):
    explicit = _v(player, 'overall', None)
    if explicit is not None:
        return _clamp(explicit, 1, 100)
    attrs = [float(_v(player, name, 0)) for name in (
        'pace', 'shooting', 'passing', 'dribbling', 'defending', 'physical', 'stamina', 'mental'
    )]
    return _clamp(sum(attrs) / max(1, len(attrs)), 1, 100)


def _player_reputation(player):
    explicit = _v(player, 'reputation', None)
    if explicit is not None:
        return _clamp(explicit, 1, 100)
    # No player-reputation field exists in the release schema. OVR is the
    # stable fallback; potential adds only a small career-status signal.
    return _clamp(0.8 * _overall(player) + 0.2 * _v(player, 'potential', _overall(player)), 1, 100)


def _league_tier(club):
    explicit = _v(club, 'league_tier', None)
    if explicit is not None:
        return int(_clamp(explicit, 1, 5))
    # No league-tier column exists. Map existing club reputation to a stable
    # five-tier proxy; tier 1 is strongest.
    rep = _clamp(_v(club, 'reputation', 1), 1, 100)
    return max(1, min(5, 6 - ceil(rep / 20)))


def _verdict(score):
    if score < 25:
        return 'NOT_INTERESTED'
    if score < 50:
        return 'LOW'
    if score < 75:
        return 'INTERESTED'
    return 'VERY_INTERESTED'


def calculate_interest(player, club, current_club=None) -> dict:
    """Return a deterministic 0..100 transfer-interest decision.

    Optional club context keys used by the integration are:
    offered_salary, contract_years, release_clause, position_count,
    squad_size and league_tier.  They are deliberately optional so the
    function remains usable by tests and UI previews.
    """
    overall = _overall(player)
    potential = _clamp(_v(player, 'potential', overall), 1, 100)
    age = int(_v(player, 'age', 25))
    player_rep = _player_reputation(player)
    club_rep = _clamp(_v(club, 'reputation', 1), 1, 100)
    budget = max(0, int(_v(club, 'budget', 0)))

    # 0..25: reputation fit.
    rep_gap = club_rep - player_rep
    reputation_fit = _clamp(25 + rep_gap * 0.5, 0, 25)

    # 0..20: proposed wage against the player's expected wage.
    market_value = max(0, int(_v(player, 'market_value', 0)))
    base_salary = max(15_000, market_value // 35) if market_value else max(15_000, int(_v(player, 'salary', 15_000)))
    expected_salary = max(int(_v(player, 'salary', 0)), base_salary)
    offered_salary = int(_v(club, 'offered_salary', expected_salary))
    wage_ratio = offered_salary / max(1, expected_salary)
    wage_fit = 0 if wage_ratio < 0.5 else _clamp(wage_ratio * 20, 0, 20)

    # 0..15: squad competition. Zero means a clear starting opportunity.
    position_count = int(_v(club, 'position_count', 0))
    if position_count <= 0:
        playing_time_fit = 15
    elif position_count == 1:
        playing_time_fit = 11
    elif position_count == 2:
        playing_time_fit = 5
    else:
        playing_time_fit = 1

    # 0..10: career-stage fit.
    if age <= 23:
        age_career_fit = 4 + 6 * (playing_time_fit / 15)
    elif age <= 29:
        age_career_fit = 5 + 5 * (_clamp(club_rep / 100, 0, 1))
    else:
        money_signal = _clamp(budget / max(1, market_value * 3), 0, 1) if market_value else 0
        age_career_fit = 4 + 6 * max(club_rep / 100, money_signal)
    age_career_fit = _clamp(age_career_fit, 0, 10)

    # 0..10: ambition / potential fit.
    ambition_gap = club_rep - potential
    potential_fit = _clamp(10 + ambition_gap * 0.2, 0, 10)

    # 0..10: league quality, tier 1 strongest.
    tier = _league_tier(club)
    competition_fit = {1: 10, 2: 8, 3: 6, 4: 4, 5: 2}.get(tier, 2)

    # 0..10: contract context.
    years = int(_v(club, 'contract_years', 3))
    release_clause = int(_v(club, 'release_clause', 0))
    contract_fit = _clamp(4 + min(4, max(0, years - 1)) + (2 if release_clause <= 0 or release_clause >= market_value * 2 else 0), 0, 10)
    if current_club is not None:
        old_salary = int(_v(current_club, 'contract_salary', _v(player, 'salary', 0)) or 0)
        if old_salary and offered_salary < old_salary:
            contract_fit -= 2
        contract_fit = _clamp(contract_fit, 0, 10)

    penalties = 0
    reasons = []
    if club_rep + 15 < player_rep:
        penalties += 40
        reasons.append('Репутация клуба слишком низкая')
    elif club_rep < player_rep:
        penalties += 5
        reasons.append('Репутация клуба ниже ожиданий')
    if wage_ratio < 0.5:
        penalties += 60
        reasons.append('Зарплата ниже ожиданий')
    elif wage_ratio < 0.8:
        penalties += 12
        reasons.append('Зарплата ниже ожиданий')
    elif wage_ratio < 1.0:
        penalties += 5
        reasons.append('Зарплата ниже ожиданий')
    if position_count >= 3:
        penalties += 10
        reasons.append('Нет места в составе')
    elif position_count == 2:
        penalties += 4
        reasons.append('Сильная конкуренция за игровое время')
    if age <= 23 and playing_time_fit < 11:
        penalties += 5
        reasons.append('Ограниченные возможности для развития')
    if age >= 30 and club_rep < 50 and offered_salary < expected_salary:
        penalties += 4
        reasons.append('Низкая привлекательность карьерных условий')
    if potential >= 85 and club_rep < 40:
        penalties += 6
        reasons.append('Амбиции клуба ниже потенциала игрока')
    if tier >= 4 and competition_fit <= 4 and potential >= 85:
        penalties += 4
        reasons.append('Уровень лиги ниже ожиданий')
    if budget < offered_salary:
        penalties += 15
        reasons.append('Бюджет клуба не позволяет платить ожидаемую зарплату')

    score = int(round(_clamp(
        reputation_fit + wage_fit + playing_time_fit + age_career_fit +
        potential_fit + competition_fit + contract_fit - penalties,
        0, 100,
    )))
    return {
        'interest_score': score,
        'verdict': _verdict(score),
        'reasons': reasons,
        'components': {
            'club_reputation_fit': int(round(reputation_fit)),
            'wage_fit': int(round(wage_fit)),
            'playing_time_fit': int(round(playing_time_fit)),
            'age_career_fit': int(round(age_career_fit)),
            'potential_fit': int(round(potential_fit)),
            'competition_fit': int(round(competition_fit)),
            'contract_context': int(round(contract_fit)),
            'penalties': penalties,
        },
    }
