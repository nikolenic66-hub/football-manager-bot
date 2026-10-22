from .visual import club_crest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .services import build_starting_lineup

async def get_or_create_user(s,telegram_id,username,first_name):
    row=(await s.execute(text('''
      INSERT INTO users(telegram_id,username,first_name) VALUES(:t,:u,:f)
      ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name
      RETURNING id
    '''),{'t':telegram_id,'u':username,'f':first_name})).first()
    await s.execute(text('INSERT INTO notification_settings(user_id) VALUES(:u) ON CONFLICT DO NOTHING'), {'u': row.id})
    return row.id

async def create_club(s, owner_id, name):
    name = (name or '').strip()
    if not 3 <= len(name) <= 30:
        raise ValueError('Club name must contain 3-30 characters.')

    # Serialize club creation for this owner.  This makes the single-club
    # invariant deterministic without relying on a misleading pre-check for
    # the club name.
    existing = (await s.execute(
        text('SELECT id FROM clubs WHERE owner_user_id=:o FOR UPDATE'),
        {'o': owner_id},
    )).scalar_one_or_none()
    if existing:
        raise ValueError('User already owns a club.')

    try:
        row = (await s.execute(text("""
            INSERT INTO clubs(
                owner_user_id,name,short_name,stadium_name,budget,fans,reputation
            )
            VALUES(
                :o,:n,:sn,:st,1000000,5000,1
            )
            RETURNING id
        """), {
            'o': owner_id,
            'n': name,
            'sn': name[:4].upper(),
            'st': name + ' Arena',
        })).first()
    except IntegrityError as exc:
        # Do NOT rollback here.  The caller owns the transaction and must
        # decide whether to rollback.  Inspect the actual PostgreSQL
        # constraint instead of treating every IntegrityError as a name clash.
        constraint = getattr(getattr(exc, 'orig', None), 'diag', None)
        constraint = getattr(constraint, 'constraint_name', None)
        if constraint in {'clubs_name_key', 'uq_clubs_name'}:
            raise ValueError('Club name is already taken.') from exc
        if constraint in {'uq_clubs_owner_user', 'clubs_owner_user_id_key'}:
            raise ValueError('User already owns a club.') from exc
        raise

    cid = row.id
    try:
        club_crest(name, cid)
    except Exception:
        pass

    await build_starting_lineup(s, cid)
    await s.execute(
        text('INSERT INTO club_training(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),
        {'c': cid},
    )
    await s.execute(
        text('INSERT INTO club_tactics(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),
        {'c': cid},
    )
    from .services import auto_join_rookie_league
    await auto_join_rookie_league(s, owner_id, cid)
    return cid

