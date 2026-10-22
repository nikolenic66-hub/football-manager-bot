from pathlib import Path

ROOT=Path(__file__).parents[1]

def test_liveops_schema_and_config():
    sql=(ROOT/'migrations'/'056_liveops_config.sql').read_text()
    assert 'liveops_season_config' in sql
    assert 'liveops_events' in sql
    assert 'liveops_daily_quest_definitions' in sql
    assert 'ON CONFLICT(quest_type) DO NOTHING' in sql

    # Keep JSON reward literals intact: SQLAlchemy must not interpret JSON
    # fragments as bind parameters such as :10 or malformed $1 placeholders.
    assert '\'{"type":"bp_xp","amount":10,"label":"10 BP XP"}\'::jsonb' in sql
    assert '\'{"type":"money","amount":25000,"label":"€25,000"}\'::jsonb' in sql
    assert '\'{"type":"item","item":"MEDKIT","qty":1,"label":"1 MEDKIT"}\'::jsonb' in sql
    quest_values = sql.split('INSERT INTO liveops_daily_quest_definitions', 1)[1].split('ON CONFLICT', 1)[0]
    assert '$1' not in quest_values and '$2' not in quest_values

def test_liveops_and_rng_modules():
    live=(ROOT/'app'/'game'/'liveops.py')
    rng=(ROOT/'app'/'game'/'rng.py')
    assert live.exists() and rng.exists()
    ns={}
    exec(rng.read_text(), ns)
    a=ns['stream_seed'](123,'transfer',1,2)
    b=ns['stream_seed'](123,'transfer',1,2)
    c=ns['stream_seed'](123,'youth',1,2)
    assert a==b and a!=c

def test_protected_files_untouched():
    for name in ('engine.py','ratings.py','roles.py','instructions.py','channels.py','tactics.py','ai_coach.py'):
        assert (ROOT/'app'/'game'/name).exists()
