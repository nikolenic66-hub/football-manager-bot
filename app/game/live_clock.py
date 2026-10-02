from dataclasses import dataclass
SECONDS_PER_GAME_MINUTE = 2
HALFTIME_SECONDS = 60
FIRST_HALF_SECONDS = 45 * SECONDS_PER_GAME_MINUTE
SECOND_HALF_SECONDS = 45 * SECONDS_PER_GAME_MINUTE
@dataclass(frozen=True)
class LiveClock:
    phase: str
    elapsed_seconds: float
    @property
    def minute(self) -> int:
        if self.phase == 'FIRST_HALF': return min(45, int(self.elapsed_seconds // SECONDS_PER_GAME_MINUTE))
        if self.phase == 'SECOND_HALF': return min(90, 45 + int(self.elapsed_seconds // SECONDS_PER_GAME_MINUTE))
        if self.phase in {'HALFTIME','FINISHED'}: return 45 if self.phase=='HALFTIME' else 90
        return 0
