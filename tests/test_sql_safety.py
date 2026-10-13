from pathlib import Path
def test_join_queries_do_not_use_bare_order_by_id():
    bot = (Path(__file__).resolve().parents[1] / 'app' / 'bot.py').read_text(encoding='utf-8')
    # The production failure was a JOIN over match_events/players followed by
    # an unqualified ORDER BY id. Keep this regression test intentionally exact.
    assert 'FROM match_events e' in bot
    assert 'ORDER BY e.minute,e.id' in bot


def test_match_replay_orders_event_id_with_alias():
    source = (Path(__file__).resolve().parents[1] / 'app' / 'bot.py').read_text(encoding='utf-8')
    start = source.index('FROM match_events e')
    block = source[start:start + 900]
    assert 'ORDER BY e.minute,e.id' in block
    assert 'ORDER BY minute,id' not in block
