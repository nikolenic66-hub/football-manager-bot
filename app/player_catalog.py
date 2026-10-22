"""Deterministic fictional player catalog with diverse, unique premium names."""

POSITION_CYCLE = ('GK', 'CB', 'LB', 'RB', 'DM', 'CM', 'AM', 'LW', 'RW', 'ST')
_FIRST = (
    'Lars', 'Marco', 'Jonas', 'Daniel', 'Aren', 'Alex',
    'Noah', 'Sven', 'Rafael', 'Nico', 'Victor', 'Leo',
    'Max', 'Tom', 'Elias', 'Felix', 'Oscar', 'Lucas',
    'Mateo', 'Ivan', 'Adrian', 'Julian', 'Matias', 'Dario',
    'Enzo', 'Leon', 'Theo', 'Samuel', 'David', 'Oskar',
    'Anton', 'Bruno', 'Carlos', 'Diego', 'Emil', 'Fabian',
    'Gabriel', 'Hugo', 'Isaac', 'Jasper', 'Kai', 'Liam',
    'Louis', 'Marek', 'Martin', 'Nils', 'Oliver', 'Paul',
    'Robin', 'Simon', 'Stefan', 'Tobias', 'Valentin', 'Willem',
    'Yannick', 'Aaron', 'Adam', 'Bastian', 'Benedikt', 'Cedric',
    'Christian', 'Damian', 'Dennis', 'Dominik', 'Edgar', 'Erik',
    'Finn', 'Florian', 'Frederik', 'Georg', 'Henrik', 'Jan',
    'Jannik', 'Jens', 'Joel', 'Jonathan', 'Josip', 'Kevin',
    'Kilian', 'Konrad', 'Lennart', 'Luca', 'Marcel', 'Mario',
    'Markus', 'Marius', 'Mats', 'Maurice', 'Michael', 'Mika',
    'Niklas', 'Nikolai', 'Patrick', 'Philipp', 'Raphael', 'Robert',
    'Ruben', 'Sebastian', 'Svenja', 'Tim', 'Timothy', 'Vincent',
    'Walter', 'Xavier', 'Yuri', 'Zacharias', 'Alessio', 'Andrej',
    'Artur', 'Bogdan', 'Boris', 'Cristian', 'Damir', 'Emanuel',
    'Fabio', 'Filip', 'Goran', 'Igor', 'Jakub', 'Kamil',
)
_LAST = (
    'van Dijk', 'Silva', 'Jansen', 'Costa', 'Martin', 'Santos',
    'Muller', 'Rossi', 'Bakker', 'Novak', 'Kovacs', 'Fischer',
    'Berg', 'Moretti', 'de Jong', 'Keller', 'Meyer', 'Pereira',
    'Lind', 'Romero', 'Petrov', 'Nielsen', 'Andersson', 'Garcia',
    'Fernandez', 'Mendez', 'Serrano', 'Kovac', 'Popescu', 'Marin',
    'Schmidt', 'Schneider', 'Weber', 'Wagner', 'Becker', 'Hoffmann',
    'Richter', 'Klein', 'Wolf', 'Schroder', 'Neumann', 'Braun',
    'Kruger', 'Hartmann', 'Lange', 'Werner', 'Schmitz', 'Krause',
    'Meier', 'Lehmann', 'Huber', 'Maier', 'Kaiser', 'Fuchs',
    'Pfeiffer', 'Winter', 'Kramer', 'Brandt', 'Busch', 'Leroy',
    'Dupont', 'Laurent', 'Moreau', 'Lefevre', 'Fontaine', 'Girard',
    'Mercier', 'Lambert', 'Rousseau', 'Bernard', 'Dubois', 'Robert',
    'Richard', 'Petit', 'Durand', 'Leroux', 'Garnier', 'Chevalier',
    'Barbier', 'Bianchi', 'Conti', 'Marino', 'Greco', 'Ferrari',
    'Romano', 'Colombo', 'Ricci', 'Gallo', 'Esposito', 'Rinaldi',
    'De Luca', 'Caruso', 'Ferraro', 'Bianco', 'Mancini', 'Giordano',
    'Martino', 'Longo', 'Serra', 'Svensson', 'Johansson', 'Karlsson',
    'Larsson', 'Olsson', 'Eriksson', 'Persson', 'Gustafsson', 'Pettersson',
    'Lindberg', 'Lundberg', 'Bergstrom', 'Axelsson', 'Holm', 'Berglund',
    'Soderberg', 'Nyberg', 'Ekstrom', 'Lindgren', 'Wallin', 'Kral',
)

def _catalog():
    out=[]
    nationalities=('NLD','ESP','ARG','BRA','FRA','DEU','ITA','PRT','ENG','SCO','IRL','BEL','CHE','AUT','DNK','SWE','NOR','FIN','POL','CZE','SVK','HRV','SRB','ROU','GRC','TUR','MAR','NGA','GHA','CIV','SEN','CMR','JPN','KOR','AUS','USA','MEX','COL','URY','CHL')
    for i in range(120):
        pos=POSITION_CYCLE[i % len(POSITION_CYCLE)]
        first=_FIRST[i]
        last=_LAST[i]
        nat=nationalities[(i * 17 + i // 7) % len(nationalities)]
        out.append((first,last,nat,pos))
    return out

REAL_PLAYERS = _catalog()
REAL_ORDER = list(REAL_PLAYERS[:120])
LEGEND_NAMES = set()
assert len(REAL_ORDER) == 120
assert len({f'{a} {b}' for a,b,_,_ in REAL_ORDER}) == 120
assert len({b.casefold() for _,b,_,_ in REAL_ORDER}) == 120
