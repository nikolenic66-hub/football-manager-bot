import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

LeagueTier = Literal['ROOKIE','BRONZE','SILVER','GOLD','DIAMOND','ELITE','LEGEND']
CompetitionType = Literal['LEAGUE','CUP','CHAMPIONS']
Position=Literal['GK','CB','LB','RB','DM','CM','AM','LW','RW','ST']
@dataclass(frozen=True)
class Player:
    id:int; first_name:str; last_name:str; position:Position; age:int
    pace:int; shooting:int; passing:int; dribbling:int; defending:int; physical:int; stamina:int; mental:int
    fitness:int=100; form:int=0; suspended:bool=False; talents:tuple[str,...]=(); morale:int=70
    @property
    def name(self):
        value=f'{self.first_name or ""} {self.last_name or ""}'.strip()
        cleaned=re.sub(r'\s+\d{4}$','',value).strip()
        if re.fullmatch(r'\d{4}', cleaned):
            cleaned=''
        return cleaned or f'Игрок {self.id}'


@dataclass(frozen=True)
class League:
    id: int
    name: str
    tier: LeagueTier = 'ROOKIE'
    status: str = 'WAITING'
    max_teams: int = 16
    current_round: int = 0
    total_rounds: int = 0
    match_interval_days: int = 1
    match_interval_minutes: int | None = None
    match_time: str = '20:00'
    start_date: date | None = None


@dataclass(frozen=True)
class Match:
    id: int
    league_id: int
    round: int
    home_club_id: int
    away_club_id: int
    competition_type: CompetitionType = 'LEAGUE'
