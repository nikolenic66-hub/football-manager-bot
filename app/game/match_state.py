from dataclasses import dataclass
from typing import Iterable
from app.models import Player

@dataclass(frozen=True)
class MatchStats:
    possession_home: float
    shots_home: int
    shots_on_target_home: int
    corners_home: int
    possession_away: float
    shots_away: int
    shots_on_target_away: int
    corners_away: int


def fatigue_factor(p: Player, minute: int) -> float:
    base = max(0.72, min(1.02, 0.82 + p.fitness / 500 + p.stamina / 500))
    drain = max(0, minute - 45) * max(0.0008, (100 - p.stamina) / 18000)
    return max(0.70, base - drain)


def squad_average(players: Iterable[Player], attr: str) -> float:
    xs=list(players)
    return sum(getattr(p,attr,50) for p in xs)/max(1,len(xs))
