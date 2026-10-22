"""Deterministic fictional club names used by anonymization scripts."""

CLUB_NAMES = (
    'Arden Wolves', 'Stonebridge Lions', 'Kingsport United', 'Ravenhill Rovers',
    'Westhaven Athletic', 'Ironwood FC', 'Redmont City', 'Silverlake Town',
    'Northstar Albion', 'Cedarfield Wanderers', 'Ashford Royals', 'Brightwater FC',
    'Eastcliff United', 'Foxborough Athletic', 'Goldcrest Rovers', 'Highmoor City',
    'Lakewood Rangers', 'Meadowgate FC', 'Oakridge Albion', 'Pinehurst Town',
    'Queensvale United', 'Riverstone Athletic', 'Southbank Rovers', 'Thornfield FC',
    'Upton Vale', 'Valewick City', 'Willowmere United', 'Yorkford Athletic',
    'Amberley FC', 'Blackthorn Rovers', 'Copperfield United', 'Dunhaven City',
    'Eagleford Athletic', 'Fairmont FC', 'Glenhaven Town', 'Harborstone United',
    'Ivydale Rovers', 'Juniper City', 'Kestrelford FC', 'Longbridge Athletic',
    'Moonridge United', 'Newborough Town', 'Oldcastle Rovers', 'Parkstone FC',
    'Quartzside Athletic', 'Rosewick United', 'Stormhaven City', 'Timberford FC',
    'Umberleigh Rovers', 'Violetbridge United', 'Windermere Athletic', 'Yarrowfield FC',
    'Briarcliff City', 'Cloudmere United', 'Driftwood Rovers', 'Elmstead Athletic',
    'Frostford FC', 'Greenhaven Town', 'Holloway United', 'Kingswell Rovers',
    'Larkspur City', 'Moorland Athletic', 'Northridge FC', 'Orchard Vale United',
    'Portwell Rovers', 'Quarryside City', 'Ridgebourne Athletic', 'Seabrook FC',
    'Tanglewood United', 'Westmere Rovers', 'Yorkshire Vale Athletic', 'Zenith Borough FC',
)

ROMAN = ('I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X')


def fictional_club_name(index: int) -> str:
    """Return a meaningful deterministic club name for a zero-based index."""
    index = max(0, int(index))
    if index < len(CLUB_NAMES):
        return CLUB_NAMES[index]
    base_index = index % len(CLUB_NAMES)
    cycle = index // len(CLUB_NAMES)
    numeral = ROMAN[cycle] if cycle < len(ROMAN) else str(cycle + 1)
    return f'{CLUB_NAMES[base_index]} {numeral}'
