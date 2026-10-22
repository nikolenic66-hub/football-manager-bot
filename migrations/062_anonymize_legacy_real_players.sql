-- Replace legacy real-player identities without changing ratings, OVR inputs, or player attributes.
-- The deterministic ID-based names match app.player_names.fictional_player_name().
WITH name_pools AS (
    SELECT
        ARRAY['Aren','Bren','Cirov','Dalen','Eron','Farel','Galen','Havor','Iven','Joren',
              'Kalen','Lior','Maren','Navor','Orel','Paven','Quorin','Riven','Salen','Tavor',
              'Uren','Varek','Walen','Xeran','Yorin','Zaven','Belor','Coren','Davor','Elvar']::text[] AS first_names,
        ARRAY['Voss','Keld','Ruun','Tarek','Morn','Velden','Sorin','Drex','Kovan','Neral',
              'Orvek','Prynn','Savel','Torrin','Uldek','Veyra','Wexel','Yarro','Zorin','Brax',
              'Caldor','Denvik','Esmor','Falken','Grev','Haldor','Isken','Javik','Lunet','Mavren',
              'Noxen','Orrik','Peld','Quarn','Rask','Selvik','Tervan','Urvell','Varn','Zelko']::text[] AS last_names
), legacy AS (
    SELECT p.id,
           np.first_names[((p.id - 1) % array_length(np.first_names, 1)) + 1] AS fictional_first,
           np.last_names[((((p.id - 1) / array_length(np.first_names, 1)) % array_length(np.last_names, 1)) + 1)]
             || CASE WHEN ((p.id - 1) / (array_length(np.first_names, 1) * array_length(np.last_names, 1))) > 0
                     THEN '-' || (((p.id - 1) / (array_length(np.first_names, 1) * array_length(np.last_names, 1))) + 1)::text
                     ELSE '' END AS fictional_last
    FROM players p CROSS JOIN name_pools np
    WHERE p.real_player IS TRUE
       OR p.data_source LIKE 'curated_real%'
       OR p.data_source LIKE 'csv:%'
)
UPDATE players p
SET first_name = legacy.fictional_first,
    last_name = legacy.fictional_last,
    real_player = FALSE,
    portrait_url = NULL,
    portrait_source = NULL,
    data_source = 'synthetic'
FROM legacy
WHERE p.id = legacy.id;
