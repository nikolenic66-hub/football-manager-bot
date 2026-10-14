from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SERVICES=(ROOT/'app/services.py').read_text(encoding='utf-8')
BOT=(ROOT/'app/bot.py').read_text(encoding='utf-8')
MIGRATION=(ROOT/'migrations/017_training_batches.sql').read_text(encoding='utf-8')


def test_training_run_creates_one_batch_and_no_per_player_notification():
    start=SERVICES.index('async def train_squad')
    end=SERVICES.index('async def training_history', start)
    block=SERVICES[start:end]
    assert 'batch_id=token_urlsafe(18)' in block
    assert 'batch_id) VALUES' in block
    assert 'INSERT INTO player_development_log' in block
    assert 'queue_club_notification' not in block
    assert 'return changes,batch_id' in block


def test_training_history_is_grouped_by_batch_and_limited_to_ten():
    start=SERVICES.index('async def training_history')
    end=SERVICES.index('async def set_player_expectation', start)
    block=SERVICES[start:end]
    assert 'GROUP BY batch_id,focus' in block
    assert 'MAX(intensity)' in block
    assert 'LIMIT :n' in block
    assert 'batch_id IS NOT NULL' in block


def test_training_details_only_shows_players_with_growth():
    start=SERVICES.index('async def training_details')
    end=SERVICES.index('async def set_player_expectation', start)
    block=SERVICES[start:end]
    assert 'l.batch_id=:b' in block
    assert "l.changes <> '{}'::jsonb" in block
    assert 'JOIN players p ON p.id=l.player_id' in block


def test_training_completion_sends_one_summary_with_details_button():
    start=BOT.index("@router.callback_query(F.data=='training:run')")
    end=BOT.index("@router.callback_query(F.data=='training:history')", start)
    block=BOT[start:end]
    assert 'changes,batch_id=await train_squad' in block
    assert "training:details:{batch_id}" in block
    assert 'Игроков с ростом:' in block
    assert 'Очков развития:' in block
    assert "📈 Игрок вырос" not in block


def test_training_history_and_details_callbacks_are_registered():
    assert "F.data=='training:history'" in BOT
    assert "F.data.startswith('training:details:')" in BOT
    assert "callback_data=f'training:details:{r[\"batch_id\"]}'" in BOT
    assert "callback_data='training:history'" in BOT


def test_training_keyboard_has_history_button():
    start=BOT.index('def training_keyboard')
    end=BOT.index('async def _show_training_menu', start)
    block=BOT[start:end]
    assert "📜 История тренировок" in block
    assert "callback_data='training:history'" in block


def test_training_batch_migration_adds_batch_and_intensity():
    assert 'ADD COLUMN IF NOT EXISTS batch_id TEXT' in MIGRATION
    assert 'ADD COLUMN IF NOT EXISTS intensity TEXT' in MIGRATION
    assert 'idx_player_development_log_batch' in MIGRATION
