from dataclasses import dataclass
from math import floor

ATTRS = ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')
FOCUS_ATTRS = {
    'ATTACK': ('shooting','dribbling'),
    'PLAYMAKING': ('passing','mental'),
    'DEFENSE': ('defending','physical'),
    'PHYSICAL': ('physical','stamina','pace'),
    'TECHNICAL': ('passing','dribbling'),
    'MENTAL': ('mental','passing'),
    'BALANCED': ATTRS,
    'RECOVERY': (),
}
INTENSITIES = {'LIGHT': 0.65, 'NORMAL': 1.0, 'HIGH': 1.35}

@dataclass(frozen=True)
class TrainingResult:
    focus: str
    intensity: str
    development_points: int
    injury_risk: float
    morale_delta: int

@dataclass(frozen=True)
class ContractOffer:
    salary: int
    years: int
    release_clause: int

@dataclass(frozen=True)
class TransferQuote:
    asking_price: int
    minimum_price: int
    wage: int

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def age_factor(age):
    age = int(age)
    if age <= 20: return 1.35
    if age <= 23: return 1.15
    if age <= 27: return 1.0
    if age <= 30: return 0.70
    if age <= 32: return 0.40
    return -0.20

def development_points(age, potential, overall, training_focus='BALANCED', intensity='NORMAL', minutes=0, morale=70, matches=0):
    gap = max(0, int(potential) - int(overall))
    if gap <= 0 and age > 29: return 0
    base = (1.0 + gap / 12.0) * age_factor(age)
    base *= INTENSITIES.get(str(intensity).upper(), 1.0)
    base *= 0.75 + clamp(int(morale),0,100) / 400
    base += min(1.5, max(0, int(minutes)) / 900)
    if matches >= 5: base += 0.25
    if str(training_focus).upper() == 'RECOVERY': base *= 0.55
    return max(0, int(floor(base)))

def attribute_growth(current, potential, points, focus, attr):
    if points <= 0 or attr not in ATTRS: return int(current)
    if int(current) >= int(potential): return int(current)
    focus = str(focus).upper()
    multiplier = 1.45 if attr in FOCUS_ATTRS.get(focus, ()) else 0.65
    delta = max(0, int(round(points * multiplier)))
    return clamp(int(current) + delta, 1, min(99, int(potential)))

def training_result(age, potential, overall, focus, intensity, minutes, morale, matches):
    pts = development_points(age,potential,overall,focus,intensity,minutes,morale,matches)
    intensity = str(intensity).upper()
    focus = str(focus).upper()
    risk = 0.01 + (0.025 if intensity == 'HIGH' else 0.008 if intensity == 'NORMAL' else 0.002)
    risk += max(0, 65-int(morale)) * 0.00035
    if focus == 'PHYSICAL': risk += 0.012
    morale_delta = 1 if intensity == 'NORMAL' and pts > 0 else (0 if intensity == 'LIGHT' else -1)
    if minutes < 45 and matches > 0: morale_delta -= 1
    return TrainingResult(focus,intensity,pts,min(0.25,risk),morale_delta)

def recommended_salary(market_value, overall, age, role='SQUAD'):
    base = max(15000, int(market_value) // 35)
    role_factor = {'STAR':1.35,'KEY':1.18,'SQUAD':1.0,'PROSPECT':0.82}.get(str(role).upper(),1.0)
    age_factor_v = 1.08 if age <= 23 else (1.05 if age <= 28 else 0.94)
    return int(base * role_factor * age_factor_v * (0.92 + clamp(overall,50,99)/500))

def contract_offer(market_value, overall, age, years=3, role='SQUAD', release_clause=None):
    years = clamp(int(years), 1, 5)
    salary = recommended_salary(market_value,overall,age,role)
    clause = int(release_clause) if release_clause is not None else max(int(market_value)*2, salary*years*18)
    return ContractOffer(salary, years, clause)

def transfer_quote(market_value, age, form=0, contract_years=2, listed=True):
    value = max(50000, int(market_value))
    age_factor_v = 1.12 if age <= 21 else (1.05 if age <= 25 else (0.96 if age <= 29 else 0.78))
    form_factor = 1 + clamp(int(form),-2,2)*0.035
    contract_factor = 0.88 if contract_years <= 0 else (0.95 if contract_years == 1 else 1.0)
    asking = int(value * age_factor_v * form_factor * contract_factor)
    minimum = int(asking * (0.88 if listed else 0.94))
    wage = max(15000, value // 35)
    return TransferQuote(asking, minimum, wage)

def morale_after_match(morale, minutes, rating, result, expectation='SQUAD', starts=False):
    delta = 0
    delta += 1 if rating >= 7.2 else (-1 if rating < 6.2 else 0)
    if result == 'WIN': delta += 2
    elif result == 'LOSS': delta -= 2
    if starts: delta += 1
    elif minutes < 30: delta -= 1
    if expectation == 'STAR' and minutes < 60: delta -= 1
    return clamp(int(morale)+delta,0,100)

def practice_minutes(matches_played, minutes_played, starts):
    return max(0, int(matches_played))*1 + max(0,int(minutes_played))//90 + max(0,int(starts))*1
