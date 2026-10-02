from collections import Counter
from dataclasses import dataclass

@dataclass(frozen=True)
class ScoutingReport:
    opponent_id: int
    opponent_name: str
    sample_matches: int
    typical_formation: str
    typical_style: str
    avg_goals_for: float
    avg_goals_against: float
    key_players: tuple[str, ...]
    tendencies: tuple[str, ...]
    counter_recommendations: tuple[str, ...]


def build_scout_report(opponent_id, opponent_name, matches, player_events):
    """Build a compact, explainable opponent report from observed match data."""
    n=len(matches)
    formations=Counter(m.get('formation') for m in matches if m.get('formation'))
    styles=Counter(m.get('style') for m in matches if m.get('style'))
    formation=formations.most_common(1)[0][0] if formations else 'UNKNOWN'
    style=styles.most_common(1)[0][0] if styles else 'UNKNOWN'
    gf=sum(int(m.get('goals_for') or 0) for m in matches)
    ga=sum(int(m.get('goals_against') or 0) for m in matches)
    key=tuple(x[0] for x in sorted(player_events.items(), key=lambda kv:(-kv[1],kv[0]))[:5])
    tendencies=[]
    if n:
        if gf/n >= 1.75: tendencies.append('Сильная результативность')
        elif gf/n <= .85: tendencies.append('Невысокая результативность')
        if ga/n >= 1.50: tendencies.append('Уязвимость в обороне')
        elif ga/n <= .85: tendencies.append('Надёжная оборона')
    if formation in {'3-5-2','5-3-2'}: tendencies.append('Численное преимущество в центре/низкий блок')
    if formation=='4-3-3': tendencies.append('Опасность ширины и быстрых флангов')
    if formation=='4-2-3-1': tendencies.append('Опора на десятку между линиями')
    if style=='COUNTER': tendencies.append('Ожидает ошибки после потери мяча')
    if style=='POSSESSION': tendencies.append('Старается контролировать мяч')
    if style=='ATTACK': tendencies.append('Высокий атакующий риск')
    rec=[]
    if style=='COUNTER': rec.append('Не завышать линию без страховки')
    if formation=='4-3-3': rec.append('Закрывать фланги и пространство за крайними защитниками')
    if formation=='4-2-3-1': rec.append('Перекрыть передачу в зону AM')
    if formation in {'3-5-2','5-3-2'}: rec.append('Использовать ширину и перегружать фланги')
    if 'Надёжная оборона' in tendencies: rec.append('Увеличить терпение и качество последнего паса')
    if 'Уязвимость в обороне' in tendencies: rec.append('Повысить интенсивность прессинга')
    return ScoutingReport(opponent_id,opponent_name,n,formation,style,round(gf/n,2) if n else 0.0,round(ga/n,2) if n else 0.0,key,tuple(tendencies),tuple(rec))
