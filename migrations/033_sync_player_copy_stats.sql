UPDATE players AS copy
SET first_name=src.first_name,
    last_name=src.last_name,
    nationality=src.nationality,
    age=src.age,
    position=src.position,
    pace=src.pace,
    shooting=src.shooting,
    passing=src.passing,
    dribbling=src.dribbling,
    defending=src.defending,
    physical=src.physical,
    stamina=src.stamina,
    mental=src.mental,
    salary=src.salary,
    rarity=src.rarity,
    card_version=src.card_version,
    real_player=src.real_player,
    preferred_foot=src.preferred_foot,
    secondary_positions=src.secondary_positions,
    talents=src.talents,
    tactical_archetype=src.tactical_archetype
FROM players AS src
WHERE copy.is_template=FALSE
  AND copy.template_id=src.id
  AND src.is_template=TRUE;
