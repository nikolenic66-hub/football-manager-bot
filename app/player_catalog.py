# Curated real-player cards used for the premium part of the initial pool.
# The importer can extend this catalog with a licensed/publicly usable dataset.
REAL_PLAYERS = [
('Gianluigi','Donnarumma','ITA','GK'),('Thibaut','Courtois','BEL','GK'),('Alisson','Becker','BRA','GK'),('Manuel','Neuer','GER','GK'),
('Jan','Oblak','SVN','GK'),('Ederson','Moraes','BRA','GK'),('Mike','Maignan','FRA','GK'),('Emiliano','Martinez','ARG','GK'),
('Marc-Andre','ter Stegen','GER','GK'),('Yassine','Bounou','MAR','GK'),('David','Raya','ESP','GK'),('Diogo','Costa','POR','GK'),
('Virgil','van Dijk','NED','CB'),('Ruben','Dias','POR','CB'),('William','Saliba','FRA','CB'),('Antonio','Rudiger','GER','CB'),
('Marquinhos','','BRA','CB'),('Alessandro','Bastoni','ITA','CB'),('Ronald','Araujo','URU','CB'),('Eder','Militao','BRA','CB'),
('Matthijs','de Ligt','NED','CB'),('William','Pacho','ECU','CB'),('Pau','Torres','ESP','CB'),('Kim','Min-jae','KOR','CB'),
('Achraf','Hakimi','MAR','RB'),('Trent','Alexander-Arnold','ENG','RB'),('Kyle','Walker','ENG','RB'),('Reece','James','ENG','RB'),
('Dani','Carvajal','ESP','RB'),('Jeremie','Frimpong','NED','RB'),('Jules','Kounde','FRA','RB'),('Joao','Cancelo','POR','RB'),
('Theo','Hernandez','FRA','LB'),('Alphonso','Davies','CAN','LB'),('Andrew','Robertson','SCO','LB'),('Nuno','Mendes','POR','LB'),
('Federico','Dimarco','ITA','LB'),('Alejandro','Grimaldo','ESP','LB'),('Marc','Cucurella','ESP','LB'),('Ferland','Mendy','FRA','LB'),
('Rodri','Hernandez','ESP','DM'),('Declan','Rice','ENG','DM'),('Martin','Zubimendi','ESP','DM'),('Joshua','Kimmich','GER','DM'),
('Aurelien','Tchouameni','FRA','DM'),('Moisés','Caicedo','ECU','DM'),('Bruno','Guimaraes','BRA','DM'),('Nicolo','Barella','ITA','CM'),
('Jude','Bellingham','ENG','CM'),('Pedri','Gonzalez','ESP','CM'),('Federico','Valverde','URU','CM'),('Toni','Kroos','GER','CM'),
('Luka','Modric','CRO','CM'),('Bernardo','Silva','POR','AM'),('Kevin','De Bruyne','BEL','AM'),('Martin','Odegaard','NOR','AM'),
('Jamal','Musiala','GER','AM'),('Florian','Wirtz','GER','AM'),('Phil','Foden','ENG','AM'),('Cole','Palmer','ENG','AM'),
('Vinicius','Junior','BRA','LW'),('Kylian','Mbappe','FRA','LW'),('Khvicha','Kvaratskhelia','GEO','LW'),('Rafael','Leao','POR','LW'),
('Luis','Diaz','COL','LW'),('Nico','Williams','ESP','LW'),('Jack','Grealish','ENG','LW'),('Leroy','Sane','GER','RW'),
('Mohamed','Salah','EGY','RW'),('Bukayo','Saka','ENG','RW'),('Rodrygo','Goes','BRA','RW'),('Lamine','Yamal','ESP','RW'),
('Riyad','Mahrez','ALG','RW'),('Lionel','Messi','ARG','RW'),('Cristiano','Ronaldo','POR','ST'),('Erling','Haaland','NOR','ST'),
('Harry','Kane','ENG','ST'),('Robert','Lewandowski','POL','ST'),('Victor','Osimhen','NGA','ST'),('Lautaro','Martinez','ARG','ST'),
('Alexander','Isak','SWE','ST'),('Victor','Gyokeres','SWE','ST'),('Julian','Alvarez','ARG','ST'),('Ollie','Watkins','ENG','ST'),
('Dusan','Vlahovic','SRB','ST'),('Romelu','Lukaku','BEL','ST'),('Karim','Benzema','FRA','ST'),('Luis','Suarez','URU','ST'),
('Neymar','Junior','BRA','LW'),('Zlatan','Ibrahimovic','SWE','ST'),('Sergio','Ramos','ESP','CB'),('Marcelo','Vieira','BRA','LB'),
('Andres','Iniesta','ESP','CM'),('Xavi','Hernandez','ESP','CM'),('Andrea','Pirlo','ITA','CM'),('Francesco','Totti','ITA','AM'),
('Kaká','','BRA','AM'),('Ronaldinho','Gaucho','BRA','LW'),('Ronaldo','Nazario','BRA','ST'),('Thierry','Henry','FRA','ST'),
('Didier','Drogba','CIV','ST'),('Samuel','Eto\'o','CMR','ST'),('George','Weah','LBR','ST'),('Pelé','','BRA','ST'),
('Diego','Maradona','ARG','AM'),('Johan','Cruyff','NED','AM'),('Franz','Beckenbauer','GER','CB'),('Paolo','Maldini','ITA','CB'),
('Lev','Yashin','URS','GK'),('Bobby','Charlton','ENG','AM'),('Michel','Platini','FRA','AM'),('Marco','van Basten','NED','ST'),
('Ruud','Gullit','NED','CM'),('Lothar','Matthaus','GER','CM'),('Gerd','Muller','GER','ST'),('Roberto','Baggio','ITA','AM'),
('Zinedine','Zidane','FRA','AM'),('Fabio','Cannavaro','ITA','CB'),('Cafu','','BRA','RB'),('Roberto','Carlos','BRA','LB'),
]

LEGEND_NAMES = {
'Pelé','Diego Maradona','Johan Cruyff','Franz Beckenbauer','Lev Yashin','Bobby Charlton','Michel Platini','Marco van Basten',
'Ruud Gullit','Lothar Matthaus','Gerd Muller','Roberto Baggio','Zinedine Zidane','Fabio Cannavaro','Cafu','Roberto Carlos',
'Ronaldo Nazario','Ronaldinho Gaucho','Thierry Henry','Paolo Maldini','Xavi Hernandez','Andres Iniesta','Andrea Pirlo','Kaká',
'Francesco Totti','George Weah','Didier Drogba',"Samuel Eto'o",'Zlatan Ibrahimovic','Luis Suarez'
}


# Single canonical ordering shared by DB seeding, card generation and visual audits.
_non_legends=[x for x in REAL_PLAYERS if f'{x[0]} {x[1]}'.strip() not in LEGEND_NAMES]
_legends=[x for x in REAL_PLAYERS if f'{x[0]} {x[1]}'.strip() in LEGEND_NAMES]
REAL_ORDER=_legends[:30]+_non_legends[:80]+_non_legends[80:]
assert len(REAL_ORDER)==120 and len({f'{a} {b}'.strip() for a,b,_,_ in REAL_ORDER})==120
