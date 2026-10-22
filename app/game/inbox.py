from datetime import datetime, timezone
from sqlalchemy import text

async def send_message(club_id, title, body, session):
    row=(await session.execute(text('''INSERT INTO inbox_messages(club_id,created_at,title,body,is_read)
        VALUES(:c,CAST(:created_at AS timestamptz),:t,:b,FALSE) RETURNING id'''),{
        'c':club_id,'created_at':datetime.now(timezone.utc),'t':title,'b':body,
    })).first()
    return int(row.id)


async def get_unread(club_id, session, limit=20):
    return (await session.execute(text('''SELECT id,club_id,created_at,title,body,is_read
        FROM inbox_messages WHERE club_id=:c AND is_read=FALSE
        ORDER BY created_at DESC,id DESC LIMIT :n'''),{'c':club_id,'n':limit})).mappings().all()


async def get_messages(club_id, session, limit=30):
    return (await session.execute(text('''SELECT id,club_id,created_at,title,body,is_read
        FROM inbox_messages WHERE club_id=:c
        ORDER BY created_at DESC,id DESC LIMIT :n'''),{'c':club_id,'n':limit})).mappings().all()


async def mark_read(message_id, club_id, session):
    await session.execute(text('UPDATE inbox_messages SET is_read=TRUE WHERE id=:m AND club_id=:c'),{'m':message_id,'c':club_id})


async def unread_count(club_id, session):
    return int((await session.execute(text('SELECT count(*) FROM inbox_messages WHERE club_id=:c AND is_read=FALSE'),{'c':club_id})).scalar_one())
