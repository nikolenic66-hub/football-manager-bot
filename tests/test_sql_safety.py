import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A bare `id` is only dangerous when the same SQL statement contains a JOIN
# with another table that also exposes an id column. Keep this detector simple
# and conservative: it checks SELECT/WHERE/AND/ORDER BY positions.
BARE_ID_SQL = re.compile(
    r"(?i)(?:\bSELECT\s+|\bWHERE\s+|\bAND\s+|\bOR\s+|\bORDER\s+BY\s+)id\b"
)


def _sql_literals(path: Path):
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'text' and node.args):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            sql = arg.value
        elif isinstance(arg, ast.JoinedStr):
            parts=[]
            for value in arg.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                else:
                    parts.append(' {EXPR} ')
            sql=''.join(parts)
        else:
            continue
        yield node.lineno, sql


def _assert_no_bare_id_in_join_sql(path: Path):
    bad=[]
    for lineno, sql in _sql_literals(path):
        if re.search(r'(?i)\bJOIN\b', sql) and BARE_ID_SQL.search(sql):
            bad.append((lineno, ' '.join(sql.split())))
    assert not bad, f'Unqualified id in JOIN SQL: {bad}'


def test_all_app_join_sql_has_qualified_id_references():
    bad=[]
    for path in (ROOT / 'app').rglob('*.py'):
        try:
            for lineno, sql in _sql_literals(path):
                if re.search(r'(?i)\bJOIN\b', sql) and BARE_ID_SQL.search(sql):
                    bad.append((str(path.relative_to(ROOT)), lineno, ' '.join(sql.split())))
        except SyntaxError:
            raise
    assert not bad, 'Found ambiguous JOIN SQL: ' + repr(bad)


def test_live_worker_sql_is_qualified():
    path = ROOT / 'app' / 'live_worker.py'
    _assert_no_bare_id_in_join_sql(path)
    source = path.read_text(encoding='utf-8')
    assert 'SELECT m.id,m.league_id,m.round' in source
    assert 'ORDER BY m.league_id,m.round,m.id' in source
    assert 'SELECT e.id,e.minute,e.event_type' in source
    assert 'ORDER BY e.minute,e.id LIMIT 2' in source


def test_start_round_and_live_helpers_sql_are_qualified():
    for name in ('services.py', 'live_worker.py'):
        _assert_no_bare_id_in_join_sql(ROOT / 'app' / name)

    services = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    for needle in (
        'FROM club_players cp JOIN players p ON p.id=cp.player_id',
        'FROM matches m JOIN match_tactics mt ON mt.match_id=m.id',
        'FROM notifications n JOIN users u ON u.id=n.user_id',
    ):
        assert needle in services
    assert 'ORDER BY e.minute,e.id' in services


def test_match_replay_orders_event_id_with_alias():
    source = (ROOT / 'app' / 'bot.py').read_text(encoding='utf-8')
    start = source.index('FROM match_events e')
    block = source[start:start + 900]
    assert 'ORDER BY e.minute,e.id' in block
    assert 'ORDER BY minute,id' not in block


def test_match_center_event_queries_use_event_alias():
    source = (ROOT / 'app' / 'bot.py').read_text(encoding='utf-8')
    assert 'ORDER BY e.minute,e.id LIMIT 30' in source
    assert 'ORDER BY e.minute,e.id LIMIT 20' in source
    assert 'ORDER BY minute,id' not in source


def test_player_fields_are_qualified_for_club_player_joins():
    source = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    assert "PLAYER_FIELDS='p.id,p.first_name,p.last_name,p.position" in source
    assert 'FROM club_players cp JOIN players p ON p.id=cp.player_id' in source
    assert 'SELECT {PLAYER_FIELDS} FROM players p WHERE p.id=ANY(:ids)' in source
    assert 'PLAYER_FIELDS=\'id,first_name,last_name' not in source


def test_live_kickoff_only_starts_current_round():
    source = (ROOT / 'app' / 'live_worker.py').read_text(encoding='utf-8')
    assert 'JOIN leagues l ON l.id=m.league_id' in source
    assert "AND l.status='ACTIVE'" in source
    assert "expected=row['current_round']" in source
    assert "if row['round'] != expected:" in source
    assert 'waiting for round %s' in source
    assert 'Could not start due match round' in source


def test_finalize_live_round_advances_current_round():
    source = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    assert "pending=(await s.execute(text(\"SELECT count(*) FROM matches WHERE league_id=:l AND round=:r AND status!='FINISHED'\")" in source
    assert "league['current_round']+1" in source
    assert "UPDATE leagues SET current_round=:n" in source
