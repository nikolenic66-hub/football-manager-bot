"""Stable, renderer-agnostic match event contract for future 2D clients.

This module intentionally contains data structures only; it does not implement
animation, field coordinates, persistence, or a 2D engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VisualEventType = Literal[
    'GOAL', 'SHOT', 'PASS', 'FOUL', 'CORNER', 'YELLOW', 'RED', 'SUBSTITUTION'
]


@dataclass(slots=True)
class MatchVisualEvent:
    match_id: int
    sequence: int
    minute: int
    type: str
    team_id: int
    player_id: int | None
    outcome: str
    home_score: int
    away_score: int
    commentary_key: str
    coordinates: dict | None = None
    schema_version: int = 1
