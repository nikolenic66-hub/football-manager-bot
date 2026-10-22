DO $$
DECLARE
    rec RECORD;
    candidate TEXT;
BEGIN
    FOR rec IN
        SELECT c.id
        FROM clubs c
        JOIN users u ON u.id=c.owner_user_id
        WHERE u.telegram_id < 0
        ORDER BY c.id
    LOOP
        candidate := NULL;
        SELECT x.name INTO candidate
        FROM unnest(ARRAY[
            'Northvale FC','Redhaven Athletic','Blueford United','Eastmere Town','Westcliff FC',
            'Kingsport Rovers','Stonebridge FC','Ashbourne Athletic','Fairmont United','Rivermoor FC',
            'Oakridge Town','Highfield Rovers','Silverlake FC','Greenhaven Athletic','Cedarfield United',
            'Brightwater FC','Ironwood Town','Foxborough Rovers','Windermere FC','Goldcrest Athletic',
            'Brookstone United','Pinehurst FC','Meadowgate Town','Clearwater Rovers','Ravenford FC',
            'Sunvale Athletic','Hillcrest United','Lakewood FC','Duneshire Town','Wexford Vale FC'
        ]) AS x(name)
        WHERE NOT EXISTS (SELECT 1 FROM clubs used WHERE used.name=x.name AND used.id<>rec.id)
        ORDER BY random()
        LIMIT 1;
        IF candidate IS NULL THEN
            candidate := 'FC Bot ' || rec.id;
            WHILE EXISTS (SELECT 1 FROM clubs used WHERE used.name=candidate AND used.id<>rec.id) LOOP
                candidate := candidate || ' FC';
            END LOOP;
        END IF;
        UPDATE clubs SET name=candidate WHERE id=rec.id;
    END LOOP;
END $$;
