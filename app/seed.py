import asyncio
import json
import os
from random import Random
from sqlalchemy import text
from .db import SessionLocal
from .player_catalog import REAL_ORDER

TOTAL_PLAYERS=3000
RARITY_COUNTS={'BASE':2080,'RARE':500,'EPIC':300,'LEGENDARY':120}
LEGACY_SEED_PLAYERS=1248
LEGACY_RARITY_COUNTS={'BASE':888,'RARE':250,'EPIC':80,'LEGENDARY':30}
NATIONALITIES=['NLD','ESP','ARG','BRA','FRA','DEU','ITA','PRT','ENG','SCO','IRL','BEL','CHE','AUT','DNK','SWE','NOR','FIN','POL','CZE','SVK','HRV','SRB','ROU','GRC','TUR','MAR','NGA','GHA','CIV','SEN','CMR','JPN','KOR','AUS','USA','MEX','COL','URY','CHL']
POSITIONS=['GK']*100+['CB']*190+['LB']*90+['RB']*90+['DM']*100+['CM']*210+['AM']*120+['LW']*80+['RW']*80+['ST']*188
FIRST_NAMES=[
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
    'Kristian', 'Luka', 'Milos', 'Nando', 'Nikola', 'Pavel',
    'Roman', 'Sandro', 'Sergio', 'Tomas', 'Viktor', 'Vladimir',
    'Alejandro', 'Alvaro', 'Andres', 'Esteban', 'Joaquin', 'Thiago',
    'Rene', 'Bran', 'Abel', 'Abraham', 'Achille', 'Adem',
    'Adil', 'Adnan', 'Agustin', 'Ahmad', 'Ahmed', 'Aitor',
    'Akira', 'Alain', 'Alan', 'Alberto', 'Albin', 'Aleksandar',
    'Aleksander', 'Alexandre', 'Alfonso', 'Alfred', 'Ali', 'Amadou',
    'Amir', 'Ander', 'Andreas', 'Andrei', 'Angel', 'Angelo',
    'Anis', 'Ansel', 'Arda', 'Armand', 'Armin', 'Arnaud',
    'Aron', 'Arthur', 'Arturo', 'Aslan', 'August', 'Augustin',
    'Aurel', 'Axel', 'Baptiste', 'Baran', 'Bartosz', 'Ben',
    'Benjamin', 'Berkay', 'Bernardo', 'Bilal', 'Bjorn', 'Bo',
    'Borja', 'Brandon', 'Branislav', 'Brent', 'Brian', 'Callum',
    'Camilo', 'Carlo', 'Casper', 'Charles', 'Cheikh', 'Chico',
    'Chris', 'Clement', 'Colin', 'Conor', 'Cristiano', 'Cyril',
    'Damien', 'Danny', 'Darius', 'Dean', 'Denis', 'Diogo',
    'Dion', 'Donovan', 'Douglas', 'Dries', 'Dušan', 'Ederson',
    'Edin', 'Edouard', 'Edward', 'Elian', 'Elijah', 'Ellis',
    'Elvis', 'Emre', 'Enrico', 'Enrique', 'Etienne', 'Evan',
    'Ezequiel', 'Fabien', 'Faris', 'Federico', 'Felipe', 'Ferenc',
    'Fernando', 'Ferran', 'Filipe', 'Flavio', 'Francesco', 'Francis',
    'Franck', 'Franco', 'Frank', 'Freddie', 'Gael', 'Gaston',
    'Gennaro', 'Gerard', 'Gian', 'Gianluca', 'Gil', 'Goncalo',
    'Gonzalo', 'Guillermo', 'Gustav', 'Habib', 'Hamza', 'Hans',
    'Haris', 'Harvey', 'Hasan', 'Hassan', 'Hatem', 'Hiro',
    'Hussein', 'Ibrahim', 'Iker', 'Imran', 'Ismael', 'Jaime',
    'Jamal', 'James', 'Jamie', 'Jean', 'Jefferson', 'Jeremy',
    'Jesper', 'Joao', 'Johan', 'Johannes', 'John', 'Jon',
    'Jordan', 'Jorge', 'Jose', 'Juan', 'Julio', 'Junior',
    'Juste', 'Kacper', 'Kaique', 'Karim', 'Kasper', 'Keita',
    'Kenan', 'Kentaro', 'Khaled', 'Kim', 'Kirill', 'Klaus',
    'Kofi', 'Krzysztof', 'Lamine', 'Leandro', 'Lee', 'Leonardo',
    'Levi', 'Lorenzo', 'Luc', 'Manuel', 'Marc', 'Marcos',
    'Mariano', 'Marko', 'Mathias', 'Mathieu', 'Mehdi', 'Melvin',
    'Mesut', 'Miguel', 'Mohamed', 'Mohammad', 'Moussa', 'Nabil',
    'Nathan', 'Nelson', 'Neymar', 'Nicolas', 'Niels', 'Nordin',
    'Orhan', 'Oumar', 'Pascal', 'Pedro', 'Peter', 'Piotr',
    'Quincy', 'Rachid', 'Rami', 'Rasmus', 'Raul', 'Rayane',
    'Reece', 'Renato', 'Ricardo', 'Riyad', 'Rodrigo', 'Romain',
    'Ronald', 'Ryan', 'Said', 'Salah', 'Salim', 'Salvatore',
    'Sami', 'Serge', 'Shota', 'Simeon', 'Soeren', 'Stephan',
    'Suleiman', 'Taha', 'Takumi', 'Tariq', 'Taylor', 'Teemu',
    'Thierry', 'Thomas', 'Tijani', 'Tomasz', 'Valerio', 'Vicente',
    'Victor Hugo', 'Vinicius', 'William', 'Yacine', 'Yanis', 'Yassine',
    'Yusuf', 'Zlatan', 'Abdou', 'Abelardo', 'Abner', 'Adrianus',
    'Alessandro', 'Amine', 'Anatole', 'Anatoliy', 'Andrzej', 'Anwar',
    'Arsen', 'Artem', 'Atakan', 'Aziz', 'Badr', 'Balint',
    'Bartos', 'Basel', 'Benicio', 'Brayan', 'Caleb', 'Cem',
    'Chinedu', 'Chukwudi', 'Cristobal', 'Demba', 'Demir', 'Devon',
    'Dusan', 'Efe', 'Elif', 'Emiliano', 'Emilio', 'Eray',
]
LAST_NAMES=[
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
    'Horvat', 'Varga', 'Balogh', 'Nagy', 'Szabo', 'Toth',
    'Kiss', 'Mihailov', 'Dimitrov', 'Stoyanov', 'Georgiev', 'Iliev',
    'Kostov', 'Markovic', 'Jovanovic', 'Nikolic', 'Stankovic', 'Ilic',
    'Vukovic', 'Alves', 'Teixeira', 'Coelho', 'Pinto', 'Carvalho',
    'Fernandes', 'Monteiro', 'Matos', 'Correia', 'Nunes', 'Sousa',
    'Araujo', 'Cardoso', 'Rocha', 'Barbosa', 'Ribeiro', 'Cunha',
    'Fonseca', 'Machado', 'Vaz', 'Pires', 'Abadi', 'Abbas',
    'Abdalla', 'Abdullah', 'Abraham', 'Abreu', 'Acosta', 'Adams',
    'Aguilar', 'Ahmed', 'Akhtar', 'Alami', 'Alarcon', 'Alba',
    'Alberti', 'Alcantara', 'Alcaraz', 'Aldana', 'Aleman', 'Alfaro',
    'Ali', 'Almeida', 'Alonso', 'Altun', 'Alvarez', 'Amador',
    'Amari', 'Amaya', 'Andrade', 'Andrews', 'Angeli', 'Ansaldi',
    'Antonelli', 'Aoki', 'Aranda', 'Archer', 'Arellano', 'Armas',
    'Armstrong', 'Arriaga', 'Asano', 'Aslan', 'Atkinson', 'Avila',
    'Ayala', 'Aziz', 'Ba', 'Baba', 'Babic', 'Bae',
    'Baena', 'Bah', 'Bahri', 'Bai', 'Bailey', 'Baines',
    'Baker', 'Balazs', 'Ball', 'Ban', 'Banerjee', 'Baptista',
    'Baran', 'Barclay', 'Barrera', 'Barreto', 'Barron', 'Barry',
    'Barton', 'Basak', 'Bass', 'Batista', 'Bauer', 'Bautista',
    'Baxter', 'Bayram', 'Beasley', 'Beck', 'Beckham', 'Bello',
    'Beltran', 'Benali', 'Benitez', 'Benjamin', 'Benn', 'Benson',
    'Berisha', 'Bermudez', 'Bernardi', 'Berry', 'Bessa', 'Bessaoud',
    'Bidwell', 'Bielik', 'Birch', 'Bishop', 'Blanco', 'Blanchard',
    'Blom', 'Bolton', 'Bonilla', 'Bonnet', 'Borges', 'Bosch',
    'Boskovic', 'Bouchard', 'Boufal', 'Bouma', 'Bourne', 'Bowen',
    'Boyle', 'Braga', 'Brandao', 'Bridges', 'Briggs', 'Briones',
    'Brito', 'Broadbent', 'Broberg', 'Brown', 'Bryant', 'Bucur',
    'Bull', 'Burgess', 'Burns', 'Burton', 'Byrne', 'Cabrera',
    'Caceres', 'Cadiz', 'Caicedo', 'Cairney', 'Calderon', 'Calvert',
    'Campana', 'Campos', 'Candela', 'Cannon', 'Cano', 'Cardenas',
    'Carey', 'Carr', 'Carrasco', 'Carreño', 'Carroll', 'Carter',
    'Casas', 'Castaneda', 'Castro', 'Cavalli', 'Celik', 'Chacon',
    'Chambers', 'Chan', 'Chang', 'Chapman', 'Chavez', 'Chen',
    'Cheng', 'Choi', 'Chung', 'Cisse', 'Clarke', 'Clay',
    'Coates', 'Cohen', 'Collins', 'Conceicao', 'Connor', 'Contreras',
    'Cooper', 'Cordoba', 'Correa', 'Cortes', 'Cotter', 'Coutinho',
    'Cox', 'Cruz', 'Curry', 'Dahl', 'Dalby', 'Dale',
    'Damiani', 'Daniel', 'Dao', 'Darwish', 'Das', 'Davies',
    'Dawson', 'Deane', 'Delgado', 'Dembele', 'Demir', 'Denis',
    'Dia', 'Diaz', 'Dickson', 'Diniz', 'Dixon', 'Djokovic',
    'Dlamini', 'Doan', 'Dobson', 'Doherty', 'Dolan', 'Dominguez',
    'Donati', 'Donovan', 'Doyle', 'Drummond', 'Duarte', 'Duffy',
    'Dumont', 'Duncan', 'Duran', 'Dyer', 'Echeverria', 'Edwards',
    'Ek', 'El Amrani', 'El Haddad', 'El Masri', 'Elbaz', 'Ellis',
    'Emerson', 'Engel', 'Estevez', 'Evans', 'Fabregas', 'Fahmi',
    'Fairweather', 'Fan', 'Farias', 'Ferrara', 'Ferrer', 'Figueroa',
    'Finch', 'Flores', 'Foley', 'Ford', 'Foster', 'Franco',
    'Fraser', 'Friedrich', 'Fujita', 'Fukuda', 'Fuentes', 'Fuller',
    'Furlan', 'Gallardo', 'Galloway', 'Gama', 'Gamero', 'Gao',
    'Garay', 'Garland', 'Garrido', 'Gates', 'Gavrilovic', 'Gayle',
    'Geiger', 'George', 'Gibbs', 'Gil', 'Giles', 'Gill',
    'Gilmore', 'Giuliani', 'Glass', 'Godoy', 'Gomes', 'Gomez',
    'Gonzales', 'Gonzalez', 'Goodman', 'Gordon', 'Goto', 'Graham',
    'Grant', 'Grassi', 'Green', 'Griffin', 'Grimaldi', 'Guerrero',
    'Guimaraes', 'Gutierrez', 'Guzman', 'Haddad', 'Hahn', 'Hale',
    'Hamada', 'Hamilton', 'Hammond', 'Hansen', 'Hao', 'Hardy',
    'Hargreaves', 'Harris', 'Harrison', 'Hart', 'Hasegawa', 'Hassan',
    'Hawkins', 'Hayashi', 'Hayes', 'He', 'Hernandez', 'Herrera',
    'Hidalgo', 'Hill', 'Hirano', 'Hobbs', 'Hodgson', 'Hoffman',
    'Hogan', 'Holmes', 'Honda', 'Hong', 'Hopkins', 'Howard',
    'Huang', 'Hughes', 'Hunt', 'Hunter', 'Hussain', 'Ibrahim',
    'Iglesias', 'Imai', 'Inoue', 'Irwin', 'Ismail', 'Jackson',
    'Jafari', 'James', 'Jaramillo', 'Jarvis', 'Jeffers', 'Jenkins',
    'Jimenez', 'Johnston', 'Jones', 'Jordan', 'Jung', 'Kadlec',
    'Kahn', 'Kang', 'Kaplan', 'Kato', 'Kaur', 'Kaya',
    'Kazemi', 'Keane', 'Kelly', 'Kemp', 'Kennedy', 'Khan',
    'Kim', 'King', 'Kirk', 'Kirkland', 'Kobayashi', 'Koch',
    'Kohler', 'Kok', 'Kolar', 'Kong', 'Kowalski', 'Kozak',
    'Kubota', 'Kumar', 'Kurihara', 'Kwon', 'Lam', 'Lara',
    'Larsen', 'Lau', 'Law', 'Lawson', 'Lee', 'Legrand',
    'Leite', 'Lemaitre', 'Leon', 'Leone', 'Lewis', 'Li',
    'Lima', 'Lin', 'Liu', 'Lloyd', 'Lopez', 'Lorenzo',
    'Lowe', 'Lu', 'Luna', 'Lyons', 'Ma', 'Macias',
    'Mackenzie', 'Mahmoud', 'Malone', 'Mancuso', 'Mani', 'Marques',
    'Marshall', 'Martel', 'Martinez', 'Masuda', 'Mathis', 'Matthews',
    'Mazur', 'Mbaye', 'McBride', 'McCarthy', 'McConnell', 'McDonald',
    'McGrath', 'McKenzie', 'Medina', 'Mejia', 'Mendes', 'Mendoza',
    'Menon', 'Merino', 'Middleton', 'Mihajlovic', 'Milani', 'Miller',
    'Miranda', 'Miyazaki', 'Molina', 'Monroe', 'Montoya', 'Moore',
    'Morales', 'Morel', 'Moreno', 'Morgan', 'Mori', 'Morris',
    'Morrison', 'Morton', 'Mosley', 'Moura', 'Munoz', 'Murphy',
    'Murray', 'Nakamoto', 'Narayan', 'Nash', 'Navarro', 'Neves',
    'Ng', 'Nguyen', 'Nichols', 'Nishimura', 'Noguchi', 'Nolan',
    'Norris', 'Norton', 'Nunez', 'Ocampo', 'Ochoa', 'Oda',
    'Ohashi', 'Okafor', 'Okamoto', 'Oliveira', 'Ordonez', 'Ortega',
    'Ortiz', 'Osborne', 'Ota', 'Owens', 'Pacheco', 'Padilla',
    'Page', 'Palacios', 'Palmer', 'Pandey', 'Park', 'Parsons',
    'Patel', 'Payet', 'Peeters', 'Peña', 'Perez', 'Perkins',
    'Perry', 'Pham', 'Pierce', 'Pieters', 'Pineda', 'Pollard',
    'Porter', 'Prado', 'Prat', 'Price', 'Quintero', 'Quispe',
    'Rahman', 'Ramirez', 'Ramos', 'Randall', 'Rao', 'Rasmussen',
    'Raymond', 'Reed', 'Reeves', 'Reid', 'Reyes', 'Reynolds',
    'Rice', 'Rios', 'Rivera', 'Robles', 'Robinson', 'Rodrigues',
    'Rodriguez', 'Rojas', 'Roman', 'Rosales', 'Rosario', 'Roth',
    'Roy', 'Ruiz', 'Russell', 'Saito', 'Sakai', 'Salas',
    'Salazar', 'Sanchez', 'Sanders', 'Sato', 'Scholes', 'Schultz',
    'Scott', 'Seidel', 'Seo', 'Serna', 'Shah', 'Sharma',
    'Shaw', 'Shepherd', 'Shimizu', 'Shin', 'Short', 'Singh',
    'Smith', 'Snyder', 'Solis', 'Song', 'Sosa', 'Spencer',
    'Stark', 'Stein', 'Stephens', 'Stewart', 'Suarez', 'Sugimoto',
    'Sullivan', 'Suzuki', 'Swanson', 'Takahashi', 'Tanaka', 'Tan',
    'Taylor', 'Teague', 'Thomas', 'Thompson', 'Thorne', 'Tran',
    'Trujillo', 'Tsai', 'Turner', 'Valdez', 'Valente', 'Vasquez',
    'Vega', 'Velasquez', 'Velez', 'Ventura', 'Vera', 'Vidal',
    'Villanueva', 'Vincent', 'Voigt', 'Walker', 'Wallace', 'Wang',
    'Ward', 'Watson', 'White', 'Williams', 'Wilson', 'Wong',
    'Wood', 'Wright', 'Wu', 'Yamada', 'Yamashita', 'Yang',
    'Yilmaz', 'Yoon', 'Yoshida', 'Young', 'Yu', 'Zamora',
    'Zapata', 'Zhang', 'Zhao', 'Zhou', 'Zuniga', 'Abildgaard',
    'Albrektsen', 'Bergqvist', 'Bjorklund', 'Bonde', 'Christensen', 'Christiansen',
    'Dahlstrom', 'Eklund', 'Engstrom', 'Erlandsson', 'Falkenberg', 'Fredriksson',
    'Granqvist', 'Gunnarsson', 'Hansson', 'Hedlund', 'Henriksson', 'Holmberg',
    'Isaksson', 'Jorgensen', 'Kallstrom', 'Kjellberg', 'Lindqvist', 'Ljungberg',
    'Lundqvist', 'Magnusson', 'Nilsson', 'Nordstrom', 'Nygaard', 'Rosenberg',
    'Sandberg', 'Soderstrom', 'Stromberg', 'Sundqvist', 'Thorsson', 'Westberg',
    'Abramov', 'Antonov', 'Balabanov', 'Baranov', 'Belov', 'Bogdanov',
    'Bondarenko', 'Borisov', 'Bykov', 'Chernov', 'Cherny', 'Davydov',
    'Denisov', 'Dobrov', 'Dragic', 'Drozdov', 'Fedorov', 'Frolov',
    'Gavrilov', 'Golubev', 'Goncharov', 'Gusev', 'Ignatov', 'Ivanov',
    'Karpov', 'Kazakov', 'Kirilov', 'Klimov', 'Kolesnikov', 'Komarov',
    'Kondratyev', 'Korolev', 'Kotov', 'Kravchenko', 'Kuznetsov', 'Lebedev',
    'Leonov', 'Loginov', 'Markov', 'Melnik', 'Morozov', 'Muraviev',
    'Nikitin', 'Orlov', 'Osipov', 'Pavlov', 'Polyakov', 'Popov',
    'Prokhorov', 'Romanov', 'Savchenko', 'Sergeev', 'Smirnov', 'Sokolov',
    'Solovyov', 'Stepanov', 'Tarasov', 'Titov', 'Volkov', 'Voronin',
    'Yakovlev', 'Zaitsev', 'Zhukov', 'Andric', 'Baletic', 'Bogdanovic',
    'Bozic', 'Brankovic', 'Draganic', 'Djuric', 'Gajic', 'Grbic',
    'Hadžic', 'Jankovic', 'Juric', 'Knezevic', 'Kovacevic', 'Lazic',
    'Maric', 'Milic', 'Mitrovic', 'Obradovic', 'Petrovic', 'Radic',
    'Radovanovic', 'Simic', 'Stojanovic', 'Subotic', 'Topic', 'Vukasinovic',
    'Zoric', 'Antunes', 'Braganca', 'Branco', 'Carneiro', 'Castanheira',
    'Esteves', 'Figueiredo', 'Guerreiro', 'Lopes', 'Lourenco', 'Madeira',
    'Marinho', 'Nascimento', 'Peixoto', 'Queiroz', 'Reis', 'Camacho',
    'Carvajal', 'Castellanos', 'Del Rio', 'Domenech', 'Esparza', 'Gallego',
    'Ibanez', 'Llorente', 'Marquez', 'Navarrete', 'Quintana', 'Requena',
    'Salcedo', 'Solano', 'Valera', 'Zaragoza', 'Bouazizi', 'Bouzid',
    'Chakir', 'Charef', 'El Idrissi', 'Farouk', 'Ghanem', 'Haddou',
    'Hamdi', 'Jaziri', 'Khalifa', 'Lahlou', 'Mansouri', 'Mokhtar',
    'Naciri', 'Ouali', 'Rahimi', 'Slimani', 'Toumi', 'Abdullayev',
    'Aliyev', 'Aslanov', 'Babayev', 'Gasimov', 'Hasanov', 'Ismayilov',
    'Mammadov', 'Mirzayev', 'Mustafayev', 'Rahimov', 'Safarov', 'Taghiyev',
    'Yusifov', 'Alam', 'Anwar', 'Chowdhury', 'Dasgupta', 'Desai',
    'Dutta', 'Ghosh', 'Iyer', 'Jain', 'Kapoor', 'Khanna',
    'Malik', 'Mehta', 'Mukherjee', 'Nair', 'Reddy', 'Verma',
    'Chaudhry', 'Farooq', 'Hameed', 'Javed', 'Qureshi', 'Siddiqui',
    'van Dijk-Pereira', 'van Dijk-Pfeiffer', 'van Dijk-Caruso', 'van Dijk-Dimitrov', 'van Dijk-Abreu', 'van Dijk-Atkinson',
    'van Dijk-Bello', 'van Dijk-Brito', 'van Dijk-Chan', 'van Dijk-Delgado', 'van Dijk-Evans', 'van Dijk-Giles',
    'van Dijk-Hassan', 'van Dijk-Jimenez', 'van Dijk-Lau', 'van Dijk-McBride', 'van Dijk-Nash', 'van Dijk-Perez',
    'van Dijk-Roth', 'van Dijk-Sullivan', 'van Dijk-Wright', 'van Dijk-Kallstrom', 'van Dijk-Gusev', 'van Dijk-Volkov',
    'van Dijk-Castanheira', 'van Dijk-Hamdi', 'van Dijk-Mehta', 'Silva-Lind', 'Silva-Winter', 'Silva-Ferraro',
    'Silva-Stoyanov', 'Silva-Acosta', 'Silva-Avila', 'Silva-Beltran', 'Silva-Broadbent', 'Silva-Chang',
    'Silva-Dembele', 'Silva-Fabregas', 'Silva-Gill', 'Silva-Hawkins', 'Silva-Johnston', 'Silva-Law',
    'Silva-McCarthy', 'Silva-Navarro', 'Silva-Perkins', 'Silva-Roy', 'Silva-Suzuki', 'Silva-Wu',
    'Silva-Kjellberg', 'Silva-Ignatov', 'Silva-Voronin', 'Silva-Esteves', 'Silva-Jaziri', 'Silva-Mukherjee',
    'Jansen-Romero', 'Jansen-Kramer', 'Jansen-Bianco', 'Jansen-Georgiev', 'Jansen-Adams', 'Jansen-Ayala',
    'Jansen-Benali', 'Jansen-Broberg', 'Jansen-Chapman', 'Jansen-Demir', 'Jansen-Fahmi', 'Jansen-Gilmore',
    'Jansen-Hayashi', 'Jansen-Jones', 'Jansen-Lawson', 'Jansen-McConnell', 'Jansen-Neves', 'Jansen-Perry',
    'Jansen-Ruiz', 'Jansen-Swanson', 'Jansen-Yamada', 'Jansen-Lindqvist', 'Jansen-Ivanov', 'Jansen-Yakovlev',
    'Jansen-Figueiredo', 'Jansen-Khalifa', 'Jansen-Nair', 'Costa-Petrov', 'Costa-Brandt', 'Costa-Mancini',
    'Costa-Iliev', 'Costa-Aguilar', 'Costa-Aziz', 'Costa-Benitez', 'Costa-Brown', 'Costa-Chavez',
    'Costa-Denis', 'Costa-Fairweather', 'Costa-Giuliani', 'Costa-Hayes', 'Costa-Jordan', 'Costa-Lee',
    'Costa-McDonald', 'Costa-Ng', 'Costa-Pham', 'Costa-Russell', 'Costa-Takahashi', 'Costa-Yamashita',
    'Costa-Ljungberg', 'Costa-Karpov', 'Costa-Zaitsev', 'Costa-Guerreiro', 'Costa-Lahlou', 'Costa-Reddy',
    'Martin-Nielsen', 'Martin-Busch', 'Martin-Giordano', 'Martin-Kostov', 'Martin-Ahmed', 'Martin-Ba',
    'Martin-Benjamin', 'Martin-Bryant', 'Martin-Chen', 'Martin-Dia', 'Martin-Fan', 'Martin-Glass',
    'Martin-He', 'Martin-Jung', 'Martin-Legrand', 'Martin-McGrath', 'Martin-Nguyen', 'Martin-Pierce',
    'Martin-Saito', 'Martin-Tanaka', 'Martin-Yang', 'Martin-Lundqvist', 'Martin-Kazakov', 'Martin-Zhukov',
    'Martin-Lopes', 'Martin-Mansouri', 'Martin-Verma', 'Santos-Andersson', 'Santos-Leroy', 'Santos-Martino',
    'Santos-Markovic', 'Santos-Akhtar', 'Santos-Baba', 'Santos-Benn', 'Santos-Bucur', 'Santos-Cheng',
    'Santos-Diaz', 'Santos-Farias', 'Santos-Godoy', 'Santos-Hernandez', 'Santos-Kadlec', 'Santos-Leite',
    'Santos-McKenzie', 'Santos-Nichols', 'Santos-Pieters', 'Santos-Sakai', 'Santos-Tan', 'Santos-Yilmaz',
    'Santos-Magnusson', 'Santos-Kirilov', 'Santos-Andric', 'Santos-Lourenco', 'Santos-Mokhtar', 'Santos-Chaudhry',
    'Muller-Garcia', 'Muller-Dupont', 'Muller-Longo', 'Muller-Jovanovic', 'Muller-Alami', 'Muller-Babic',
    'Muller-Benson', 'Muller-Bull', 'Muller-Choi', 'Muller-Dickson', 'Muller-Ferrara', 'Muller-Gomes',
    'Muller-Herrera', 'Muller-Kahn', 'Muller-Lemaitre', 'Muller-Medina', 'Muller-Nishimura', 'Muller-Pineda',
    'Muller-Salas', 'Muller-Taylor', 'Muller-Yoon', 'Muller-Nilsson', 'Muller-Klimov', 'Muller-Baletic',
    'Muller-Madeira', 'Muller-Naciri', 'Muller-Farooq', 'Rossi-Fernandez', 'Rossi-Laurent', 'Rossi-Serra',
    'Rossi-Nikolic', 'Rossi-Alarcon', 'Rossi-Bae', 'Rossi-Berisha', 'Rossi-Burgess', 'Rossi-Chung',
    'Rossi-Diniz', 'Rossi-Ferrer', 'Rossi-Gomez', 'Rossi-Hidalgo', 'Rossi-Kang', 'Rossi-Leon',
    'Rossi-Mejia', 'Rossi-Noguchi', 'Rossi-Pollard', 'Rossi-Salazar', 'Rossi-Teague', 'Rossi-Yoshida',
    'Rossi-Nordstrom', 'Rossi-Kolesnikov', 'Rossi-Bogdanovic', 'Rossi-Marinho', 'Rossi-Ouali', 'Rossi-Hameed',
    'Bakker-Mendez', 'Bakker-Moreau', 'Bakker-Svensson', 'Bakker-Stankovic', 'Bakker-Alba', 'Bakker-Baena',
    'Bakker-Bermudez', 'Bakker-Burns', 'Bakker-Cisse', 'Bakker-Dixon', 'Bakker-Figueroa', 'Bakker-Gonzales',
    'Bakker-Hill', 'Bakker-Kaplan', 'Bakker-Leone', 'Bakker-Mendes', 'Bakker-Nolan', 'Bakker-Porter',
    'Bakker-Sanchez', 'Bakker-Thomas', 'Bakker-Young', 'Bakker-Nygaard', 'Bakker-Komarov', 'Bakker-Bozic',
    'Bakker-Nascimento', 'Bakker-Rahimi', 'Bakker-Javed', 'Novak-Serrano', 'Novak-Lefevre', 'Novak-Johansson',
    'Novak-Ilic', 'Novak-Alberti', 'Novak-Bah', 'Novak-Bernardi', 'Novak-Burton', 'Novak-Clarke',
    'Novak-Djokovic', 'Novak-Finch', 'Novak-Gonzalez', 'Novak-Hirano', 'Novak-Kato', 'Novak-Lewis',
    'Novak-Mendoza', 'Novak-Norris', 'Novak-Prado', 'Novak-Sanders', 'Novak-Thompson', 'Novak-Yu',
    'Novak-Rosenberg', 'Novak-Kondratyev', 'Novak-Brankovic', 'Novak-Peixoto', 'Novak-Slimani', 'Novak-Qureshi',
    'Kovacs-Kovac', 'Kovacs-Fontaine', 'Kovacs-Karlsson', 'Kovacs-Vukovic', 'Kovacs-Alcantara', 'Kovacs-Bahri',
    'Kovacs-Berry', 'Kovacs-Byrne', 'Kovacs-Clay', 'Kovacs-Dlamini', 'Kovacs-Flores', 'Kovacs-Goodman',
    'Kovacs-Hobbs', 'Kovacs-Kaur', 'Kovacs-Li', 'Kovacs-Menon', 'Kovacs-Norton', 'Kovacs-Prat',
    'Kovacs-Sato', 'Kovacs-Thorne', 'Kovacs-Zamora', 'Kovacs-Sandberg', 'Kovacs-Korolev', 'Kovacs-Draganic',
    'Kovacs-Queiroz', 'Kovacs-Toumi', 'Kovacs-Siddiqui', 'Fischer-Popescu', 'Fischer-Girard', 'Fischer-Larsson',
    'Fischer-Alves', 'Fischer-Alcaraz', 'Fischer-Bai', 'Fischer-Bessa', 'Fischer-Cabrera', 'Fischer-Coates',
    'Fischer-Doan', 'Fischer-Foley', 'Fischer-Gordon', 'Fischer-Hodgson', 'Fischer-Kaya', 'Fischer-Lima',
    'Fischer-Merino', 'Fischer-Nunez', 'Fischer-Price', 'Fischer-Scholes', 'Fischer-Tran', 'Fischer-Zapata',
    'Fischer-Soderstrom', 'Fischer-Kotov', 'Fischer-Djuric', 'Fischer-Reis', 'Fischer-Abdullayev', 'Berg-Marin',
    'Berg-Mercier', 'Berg-Olsson', 'Berg-Teixeira', 'Berg-Aldana', 'Berg-Bailey', 'Berg-Bessaoud',
    'Berg-Caceres', 'Berg-Cohen', 'Berg-Dobson', 'Berg-Ford', 'Berg-Goto', 'Berg-Hoffman',
    'Berg-Kazemi', 'Berg-Lin', 'Berg-Middleton', 'Berg-Ocampo', 'Berg-Quintero', 'Berg-Schultz',
    'Berg-Trujillo', 'Berg-Zhang', 'Berg-Stromberg', 'Berg-Kravchenko', 'Berg-Gajic', 'Berg-Camacho',
    'Berg-Aliyev', 'Moretti-Schmidt', 'Moretti-Lambert', 'Moretti-Eriksson', 'Moretti-Coelho', 'Moretti-Aleman',
    'Moretti-Baines', 'Moretti-Bidwell', 'Moretti-Cadiz', 'Moretti-Collins', 'Moretti-Doherty', 'Moretti-Foster',
    'Moretti-Graham', 'Moretti-Hogan', 'Moretti-Keane', 'Moretti-Liu', 'Moretti-Mihajlovic', 'Moretti-Ochoa',
    'Moretti-Quispe', 'Moretti-Scott', 'Moretti-Tsai', 'Moretti-Zhao', 'Moretti-Sundqvist', 'Moretti-Kuznetsov',
    'Moretti-Grbic', 'Moretti-Carvajal', 'Moretti-Aslanov', 'de Jong-Schneider', 'de Jong-Rousseau', 'de Jong-Persson',
    'de Jong-Pinto', 'de Jong-Alfaro', 'de Jong-Baker', 'de Jong-Bielik', 'de Jong-Caicedo', 'de Jong-Conceicao',
    'de Jong-Dolan', 'de Jong-Franco', 'de Jong-Grant', 'de Jong-Holmes', 'de Jong-Kelly', 'de Jong-Lloyd',
    'de Jong-Milani', 'de Jong-Oda', 'de Jong-Rahman', 'de Jong-Seidel', 'de Jong-Turner', 'de Jong-Zhou',
    'de Jong-Thorsson', 'de Jong-Lebedev', 'de Jong-Hadžic', 'de Jong-Castellanos', 'de Jong-Babayev', 'Keller-Weber',
    'Keller-Bernard', 'Keller-Gustafsson', 'Keller-Carvalho', 'Keller-Ali', 'Keller-Balazs', 'Keller-Birch',
    'Keller-Cairney', 'Keller-Connor',
]

# Backward-compatible aliases for code/tests that refer to the shorter names.
FIRST=FIRST_NAMES
LAST=LAST_NAMES


def _synthetic_name_pool(rng):
    pairs=[
        (first_name,last_name)
        for first_name in FIRST_NAMES
        for last_name in LAST_NAMES
    ]
    rng.shuffle(pairs)
    return pairs


def _synthetic_name(pool, used_names):
    while pool:
        pair=pool.pop()
        key=(pair[0].casefold(),pair[1].casefold())
        if key not in used_names:
            used_names.add(key)
            return pair
    raise RuntimeError(
        'Synthetic player name pool exhausted: '
        'not enough unique first_name/last_name combinations'
    )

def _stats(rng,base,pos):
    v={k:max(35,min(99,base+rng.randint(-9,9))) for k in ['pace','shooting','passing','dribbling','defending','physical','stamina','mental']}
    if pos=='GK':
        v.update(pace=max(35,min(99,base-8)),shooting=max(25,min(70,base-25)),passing=max(35,min(99,base-3)),dribbling=max(25,min(80,base-15)),defending=max(45,min(99,base+3)),physical=max(40,min(99,base+2)),stamina=max(40,min(99,base-2)),mental=max(40,min(99,base+4)))
    elif pos in ('CB','LB','RB'): v.update(defending=max(45,min(99,base+6)),physical=max(40,min(99,base+3)))
    elif pos in ('DM','CM','AM'): v.update(passing=max(45,min(99,base+5)),mental=max(40,min(99,base+3)))
    else: v.update(shooting=max(45,min(99,base+6)),dribbling=max(45,min(99,base+5)),pace=max(40,min(99,base+4)))
    return v

def _base_for(rng,rarity):
    return {'BASE':rng.randint(55,72),'RARE':rng.randint(70,82),'EPIC':rng.randint(82,91),'LEGENDARY':rng.randint(90,96)}[rarity]

TALENTS={
    'GK':['SWEEPER_KEEPER','SHOT_STOPPER','AERIAL_COMMANDER','DISTRIBUTOR'],
    'CB':['BALL_PLAYING_DEFENDER','STOPPER','AERIAL_BEAST','RECOVERY_DEFENDER','LEADER'],
    'LB':['OVERLAP_RUNNER','INVERTED_FULLBACK','CROSSER','ONE_V_ONE_DEFENDER'],
    'RB':['OVERLAP_RUNNER','INVERTED_FULLBACK','CROSSER','ONE_V_ONE_DEFENDER'],
    'DM':['ANCHOR','BALL_WINNER','DEEP_PLAYMAKER','PRESS_RESISTANT','DESTROYER'],
    'CM':['BOX_TO_BOX','DEEP_PLAYMAKER','CARRIER','PRESS_RESISTANT','TEMPO_CONTROLLER'],
    'AM':['PLAYMAKER','THROUGH_BALLER','SECOND_STRIKER','PRESSING_10','CREATIVE_ENGINE'],
    'LW':['INSIDE_FORWARD','WINGER','CREATIVE_DRIBBLER','COUNTER_RUNNER','PRESSER'],
    'RW':['INSIDE_FORWARD','WINGER','CREATIVE_DRIBBLER','COUNTER_RUNNER','PRESSER'],
    'ST':['POACHER','TARGET_MAN','PRESSING_FORWARD','FALSE_NINE','COMPLETE_FORWARD'],
}

def _talents(rng,pos,rarity):
    pool=TALENTS[pos]; count=1 if rarity=='BASE' else (2 if rarity=='RARE' else 3)
    return rng.sample(pool,k=min(count,len(pool)))

def _insert_values(i,first,last,nat,pos,rarity,rng,real=False,card='STANDARD',market=None,age=None,stats=None):
    base=_base_for(rng,rarity); st=dict(stats) if stats is not None else _stats(rng,base,pos)
    if market is None: market=int((base/60)**6*180_000)
    if rarity=='LEGENDARY': market=max(market,25_000_000)
    elif rarity=='EPIC': market=max(market,8_000_000)
    elif rarity=='RARE': market=max(market,2_000_000)
    talents=_talents(rng,pos,rarity)
    return dict(id=i,first_name=first,last_name=last,nationality=nat[:3].upper(),age=age or rng.randint(18,34),position=pos,**st,market_value=market,salary=max(15_000,market//50),potential=min(99,base+rng.randint(0,7)),rarity=rarity,card_version=card,real_player=real,preferred_foot='L' if rng.random()<.28 else 'R',secondary_positions=[],portrait_url=None,portrait_source=None,talents=json.dumps(talents),tactical_archetype=talents[0],data_source='curated_real' if real else 'seed')


def _build_seed_rows(rng, total_players=TOTAL_PLAYERS):
    rows=[]
    # Premium cards are fictional names from the deterministic catalog.
    used_names={(first.casefold(),last.casefold()) for first,last,_,_ in REAL_ORDER}
    premium_count=min(len(REAL_ORDER), total_players)
    for i,(first,last,nat,pos) in enumerate(REAL_ORDER[:premium_count],1):
        # Keep the original first-120 rarity and stat rolls stable on FORCE_RESEED.
        rarity='LEGENDARY' if i<=30 else ('EPIC' if i<=110 else 'RARE')
        card='ICON' if rarity=='LEGENDARY' else ('EPIC' if rarity=='EPIC' else 'STANDARD')
        rows.append(_insert_values(i,first,last,nat,pos,rarity,rng,real=False,card=card))
    synthetic_name_pool=_synthetic_name_pool(rng)
    added_rarities={rarity: RARITY_COUNTS[rarity]-LEGACY_RARITY_COUNTS[rarity]
                    for rarity in RARITY_COUNTS}
    for i in range(premium_count+1,total_players+1):
        if i<=360:
            # Preserve the legacy seed's rarity/stat rolls for existing IDs 121..360.
            rarity='RARE'
        elif i<=LEGACY_SEED_PLAYERS:
            rarity='BASE'
        else:
            ordinal=i-LEGACY_SEED_PLAYERS
            legendary_end=added_rarities['LEGENDARY']
            epic_end=legendary_end+added_rarities['EPIC']
            rare_end=epic_end+added_rarities['RARE']
            if ordinal<=legendary_end:
                rarity='LEGENDARY'
            elif ordinal<=epic_end:
                rarity='EPIC'
            elif ordinal<=rare_end:
                rarity='RARE'
            else:
                rarity='BASE'
        pos=POSITIONS[(i-121)%len(POSITIONS)]
        first,last=_synthetic_name(synthetic_name_pool,used_names)
        card='ICON' if rarity=='LEGENDARY' else ('EPIC' if rarity=='EPIC' else 'STANDARD')
        nat=NATIONALITIES[(i * 17 + i // 7) % len(NATIONALITIES)]
        rows.append(_insert_values(i,first,last,nat,pos,rarity,rng,real=False,card=card))
    return rows

async def seed():
    rng=Random(20261002)
    async with SessionLocal() as s:
        # Serialize seed/bootstrap across processes and always repair the identity sequence.
        await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('football-manager-seed'))"))
        force_reseed=os.getenv('FORCE_RESEED','0') == '1'
        if not force_reseed:
            count=(await s.execute(text('SELECT count(*) FROM players'))).scalar_one()
            if count:
                await s.execute(text("SELECT setval('players_id_seq', COALESCE((SELECT MAX(id) FROM players), 1), (SELECT COUNT(*) > 0 FROM players))"))
                await s.commit()
                return count
        else:
            await s.execute(text('DELETE FROM club_players'))
            await s.execute(text('DELETE FROM players'))
            await s.execute(text("SELECT setval('players_id_seq', 1, FALSE)"))
        # Build all 3,000 fictional templates deterministically. Existing DB rows
        # are only replaced when FORCE_RESEED=1; this branch is a full reseed.
        rows=_build_seed_rows(rng,TOTAL_PLAYERS)
        for v in rows:
            await s.execute(text('''INSERT INTO players(id,first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,real_player,preferred_foot,secondary_positions,portrait_url,portrait_source,talents,tactical_archetype,data_source,is_seed,is_template,template_id)
            VALUES(:id,:first_name,:last_name,:nationality,:age,:position,:pace,:shooting,:passing,:dribbling,:defending,:physical,:stamina,:mental,:market_value,:salary,:potential,:rarity,:card_version,:real_player,:preferred_foot,:secondary_positions,:portrait_url,:portrait_source,:talents,:tactical_archetype,:data_source,TRUE,TRUE,NULL)'''),v)
        await s.execute(text("SELECT setval('players_id_seq', COALESCE((SELECT MAX(id) FROM players), 1), TRUE)"))
        await s.commit()
    return len(rows)

async def main(): print(f'Seeded {await seed()} players.')
if __name__=='__main__': asyncio.run(main())
