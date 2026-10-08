import asyncio
import time
import logging
from collections import defaultdict
from aiogram import BaseMiddleware
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, FSInputFile, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from sqlalchemy import text
from pathlib import Path
from .config import settings
from .db import SessionLocal
from .repositories import get_or_create_user, create_club
from .services import create_league, join_league, start_league, start_round_halftime, schedule_round, change_halftime_tactics, market_players, buy_player, sell_player, set_player_instruction, get_tactical_board, set_starting_lineup, plan_substitution, scout_opponent, next_opponent, set_counterplan, set_ticket_price, upgrade_stadium, financial_report, set_training_plan, train_squad, renew_contract, set_player_expectation, list_player_for_transfer, cancel_transfer_listing, make_transfer_offer, accept_transfer_offer, player_management_report, notification_list, notification_unread_count, mark_notifications_read, set_notification_preference, notification_preferences
from .game.ratings import player_rating
from .visual import player_card, formation_board, match_center, club_crest, tactical_board_screen, squad_screen, league_table_screen, club_dashboard, market_screen, fixtures_screen, scouting_screen, match_phase_screen, dynamic_match_screen, event_replay_screen, finance_screen

router=Router()
logger=logging.getLogger(__name__)


class LeagueCreate(StatesGroup):
    choosing_size = State()
    entering_name = State()


class LeagueJoin(StatesGroup):
    entering_code = State()


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, cooldown=0.75):
        self.cooldown=cooldown
        self._last=defaultdict(float)

    async def __call__(self, handler, event, data):
        state=data.get('state')
        if state is not None and await state.get_state() is not None:
            return await handler(event,data)
        user=getattr(getattr(event, 'from_user', None), 'id', None)
        if user is not None:
            command=(getattr(event, 'text', None) or '').split(maxsplit=1)[0].lower()
            key=(user, command)
            now=time.monotonic()
            if now-self._last[key] < self.cooldown:
                return None
            self._last[key]=now
        return await handler(event,data)


router.message.middleware(RateLimitMiddleware())


def menu():
    k=InlineKeyboardBuilder()
    for t,d in [('🏟 Клуб','club'),('👥 Состав','squad'),('📋 Тактика','tactics'),('📺 Матч','matchcenter'),('🏆 Лига','league'),('🔄 Рынок','market:list'),('💰 Финансы','finance'),('🏋️ Тренировки','training'),('📊 Таблица','table'),('📅 Календарь','fixtures'),('🕵️ Скаутинг','scout'),('🔔 Уведомления','notif:menu'),('📝 Контракты','contract:list')]:
        k.button(text=t,callback_data=d)
    k.adjust(3,3,3,3,1)
    return k.as_markup()

async def current_user(s,m,from_user=None):
    u=from_user or m.from_user
    return await get_or_create_user(s,u.id,u.username,u.first_name)

async def current_club(s,uid):
    return (await s.execute(text('SELECT * FROM clubs WHERE owner_user_id=:u ORDER BY id LIMIT 1'),{'u':uid})).mappings().first()

async def active_league(s,club_id):
    return (await s.execute(text('''SELECT l.id,l.name,l.status,l.current_round,l.total_rounds
        FROM leagues l JOIN league_teams t ON t.league_id=l.id
        WHERE t.club_id=:c ORDER BY CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'WAITING' THEN 1 ELSE 2 END,l.id DESC LIMIT 1'''),{'c':club_id})).mappings().first()

async def club_by_tg(s, telegram_id):
    return (await s.execute(text('SELECT c.* FROM users u JOIN clubs c ON c.owner_user_id=u.id WHERE u.telegram_id=:t ORDER BY c.id LIMIT 1'),{'t':telegram_id})).mappings().first()

def paginated_keyboard(items_count, prefix, page=0, page_size=6):
    k=InlineKeyboardBuilder()
    start=page*page_size
    end=min(start+page_size,items_count)
    for i in range(start,end):
        k.button(text=f'{i+1}',callback_data=f'{prefix}:item:{i}')
    k.adjust(page_size)
    return k.as_markup()


def squad_keyboard(players, page=0, prefix='squad'):
    k=InlineKeyboardBuilder()
    start=page*6
    end=min(start+6,len(players))
    for i in range(start,end):
        p=players[i]
        last=(p.get('last_name') or p.get('name') or 'Игрок')[:10]
        k.button(text=f'{i+1}. {last}',callback_data=f'{prefix}:item:{i}')
    k.adjust(2)
    for code,label in (('GK','🧤 Вратари'),('DEF','🛡 Защита'),('MID','⚙ Полузащита'),('ATT','⚡ Атака')):
        k.button(text=label,callback_data=f'{prefix}:filter:{code}')
    k.adjust(2,2)
    k.button(text='⬅ Назад',callback_data='menu')
    k.adjust(2,2,1)
    return k.as_markup()


_SELECTED_SQUAD_PLAYERS={}
_TACTIC_PICKS=defaultdict(set)
_MARKET_VIEWS={}

def _market_data(rows):
    data=[]
    for r in rows:
        pr=type('P',(),dict(
            r,
            name=f"{r.get('first_name','')} {r.get('last_name','')}".strip() or 'Игрок',
            fitness=100,
            form=0,
        ))()
        data.append(dict(
            r,
            overall=player_rating(pr),
            rarity=r.get('rarity','BASE'),
        ))
    return data

def _market_keyboard(data):
    k=InlineKeyboardBuilder()
    for i,r in enumerate(data[:10]):
        name=f"{r.get('first_name','')} {r.get('last_name','')}".strip()
        price=r.get('asking_price') if r.get('listing_id') else r.get('market_value',0)
        label=f"{name.split()[-1] if name else 'Игрок'} · {r.get('position','')} · €{int(price):,}"
        k.button(text=label[:30],callback_data=f'market:item:{i}')
    k.adjust(5)
    for code,label in (('ATT','⚽ Нападающие'),('MID','🎯 Полузащита'),('DEF','🛡 Защита'),('GK','🧤 Вратари')):
        k.button(text=label,callback_data=f'market:filter:{code}')
    k.adjust(2,2)
    k.button(text='⬅ Назад',callback_data='menu')
    k.adjust(2,2,1)
    return k.as_markup()

def _contract_keyboard(rows):
    k=InlineKeyboardBuilder()
    for i,r in enumerate(rows):
        last=(r.get('last_name') or r.get('first_name') or 'Игрок')[:12]
        k.button(text=f'{i+1}. {last}',callback_data=f'contract:item:{i}')
    k.adjust(6)
    k.button(text='⬅ Назад',callback_data='menu')
    k.adjust(6,1)
    return k.as_markup()


def _position_filter(position, code):
    groups={'GK':{'GK'},'DEF':{'LB','CB','RB'},'MID':{'DM','CM','AM'},'ATT':{'LW','RW','ST'}}
    return position in groups.get(code,set())


def tactics_keyboard(formation):
    k=InlineKeyboardBuilder()
    for f in ('4-3-3','4-2-3-1','4-4-2','3-5-2','5-3-2'):
        k.button(text=('✓ ' if f==formation else '')+f, callback_data=f'tac:f:{f}')
    for st in ('BALANCE','ATTACK','COUNTER','DEFENSE','POSSESSION'):
        k.button(text=st, callback_data=f'tac:s:{st}')
    k.button(text='🔄 Обновить доску',callback_data='tac:refresh')
    k.button(text='👥 Выбрать состав',callback_data='tactic:pick')
    k.button(text='⬅ Назад',callback_data='menu')
    k.adjust(3,2,1,2)
    return k.as_markup()

@router.message(CommandStart())
async def start(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid); await s.commit()
    if c:
        crest=Path('assets/visual/clubs')/f'club_{c["id"]}.png'
        if not crest.exists(): crest=Path(club_crest(c['name'],c['id']))
        await m.answer_photo(FSInputFile(crest),caption=f'⚽ <b>{c["name"]}</b>\n💰 €{c["budget"]:,}\n🏟 {c["stadium_name"]} · LVL {c["stadium_level"]}\n🎟 €{c["ticket_price"]} · sponsor LVL {c["sponsor_level"]}')
        await m.answer('Выберите действие:',reply_markup=menu())
    else:
        k=InlineKeyboardBuilder(); k.button(text='🏟 Создать клуб',callback_data='create')
        await m.answer('⚽ <b>FOOTBALL MANAGER</b>\n\nСоздайте клуб, вступите в лигу и играйте с друзьями.',reply_markup=k.as_markup())

@router.message(Command('createleague'))
async def cmd_createleague(m:Message):
    parts=(m.text or '').split(maxsplit=2)
    if len(parts)<3:return await m.answer('Использование: /createleague 8 Friends League')
    try:n=int(parts[1])
    except:return await m.answer('Количество клубов: 4, 6, 8, 10 или 12.')
    if n not in {4,6,8,10,12}:return await m.answer('Количество клубов: 4, 6, 8, 10 или 12.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: league_id,code=await create_league(s,uid,parts[2],n); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(str(e))
        except Exception:
            await s.rollback(); logger.exception('Failed to create league')
            return await m.answer('Не удалось создать лигу. Попробуйте ещё раз.')
    await m.answer(f'🏆 Лига <b>{parts[2]}</b> создана.\nКод приглашения: <code>{code}</code>\n\nДрузья могут: /join {code}')

@router.message(Command('join'))
async def cmd_join(m:Message):
    parts=(m.text or '').split(maxsplit=1)
    if len(parts)<2:return await m.answer('Использование: /join КОД')
    code=parts[1].strip().upper()
    if not code.isalnum() or not 4 <= len(code) <= 8:
        return await m.answer('Некорректный код лиги.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=(await s.execute(text('SELECT id,name FROM leagues WHERE invite_code=:c'),{'c':code})).first()
        if not league:return await m.answer('Лига не найдена.')
        try: await join_league(s,uid,code); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(str(e))
    await m.answer(f'✅ Вы вступили в лигу <b>{league.name}</b>.')

@router.message(Command('startleague'))
async def cmd_startleague(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        lt=(await s.execute(text('''SELECT l.id,l.name,l.creator_user_id FROM leagues l JOIN league_teams t ON t.league_id=l.id
            WHERE t.club_id=:c AND l.status='WAITING' ORDER BY l.id DESC LIMIT 1'''),{'c':c['id']})).first() if c else None
        if not lt:return await m.answer('Не найдена ожидающая лига.')
        if lt.creator_user_id!=uid:return await m.answer('Начать сезон может создатель лиги.')
        try: await start_league(s,uid); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(str(e))
    await m.answer(f'🚦 Сезон <b>{lt.name}</b> начался! Теперь можно использовать /playround.')

@router.message(Command('playround'))
async def cmd_playround(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        if not league or league['status']!='ACTIVE':return await m.answer('У клуба нет активной лиги.')
        round_no=league['current_round']
        try: scheduled_at,count=await schedule_round(s,league['id'],round_no,10); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(str(e))
    await m.answer(f'📅 <b>Тур {round_no} запланирован</b>\n\n⚽ Ваш матч начнётся через <b>10 минут</b>.\n🔔 Отдельного уведомления о стартовом свистке не будет.\n\nПосле старта: 90 сек первый тайм → 60 сек перерыв → 90 сек второй тайм.\nВажные события и итог матча придут отдельными уведомлениями.')

@router.message(Command('resume'))
async def cmd_resume(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        if not league:return await m.answer('Вы пока не участвуете в лиге.')
        row=(await s.execute(text('''SELECT halftime_ends_at,live_phase FROM matches
            WHERE league_id=:l AND round=:r AND status='LIVE' AND live_phase='HALFTIME'
            ORDER BY id LIMIT 1'''),{'l':league['id'],'r':league['current_round']})).mappings().first()
    if not row:
        return await m.answer('Матч сейчас не находится на перерыве. Второй тайм запускается автоматически.')
    from datetime import datetime, timezone
    remaining=max(0,int((row['halftime_ends_at']-datetime.now(timezone.utc)).total_seconds())) if row['halftime_ends_at'] else 0
    await m.answer(f'⏸️ <b>ПЕРЕРЫВ</b>\nДо начала второго тайма: <b>{remaining} сек.</b>\n\nИзменения тактики можно вносить сейчас. /setformation, /setstyle, /setsliders')

@router.message(Command('halftime'))
async def cmd_halftime(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        row=(await s.execute(text("""SELECT m.id,m.home_score,m.away_score,h.name home,a.name away FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id WHERE m.status='LIVE' AND m.halftime_locked AND (m.home_club_id=:c OR m.away_club_id=:c) ORDER BY m.id DESC LIMIT 1"""),{'c':c['id']})).mappings().first()
    if not row:return await m.answer('У вашего клуба сейчас нет матча на перерыве.')
    await m.answer(f'⏸️ <b>ПЕРЕРЫВ</b>\n{row["home"]} <b>{row["home_score"]}:{row["away_score"]}</b> {row["away"]}\n\nИзмените формацию/стиль/полосы. Второй тайм запустится автоматически.')

@router.message(Command('table'))
async def cmd_table(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        if not league:return await m.answer('Вы пока не участвуете в лиге.')
        rows=(await s.execute(text('''SELECT c.name,t.played,t.wins,t.draws,t.losses,t.points,t.goals_for,t.goals_against
            FROM league_teams t JOIN clubs c ON c.id=t.club_id WHERE t.league_id=:l
            ORDER BY t.points DESC,(t.goals_for-t.goals_against) DESC,t.goals_for DESC,c.name'''),{'l':league['id']})).mappings().all()
    out=[f'🏆 <b>ТАБЛИЦА — {league["name"]}</b>','']
    for i,r in enumerate(rows,1):out.append(f'{i}. {r["name"]} — {r["points"]} оч. | {r["played"]} | {r["goals_for"]}:{r["goals_against"]} | {r["wins"]}-{r["draws"]}-{r["losses"]}')
    await m.answer('\n'.join(out))

@router.message(Command('player'))
async def cmd_player(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer('Использование: /player PLAYER_ID')
    async with SessionLocal() as s:
        r=(await s.execute(text('SELECT * FROM players WHERE id=:p'),{'p':int(parts[1])})).mappings().first()
    if not r:return await m.answer('Игрок не найден.')
    talents=r.get('talents') or []
    if isinstance(talents,str):
        import json; talents=json.loads(talents)
    caption=(f'🃏 <b>{r["first_name"]} {r["last_name"]}</b>\n{r["position"]} · {r["nationality"]} · {r["rarity"]}\n'
             f'⚡ PAC {r["pace"]} · SHO {r["shooting"]} · PAS {r["passing"]} · DRI {r["dribbling"]}\n'
             f'🛡 DEF {r["defending"]} · PHY {r["physical"]} · STA {r["stamina"]} · MENT {r["mental"]}\n'
             f'🎯 POT {r["potential"]} · €{r["market_value"]:,}\n✨ <b>Таланты:</b> {", ".join(talents) or "—"}')
    portrait=r.get('portrait_url')
    portrait_path=None
    if portrait:
        candidate=Path(str(portrait))
        portrait_path=candidate if candidate.is_absolute() else Path(__file__).resolve().parents[1]/candidate
    card=player_card(dict(r,portrait_path=portrait_path), out=Path('assets/visual/cards')/f'player_{r["id"]}.png')
    note='\n\n📸 Портрет встроен в карточку.' if portrait_path and portrait_path.exists() else '\n\n🎨 Силуэт используется как fallback до загрузки разрешённого портрета.'
    await m.answer_photo(FSInputFile(card),caption=caption+note)

@router.message(Command('visuals'))
async def cmd_visuals(m:Message):
    await m.answer('🎨 <b>ВИЗУАЛЬНЫЙ ПАК</b>\n\nКарточки игроков, схемы, эмблемы и Match Center подключены.\n\n/player ID · /formationimage 4-3-3 · /matchimage')

@router.message(Command('formationimage'))
async def cmd_formationimage(m:Message):
    parts=(m.text or '').split(); formation=parts[1] if len(parts)==2 else '4-3-3'
    if formation not in {'4-3-3','4-4-2','4-2-3-1','3-5-2','5-3-2'}:
        return await m.answer('Доступно: 4-3-3, 4-4-2, 4-2-3-1, 3-5-2, 5-3-2')
    path=formation_board(formation)
    await m.answer_photo(FSInputFile(path),caption=f'🧠 <b>{formation}</b> — базовая схема.')

@router.message(Command('matchimage'))
async def cmd_matchimage(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        row=(await s.execute(text("""SELECT m.id,m.home_score,m.away_score,m.home_xg,m.away_xg,h.name home,a.name away
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            WHERE m.status='FINISHED' AND (m.home_club_id=:c OR m.away_club_id=:c) ORDER BY m.id DESC LIMIT 1"""),{'c':c['id']})).mappings().first()
        events=(await s.execute(text('SELECT minute,event_type,description,metadata FROM match_events WHERE match_id=:m ORDER BY minute,id LIMIT 30'),{'m':row['id']})).mappings().all() if row else []
    if not row:return await m.answer('Завершённых матчей пока нет.')
    path=match_center(row['home'],row['away'],row['home_score'] or 0,row['away_score'] or 0,float(row['home_xg'] or 0),float(row['away_xg'] or 0),events)
    await m.answer_photo(FSInputFile(path),caption=f'📺 <b>{row["home"]} {row["home_score"]}:{row["away_score"]} {row["away"]}</b>')

@router.message(Command('squad'))
async def cmd_squad(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        rows=(await s.execute(text('''SELECT p.*,cp.shirt_number,cp.fitness,cp.form,cp.is_injured
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c ORDER BY p.position,p.potential DESC,p.id'''),{'c':c['id']})).mappings().all()
    if not rows:return await m.answer('Состав пуст.')
    out=[f'👥 <b>СОСТАВ — {len(rows)} игроков</b>','']
    for r in rows:
        rating=player_rating(type('P',(),dict(r, suspended=False, name=f'{r["first_name"]} {r["last_name"]}'))())
        injury=' 🩹' if r['is_injured'] else ''
        out.append(f'#{r["shirt_number"] or "-"} {r["first_name"]} {r["last_name"]} · {r["position"]} · <b>{rating}</b> · FIT {r["fitness"]} · FORM {r["form"]:+d}{injury}')
    await m.answer('\n'.join(out))

@router.message(Command('squadview'))
async def cmd_squadview(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        rows=(await s.execute(text("SELECT p.*,cp.shirt_number,cp.fitness,cp.form,cp.is_injured FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c ORDER BY p.position,p.potential DESC,p.id"),{'c':c['id']})).mappings().all()
    if not rows:return await m.answer('Состав пуст.')
    data=[]
    for r in rows:
        pr=type('P',(),dict(r,suspended=False,name=f"{r['first_name']} {r['last_name']}"))()
        data.append(dict(r,overall=player_rating(pr)))
    path=squad_screen(c['name'],data)
    await m.answer_photo(FSInputFile(path),caption=f'👥 <b>{c["name"]}</b> · {len(data)} игроков')

@router.message(Command('tableview'))
async def cmd_tableview(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        if not league:return await m.answer('Вы пока не участвуете в лиге.')
        rows=(await s.execute(text("SELECT c.name,t.played,t.wins,t.draws,t.losses,t.points,t.goals_for,t.goals_against FROM league_teams t JOIN clubs c ON c.id=t.club_id WHERE t.league_id=:l ORDER BY t.points DESC,(t.goals_for-t.goals_against) DESC,t.goals_for DESC,c.name"),{'l':league['id']})).mappings().all()
    path=league_table_screen(league['name'],rows)
    await m.answer_photo(FSInputFile(path),caption=f'🏆 <b>{league["name"]}</b>')

@router.message(Command('clubview'))
async def cmd_clubview(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
    path=club_dashboard(c)
    await m.answer_photo(FSInputFile(path),caption=f'🏟 <b>{c["name"]}</b> · профиль клуба')

@router.message(Command('marketview'))
async def cmd_marketview(m:Message):
    async with SessionLocal() as s:
        rows=await market_players(s,10)
    data=[]
    for r in rows:
        pr=type('P',(),dict(r,name=f"{r['first_name']} {r['last_name']}",fitness=100,form=0))()
        data.append(dict(r,overall=player_rating(pr),rarity=r.get('rarity','BASE')))
    if not data:return await m.answer('Рынок пуст.')
    path=market_screen(data)
    await m.answer_photo(FSInputFile(path),caption='🔄 <b>ТРАНСФЕРНЫЙ РЫНОК</b>\nСвободный агент: /buy PLAYER_ID\nИгрок другого клуба: /offer LISTING_ID AMOUNT\nСвой игрок: /list PLAYER_ID [PRICE]')

@router.message(Command('fixturesview'))
async def cmd_fixturesview(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        if not league:return await m.answer('Вы пока не участвуете в лиге.')
        rows=(await s.execute(text("SELECT m.round,h.name home,a.name away,m.home_score,m.away_score,m.status FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id WHERE m.league_id=:l ORDER BY m.round,m.id"),{'l':league['id']})).mappings().all()
    path=fixtures_screen(league['name'],rows)
    await m.answer_photo(FSInputFile(path),caption=f'📅 <b>{league["name"]}</b> · календарь')

@router.message(Command('tactics'))
async def cmd_tactics(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=await get_tactical_board(s,c['id'])
    path=tactical_board_screen(t['formation'],[dict(r) for r in rows[:11]],title=f"{c['name']} · {t['style']}")
    caption=(f'🧠 <b>ТАКТИЧЕСКАЯ ДОСКА</b>\n\nФормация: <b>{t["formation"]}</b> · стиль: <b>{t["style"]}</b>\n'
             f'Темп {t["tempo"]} · прессинг {t["pressing"]} · ширина {t["width"]} · линия {t["defensive_line"]} · агрессия {t["aggression"]}\n\n'
             'Выбирай схему или стиль кнопками ниже. После изменения доска перерисуется.')
    await m.answer_photo(FSInputFile(path),caption=caption,reply_markup=tactics_keyboard(t['formation']))

async def _apply_live_tactics(s, club_id):
    row=(await s.execute(text("SELECT id FROM matches WHERE status='LIVE' AND halftime_locked AND (home_club_id=:c OR away_club_id=:c) ORDER BY id DESC LIMIT 1"),{'c':club_id})).first()
    if not row:return False
    t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':club_id})).mappings().first()
    from .game.tactics import Tactics, Style
    await change_halftime_tactics(s,row.id,club_id,Tactics(formation=t['formation'],style=Style(t['style']),tempo=t['tempo'],pressing=t['pressing'],width=t['width'],defensive_line=t['defensive_line'],aggression=t['aggression']))
    return True

@router.message(Command('setformation'))
async def cmd_setformation(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2:return await m.answer('Использование: /setformation 4-3-3')
    if parts[1] not in {'4-3-3','4-4-2','4-2-3-1','3-5-2','5-3-2'}:return await m.answer('Доступно: 4-3-3, 4-4-2, 4-2-3-1, 3-5-2, 5-3-2')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        await s.execute(text('INSERT INTO club_tactics(club_id,formation) VALUES(:c,:f) ON CONFLICT(club_id) DO UPDATE SET formation=EXCLUDED.formation'),{'c':c['id'],'f':parts[1]}); await _apply_live_tactics(s,c['id']); await s.commit()
    await m.answer(f'✅ Формация изменена на {parts[1]}.')

@router.message(Command('setstyle'))
async def cmd_setstyle(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2:return await m.answer('Использование: /setstyle ATTACK')
    if parts[1].upper() not in {'BALANCE','ATTACK','COUNTER','DEFENSE','POSSESSION'}:return await m.answer('Стили: BALANCE, ATTACK, COUNTER, DEFENSE, POSSESSION')
    style=parts[1].upper()
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        await s.execute(text('INSERT INTO club_tactics(club_id,style) VALUES(:c,:st) ON CONFLICT(club_id) DO UPDATE SET style=EXCLUDED.style'),{'c':c['id'],'st':style}); await _apply_live_tactics(s,c['id']); await s.commit()
    await m.answer(f'✅ Стиль изменён на {style}.')

@router.message(Command('setsliders'))
async def cmd_setsliders(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=6:return await m.answer('Использование: /setsliders tempo pressing width line aggression\nПример: /setsliders 60 70 50 65 40')
    try: vals=[int(x) for x in parts[1:]]
    except:return await m.answer('Все значения должны быть числами 0..100.')
    if any(x<0 or x>100 for x in vals):return await m.answer('Все значения должны быть 0..100.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        await s.execute(text('''INSERT INTO club_tactics(club_id,tempo,pressing,width,defensive_line,aggression) VALUES(:c,:t,:p,:w,:d,:a)
            ON CONFLICT(club_id) DO UPDATE SET tempo=EXCLUDED.tempo,pressing=EXCLUDED.pressing,width=EXCLUDED.width,defensive_line=EXCLUDED.defensive_line,aggression=EXCLUDED.aggression'''),
            {'c':c['id'],'t':vals[0],'p':vals[1],'w':vals[2],'d':vals[3],'a':vals[4]}); await _apply_live_tactics(s,c['id']); await s.commit()
    await m.answer('✅ Параметры тактики сохранены.')

@router.message(Command('board'))
async def cmd_board(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        rows=await get_tactical_board(s,c['id'])
    lines=['🧠 <b>ТАКТИЧЕСКАЯ ДОСКА</b>','']
    for r in rows[:11]:
        lines.append(f'#{r["shirt_number"] or "-"} {r["first_name"]} {r["last_name"]} · {r["position"]} · {r["role"]} · {r["instruction"]} · ({r["board_x"]},{r["board_y"]})')
    lines += ['', 'Настройка: /boardset PLAYER_ID ROLE INSTRUCTION X Y', 'Пример: /boardset 123 PLAYMAKER ROAM 68 50']
    await m.answer('\n'.join(lines))

@router.message(Command('boardset'))
async def cmd_boardset(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=6:return await m.answer('Использование: /boardset PLAYER_ID ROLE INSTRUCTION X Y')
    try: pid=int(parts[1]); x=int(parts[4]); y=int(parts[5])
    except ValueError:return await m.answer('PLAYER_ID и координаты должны быть числами.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try:
            await set_player_instruction(s,c['id'],pid,parts[2].upper(),parts[3].upper(),x,y); await s.commit()
        except ValueError as e:return await m.answer(f'❌ {e}')
    await m.answer('✅ Позиция, роль и инструкция сохранены.')

@router.message(Command('lineup'))
async def cmd_lineup(m:Message):
    parts=(m.text or '').split()
    if len(parts)==1:
        async with SessionLocal() as s:
            uid=await current_user(s,m); c=await current_club(s,uid)
            if not c:return await m.answer('Сначала создайте клуб.')
            rows=await get_tactical_board(s,c['id'])
            saved=(await s.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'),{'c':c['id']})).scalar_one_or_none()
        ids=saved or []
        await m.answer('👥 <b>СТАРТОВЫЙ СОСТАВ</b>\n\n'+(' → '.join(str(x) for x in ids) if ids else 'Автоподбор пока используется.')+'\n\nЗадать: /lineup ID1 ID2 ... ID11')
        return
    try: ids=[int(x) for x in parts[1:]]
    except ValueError:return await m.answer('ID игроков должны быть числами.')
    if len(ids)!=11:return await m.answer('Нужно ровно 11 ID игроков.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: await set_starting_lineup(s,c['id'],ids); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer('✅ Стартовые 11 сохранены. Теперь тактическая доска и следующие матчи будут использовать их.')

@router.message(Command('sub'))
async def cmd_sub(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=4:return await m.answer('Использование: /sub МИНУТА PLAYER_OFF PLAYER_ON\nПример: /sub 60 123 456')
    try: minute,off,on=map(int,parts[1:])
    except ValueError:return await m.answer('Минута и ID игроков должны быть числами.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: await plan_substitution(s,c['id'],minute,off,on); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer(f'🔄 Запланирована замена на {minute}′: {off} → {on}.')

@router.message(Command('matchcenter'))
async def cmd_matchcenter(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        row=(await s.execute(text('''SELECT m.id,m.status,m.current_minute,m.home_score,m.away_score,m.home_xg,m.away_xg,h.name home,a.name away
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            WHERE m.home_club_id=:c OR m.away_club_id=:c ORDER BY CASE WHEN m.status='LIVE' THEN 0 ELSE 1 END,m.id DESC LIMIT 1'''),{'c':c['id']})).mappings().first()
        events=(await s.execute(text('SELECT minute,description FROM match_events WHERE match_id=:m ORDER BY minute,id LIMIT 30'),{'m':row['id']})).mappings().all() if row else []
    if not row:return await m.answer('Матчей пока нет.')
    path=match_center(row['home'],row['away'],row['home_score'] or 0,row['away_score'] or 0,float(row['home_xg'] or 0),float(row['away_xg'] or 0),events)
    state='LIVE' if row['status']=='LIVE' else row['status']
    await m.answer_photo(FSInputFile(path),caption=f'📺 <b>{row["home"]} {row["home_score"] or 0}:{row["away_score"] or 0} {row["away"]}</b> · {state} · {row["current_minute"] or 0}′')

@router.message(Command('cards'))
async def cmd_cards(m:Message):
    async with SessionLocal() as s:
        rows=(await s.execute(text("SELECT rarity,count(*) n FROM players GROUP BY rarity ORDER BY CASE rarity WHEN 'LEGENDARY' THEN 1 WHEN 'EPIC' THEN 2 WHEN 'RARE' THEN 3 ELSE 4 END"))).mappings().all()
        real=(await s.execute(text('SELECT count(*) FROM players WHERE real_player=true'))).scalar_one()
    labels={'LEGENDARY':'🟡 Легендарные','EPIC':'🟣 Эпические','RARE':'🟢 Редкие','BASE':'⚪ Базовые'}
    total=sum(r['n'] for r in rows)
    out=['🃏 <b>БАЗА КАРТОЧЕК</b>','',f'Всего: <b>{total}</b>',f'Реальных игроков в seed: <b>{real}</b>','']
    out += [f'{labels.get(r["rarity"],r["rarity"])}: <b>{r["n"]}</b>' for r in rows]
    out += ['', 'Легенды — отдельный пул. Позже можно добавить Prime, Icon, Event и сезонные версии.']
    await m.answer('\n'.join(out))



@router.message(Command('scout'))
async def cmd_scout(m:Message, from_user=None):
    parts=(m.text or '').split()
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        if not league:return await m.answer('Сначала вступите в активную лигу.')
        if len(parts)>1:
            try: opponent_id=int(parts[1])
            except ValueError:return await m.answer('Использование: /scout OPPONENT_CLUB_ID')
        else:
            nxt=await next_opponent(s,c['id'],league['id'])
            if not nxt:return await m.answer('Ближайшего соперника пока нет.')
            opponent_id=nxt['opponent_id']
        try: report=await scout_opponent(s,c['id'],opponent_id)
        except ValueError as e:return await m.answer(f'❌ {e}')
    path=scouting_screen(report)
    caption=(f'🕵️ <b>{report.opponent_name}</b> · выборка {report.sample_matches}\n'
             f'Схема: <b>{report.typical_formation}</b> · стиль: <b>{report.typical_style}</b>\n'
             f'Средние голы: {report.avg_goals_for:.2f} — {report.avg_goals_against:.2f}\n\n'
             + ('\n'.join('• '+x for x in report.counter_recommendations) if report.counter_recommendations else 'Недостаточно наблюдений для контрплана.'))
    await m.answer_photo(FSInputFile(path),caption=caption)

@router.message(Command('setcounter'))
async def cmd_setcounter(m:Message):
    parts=(m.text or '').split()
    if len(parts)<3 or len(parts)>4:
        return await m.answer('Использование: /setcounter OPPONENT_ID FOCUS [TARGET_PLAYER_ID]\nFOCUS: PRESS_PLAYMAKER, ATTACK_FLANKS, HIGH_LINE_TRAP, TARGET_SLOW_CB, LOW_BLOCK, BALANCED')
    try: opponent_id=int(parts[1]); target=int(parts[3]) if len(parts)==4 else None
    except ValueError:return await m.answer('ID должны быть числами.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try:
            plan=await set_counterplan(s,c['id'],opponent_id,parts[2],70,target); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    target_txt=f' · цель #{target}' if target else ''
    await m.answer(f'🎯 Контртактика сохранена: <b>{plan["focus"]}</b> · интенсивность {plan["intensity"]}{target_txt}')

@router.message(Command('market'))
async def cmd_market(m:Message):
    async with SessionLocal() as s:
        rows=await market_players(s,10)
    if not rows:return await m.answer('Рынок пуст.')
    out=['🔄 <b>ТРАНСФЕРНЫЙ РЫНОК</b>','']
    for r in rows:
        if r.get('listing_id'):
            out.append(f'#{r["id"]} {r["first_name"]} {r["last_name"]} · {r["position"]} · €{r["asking_price"]:,} · POT {r["potential"]}\n/offer {r["listing_id"]} {r["asking_price"]}')
        else:
            out.append(f'#{r["id"]} {r["first_name"]} {r["last_name"]} · {r["position"]} · €{r["market_value"]:,} · POT {r["potential"]}\n/buy {r["id"]}')
    await m.answer('\n'.join(out))

@router.message(Command('buy'))
async def cmd_buy(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2 or not parts[1].isdigit():return await m.answer('Использование: /buy PLAYER_ID')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: player,price=await buy_player(s,c['id'],int(parts[1])); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(str(e))
        except Exception: await s.rollback(); raise
    await m.answer(f'✅ Куплен {player["first_name"]} {player["last_name"]} за €{price:,}.')

@router.message(Command('sell'))
async def cmd_sell(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2 or not parts[1].isdigit():return await m.answer('Использование: /sell PLAYER_ID')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: row,price=await sell_player(s,c['id'],int(parts[1])); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(str(e))
        except Exception: await s.rollback(); raise
    await m.answer(f'✅ {row["first_name"]} {row["last_name"]} продан за €{price:,}.')

@router.message(Command('finance'))
async def cmd_finance(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        summary,recent=await financial_report(s,c['id'],league['id'] if league else None)
    path=finance_screen(c,summary,recent)
    lines=[f'💰 <b>ФИНАНСЫ — {c["name"]}</b>',f'Баланс: <b>€{c["budget"]:,}</b>',f'Билет: <b>€{c["ticket_price"]}</b> · Спонсор: <b>LVL {c["sponsor_level"]}</b>', '', 'Настройки:', '/ticketprice 25', '/upgradestadium']
    await m.answer_photo(FSInputFile(path),caption='\n'.join(lines))


@router.message(Command('ticketprice'))
async def cmd_ticketprice(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2 or not parts[1].isdigit():return await m.answer('Использование: /ticketprice 25\nДопустимо €5..€120.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: price=await set_ticket_price(s,c['id'],int(parts[1])); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer(f'🎟 Цена билета установлена: <b>€{price}</b>. Более высокая цена повышает доход с места, но снижает спрос.')


@router.message(Command('upgradestadium'))
async def cmd_upgradestadium(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: level,cost,capacity=await upgrade_stadium(s,c['id']); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer(f'🏟 Стадион улучшен до <b>LVL {level}</b>.\nСтоимость: €{cost:,}\nВместимость: <b>{capacity:,}</b>.')


def training_keyboard(focus, intensity):
    k=InlineKeyboardBuilder()
    for value in ('ATTACK','DEFENSE','PHYSICAL','TECHNICAL','MENTAL','PLAYMAKING','BALANCED','RECOVERY'):
        k.button(text=('✅ ' if value==focus else '')+value, callback_data=f'training:focus:{value}')
    for value in ('LIGHT','NORMAL','HIGH'):
        k.button(text=('✅ ' if value==intensity else '')+value, callback_data=f'training:intensity:{value}')
    k.button(text='🏋️ Тренировать', callback_data='training:run')
    k.button(text='⬅ Назад', callback_data='training:back')
    k.adjust(3,3,2,3,2)
    return k.as_markup()


async def _show_training_menu(message, telegram_id, state=None):
    async with SessionLocal() as s:
        c=await club_by_tg(s,telegram_id)
        if not c:
            return await message.answer('Сначала создайте клуб.')
        plan=(await s.execute(text('SELECT focus,intensity FROM club_training WHERE club_id=:c'), {'c':c['id']})).mappings().first()
    focus=(plan['focus'] if plan else 'BALANCED')
    intensity=(plan['intensity'] if plan else 'NORMAL')
    if state is not None:
        await state.update_data(training_focus=focus,training_intensity=intensity)
    await message.answer(
        '🏋️ <b>ТРЕНИРОВКИ</b>\n\n'
        f'Текущий план: <b>{focus} / {intensity}</b>\n\n'
        'ФОКУС: выберите направление\n'
        'ИНТЕНСИВНОСТЬ: выберите нагрузку',
        reply_markup=training_keyboard(focus,intensity)
    )


@router.callback_query(F.data=='training')
async def cb_training(c:CallbackQuery, state:FSMContext):
    await c.answer()
    await _show_training_menu(c.message,c.from_user.id,state)


@router.callback_query(F.data.startswith('training:focus:'))
async def cb_training_focus(c:CallbackQuery, state:FSMContext):
    focus=c.data.rsplit(':',1)[1]
    if focus not in {'ATTACK','DEFENSE','PHYSICAL','TECHNICAL','MENTAL','PLAYMAKING','BALANCED','RECOVERY'}:
        await c.answer('Недопустимый фокус.',show_alert=True)
        return
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            await c.answer('Сначала создайте клуб.',show_alert=True)
            return
        plan=(await s.execute(text('SELECT focus,intensity FROM club_training WHERE club_id=:c'), {'c':club['id']})).mappings().first()
    data=await state.get_data()
    intensity=data.get('training_intensity') or (plan['intensity'] if plan else 'NORMAL')
    await state.update_data(training_focus=focus,training_intensity=intensity)
    await c.answer()
    try:
        await c.message.edit_text(
            '🏋️ <b>ТРЕНИРОВКИ</b>\n\n'
            f'Текущий план: <b>{focus} / {intensity}</b>\n\n'
            'ФОКУС: выберите направление\n'
            'ИНТЕНСИВНОСТЬ: выберите нагрузку',
            reply_markup=training_keyboard(focus,intensity)
        )
    except Exception:
        await c.message.answer(
            '🏋️ <b>ТРЕНИРОВКИ</b>\n\n'
            f'Текущий план: <b>{focus} / {intensity}</b>\n\n'
            'ФОКУС: выберите направление\n'
            'ИНТЕНСИВНОСТЬ: выберите нагрузку',
            reply_markup=training_keyboard(focus,intensity)
        )


@router.callback_query(F.data.startswith('training:intensity:'))
async def cb_training_intensity(c:CallbackQuery, state:FSMContext):
    intensity=c.data.rsplit(':',1)[1]
    if intensity not in {'LIGHT','NORMAL','HIGH'}:
        await c.answer('Недопустимая интенсивность.',show_alert=True)
        return
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            await c.answer('Сначала создайте клуб.',show_alert=True)
            return
        plan=(await s.execute(text('SELECT focus,intensity FROM club_training WHERE club_id=:c'), {'c':club['id']})).mappings().first()
    data=await state.get_data()
    focus=data.get('training_focus') or (plan['focus'] if plan else 'BALANCED')
    await state.update_data(training_focus=focus,training_intensity=intensity)
    await c.answer()
    try:
        await c.message.edit_text(
            '🏋️ <b>ТРЕНИРОВКИ</b>\n\n'
            f'Текущий план: <b>{focus} / {intensity}</b>\n\n'
            'ФОКУС: выберите направление\n'
            'ИНТЕНСИВНОСТЬ: выберите нагрузку',
            reply_markup=training_keyboard(focus,intensity)
        )
    except Exception:
        await c.message.answer(
            '🏋️ <b>ТРЕНИРОВКИ</b>\n\n'
            f'Текущий план: <b>{focus} / {intensity}</b>\n\n'
            'ФОКУС: выберите направление\n'
            'ИНТЕНСИВНОСТЬ: выберите нагрузку',
            reply_markup=training_keyboard(focus,intensity)
        )


@router.callback_query(F.data=='training:run')
async def cb_training_run(c:CallbackQuery, state:FSMContext):
    data=await state.get_data()
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            await c.answer('Сначала создайте клуб.',show_alert=True)
            return
        plan=(await s.execute(text('SELECT focus,intensity FROM club_training WHERE club_id=:c'), {'c':club['id']})).mappings().first() or {'focus':'BALANCED','intensity':'NORMAL'}
        focus=data.get('training_focus') or plan['focus']
        intensity=data.get('training_intensity') or plan['intensity']
        try:
            focus,intensity=await set_training_plan(s,club['id'],focus,intensity)
            changes=await train_squad(s,club['id'])
            await s.commit()
        except ValueError as e:
            await s.rollback()
            await c.answer(str(e),show_alert=True)
            return
        except Exception:
            await s.rollback()
            logger.exception('Failed to run training')
            await c.answer('Не удалось провести тренировку.',show_alert=True)
            return
    progressed=sum(1 for _,pts,ch in changes if ch)
    points=sum(pts for _,pts,_ in changes)
    await state.clear()
    await c.answer()
    await c.message.answer(
        f'🏋️ <b>Тренировка завершена</b>. Фокус: {focus}. Интенсивность: {intensity}.\n'
        f'Развитие: {points} очков, игроков с изменениями: {progressed}.',
        reply_markup=training_keyboard(focus,intensity)
    )


@router.callback_query(F.data=='training:back')
async def cb_training_back(c:CallbackQuery, state:FSMContext):
    await state.clear()
    await c.answer()
    await c.message.answer('Выберите действие:',reply_markup=menu())


@router.message(Command('training'))
async def cmd_training(m:Message):
    parts=(m.text or '').split()
    if len(parts)==1:
        focus='BALANCED'; intensity='NORMAL'
    elif len(parts)==3:
        focus=parts[1].upper(); intensity=parts[2].upper()
    else:
        return await m.answer('Использование: /training ATTACK NORMAL\nФокус: ATTACK, PLAYMAKING, DEFENSE, PHYSICAL, TECHNICAL, MENTAL, BALANCED, RECOVERY\nИнтенсивность: LIGHT, NORMAL, HIGH')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try:
            await set_training_plan(s,c['id'],focus,intensity); changes=await train_squad(s,c['id']); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    progressed=sum(1 for _,pts,ch in changes if ch); points=sum(pts for _,pts,_ in changes)
    await m.answer(f'🏋️ <b>Тренировка завершена</b>\nФокус: {focus} · интенсивность: {intensity}\nРазвитие: {points} очков · игроков с изменениями: {progressed}\n\nСледующий тур использует этот план автоматически.')

@router.message(Command('contract'))
async def cmd_contract(m:Message):
    parts=(m.text or '').split()
    if len(parts)<2 or not parts[1].isdigit(): return await m.answer('Использование: /contract PLAYER_ID [YEARS] [STAR|KEY|SQUAD|PROSPECT]')
    years=int(parts[2]) if len(parts)>2 and parts[2].isdigit() else 3
    role=parts[3].upper() if len(parts)>3 else None
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: offer=await renew_contract(s,c['id'],int(parts[1]),years,role); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer(f'📝 Контракт продлён на <b>{offer.years} лет</b>.\nЗарплата: <b>€{offer.salary:,}/период</b>\nОтступные: <b>€{offer.release_clause:,}</b>.')

@router.message(Command('expect'))
async def cmd_expect(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=3:return await m.answer('Использование: /expect PLAYER_ID STAR|KEY|SQUAD|PROSPECT')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: role=await set_player_expectation(s,c['id'],int(parts[1]),parts[2]); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer(f'🎯 Ожидание игрового времени установлено: <b>{role}</b>.')

@router.message(Command('contracts'))
async def cmd_contracts(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        rows=await player_management_report(s,c['id'])
    out=['📝 <b>КОНТРАКТЫ И СОСТОЯНИЕ СОСТАВА</b>','']
    for r in rows:
        out.append(f'#{r["id"]} {r["first_name"]} {r["last_name"]} · €{int(r["contract_salary"] or 0):,} · morale {r["morale"]} · {r["playing_time_expectation"]} · {r["contract_until"]}')
    await m.answer('\n'.join(out[:31]))

@router.message(Command('list'))
async def cmd_list(m:Message):
    parts=(m.text or '').split()
    if len(parts) not in (2,3) or not parts[1].isdigit():return await m.answer('Использование: /list PLAYER_ID [ASKING_PRICE]')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: lid,ask,minimum=await list_player_for_transfer(s,c['id'],int(parts[1]),int(parts[2]) if len(parts)==3 else None); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer(f'📋 Игрок выставлен на рынок. Listing #{lid}\nЦена: €{ask:,}\nМинимум: €{minimum:,}.')

@router.message(Command('offer'))
async def cmd_offer(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=3 or not parts[1].isdigit() or not parts[2].isdigit():return await m.answer('Использование: /offer LISTING_ID AMOUNT')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: oid=await make_transfer_offer(s,c['id'],int(parts[1]),int(parts[2])); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer(f'📨 Предложение #{oid} отправлено владельцу игрока.')

@router.message(Command('acceptoffer'))
async def cmd_acceptoffer(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2 or not parts[1].isdigit():return await m.answer('Использование: /acceptoffer OFFER_ID')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: offer=await accept_transfer_offer(s,c['id'],int(parts[1])); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
    await m.answer(f'✅ Трансфер завершён за €{int(offer["amount"]):,}.')

@router.message(Command('offers'))
async def cmd_offers(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        rows=(await s.execute(text('''SELECT o.id,o.amount,p.first_name,p.last_name,l.player_id
            FROM transfer_offers o JOIN transfer_listings l ON l.id=o.listing_id JOIN players p ON p.id=l.player_id
            WHERE l.seller_club_id=:c AND o.status='PENDING' ORDER BY o.id DESC'''),{'c':c['id']})).mappings().all()
    if not rows:return await m.answer('📨 Новых предложений нет.')
    await m.answer('\n'.join(["📨 <b>ПРЕДЛОЖЕНИЯ</b>"]+[f'#{r["id"]} {r["first_name"]} {r["last_name"]} — €{r["amount"]:,} → /acceptoffer {r["id"]}' for r in rows]))

@router.message(Command('fixtures'))
async def cmd_fixtures(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        if not league:return await m.answer('Вы пока не участвуете в лиге.')
        rows=(await s.execute(text('''SELECT m.round,h.name home,a.name away,m.home_score,m.away_score,m.status,m.scheduled_at
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            WHERE m.league_id=:l ORDER BY m.round,m.id LIMIT 40'''),{'l':league['id']})).mappings().all()
    out=[f'📅 <b>КАЛЕНДАРЬ — {league["name"]}</b>','']
    for r in rows:
        score=f'{r["home_score"]}:{r["away_score"]}' if r['status']=='FINISHED' else '—'
        kickoff=(r['scheduled_at'].strftime('%d.%m %H:%M UTC') if r['scheduled_at'] else 'время не назначено')
        out.append(f'Тур {r["round"]}: {kickoff} · {r["home"]} {score} {r["away"]}')
    await m.answer('\n'.join(out))

@router.message(Command('notifications'))
async def cmd_notifications(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m)
        prefs=await notification_preferences(s,uid)
        unread=await notification_unread_count(s,uid)
        await s.commit()
    labels=[('match','⚽ Матчи'),('injury','🏥 Травмы'),('transfer','🔄 Трансферы'),('finance','💰 Финансы'),('development','📈 Развитие'),('discipline','🟥 Дисциплина'),('morale','😊 Мораль'),('tournament','🏆 Турнир')]
    k=InlineKeyboardBuilder()
    for col,label in labels:
        k.button(text=('✅ ' if prefs[col] else '❌ ')+label,callback_data=f'notif:{col}')
    k.button(text='📜 Последние уведомления',callback_data='notif:list')
    k.adjust(2,2,2,2,1)
    await m.answer(f'🔔 <b>Уведомления</b> · непрочитанных: <b>{unread}</b>\n\nНажми категорию, чтобы включить или выключить её.',reply_markup=k.as_markup())

def notification_menu_keyboard(counts):
    k=InlineKeyboardBuilder()
    for category,label in (('transfer','📨 Трансферы'),('injury','🏥 Травмы'),('match','⚽ Матчи'),('finance','💰 Финансы')):
        k.button(text=f'{label} ({counts.get(category,0)})',callback_data=f'notif:cat:{category}')
    k.button(text='✅ Прочитать всё',callback_data='notif:read_all')
    k.button(text='📜 Последние 20',callback_data='notif:list')
    k.button(text='⬅ Назад',callback_data='notif:back')
    k.adjust(2,2,2,1)
    return k.as_markup()


async def _show_notification_menu(cq:CallbackQuery):
    async with SessionLocal() as s:
        uid=await get_or_create_user(s,cq.from_user.id,cq.from_user.username,cq.from_user.first_name)
        unread=await notification_unread_count(s,uid)
        rows=(await s.execute(text('''SELECT lower(category) AS category,count(*) AS count
            FROM notifications
            WHERE user_id=:u AND read_at IS NULL AND deliver_at<=now() AND sent_at IS NOT NULL AND cancelled_at IS NULL
            GROUP BY category'''), {'u':uid})).mappings().all()
    counts={r['category']:int(r['count']) for r in rows}
    await cq.message.answer(
        f'🔔 <b>УВЕДОМЛЕНИЯ</b>\nНепрочитанных: <b>{unread}</b>',
        reply_markup=notification_menu_keyboard(counts)
    )


async def _show_notification_category(cq:CallbackQuery, category):
    labels={'transfer':'📨 Трансферы','injury':'🏥 Травмы','match':'⚽ Матчи','finance':'💰 Финансы'}
    if category not in labels:
        await cq.answer('Неизвестная категория.',show_alert=True)
        return
    async with SessionLocal() as s:
        uid=await get_or_create_user(s,cq.from_user.id,cq.from_user.username,cq.from_user.first_name)
        rows=(await s.execute(text('''SELECT title,body,created_at
            FROM notifications
            WHERE user_id=:u AND lower(category)=:cat AND deliver_at<=now() AND sent_at IS NOT NULL AND cancelled_at IS NULL
            ORDER BY created_at DESC,id DESC LIMIT 20'''), {'u':uid,'cat':category})).mappings().all()
    if not rows:
        await cq.message.answer(f'{labels[category]}\n\nУведомлений пока нет.',reply_markup=notification_menu_keyboard({}))
    else:
        lines=[f'{labels[category]} <b>— последние 20</b>','']
        for r in rows:
            lines.append(f"<b>{r['title']}</b>\n{r['body']}")
        await cq.message.answer('\n\n'.join(lines),reply_markup=notification_menu_keyboard({}))
    await cq.answer()


@router.callback_query(F.data.startswith('notif:'))
async def cb_notifications(cq:CallbackQuery):
    action=cq.data.split(':',1)[1]
    if action=='menu':
        await cq.answer()
        await _show_notification_menu(cq)
        return
    if action=='back':
        await cq.answer()
        await cq.message.answer('Выберите действие:',reply_markup=menu())
        return
    if action.startswith('cat:'):
        await _show_notification_category(cq,action.split(':',1)[1])
        return
    if action=='read_all':
        async with SessionLocal() as s:
            uid=await get_or_create_user(s,cq.from_user.id,cq.from_user.username,cq.from_user.first_name)
            await mark_notifications_read(s,uid)
            await s.commit()
        await cq.answer('Все уведомления прочитаны.')
        await _show_notification_menu(cq)
        return
    allowed={'match','injury','transfer','finance','development','discipline','morale','tournament','list'}
    if action not in allowed:
        await cq.answer('Неизвестная настройка.',show_alert=True)
        return
    async with SessionLocal() as s:
        uid=await get_or_create_user(s,cq.from_user.id,cq.from_user.username,cq.from_user.first_name)
        if action=='list':
            rows=await notification_list(s,uid,20)
            await mark_notifications_read(s,uid)
            await s.commit()
            if not rows:
                await cq.message.answer('📭 Уведомлений пока нет.')
            else:
                await cq.message.answer('🔔 <b>Последние уведомления</b>\n\n'+'\n\n'.join(f"{r['title']}\n{r['body']}" for r in rows))
            await cq.answer(); return
        prefs=await notification_preferences(s,uid)
        enabled=not bool(prefs[action])
        await set_notification_preference(s,uid,action,enabled)
        prefs=await notification_preferences(s,uid)
        unread=await notification_unread_count(s,uid)
        await s.commit()
    labels=[('match','⚽ Матчи'),('injury','🏥 Травмы'),('transfer','🔄 Трансферы'),('finance','💰 Финансы'),('development','📈 Развитие'),('discipline','🟥 Дисциплина'),('morale','😊 Мораль'),('tournament','🏆 Турнир')]
    k=InlineKeyboardBuilder()
    for col,label in labels:
        k.button(text=('✅ ' if prefs[col] else '❌ ')+label,callback_data=f'notif:{col}')
    k.button(text='📜 Последние уведомления',callback_data='notif:list'); k.adjust(2,2,2,2,1)
    try: await cq.message.edit_text(f'🔔 <b>Уведомления</b> · непрочитанных: <b>{unread}</b>\n\nНажми категорию, чтобы включить или выключить её.',reply_markup=k.as_markup())
    except Exception: pass
    await cq.answer('Включено' if enabled else 'Выключено')

@router.callback_query(F.data=='create')
async def create_prompt(c:CallbackQuery):
    await c.message.answer('Напишите название клуба одним сообщением.'); await c.answer()

@router.callback_query(F.data=='club')
async def cb_club(c:CallbackQuery):
    await c.answer(); await cmd_clubview(c.message, c.from_user)

@router.callback_query(F.data=='menu')
async def cb_menu(c:CallbackQuery):
    await c.answer()
    await c.message.answer('Выберите действие:',reply_markup=menu())
async def cb_squad(c:CallbackQuery): await c.answer(); await cmd_squad(c.message, c.from_user)
@router.callback_query(F.data=='tactics')
async def cb_tactics(c:CallbackQuery): await c.answer(); await cmd_tactics(c.message, c.from_user)
@router.callback_query(F.data=='market')
async def cb_market(c:CallbackQuery): await c.answer(); await cmd_marketview(c.message)

async def _show_market(cq_or_message, telegram_id, data=None):
    if data is None:
        async with SessionLocal() as s:
            rows=await market_players(s,10)
        data=_market_data(rows)
    _MARKET_VIEWS[telegram_id]=data
    if not data:
        if isinstance(cq_or_message, CallbackQuery):
            return await cq_or_message.message.answer('Рынок пуст.')
        return await cq_or_message.answer('Рынок пуст.')
    path=market_screen(data)
    caption='🔄 <b>ТРАНСФЕРНЫЙ РЫНОК</b>'
    markup=_market_keyboard(data)
    message=cq_or_message.message if isinstance(cq_or_message,CallbackQuery) else cq_or_message
    try:
        await message.edit_media(InputMediaPhoto(media=FSInputFile(path),caption=caption),reply_markup=markup)
    except Exception:
        await message.answer_photo(FSInputFile(path),caption=caption,reply_markup=markup)

@router.callback_query(F.data=='market:list')
async def cb_market_list(cq:CallbackQuery):
    await cq.answer()
    await _show_market(cq,cq.from_user.id)

@router.callback_query(F.data.startswith('market:filter:'))
async def cb_market_filter(cq:CallbackQuery):
    code=cq.data.rsplit(':',1)[1]
    if code not in {'ATT','MID','DEF','GK'}:
        return await cq.answer('Неизвестный фильтр.',show_alert=True)
    async with SessionLocal() as s:
        rows=await market_players(s,10)
    data=[r for r in _market_data(rows) if _position_filter(r.get('position'),code)]
    _MARKET_VIEWS[cq.from_user.id]=data
    await cq.answer()
    if not data:
        return await cq.answer('В этой категории нет игроков.',show_alert=True)
    path=market_screen(data)
    try:
        await cq.message.edit_media(InputMediaPhoto(media=FSInputFile(path),caption='🔄 <b>ТРАНСФЕРНЫЙ РЫНОК</b>'),reply_markup=_market_keyboard(data))
    except Exception:
        await cq.message.answer_photo(FSInputFile(path),caption='🔄 <b>ТРАНСФЕРНЫЙ РЫНОК</b>',reply_markup=_market_keyboard(data))

@router.callback_query(F.data.startswith('market:item:'))
async def cb_market_item(cq:CallbackQuery):
    try:index=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    data=_MARKET_VIEWS.get(cq.from_user.id)
    if data is None:
        async with SessionLocal() as s:
            data=_market_data(await market_players(s,10))
        _MARKET_VIEWS[cq.from_user.id]=data
    if index<0 or index>=len(data):
        return await cq.answer('Игрок не найден.',show_alert=True)
    r=data[index]
    card=player_card(dict(r),out=Path('assets/visual/cards')/f'market_player_{r["id"]}.png')
    caption=(f'🃏 <b>{r["first_name"]} {r["last_name"]}</b>\n'
             f'{r["position"]} · рейтинг <b>{r.get("overall","—")}</b> · €{int(r.get("asking_price") or r.get("market_value") or 0):,} · POT {r.get("potential","—")}')
    k=InlineKeyboardBuilder()
    if r.get('listing_id'):
        k.button(text=f'📨 Оффер €{int(r.get("asking_price") or 0):,}',callback_data=f'market:offer:{r["listing_id"]}')
    else:
        k.button(text=f'💰 Купить за €{int(r.get("market_value") or 0):,}',callback_data=f'market:buy:{r["id"]}')
    k.button(text='⬅ Назад к рынку',callback_data='market:list')
    k.adjust(1)
    await cq.message.answer_photo(FSInputFile(card),caption=caption,reply_markup=k.as_markup())
    await cq.answer()

@router.callback_query(F.data.startswith('market:buy:'))
async def cb_market_buy(cq:CallbackQuery):
    try:index=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    data=_MARKET_VIEWS.get(cq.from_user.id,[])
    if index<0 or index>=len(data):
        return await cq.answer('Игрок не найден.',show_alert=True)
    pid=int(data[index]['id'])
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try:
            player,price=await buy_player(s,c['id'],pid); await s.commit()
        except ValueError as e:
            await s.rollback(); return await cq.answer(str(e),show_alert=True)
    await cq.answer()
    await cq.message.answer(f'✅ Куплен {player["first_name"]} {player["last_name"]} за €{price:,}.')
    await _show_market(cq,cq.from_user.id)

@router.callback_query(F.data.startswith('market:offer:'))
async def cb_market_offer(cq:CallbackQuery):
    await cq.answer('📨 Оффер отправлен')
    await _show_market(cq,cq.from_user.id)

@router.callback_query(F.data=='contract:list')
async def cb_contract_list(cq:CallbackQuery):
    await cq.answer()
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.message.answer('Сначала создайте клуб.')
        rows=await player_management_report(s,c['id'])
    if not rows:return await cq.message.answer('Состав пуст.')
    data=[dict(r) for r in rows]
    path=squad_screen(c['name'],data)
    await cq.message.answer_photo(FSInputFile(path),caption='📝 <b>КОНТРАКТЫ И СОСТОЯНИЕ СОСТАВА</b>',reply_markup=_contract_keyboard(data))

_CONTRACT_VIEWS={}

@router.callback_query(F.data.startswith('contract:item:'))
async def cb_contract_item(cq:CallbackQuery):
    try:index=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=await player_management_report(s,c['id'])
    if index<0 or index>=len(rows):return await cq.answer('Игрок не найден.',show_alert=True)
    r=dict(rows[index]); _CONTRACT_VIEWS[cq.from_user.id]=r
    card=player_card(r,out=Path('assets/visual/cards')/f'contract_player_{r["id"]}.png')
    caption=(f'📝 <b>{r["first_name"]} {r["last_name"]}</b> · {r["position"]}\n'
             f'Зарплата: €{int(r.get("contract_salary") or 0):,}\n'
             f'Окончание: {r.get("contract_until") or "—"}\n'
             f'Мораль: {r.get("morale","—")} · ожидание: {r.get("playing_time_expectation") or "—"}')
    k=InlineKeyboardBuilder()
    k.button(text='📝 3 года',callback_data=f'contract:renew:{r["id"]}:3')
    k.button(text='📝 5 лет',callback_data=f'contract:renew:{r["id"]}:5')
    for role in ('STAR','KEY','SQUAD','PROSPECT'):
        k.button(text=f'⭐ {role}' if role=='STAR' else f'🔑 {role}' if role=='KEY' else f'👥 {role}' if role=='SQUAD' else f'🎓 {role}',callback_data=f'contract:role:{r["id"]}:{role}')
    k.button(text='⬅ К контрактам',callback_data='contract:list')
    k.adjust(2,2,2,1)
    await cq.message.answer_photo(FSInputFile(card),caption=caption,reply_markup=k.as_markup())
    await cq.answer()

@router.callback_query(F.data.startswith('contract:renew:'))
async def cb_contract_renew(cq:CallbackQuery):
    parts=cq.data.split(':')
    if len(parts)!=4 or not parts[2].isdigit() or parts[3] not in {'3','5'}:
        return await cq.answer('Некорректный контракт.',show_alert=True)
    pid,years=int(parts[2]),int(parts[3])
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        role_row=(await s.execute(text('SELECT playing_time_expectation FROM club_players WHERE club_id=:c AND player_id=:p'),{'c':c['id'],'p':pid})).mappings().first()
        role=role_row['playing_time_expectation'] if role_row else None
        try: offer=await renew_contract(s,c['id'],pid,years,role); await s.commit()
        except ValueError as e:
            await s.rollback(); return await cq.answer(str(e),show_alert=True)
    await cq.answer('Контракт продлён.')
    await cq.message.answer(f'✅ Контракт продлён на {years} лет. Зарплата: €{offer.salary:,}. Отступные: €{offer.release_clause:,}.')
    # Refresh the contract card.
    await _refresh_contract_item(cq,pid)

@router.callback_query(F.data.startswith('contract:role:'))
async def cb_contract_role(cq:CallbackQuery):
    parts=cq.data.split(':')
    if len(parts)!=4 or not parts[2].isdigit() or parts[3] not in {'STAR','KEY','SQUAD','PROSPECT'}:
        return await cq.answer('Некорректная роль.',show_alert=True)
    pid,role=int(parts[2]),parts[3]
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try:
            await renew_contract(s,c['id'],pid,3,role); await s.commit()
        except ValueError as e:
            await s.rollback(); return await cq.answer(str(e),show_alert=True)
    await cq.answer()
    await cq.message.answer(f'✅ Роль обновлена: {role}')
    await _refresh_contract_item(cq,pid)

async def _refresh_contract_item(cq,pid):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return
        rows=await player_management_report(s,c['id'])
    row=next((dict(r) for r in rows if int(r['id'])==int(pid)),None)
    if not row:return
    card=player_card(row,out=Path('assets/visual/cards')/f'contract_player_{row["id"]}.png')
    caption=(f'📝 <b>{row["first_name"]} {row["last_name"]}</b> · {row["position"]}\n'
             f'Зарплата: €{int(row.get("contract_salary") or 0):,}\n'
             f'Окончание: {row.get("contract_until") or "—"}\n'
             f'Мораль: {row.get("morale","—")} · ожидание: {row.get("playing_time_expectation") or "—"}')
    k=InlineKeyboardBuilder()
    k.button(text='📝 3 года',callback_data=f'contract:renew:{row["id"]}:3')
    k.button(text='📝 5 лет',callback_data=f'contract:renew:{row["id"]}:5')
    for role in ('STAR','KEY','SQUAD','PROSPECT'):
        k.button(text=f'⭐ {role}' if role=='STAR' else f'🔑 {role}' if role=='KEY' else f'👥 {role}' if role=='SQUAD' else f'🎓 {role}',callback_data=f'contract:role:{row["id"]}:{role}')
    k.button(text='⬅ К контрактам',callback_data='contract:list')
    k.adjust(2,2,2,1)
    await cq.message.answer_photo(FSInputFile(card),caption=caption,reply_markup=k.as_markup())

@router.callback_query(F.data=='table')
async def cb_table(c:CallbackQuery): await c.answer(); await cmd_tableview(c.message, c.from_user)
@router.callback_query(F.data=='matchcenter')
async def cb_matchcenter(c:CallbackQuery): await c.answer(); await cmd_matchcenter(c.message, c.from_user)
@router.callback_query(F.data=='fixtures')
async def cb_fixtures(c:CallbackQuery): await c.answer(); await cmd_fixturesview(c.message, c.from_user)
@router.callback_query(F.data=='scout')
async def cb_scout(c:CallbackQuery): await c.answer(); await cmd_scout(c.message, c.from_user)
@router.callback_query(F.data=='finance')
async def cb_finance(c:CallbackQuery): await c.answer(); await cmd_finance(c.message, c.from_user)
def league_menu_keyboard():
    k=InlineKeyboardBuilder()
    k.button(text='➕ Создать лигу', callback_data='league:create')
    k.button(text='🔗 Вступить в лигу', callback_data='league:join')
    k.button(text='▶️ Начать сезон', callback_data='league:start')
    k.button(text='⚽ Сыграть тур', callback_data='league:play')
    k.button(text='📊 Таблица', callback_data='league:table')
    k.button(text='📅 Календарь', callback_data='league:fixtures')
    k.button(text='⬅ Назад', callback_data='menu')
    k.adjust(2,2,2,1)
    return k.as_markup()


@router.callback_query(F.data=='league')
async def cb_league(c:CallbackQuery):
    await c.answer()
    await c.message.answer(
        '🏆 <b>СОРЕВНОВАНИЕ</b>\n\nВыберите действие:',
        reply_markup=league_menu_keyboard()
    )


@router.callback_query(F.data=='league:create')
async def cb_league_create(c:CallbackQuery, state:FSMContext):
    await c.answer()
    await state.set_state(LeagueCreate.choosing_size)
    k=InlineKeyboardBuilder()
    for n in (4,6,8,10,12):
        k.button(text=str(n), callback_data=f'league:create:size:{n}')
    k.button(text='⬅ Отмена', callback_data='league')
    k.adjust(5,1)
    await c.message.answer(
        '➕ <b>Создать лигу</b>\n\n'
        'Сколько клубов будет в лиге?',
        reply_markup=k.as_markup()
    )


@router.callback_query(LeagueCreate.choosing_size, F.data.startswith('league:create:size:'))
async def cb_league_create_size(c:CallbackQuery, state:FSMContext):
    await c.answer()
    n=int(c.data.rsplit(':',1)[1])
    if n not in (4,6,8,10,12):
        await c.message.answer('Недопустимое число клубов.')
        return
    await state.update_data(max_teams=n)
    await state.set_state(LeagueCreate.entering_name)
    await c.message.answer(
        f'➕ Лига на <b>{n}</b> клубов.\n\n'
        'Отправьте название лиги одним сообщением\n'
        '(до 30 символов).\n\n'
        'Отмена — /cancel'
    )


@router.message(LeagueCreate.entering_name)
async def league_create_name(m:Message, state:FSMContext):
    name=(m.text or '').strip()
    if not 3 <= len(name) <= 30:
        return await m.answer('Название от 3 до 30 символов. Попробуйте ещё.')
    data=await state.get_data()
    n=data.get('max_teams',8)
    await state.clear()
    async with SessionLocal() as s:
        uid=await current_user(s,m)
        c=await current_club(s,uid)
        if not c:
            return await m.answer('Сначала создайте клуб.')
        try:
            league_id,code=await create_league(s,uid,name,n)
            await s.commit()
        except ValueError as e:
            await s.rollback()
            return await m.answer(str(e))
        except Exception:
            await s.rollback()
            logger.exception('Failed to create league')
            return await m.answer('Не удалось создать лигу.')
    await m.answer(
        f'🏆 Лига <b>{name}</b> создана.\n'
        f'Код приглашения: <code>{code}</code>\n\n'
        f'Друзья могут вступить: <code>/join {code}</code>',
        reply_markup=league_menu_keyboard()
    )


@router.callback_query(F.data=='league:join')
async def cb_league_join(c:CallbackQuery, state:FSMContext):
    await c.answer()
    await state.set_state(LeagueJoin.entering_code)
    await c.message.answer(
        '🔗 <b>Вступить в лигу</b>\n\n'
        'Отправьте код приглашения одним сообщением.\n\n'
        'Отмена — /cancel'
    )


@router.message(LeagueJoin.entering_code)
async def league_join_code(m:Message, state:FSMContext):
    code=(m.text or '').strip().upper()
    if not code.isalnum() or not 4 <= len(code) <= 8:
        return await m.answer('Код — 4–8 символов, буквы и цифры.')
    await state.clear()
    async with SessionLocal() as s:
        uid=await current_user(s,m)
        c=await current_club(s,uid)
        if not c:
            return await m.answer('Сначала создайте клуб.')
        league=(await s.execute(
            text('SELECT id,name FROM leagues WHERE invite_code=:c'),
            {'c':code}
        )).first()
        if not league:
            return await m.answer('Лига не найдена. Проверьте код.')
        try:
            await join_league(s,uid,code)
            await s.commit()
        except ValueError as e:
            await s.rollback()
            return await m.answer(str(e))
    await m.answer(
        f'✅ Вы вступили в лигу <b>{league.name}</b>.',
        reply_markup=league_menu_keyboard()
    )


@router.callback_query(F.data=='league:start')
async def cb_league_start(c:CallbackQuery):
    await c.answer()
    await cmd_startleague(c.message)


@router.callback_query(F.data=='league:play')
async def cb_league_play(c:CallbackQuery):
    await c.answer()
    await cmd_playround(c.message)


@router.callback_query(F.data=='league:table')
async def cb_league_table(c:CallbackQuery):
    await c.answer()
    await cmd_tableview(c.message, c.from_user)


@router.callback_query(F.data=='league:fixtures')
async def cb_league_fixtures(c:CallbackQuery):
    await c.answer()
    await cmd_fixturesview(c.message, c.from_user)


@router.message(Command('cancel'))
async def cmd_cancel(m:Message, state:FSMContext):
    current=await state.get_state()
    if current is None:
        return await m.answer('Нечего отменять.')
    await state.clear()
    await m.answer('Отменено.', reply_markup=menu())


@router.callback_query(F.data.startswith('tac:'))
async def cb_tactics_visual(cq:CallbackQuery):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:
            await cq.answer('Сначала создайте клуб.',show_alert=True); return
        action=cq.data.split(':',2)
        if action[1]=='f':
            f=action[2]
            await s.execute(text('INSERT INTO club_tactics(club_id,formation) VALUES(:c,:f) ON CONFLICT(club_id) DO UPDATE SET formation=EXCLUDED.formation'),{'c':c['id'],'f':f})
            await _apply_live_tactics(s,c['id'])
        elif action[1]=='s':
            st=action[2]
            await s.execute(text('INSERT INTO club_tactics(club_id,style) VALUES(:c,:st) ON CONFLICT(club_id) DO UPDATE SET style=EXCLUDED.style'),{'c':c['id'],'st':st})
            await _apply_live_tactics(s,c['id'])
        await s.commit()
        t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=await get_tactical_board(s,c['id'])
    path=tactical_board_screen(t['formation'],[dict(r) for r in rows[:11]],title=f"{c['name']} · {t['style']}")
    caption=(f'🧠 <b>{c["name"]}</b>\n{t["formation"]} · {t["style"]}\n\n'
             f'Темп {t["tempo"]} · прессинг {t["pressing"]} · ширина {t["width"]} · линия {t["defensive_line"]} · агрессия {t["aggression"]}')
    try:
        await cq.message.edit_media(InputMediaPhoto(media=FSInputFile(path),caption=caption),reply_markup=tactics_keyboard(t['formation']))
    except Exception:
        await cq.message.answer_photo(FSInputFile(path),caption=caption,reply_markup=tactics_keyboard(t['formation']))
    await cq.answer('Тактика обновлена.')

@router.callback_query(F.data=='squad')
async def cb_squad_screen(cq:CallbackQuery):
    await cq.answer()
    await _show_squad(cq.message,cq.from_user.id)


async def _load_squad_rows(telegram_id, filter_code=None):
    async with SessionLocal() as s:
        c=await club_by_tg(s,telegram_id)
        if not c:return None,None
        rows=(await s.execute(text("""SELECT p.*,cp.shirt_number,cp.fitness,cp.form,cp.is_injured
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c
            ORDER BY p.position,p.potential DESC,p.id"""),{'c':c['id']})).mappings().all()
    if filter_code:
        rows=[r for r in rows if _position_filter(r['position'],filter_code)]
    return c,rows


async def _show_squad(message,telegram_id,filter_code=None,page=0):
    c,rows=await _load_squad_rows(telegram_id,filter_code)
    if not c:return await message.answer('Сначала создайте клуб.')
    if not rows:return await message.answer('Состав пуст.')
    data=[]
    for r in rows:
        pr=type('P',(),dict(r,suspended=False,name=f"{r['first_name']} {r['last_name']}"))()
        data.append(dict(r,overall=player_rating(pr)))
    path=squad_screen(c['name'],data)
    caption=f'👥 <b>{c["name"]}</b> · {len(data)} игроков'
    await message.answer_photo(FSInputFile(path),caption=caption,reply_markup=squad_keyboard(data,page))


@router.callback_query(F.data.startswith('squad:item:'))
async def cb_squad_item(cq:CallbackQuery):
    try:index=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    c,rows=await _load_squad_rows(cq.from_user.id)
    if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
    if index<0 or index>=len(rows):return await cq.answer('Игрок не найден.',show_alert=True)
    r=rows[index]; _SELECTED_SQUAD_PLAYERS[cq.from_user.id]=int(r['id'])
    talents=r.get('talents') or []
    if isinstance(talents,str):
        import json; talents=json.loads(talents)
    caption=(f'🃏 <b>{r["first_name"]} {r["last_name"]}</b>\n{r["position"]} · {r["nationality"]} · {r["rarity"]}\n'
             f'⚡ PAC {r["pace"]} · SHO {r["shooting"]} · PAS {r["passing"]} · DRI {r["dribbling"]}\n'
             f'🛡 DEF {r["defending"]} · PHY {r["physical"]} · STA {r["stamina"]} · MENT {r["mental"]}\n'
             f'🎯 POT {r["potential"]} · €{r["market_value"]:,}\n✨ <b>Таланты:</b> {", ".join(talents) or "—"}')
    portrait=r.get('portrait_url'); portrait_path=None
    if portrait:
        candidate=Path(str(portrait)); portrait_path=candidate if candidate.is_absolute() else Path(__file__).resolve().parents[1]/candidate
    card=player_card(dict(r,portrait_path=portrait_path),out=Path('assets/visual/cards')/f'player_{r["id"]}.png')
    k=InlineKeyboardBuilder(); k.button(text='📝 Контракт',callback_data='squad:contract'); k.button(text='💸 Продать',callback_data='squad:sell'); k.button(text='🔄 Трансфер',callback_data='squad:transfer'); k.button(text='⬅ К составу',callback_data='squad'); k.adjust(2,2)
    await cq.message.answer_photo(FSInputFile(card),caption=caption,reply_markup=k.as_markup()); await cq.answer()


@router.callback_query(F.data.startswith('squad:filter:'))
async def cb_squad_filter(cq:CallbackQuery):
    code=cq.data.rsplit(':',1)[1]
    if code not in {'GK','DEF','MID','ATT'}:return await cq.answer('Неизвестный фильтр.',show_alert=True)
    await cq.answer(); await _show_squad(cq.message,cq.from_user.id,code)


@router.callback_query(F.data=='squad:contract')
async def cb_squad_contract(cq:CallbackQuery):
    pid=_SELECTED_SQUAD_PLAYERS.get(cq.from_user.id)
    if not pid:return await cq.answer('Сначала выберите игрока.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try: offer=await renew_contract(s,c['id'],pid); await s.commit()
        except ValueError as e: await s.rollback(); return await cq.answer(str(e),show_alert=True)
    await cq.answer('Контракт обновлён.')
    await cq.message.answer(f'📝 Контракт продлён на <b>{offer.years} лет</b>.\nЗарплата: <b>€{offer.salary:,}/период</b>.\nОтступные: <b>€{offer.release_clause:,}</b>.',reply_markup=InlineKeyboardBuilder().as_markup())


@router.callback_query(F.data=='squad:sell')
async def cb_squad_sell(cq:CallbackQuery):
    pid=_SELECTED_SQUAD_PLAYERS.get(cq.from_user.id)
    if not pid:return await cq.answer('Сначала выберите игрока.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try: row,price=await sell_player(s,c['id'],pid); await s.commit()
        except ValueError as e: await s.rollback(); return await cq.answer(str(e),show_alert=True)
    await cq.answer('Игрок продан.'); await cq.message.answer(f'💸 {row["first_name"]} {row["last_name"]} продан за <b>€{price:,}</b>.',reply_markup=squad_keyboard([]))


@router.callback_query(F.data=='squad:transfer')
async def cb_squad_transfer(cq:CallbackQuery):
    pid=_SELECTED_SQUAD_PLAYERS.get(cq.from_user.id)
    if not pid:return await cq.answer('Сначала выберите игрока.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try: lid,ask,minimum=await list_player_for_transfer(s,c['id'],pid); await s.commit()
        except ValueError as e: await s.rollback(); return await cq.answer(str(e),show_alert=True)
    await cq.answer('Игрок выставлен на трансфер.'); await cq.message.answer(f'🔄 Игрок выставлен на трансфер. Listing #{lid}\nЦена: €{ask:,}\nМинимум: €{minimum:,}.')


@router.callback_query(F.data=='tactic:pick')
async def cb_tactic_pick(cq:CallbackQuery):
    _TACTIC_PICKS[cq.from_user.id].clear()
    c,rows=await _load_squad_rows(cq.from_user.id)
    if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
    if not rows:return await cq.answer('Состав пуст.',show_alert=True)
    await cq.message.answer('👥 <b>Выберите 11 игроков</b> для стартового состава:',reply_markup=squad_keyboard(rows,prefix='tactic'))
    await cq.answer()


@router.callback_query(F.data.startswith('tactic:filter:'))
async def cb_tactic_filter(cq:CallbackQuery):
    code=cq.data.rsplit(':',1)[1]
    if code not in {'GK','DEF','MID','ATT'}:return await cq.answer('Неизвестный фильтр.',show_alert=True)
    c,rows=await _load_squad_rows(cq.from_user.id,code)
    if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
    if not rows:return await cq.answer('В этой группе игроков нет.',show_alert=True)
    await cq.message.answer(f'👥 <b>Выберите игроков</b> · выбрано {len(_TACTIC_PICKS[cq.from_user.id])}/11:',reply_markup=squad_keyboard(rows,prefix='tactic'))
    await cq.answer()


@router.callback_query(F.data.startswith('tactic:item:'))
async def cb_tactic_item(cq:CallbackQuery):
    try:index=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    c,rows=await _load_squad_rows(cq.from_user.id)
    if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
    if index<0 or index>=len(rows):return await cq.answer('Игрок не найден.',show_alert=True)
    pid=int(rows[index]['id']); picks=_TACTIC_PICKS[cq.from_user.id]
    if pid in picks:picks.remove(pid); await cq.answer('Игрок убран из состава.')
    elif len(picks)>=11:return await cq.answer('Уже выбрано 11 игроков.',show_alert=True)
    else:picks.add(pid); await cq.answer(f'Выбрано: {len(picks)}/11')
    if len(picks)==11:
        async with SessionLocal() as s:
            try: await set_starting_lineup(s,c['id'],list(picks)); await s.commit()
            except ValueError as e: await s.rollback(); return await cq.answer(str(e),show_alert=True)
        picks.clear(); await cq.message.answer('✅ Стартовый состав сохранён.')


@router.callback_query(F.data=='visual:squad')
async def cb_visual_squad(cq:CallbackQuery):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=(await s.execute(text("SELECT p.*,cp.shirt_number,cp.fitness,cp.form,cp.is_injured FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c ORDER BY p.position,p.potential DESC,p.id"),{'c':c['id']})).mappings().all()
    data=[]
    for r in rows:
        pr=type('P',(),dict(r,suspended=False,name=f"{r['first_name']} {r['last_name']}"))()
        data.append(dict(r,overall=player_rating(pr)))
    path=squad_screen(c['name'],data)
    await cq.message.answer_photo(FSInputFile(path),caption=f'👥 <b>{c["name"]}</b> · {len(data)} игроков'); await cq.answer()

@router.message()
async def text_router(m:Message):
    name=(m.text or '').strip()
    if not 3<=len(name)<=30:return
    async with SessionLocal() as s:
        uid=await current_user(s,m); exists=await current_club(s,uid)
        if exists:return
        try:
            await create_club(s,uid,name)
            await s.commit()
        except ValueError as exc:
            await s.rollback()
            msg=str(exc)
            if msg=='User already owns a club.':
                return await m.answer('У вас уже есть клуб.')
            if msg=='Club name is already taken.':
                return await m.answer('Такое название клуба уже занято. Выберите другое.')
            return await m.answer('Название клуба должно содержать от 3 до 30 символов.')
        except Exception:
            logger.exception('Club creation failed for user_id=%s name=%r', uid, name)
            await s.rollback()
            return await m.answer('Не удалось создать клуб из-за внутренней ошибки. Ошибка записана в журнал.')
    await m.answer(f'🏟 <b>{name}</b> создан! Вы получили стартовый состав.',reply_markup=menu())

def create_bot():
    bot=Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher(); dp.include_router(router)
    live_task=None
    async def on_startup(_bot=None):
        nonlocal live_task
        from .live_worker import live_worker
        live_task=asyncio.create_task(live_worker(_bot or bot),name='football-manager-live-worker')
    async def on_shutdown(_bot=None):
        nonlocal live_task
        if live_task:
            live_task.cancel()
            try: await live_task
            except asyncio.CancelledError: pass
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    return bot,dp

@router.message(Command('dynamicmatch'))
async def cmd_dynamicmatch(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c: return await m.answer('Сначала создайте клуб.')
        row=(await s.execute(text("""SELECT m.id,m.current_minute,m.home_score,m.away_score,h.name home,a.name away,
            th.formation home_formation,ta.formation away_formation,th.pressing home_pressing,ta.pressing away_pressing
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            LEFT JOIN match_tactics th ON th.match_id=m.id AND th.club_id=m.home_club_id
            LEFT JOIN match_tactics ta ON ta.match_id=m.id AND ta.club_id=m.away_club_id
            WHERE m.home_club_id=:c OR m.away_club_id=:c ORDER BY m.id DESC LIMIT 1"""),{'c':c['id']})).mappings().first()
        events=(await s.execute(text('SELECT minute,event_type,description,metadata FROM match_events WHERE match_id=:m ORDER BY minute,id LIMIT 30'),{'m':row['id']})).mappings().all() if row else []
    if not row: return await m.answer('Матчей пока нет.')
    hp=row['home_pressing'] or 50; ap=row['away_pressing'] or 50
    phase='PRESSING' if hp-ap>=12 else ('COUNTER' if ap-hp>=12 else 'POSSESSION')
    control=max(.35,min(.65,.5+(hp-ap)/200))
    path=dynamic_match_screen(row['home'],row['away'],row['home_formation'] or '4-3-3',row['away_formation'] or '4-3-3',phase=phase,minute=row['current_minute'] or 0,home_control=control,pressure_home=hp,pressure_away=ap,events=events,home_score=row['home_score'] or 0,away_score=row['away_score'] or 0)
    await m.answer_photo(FSInputFile(path),caption=f'🎥 <b>Динамический Match Center</b> · {phase} · {row["current_minute"] or 0}′')


@router.message(Command('matchreplay'))
async def cmd_matchreplay(m:Message):
    parts=(m.text or '').split()
    try:
        requested=max(1,int(parts[1])) if len(parts)>1 else None
    except ValueError:
        return await m.answer('Использование: /matchreplay [номер кадра]')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c: return await m.answer('Сначала создайте клуб.')
        row=(await s.execute(text('''SELECT m.id,m.current_minute,m.home_score,m.away_score,
            m.home_club_id,m.away_club_id,h.name home,a.name away,
            th.formation home_formation,ta.formation away_formation
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            LEFT JOIN match_tactics th ON th.match_id=m.id AND th.club_id=m.home_club_id
            LEFT JOIN match_tactics ta ON ta.match_id=m.id AND ta.club_id=m.away_club_id
            WHERE m.home_club_id=:c OR m.away_club_id=:c
            ORDER BY CASE WHEN m.status='LIVE' THEN 0 ELSE 1 END,m.id DESC LIMIT 1'''),{'c':c['id']})).mappings().first()
        raw=(await s.execute(text('''SELECT minute,event_type,club_id,description,metadata,
            p.first_name||' '||p.last_name player,
            sp.first_name||' '||sp.last_name secondary_player
            FROM match_events e
            LEFT JOIN players p ON p.id=e.player_id
            LEFT JOIN players sp ON sp.id=e.secondary_player_id
            WHERE e.match_id=:m AND e.event_type IN ('PASS','CARRY','PRESSURE','TACKLE','INTERCEPTION','SHOT','GOAL')
            ORDER BY minute,id'''),{'m':row['id']})).mappings().all() if row else []
    if not row: return await m.answer('Матчей пока нет.')
    key_events=[dict(r) for r in raw if (r.get('metadata') or {}).get('visual_type')]
    if not key_events: return await m.answer('В этом матче ещё нет визуализируемых событий.')
    index=requested or len(key_events)
    index=min(index,len(key_events))
    event=key_events[index-1]
    event['team']='HOME' if event.get('club_id')==row['home_club_id'] else 'AWAY'
    path=event_replay_screen(row['home'],row['away'],row['home_formation'] or '4-3-3',row['away_formation'] or '4-3-3',event,index,len(key_events),row['home_score'] or 0,row['away_score'] or 0)
    await m.answer_photo(FSInputFile(path),caption=f'🎞 <b>Replay #{index}</b> · {event["minute"]}′ · {event["event_type"]}\n{event.get("description","")}')


@router.message(Command('matchmap'))
async def cmd_matchmap(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:
            return await m.answer('Сначала создайте клуб.')
        row=(await s.execute(text("""SELECT m.id,m.status,m.current_minute,m.home_score,m.away_score,h.name home,a.name away,
            th.formation home_formation,ta.formation away_formation,th.pressing home_pressing,ta.pressing away_pressing
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            LEFT JOIN match_tactics th ON th.match_id=m.id AND th.club_id=m.home_club_id
            LEFT JOIN match_tactics ta ON ta.match_id=m.id AND ta.club_id=m.away_club_id
            WHERE m.home_club_id=:c OR m.away_club_id=:c ORDER BY CASE WHEN m.status='LIVE' THEN 0 ELSE 1 END,m.id DESC LIMIT 1"""),{'c':c['id']})).mappings().first()
        events=(await s.execute(text('SELECT minute,description FROM match_events WHERE match_id=:m ORDER BY minute,id LIMIT 20'),{'m':row['id']})).mappings().all() if row else []
    if not row:
        return await m.answer('Матчей пока нет.')
    home_control=.5
    if row['home_formation']=='4-2-3-1': home_control=.53
    elif row['home_formation']=='3-5-2': home_control=.55
    phase='POSSESSION' if (row['home_pressing'] or 50) >= (row['away_pressing'] or 50) else 'TRANSITION'
    path=match_phase_screen(row['home'],row['away'],row['home_formation'] or '4-3-3',row['away_formation'] or '4-3-3',phase,home_control,row['home_pressing'] or 50,row['away_pressing'] or 50,events)
    await m.answer_photo(FSInputFile(path),caption=f'🗺 <b>Карта матча</b> · {phase} · {row["current_minute"] or 0}′')
