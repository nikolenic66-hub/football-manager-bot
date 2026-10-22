"""Deterministic AI-club transfer decisions.

The decision flow follows cmanager's architecture: inspect squad depth, find a
specific gap, rank market candidates under a budget constraint, and trade only
when the player-side decision is positive. The actual money movement remains in
our existing transfer service so its transaction/invariant checks are reused.
"""

from sqlalchemy import text

from app.game.ratings import player_rating
from app.game.scouting import build_scout_query
from app.game.transfer_interest import calculate_interest

POSITION_GROUPS = {
    "GK": {"GK"},
    "DEF": {"CB", "LB", "RB"},
    "MID": {"DM", "CM", "AM"},
    "ATT": {"LW", "RW", "ST"},
}
MIN_GROUP_DEPTH = {"GK": 2, "DEF": 6, "MID": 6, "ATT": 4}
MIN_BUDGET = 50_000


def _overall(row):
    if row.get("overall") is not None:
        return int(round(float(row["overall"])))
    attrs = [int(row.get(k) or 0) for k in ("pace", "shooting", "passing", "dribbling", "defending", "physical", "stamina", "mental")]
    return int(round(sum(attrs) / max(1, len(attrs))))


def _group_gap(rows):
    counts = {key: 0 for key in POSITION_GROUPS}
    for row in rows:
        pos = str(row.get("position") or "")
        for group, positions in POSITION_GROUPS.items():
            if pos in positions:
                counts[group] += 1
                break
    gaps = [(MIN_GROUP_DEPTH[group] - counts[group], group) for group in counts if counts[group] < MIN_GROUP_DEPTH[group]]
    if gaps:
        return max(gaps)[1]
    # If depth is nominal, target the weakest positional group by average OVR.
    averages=[]
    for group, positions in POSITION_GROUPS.items():
        values=[_overall(r) for r in rows if str(r.get("position") or "") in positions]
        averages.append((sum(values)/len(values) if values else 0, group))
    return min(averages)[1]


async def ai_transfer_action(club, session):
    """Return one deterministic ``BUY`` or ``SELL`` action, or ``None``.

    The function only decides. Callers execute the returned action through the
    existing transfer service, preserving Transfer Interest and financial locks.
    """
    club_id = int(club["id"] if isinstance(club, dict) else club.id)
    budget = int(club.get("budget", 0) if isinstance(club, dict) else getattr(club, "budget", 0))
    squad = (await session.execute(text("""SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.market_value,p.salary,p.potential,
        p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,
        cp.contract_salary,cp.morale,cp.fitness
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c ORDER BY p.id"""), {"c": club_id})).mappings().all()

    if len(squad) > 25:
        # Sell the lowest-rated non-prospect first; keep the squad above the
        # existing hard minimum of 18.
        if len(squad) <= 18:
            return None
        candidates=sorted(squad, key=lambda r: (_overall(r), int(r.get("potential") or 0), int(r["id"])))
        row=candidates[0]
        return {"action":"SELL", "player_id":int(row["id"]), "reason":"squad_over_25"}

    if budget < MIN_BUDGET:
        return None

    group = _group_gap(squad)
    positions = sorted(POSITION_GROUPS[group])
    query = build_scout_query({"position": positions, "transfer_status": "free"}).limit(32)
    candidates = (await session.execute(query)).mappings().all()
    if not candidates:
        return None

    best=None
    for row in candidates:
        row=dict(row)
        price=int(row.get("market_value") or 0)
        if price <= 0 or price > budget:
            continue
        offered_salary=max(15_000, price//35)
        same_position=sum(1 for p in squad if p["position"] == row["position"])
        target={
            "id":club_id,
            "reputation":int(club.get("reputation", 1) if isinstance(club, dict) else getattr(club, "reputation", 1)),
            "budget":budget,
            "offered_salary":offered_salary,
            "contract_years":3,
            "release_clause":max(price*2,1_000_000),
            "position_count":same_position,
        }
        interest=calculate_interest(row,target,None)
        club_rep=int(target.get('reputation',1))
        player_rep=int(row.get('reputation') or row.get('overall') or _overall(row))
        if player_rep > club_rep + 25:
            continue
        if interest["interest_score"] < 50 or interest["verdict"] == "NOT_INTERESTED":
            continue
        score=(interest["interest_score"], int(row.get("potential") or 0), _overall(row), -price, -int(row["id"]))
        if best is None or score > best[0]:
            best=(score,row,interest)
    if best is None:
        return None
    _, row, interest=best
    return {
        "action":"BUY",
        "player_id":int(row["id"]),
        "price":int(row["market_value"]),
        "interest":interest,
        "reason":f"squad_gap:{group}",
    }
