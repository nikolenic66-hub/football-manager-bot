from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_admin_module_and_router_are_isolated():
    source = (ROOT / 'app' / 'handlers' / 'admin.py').read_text()
    assert 'admin_router = Router()' in source
    assert "F.data.startswith('admin:" in source
    assert 'settings.admin_id' in source
    assert 'AdminOnlyMiddleware' in source


def test_admin_migration_has_required_audit_columns():
    source = (ROOT / 'migrations' / '016_admin_actions_log.sql').read_text()
    for field in ('admin_user_id BIGINT NOT NULL', 'action TEXT NOT NULL', 'target_type TEXT', 'target_id BIGINT', 'payload JSONB', 'created_at TIMESTAMPTZ'):
        assert field in source


def test_admin_router_is_registered_after_main_router():
    source = (ROOT / 'app' / 'bot.py').read_text()
    assert 'from .handlers.admin import admin_router' in source
    assert 'dp=Dispatcher(); dp.include_router(router); dp.include_router(admin_router)' in source


def test_admin_id_is_configurable_with_requested_default():
    source = (ROOT / 'app' / 'config.py').read_text()
    env = (ROOT / '.env.example').read_text()
    assert 'admin_id: int = 6521319127' in source
    assert 'ADMIN_ID=6521319127' in env
