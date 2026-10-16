-- Repair legacy/generated player rows that have empty display names.
-- Keep real names intact and give every malformed row a stable readable name.
UPDATE players
SET first_name = CASE
    WHEN btrim(COALESCE(first_name, '')) = '' OR btrim(first_name) ~ '^[0-9]{4}$'
        THEN 'Player'
    ELSE first_name
END,
last_name = CASE
    WHEN btrim(COALESCE(last_name, '')) = '' OR btrim(last_name) ~ '^[0-9]{4}$'
        THEN id::text
    ELSE last_name
END
WHERE real_player IS NOT TRUE
  AND (btrim(COALESCE(first_name, '')) = ''
   OR btrim(COALESCE(last_name, '')) = ''
   OR btrim(COALESCE(first_name, '')) ~ '^[0-9]{4}$'
   OR btrim(COALESCE(last_name, '')) ~ '^[0-9]{4}$');
