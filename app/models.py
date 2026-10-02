from dataclasses import dataclass
from typing import Literal
Position=Literal['GK','CB','LB','RB','DM','CM','AM','LW','RW','ST']
@dataclass(frozen=True)
class Player:
    id:int; first_name:str; last_name:str; position:Position; age:int
    pace:int; shooting:int; passing:int; dribbling:int; defending:int; physical:int; stamina:int; mental:int
    fitness:int=100; form:int=0; suspended:bool=False; talents:tuple[str,...]=(); morale:int=70
    @property
    def name(self): return f'{self.first_name} {self.last_name}'
