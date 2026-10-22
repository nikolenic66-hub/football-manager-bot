"""Regression contracts for transactional football-manager gameplay paths."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
SERVER = (ROOT / "app" / "server.py").read_text(encoding="utf-8")


def function_block(source: str, name: str) -> str:
    start = source.index(f"async def {name}(")
    end = source.find("\nasync def ", start + 1)
    return source[start:end if end >= 0 else None]


def test_market_keeps_catalogue_templates_available_after_other_club_buys_copy():
    block = function_block(SERVICES, "market_players")
    assert "p.is_template=TRUE" in block
    assert "CASE WHEN p.is_template=TRUE AND tl.id IS NULL THEN NULL ELSE cp.club_id END seller_club_id" in block


def test_template_purchase_creates_owned_copy_not_catalogue_row():
    block = function_block(SERVICES, "buy_player")
    assert "if bool(player.get('is_template'))" in block
    assert "await add_copy_to_club(s,club_id,int(player['id']))" in block
    assert "result['id']=acquired_player_id" in block
    assert "squad_size>=30" in block
    assert "UPDATE club_players SET contract_salary=:sal,release_clause=:clause" in block


def test_transfer_acceptance_uses_agreed_offer_and_validates_both_squads():
    block = function_block(SERVICES, "accept_transfer_offer")
    assert "FOR UPDATE OF o,l,p" in block
    assert "agreed_amount=int(offer['amount'])" in block
    assert "adjusted_amount" not in block
    assert "int(seller_count)<=18" in block
    assert "seller_owns_player" in block
    assert "if count>=30" in block
    assert "COALESCE(template_id,id)" in block


def test_database_readiness_probe_is_separate_from_liveness_probe():
    assert '@app.get("/api/health")' in SERVER
    assert '@app.get("/api/ready")' in SERVER
    ready = SERVER[SERVER.index('async def readiness()'):SERVER.index('@app.get("/api/me")')]
    assert 'await session.execute(text("SELECT 1"))' in ready
    assert 'status_code=503' in ready


def test_training_locks_club_before_checking_cooldown():
    block = function_block(SERVICES, "train_squad")
    assert "SELECT id FROM clubs WHERE id=:c FOR UPDATE" in block
    assert block.index("FOR UPDATE") < block.index("max(last_training_at)")


def test_new_player_copies_receive_a_real_contract_expiry():
    block = function_block(SERVICES, "add_copy_to_club")
    assert "contract_until" in block
    assert "CURRENT_DATE+INTERVAL '3 years'" in block
