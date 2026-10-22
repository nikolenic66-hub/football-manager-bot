from random import Random

WEATHER_PROBABILITIES=(
    ('CLEAR',0.60),
    ('RAIN',0.20),
    ('WIND',0.10),
    ('SNOW',0.05),
    ('HEAT',0.05),
)
WEATHER_LABELS={
    'CLEAR':'☀️ Ясно',
    'RAIN':'🌧️ Дождь',
    'SNOW':'❄️ Снег',
    'WIND':'💨 Ветер',
    'HEAT':'🥵 Жара',
}


def generate_weather(rng=None) -> str:
    rng=rng or Random()
    roll=rng.random()
    total=0.0
    for weather,probability in WEATHER_PROBABILITIES:
        total += probability
        if roll < total:
            return weather
    return 'HEAT'


def weather_label(weather) -> str:
    return WEATHER_LABELS.get(str(weather or 'CLEAR').upper(),WEATHER_LABELS['CLEAR'])


def weather_xg_modifier(weather):
    weather=str(weather or 'CLEAR').upper()
    return {
        'RAIN': {'passing':0.92,'shooting':1.00,'tempo':1.00},
        'SNOW': {'passing':0.85,'shooting':1.00,'tempo':1.00},
        'WIND': {'passing':1.00,'shooting':0.90,'tempo':1.00},
        'HEAT': {'passing':1.00,'shooting':1.00,'tempo':0.95},
        'CLEAR': {'passing':1.00,'shooting':1.00,'tempo':1.00},
    }.get(weather,{'passing':1.00,'shooting':1.00,'tempo':1.00})


def weather_fatigue_multiplier(weather):
    weather=str(weather or 'CLEAR').upper()
    return {'SNOW':1.10,'HEAT':1.15}.get(weather,1.00)
