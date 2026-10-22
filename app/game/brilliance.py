from random import Random


def brilliance_chance(player) -> float:
    """Rare per-player chance for a one-off moment of brilliance."""
    dribbling=float(getattr(player,'dribbling',0) or 0)
    mental=float(getattr(player,'mental',0) or 0)
    flair=float(getattr(player,'flair',0) or 0)
    chance=0.010 + 0.005*(dribbling/100.0) + 0.003*(mental/100.0)
    if flair:
        chance += 0.004*(max(0.0,min(100.0,flair))/100.0)
    return max(0.01,min(0.02,chance))


def roll_brilliance(player, rng) -> bool:
    """Return True when this player produces a rare individual brilliance roll."""
    return rng.random() < brilliance_chance(player)


def brilliance_xg_bonus(rng) -> float:
    return round(rng.uniform(0.30,0.50),2)
