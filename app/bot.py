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
from .services import create_league, join_league, start_league, start_round_halftime, schedule_round, change_halftime_tactics, market_players, buy_player, sell_player, set_player_instruction, get_tactical_board, set_starting_lineup, plan_substitution, scout_opponent, next_opponent, set_counterplan, set_ticket_price, upgrade_stadium, financial_report, set_training_plan, train_squad, renew_contract, set_player_expectation, list_player_for_transfer, cancel_transfer_listing, make_transfer_offer, accept_transfer_offer, player_management_report, notification_list, notification_unread_count, mark_notifications_read, set_notification_preference, notification_preferences, build_starting_lineup
from .game.ratings import player_rating
from .game.economy import STADIUM_CAPACITY_STEP
from .game.board import FORMATION_SLOTS
from .handlers.admin import admin_router
from .visual import player_card, formation_board, match_center, club_crest, tactical_board_screen, squad_screen, league_table_screen, club_dashboard, market_screen, fixtures_screen, scouting_screen, match_phase_screen, dynamic_match_screen, event_replay_screen, finance_screen

router=Router()
logger=logging.getLogger(__name__)


def _card_player(r):
    data=dict(r)
    if data.get('overall') is None:
        attrs={k:int(data.get(k) or 0) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
        pr=type('CardPlayer',(),dict(attrs,position=str(data.get('position') or 'CM'),fitness=100,form=0,morale=70))()
        data['overall']=player_rating(pr)
    return data


class LeagueCreate(StatesGroup):
    choosing_size = State()
    entering_name = State()


class LeagueJoin(StatesGroup):
    entering_code = State()


class FinanceStates(StatesGroup):
    entering_ticket_price = State()


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

def back_keyboard(back_cb: str):
    k=InlineKeyboardBuilder()
    k.button(text='⬅️ Назад', callback_data=back_cb)
    k.button(text='🏠 Главное меню', callback_data='menu:main')
    k.adjust(2)
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
    page_size=8
    total_pages=max(1,(len(players)+page_size-1)//page_size)
    page=max(0,min(page,total_pages-1))
    k=InlineKeyboardBuilder()
    start=page*page_size
    end=min(start+page_size,len(players))
    for i in range(start,end):
        p=players[i]
        last=(p.get('last_name') or p.get('name') or 'Игрок')[:10]
        k.button(text=f'{i+1}. {last}',callback_data=f'{prefix}:item:{i}')
    for code,label in (('GK','🧤 Вратари'),('DEF','🛡 Защита'),('MID','⚙ Полузащита'),('ATT','⚡ Атака')):
        k.button(text=label,callback_data=f'{prefix}:filter:{code}')
    nav=[]
    if page>0:
        nav.append(('◀️ Назад',f'{prefix}:page:{page-1}'))
    if page+1<total_pages:
        nav.append(('Вперёд ▶️',f'{prefix}:page:{page+1}'))
    for label,callback in nav:
        k.button(text=label,callback_data=callback)
    k.button(text='⬅ Назад',callback_data='menu')
    k.adjust(2,2,2,2,len(nav) or 1,1)
    return k.as_markup()


def tactic_keyboard(players, filter_code, page=0, selected=None, page_size=8):
    selected=selected or set()
    k=InlineKeyboardBuilder()
    total_pages=max(1,(len(players)+page_size-1)//page_size)
    page=max(0,min(page,total_pages-1))
    start=page*page_size
    end=min(start+page_size,len(players))
    for r in players[start:end]:
        pid=int(r['id'])
        last=(r.get('last_name') or r.get('first_name') or 'Игрок')[:12]
        mark='✓ ' if pid in selected else ''
        k.button(text=f'{mark}{last}',callback_data=f'tactic:item:{pid}:{filter_code}:{page}')
    k.adjust(2)
    for code,label in (('ATT','⚡ Атака'),('MID','⚙ Полузащита'),('DEF','🛡 Защита'),('GK','🧤 Вратари')):
        k.button(text=('✓ ' if code==filter_code else '')+label,callback_data=f'tactic:filter:{code}:0')
    k.adjust(2,2)
    nav=[]
    if page>0: nav.append(('◀️ Назад',f'tactic:filter:{filter_code}:{page-1}'))
    if page+1<total_pages: nav.append(('Вперёд ▶️',f'tactic:filter:{filter_code}:{page+1}'))
    for label,callback in nav: k.button(text=label,callback_data=callback)
    if nav: k.adjust(len(nav))
    k.button(text='⬅ Назад',callback_data='tactics')
    k.adjust(1)
    return k.as_markup()


_TACTIC_ASSIGNMENTS=defaultdict(dict)
_TACTIC_ACTIVE_SLOT={}


def _slot_labels(formation):
    slots=[slot for slot,_,_ in FORMATION_SLOTS[formation]]
    counts=defaultdict(int)
    labels=[]
    for slot in slots:
        counts[slot]+=1
        suffix=f'₍{counts[slot]}₎' if slots.count(slot)>1 else ''
        # Telegram-friendly compact labels for duplicate positions.
        if slots.count(slot)>1:
            suffix={1:'₁',2:'₂',3:'₃',4:'₄'}[counts[slot]]
        labels.append(f'{POSITION_LABELS_RU.get(slot,slot)}{suffix}')
    return slots,labels


def _rating_player(row):
    attrs={k:int(row.get(k) or 0) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
    return type('LineupPlayer',(),dict(attrs,position=str(row.get('position') or 'CM'),fitness=int(row.get('fitness') or 100),form=int(row.get('form') or 0),morale=int(row.get('morale') or 70),suspended=False))()


def _lineup_text(rows, formation, assignments):
    slots,labels=_slot_labels(formation)
    count=len(assignments)
    lines=[f'⚽ <b>СТАРТОВЫЙ СОСТАВ ({count}/11)</b>',f'Формация: <b>{formation}</b>','']
    by_id={int(r['id']):r for r in rows}
    for idx,(slot,label) in enumerate(zip(slots,labels)):
        pid=assignments.get(idx)
        if pid is None or pid not in by_id:
            lines.append(f'{label}:  ⬜ Свободно')
            continue
        r=by_id[pid]
        pr=_rating_player(r); base=player_rating(pr); adjusted=player_rating(pr,slot)
        if r['position'] != slot:
            penalty=round((1-adjusted/max(base,1))*100)
            lines.append(f'{label}:  ⚠️ {r["first_name"]} {r["last_name"]} → {POSITION_LABELS_RU.get(slot,slot)} (OVR {base:.0f} → {adjusted:.0f}, −{penalty}%)')
        else:
            lines.append(f'{label}:  ✅ {r["first_name"]} {r["last_name"]} ({adjusted:.0f})')
    return '\n'.join(lines)


def _lineup_keyboard(formation, assignments, active_slot=None):
    slots,labels=_slot_labels(formation)
    k=InlineKeyboardBuilder()
    for idx,label in enumerate(labels):
        pid=assignments.get(idx)
        icon='✅' if pid is not None else '⬜'
        if active_slot==idx: icon='🔹'
        k.button(text=f'{icon} {label}',callback_data=f'tactic:slot:{idx}')
    k.adjust(2)
    k.button(text='♻️ Автосостав',callback_data='tactic:autosquad')
    if len(assignments)==11:
        k.button(text='✅ Сохранить',callback_data='tactic:save')
    k.button(text='⬅️ Назад',callback_data='tactics')
    k.adjust(2,2,2,2,2,2,2,2,2,2,2,2)
    return k.as_markup()


def _player_picker_keyboard(rows, slot):
    k=InlineKeyboardBuilder()
    for r in rows:
        pid=int(r['id'])
        pr=_rating_player(r); overall=player_rating(pr,slot)
        label=POSITION_LABELS_RU.get(r['position'],r['position'])
        k.button(text=f'{r["first_name"]} {r["last_name"]} · {label} · {overall:.0f}',callback_data=f'tactic:player:{pid}')
    k.adjust(1)
    k.button(text='⬜ Снять игрока',callback_data='tactic:clear')
    k.button(text='⬅️ К составу',callback_data='tactic:pick')
    return k.as_markup()


_SELECTED_SQUAD_PLAYERS={}
_TACTIC_PICKS=defaultdict(set)
_MARKET_VIEWS={}
_SCOUT_FILTERS={}

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
    style_labels={'BALANCE':'Баланс','ATTACK':'Атака','DEFENSE':'Защита','COUNTER':'Контратака','POSSESSION':'Владение'}
    for st in ('BALANCE','ATTACK','COUNTER','DEFENSE','POSSESSION'):
        k.button(text=style_labels[st], callback_data=f'tac:s:{st}')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to join league')
            return await m.answer('Не удалось вступить в лигу. Попробуйте ещё раз.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to start league')
            return await m.answer('Не удалось начать сезон. Попробуйте ещё раз.')
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
    card=player_card(_card_player(r), out=Path('assets/visual/cards')/f'player_{r["id"]}.png')
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
        rows=(await s.execute(text('''SELECT p.*,cp.club_id,cp.shirt_number,cp.fitness,cp.form,cp.is_injured
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
        rows=(await s.execute(text("SELECT c.id club_id,c.name,t.played,t.wins,t.draws,t.losses,t.points,t.goals_for,t.goals_against FROM league_teams t JOIN clubs c ON c.id=t.club_id WHERE t.league_id=:l ORDER BY t.points DESC,(t.goals_for-t.goals_against) DESC,t.goals_for DESC,c.name"),{'l':league['id']})).mappings().all()
    path=league_table_screen(league['name'],rows)
    await m.answer_photo(FSInputFile(path),caption=f'🏆 <b>{league["name"]}</b>',reply_markup=back_keyboard('menu:main'))

@router.message(Command('clubview'))
async def cmd_clubview(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
    path=club_dashboard(c)
    await m.answer_photo(FSInputFile(path),caption=f'🏟 <b>{c["name"]}</b> · профиль клуба',reply_markup=back_keyboard('menu:main'))

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
    await m.answer_photo(FSInputFile(path),caption=f'📅 <b>{league["name"]}</b> · календарь',reply_markup=back_keyboard('menu:main'))

@router.message(Command('tactics'))
async def cmd_tactics(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=await get_tactical_board(s,c['id'])
    path=tactical_board_screen(t['formation'],[dict(r) for r in rows[:11]],title=f"{c['name']} · {t['style']}")
    style_labels={'BALANCE':'Баланс','ATTACK':'Атака','COUNTER':'Контратака','DEFENSE':'Защита','POSSESSION':'Владение'}
    caption=(f'🧠 <b>ТАКТИЧЕСКАЯ ДОСКА</b>\n\nФормация: <b>{t["formation"]}</b> · стиль: <b>{style_labels.get(t["style"],t["style"])}</b>\n'
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
    style_enum=Style(style)
    tempo,pressing,width,defensive_line,aggression=STYLE_SLIDERS[style_enum]
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        await s.execute(text('''INSERT INTO club_tactics(club_id,style,tempo,pressing,width,defensive_line,aggression)
            VALUES(:c,:st,:t,:p,:w,:d,:a) ON CONFLICT(club_id) DO UPDATE SET style=EXCLUDED.style,tempo=EXCLUDED.tempo,pressing=EXCLUDED.pressing,width=EXCLUDED.width,defensive_line=EXCLUDED.defensive_line,aggression=EXCLUDED.aggression'''),
            {'c':c['id'],'st':style,'t':tempo,'p':pressing,'w':width,'d':defensive_line,'a':aggression})
        await _apply_live_tactics(s,c['id']); await s.commit()
    labels={'BALANCE':'Баланс','ATTACK':'Атака','COUNTER':'Контратака','DEFENSE':'Защита','POSSESSION':'Владение'}
    await m.answer(f'✅ Стиль изменён на {labels[style]}. Слайдеры: {tempo}/{pressing}/{width}/{defensive_line}/{aggression}.')

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
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
        except Exception:
            await s.rollback(); logger.exception('Failed to set player instruction')
            return await m.answer('❌ Не удалось сохранить инструкцию. Попробуйте ещё раз позже.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to save starting lineup')
            return await m.answer('❌ Не удалось сохранить состав. Попробуйте ещё раз позже.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to plan substitution')
            return await m.answer('❌ Не удалось запланировать замену. Попробуйте ещё раз позже.')
    await m.answer(f'🔄 Запланирована замена на {minute}′: {off} → {on}.')

@router.message(Command('matchcenter'))
async def cmd_matchcenter(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        row=(await s.execute(text('''SELECT m.id,m.league_id,m.round,m.status,m.scheduled_at,m.current_minute,m.home_score,m.away_score,m.home_xg,m.away_xg,h.name home,a.name away
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            WHERE m.home_club_id=:c OR m.away_club_id=:c ORDER BY CASE WHEN m.status='LIVE' THEN 0 ELSE 1 END,m.id DESC LIMIT 1'''),{'c':c['id']})).mappings().first()
        if row and row['status']=='SCHEDULED' and row['scheduled_at'] is not None:
            from datetime import datetime, timezone
            if row['scheduled_at'] <= datetime.now(timezone.utc):
                from .services import start_round_halftime
                try:
                    await start_round_halftime(s,row['league_id'],row['round'])
                    await s.commit()
                    row=(await s.execute(text('''SELECT m.id,m.league_id,m.round,m.status,m.scheduled_at,m.current_minute,m.home_score,m.away_score,m.home_xg,m.away_xg,h.name home,a.name away
                        FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id WHERE m.id=:m'''),{'m':row['id']})).mappings().first()
                except ValueError:
                    await s.rollback()
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
    if len(parts)>1:
        # Keep the legacy opponent-scout command working.
        async with SessionLocal() as s:
            uid=await current_user(s,m,from_user); c=await current_club(s,uid)
            if not c:return await m.answer('Сначала создайте клуб.')
            league=await active_league(s,c['id'])
            if not league:return await m.answer('Сначала вступите в активную лигу.')
            try: opponent_id=int(parts[1])
            except ValueError:return await m.answer('Использование: /scout OPPONENT_CLUB_ID')
            try: report=await scout_opponent(s,c['id'],opponent_id)
            except ValueError as e:return await m.answer(f'❌ {e}')
            except Exception:
                logger.exception('Failed to scout opponent: club_id=%s opponent_id=%s', c['id'], opponent_id)
                return await m.answer('❌ Не удалось выполнить скаутинг. Попробуйте ещё раз позже.')
        path=scouting_screen(report)
        caption=(f'🕵️ <b>{report.opponent_name}</b> · выборка {report.sample_matches}\n'
                 f'Схема: <b>{report.typical_formation}</b> · стиль: <b>{report.typical_style}</b>\n'
                 f'Средние голы: {report.avg_goals_for:.2f} — {report.avg_goals_against:.2f}\n\n'
                 + ('\n'.join('• '+x for x in report.counter_recommendations) if report.counter_recommendations else 'Недостаточно наблюдений для контрплана.'))
        return await m.answer_photo(FSInputFile(path),caption=caption,reply_markup=back_keyboard('scout'))
    uid=from_user.id if from_user is not None else m.from_user.id
    _SCOUT_FILTERS[uid]={'position':None,'age':None,'pot':None,'page':0}
    await m.answer(_scout_menu_text(uid),reply_markup=_scout_menu_keyboard(uid))


def _scout_filter_state(uid):
    return _SCOUT_FILTERS.setdefault(uid, {'position':None,'age':None,'pot':None,'page':0})


def _scout_menu_text(uid):
    f=_scout_filter_state(uid)
    pos={'GK':'Вратари','DEF':'Защита','MID':'Полузащита','ATT':'Атака'}.get(f['position'],'Любая')
    age={'16_19':'16-19','20_23':'20-23','24_27':'24-27','28_31':'28-31','32P':'32+'}.get(f['age'],'Любой')
    pot={'LT70':'<70','70_79':'70-79','80_89':'80-89','90P':'90+'}.get(f['pot'],'Любой')
    return (f'🕵️ <b>СКАУТИНГ</b>\n\n'
            f'Текущие фильтры:\n'
            f'📍 Позиция: <b>{pos}</b>\n'
            f'🎂 Возраст: <b>{age}</b>\n'
            f'⭐ POT: <b>{pot}</b>')


def _scout_menu_keyboard(uid):
    k=InlineKeyboardBuilder()
    k.button(text='📍 Позиция',callback_data='scout:position')
    k.button(text='🎂 Возраст',callback_data='scout:age')
    k.button(text='⭐ POT',callback_data='scout:pot')
    k.button(text='🔍 Найти',callback_data='scout:search')
    k.button(text='♻️ Сбросить',callback_data='scout:reset')
    k.button(text='🏠 Главное меню',callback_data='menu:main')
    k.adjust(2,2,2)
    return k.as_markup()


def _scout_position_keyboard(uid):
    value=_scout_filter_state(uid)['position']
    k=InlineKeyboardBuilder()
    for code,label in (('GK','🧤 Вратари'),('DEF','🛡 Защита'),('MID','⚙ Полузащита'),('ATT','⚡ Атака')):
        k.button(text=('✓ ' if value==code else '')+label,callback_data=f'scout:position:{code}')
    k.button(text=('✓ ' if value is None else '')+'Любая',callback_data='scout:position:any')
    k.button(text='⬅️ Назад',callback_data='scout')
    k.adjust(2,2,1,1)
    return k.as_markup()


def _scout_age_keyboard(uid):
    value=_scout_filter_state(uid)['age']
    k=InlineKeyboardBuilder()
    for code,label in (('16_19','16-19'),('20_23','20-23'),('24_27','24-27'),('28_31','28-31'),('32P','32+')):
        k.button(text=('✓ ' if value==code else '')+label,callback_data=f'scout:age:{code}')
    k.button(text=('✓ ' if value is None else '')+'Любой',callback_data='scout:age:any')
    k.button(text='⬅️ Назад',callback_data='scout')
    k.adjust(3,2,1,1)
    return k.as_markup()


def _scout_pot_keyboard(uid):
    value=_scout_filter_state(uid)['pot']
    k=InlineKeyboardBuilder()
    for code,label in (('LT70','<70'),('70_79','70-79'),('80_89','80-89'),('90P','90+')):
        k.button(text=('✓ ' if value==code else '')+label,callback_data=f'scout:pot:{code}')
    k.button(text=('✓ ' if value is None else '')+'Любой',callback_data='scout:pot:any')
    k.button(text='⬅️ Назад',callback_data='scout')
    k.adjust(2,2,1,1)
    return k.as_markup()


async def _scout_search(s, club_id, filters, page=0):
    conditions=['NOT EXISTS (SELECT 1 FROM club_players cp WHERE cp.player_id=p.id)']
    params={'offset':max(0,int(page))*8}
    if filters.get('position'):
        groups={'GK':['GK'],'DEF':['LB','CB','RB'],'MID':['DM','CM','AM'],'ATT':['LW','RW','ST']}
        positions=groups[filters['position']]
        conditions.append('p.position = ANY(:positions)')
        params['positions']=positions
    age=filters.get('age')
    if age=='16_19': conditions.append('p.age BETWEEN 16 AND 19')
    elif age=='20_23': conditions.append('p.age BETWEEN 20 AND 23')
    elif age=='24_27': conditions.append('p.age BETWEEN 24 AND 27')
    elif age=='28_31': conditions.append('p.age BETWEEN 28 AND 31')
    elif age=='32P': conditions.append('p.age >= 32')
    pot=filters.get('pot')
    if pot=='LT70': conditions.append('p.potential < 70')
    elif pot=='70_79': conditions.append('p.potential BETWEEN 70 AND 79')
    elif pot=='80_89': conditions.append('p.potential BETWEEN 80 AND 89')
    elif pot=='90P': conditions.append('p.potential >= 90')
    where=' AND '.join(conditions)
    total=int((await s.execute(text(f'SELECT count(*) FROM players p WHERE {where}'),params)).scalar_one())
    rows=(await s.execute(text(f'''SELECT p.* FROM players p WHERE {where}
        ORDER BY random() LIMIT 8 OFFSET :offset'''),params)).mappings().all()
    return total,rows


def _scout_results_keyboard(rows, total, page):
    k=InlineKeyboardBuilder()
    for i,r in enumerate(rows):
        last=(r.get('last_name') or r.get('first_name') or 'Игрок')[:16]
        k.button(text=f'{i+1}. {last}',callback_data=f'scout:item:{r["id"]}')
    k.adjust(2)
    pages=max(1,(total+7)//8)
    nav=[]
    if page>0: nav.append(('◀️ Назад',f'scout:page:{page-1}'))
    if page+1<pages: nav.append(('Вперёд ▶️',f'scout:page:{page+1}'))
    for label,cb in nav: k.button(text=label,callback_data=cb)
    if nav: k.adjust(len(nav))
    k.button(text='⬅️ Назад к фильтрам',callback_data='scout')
    k.adjust(2,2,2,2,len(nav) or 1,1)
    return k.as_markup()


def _scout_results_text(rows, total, page):
    return f'🔍 <b>Результаты скаутинга</b> · найдено: <b>{total}</b> · страница <b>{page+1}</b>'


async def _show_scout_results(target, uid, page=0):
    filters=_scout_filter_state(uid)
    async with SessionLocal() as s:
        c=await club_by_tg(s,uid)
        if not c:
            if isinstance(target,CallbackQuery): return await target.answer('Сначала создайте клуб.',show_alert=True)
            return await target.answer('Сначала создайте клуб.')
        total,rows=await _scout_search(s,c['id'],filters,page)
    if not rows:
        text_msg='Ничего не найдено.\nПопробуйте изменить фильтры.'
        markup=back_keyboard('scout')
    else:
        filters['page']=page
        text_msg=_scout_results_text(rows,total,page)
        markup=_scout_results_keyboard(rows,total,page)
    if isinstance(target,CallbackQuery):
        await target.message.edit_text(text_msg,reply_markup=markup)
        return await target.answer()
    await target.answer(text_msg,reply_markup=markup)

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
        except Exception:
            await s.rollback(); logger.exception('Failed to set counterplan')
            return await m.answer('❌ Не удалось сохранить контртактику. Попробуйте ещё раз позже.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to buy player via /buy: player_id=%s', int(parts[1]))
            return await m.answer('❌ Не удалось выполнить покупку. Попробуйте ещё раз позже.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to sell player via /sell: player_id=%s', int(parts[1]))
            return await m.answer('❌ Не удалось выполнить продажу. Попробуйте ещё раз позже.')
    await m.answer(f'✅ {row["first_name"]} {row["last_name"]} продан за €{price:,}.')

def _finance_keyboard():
    k=InlineKeyboardBuilder()
    k.button(text='💰 Изменить цену билета',callback_data='finance:ticket')
    k.button(text='🏟 Улучшить стадион',callback_data='finance:stadium')
    k.button(text='📊 История транзакций',callback_data='finance:transactions')
    k.button(text='⚖️ Зарплатная ведомость',callback_data='finance:salaries')
    k.button(text='🏠 Главное меню',callback_data='menu:main')
    k.adjust(1,1,1,1,1)
    return k.as_markup()


def _finance_stadium_keyboard():
    k=InlineKeyboardBuilder()
    k.button(text='✅ Подтвердить',callback_data='finance:stadium:confirm')
    k.button(text='⬅️ Назад',callback_data='finance')
    k.adjust(2)
    return k.as_markup()


async def _show_finance(message, uid):
    async with SessionLocal() as s:
        c=await current_club(s,uid)
        if not c:return await message.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        summary,recent=await financial_report(s,c['id'],league['id'] if league else None)
    path=finance_screen(c,summary,recent)
    lines=[f'💰 <b>ФИНАНСЫ — {c["name"]}</b>',
           f'Баланс: <b>€{c["budget"]:,}</b>',
           f'Билет: <b>€{c["ticket_price"]}</b> · Спонсор: <b>LVL {c["sponsor_level"]}</b>']
    await message.answer_photo(FSInputFile(path),caption='\n'.join(lines),reply_markup=_finance_keyboard())


@router.message(Command('finance'))
async def cmd_finance(m:Message, from_user=None):
    await _show_finance(m, from_user.id if from_user is not None else m.from_user.id)


@router.callback_query(F.data=='finance:ticket')
async def cb_finance_ticket(cq:CallbackQuery,state:FSMContext):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
    if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
    await state.set_state(FinanceStates.entering_ticket_price)
    await cq.answer()
    await cq.message.answer(f'Введите новую цену билета (текущая: €{c["ticket_price"]}):\nДопустимо €5..€120.\nОтмена — /cancel')


@router.message(FinanceStates.entering_ticket_price)
async def finance_ticket_price_input(m:Message,state:FSMContext):
    value=(m.text or '').strip()
    if not value.isdigit():
        return await m.answer('Введите целое число от €5 до €120.')
    async with SessionLocal() as s:
        c=await club_by_tg(s,m.from_user.id)
        if not c:
            await state.clear()
            return await m.answer('Сначала создайте клуб.')
        try:
            price=await set_ticket_price(s,c['id'],int(value)); await s.commit()
        except ValueError as e:
            await s.rollback(); return await m.answer(f'❌ {e}')
        except Exception:
            await s.rollback(); logger.exception('Failed to set ticket price from Finance FSM')
            return await m.answer('❌ Не удалось изменить цену билета. Попробуйте ещё раз позже.')
    await state.clear()
    await m.answer(f'✅ Цена билета обновлена: €{price}')
    await _show_finance(m,m.from_user.id)


@router.callback_query(F.data=='finance:stadium')
async def cb_finance_stadium(cq:CallbackQuery):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
    if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
    level=int(c['stadium_level'])
    if level>=20:
        return await cq.message.answer('🏟 <b>Стадион уже максимального уровня — LVL 20.</b>',reply_markup=back_keyboard('finance'))
    cost=1_000_000*level
    capacity_gain=STADIUM_CAPACITY_STEP
    next_capacity=8_000+level*STADIUM_CAPACITY_STEP
    income_gain=capacity_gain*int(c['ticket_price'])
    text_msg=(f'🏟 <b>УЛУЧШЕНИЕ СТАДИОНА</b>\n\n'
              f'Текущий уровень: <b>LVL {level}</b>\n'
              f'Стоимость: <b>€{cost:,}</b>\n'
              f'Эффект: вместимость <b>+{capacity_gain:,}</b>\n'
              f'Потенциальный доход при аншлаге: <b>+€{income_gain:,}</b> за домашний матч\n'
              f'После улучшения: <b>{next_capacity:,}</b> мест')
    await cq.answer()
    await cq.message.answer(text_msg,reply_markup=_finance_stadium_keyboard())


@router.callback_query(F.data=='finance:stadium:confirm')
async def cb_finance_stadium_confirm(cq:CallbackQuery):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try:
            level,cost,capacity=await upgrade_stadium(s,c['id']); await s.commit()
        except ValueError as e:
            await s.rollback(); return await cq.answer(str(e),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Finance stadium upgrade failed')
            return await cq.answer('❌ Не удалось выполнить операцию.', show_alert=True)
    await cq.answer('Стадион улучшен.')
    await cq.message.answer(f'✅ Стадион улучшен до <b>LVL {level}</b>.\nСтоимость: €{cost:,}\nВместимость: <b>{capacity:,}</b>.')
    await _show_finance(cq.message,cq.from_user.id)


@router.callback_query(F.data=='finance:transactions')
async def cb_finance_transactions(cq:CallbackQuery):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=(await s.execute(text('''SELECT created_at,category,amount,description FROM club_financial_transactions
            WHERE club_id=:c ORDER BY id DESC LIMIT 30'''),{'c':c['id']})).mappings().all()
    labels={'TICKETS':'Билеты','SPONSOR':'Спонсор','WAGES':'Зарплаты','STADIUM':'Стадион','TRANSFER_IN':'Продажа','TRANSFER_OUT':'Покупка','PRIZE':'Призовые','OTHER':'Прочее'}
    lines=[f'📊 <b>ИСТОРИЯ ТРАНЗАКЦИЙ — {c["name"]}</b>','']
    if not rows:
        lines.append('Транзакций пока нет.')
    else:
        for r in rows:
            dt=r['created_at']
            date=dt.strftime('%d.%m.%Y %H:%M') if hasattr(dt,'strftime') else str(dt)[:16]
            amount=int(r['amount'])
            sign='+' if amount>0 else ''
            lines.append(f'{date} · {labels.get(r["category"],r["category"])} · <b>{sign}€{amount:,}</b> · {r["description"]}')
    await cq.answer()
    await cq.message.answer('\n'.join(lines),reply_markup=back_keyboard('finance'))


@router.callback_query(F.data=='finance:salaries')
async def cb_finance_salaries(cq:CallbackQuery):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        total=int((await s.execute(text('''SELECT COALESCE(SUM(COALESCE(cp.contract_salary,p.salary)),0)
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c'''),{'c':c['id']})).scalar_one())
        rows=(await s.execute(text('''SELECT p.first_name,p.last_name,p.position,COALESCE(cp.contract_salary,p.salary) salary
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c
            ORDER BY salary DESC,p.id LIMIT 10'''),{'c':c['id']})).mappings().all()
    lines=[f'⚖️ <b>ЗАРПЛАТНАЯ ВЕДОМОСТЬ — {c["name"]}</b>','',f'Итого по составу: <b>€{total:,}</b> / период','', 'Топ-10 по зарплате:']
    for r in rows:
        lines.append(f'• {r["first_name"]} {r["last_name"]} · {r["position"]} · <b>€{int(r["salary"]):,}</b>')
    await cq.answer()
    await cq.message.answer('\n'.join(lines),reply_markup=back_keyboard('finance'))


@router.message(Command('ticketprice'))
async def cmd_ticketprice(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2 or not parts[1].isdigit():return await m.answer('Использование: /ticketprice 25\nДопустимо €5..€120.')
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: price=await set_ticket_price(s,c['id'],int(parts[1])); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
        except Exception:
            await s.rollback(); logger.exception('Failed to set ticket price via /ticketprice')
            return await m.answer('❌ Не удалось изменить цену билета. Попробуйте ещё раз позже.')
    await m.answer(f'🎟 Цена билета установлена: <b>€{price}</b>. Более высокая цена повышает доход с места, но снижает спрос.')


@router.message(Command('upgradestadium'))
async def cmd_upgradestadium(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        try: level,cost,capacity=await upgrade_stadium(s,c['id']); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
        except Exception:
            await s.rollback(); logger.exception('Failed to upgrade stadium via /upgradestadium')
            return await m.answer('❌ Не удалось улучшить стадион. Попробуйте ещё раз позже.')
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
    if changes and changes[0][0]=='COOLDOWN':
        remaining=max(0,int(changes[0][1]))
        hours,rem=divmod(remaining,3600)
        minutes=rem//60
        await c.answer()
        await c.message.answer(
            f'⏱️ Следующая тренировка доступна через: <b>{hours:02d}ч {minutes:02d}м</b>',
            reply_markup=training_keyboard(focus,intensity)
        )
        return
    progressed=sum(1 for _,pts,changed in changes if changed)
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
        except Exception:
            await s.rollback(); logger.exception('Failed to run training via /training')
            return await m.answer('❌ Не удалось провести тренировку. Попробуйте ещё раз позже.')
    progressed=sum(1 for _,_,upd in changes if upd); points=sum(pts for _,pts,_ in changes)
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
        except Exception:
            await s.rollback(); logger.exception('Failed to renew contract via /contract: player_id=%s', int(parts[1]))
            return await m.answer('❌ Не удалось обновить контракт. Попробуйте ещё раз позже.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to set player expectation: player_id=%s', int(parts[1]))
            return await m.answer('❌ Не удалось изменить ожидание. Попробуйте ещё раз позже.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to list player for transfer: player_id=%s', int(parts[1]))
            return await m.answer('❌ Не удалось выставить игрока на рынок. Попробуйте ещё раз позже.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to make transfer offer: player_id=%s', int(parts[1]))
            return await m.answer('❌ Не удалось отправить предложение. Попробуйте ещё раз позже.')
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
        except Exception:
            await s.rollback(); logger.exception('Failed to accept transfer offer: offer_id=%s', int(parts[1]))
            return await m.answer('❌ Не удалось завершить трансфер. Попробуйте ещё раз позже.')
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
        kickoff=(r['scheduled_at'].strftime('%d.%m %H:%M UTC') if r['scheduled_at'] else 'дата не назначена')
        out.append(f'R{r["round"]} · {kickoff} · {r["home"]} — {r["away"]} · {score} · {r["status"]}')
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

@router.callback_query(F.data=='menu:main')
async def cb_menu_main(c:CallbackQuery):
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
    card=player_card(_card_player(r),out=Path('assets/visual/cards')/f'market_player_{r["id"]}.png')
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
    try:pid=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try:
            player,price=await buy_player(s,c['id'],pid); await s.commit()
        except ValueError as e:
            await s.rollback(); return await cq.answer(str(e),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Market buy failed: player_id=%s', pid)
            return await cq.answer('❌ Не удалось выполнить операцию.', show_alert=True)
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
    card=player_card(_card_player(r),out=Path('assets/visual/cards')/f'contract_player_{r["id"]}.png')
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
        except Exception:
            await s.rollback(); logger.exception('Contract renewal callback failed: player_id=%s', pid)
            return await cq.answer('❌ Не удалось обновить контракт.', show_alert=True)
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
        except Exception:
            await s.rollback(); logger.exception('Contract role update callback failed: player_id=%s', pid)
            return await cq.answer('❌ Не удалось обновить роль.', show_alert=True)
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
    card=player_card(_card_player(row),out=Path('assets/visual/cards')/f'contract_player_{row["id"]}.png')
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
async def cb_scout(c:CallbackQuery):
    _scout_filter_state(c.from_user.id)['page']=0
    await c.answer()
    await c.message.answer(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))


@router.callback_query(F.data=='finance')
async def cb_finance(c:CallbackQuery):
    await c.answer()
    await _show_finance(c.message,c.from_user.id)


@router.callback_query(F.data=='scout:position')
async def cb_scout_position(c:CallbackQuery):
    await c.answer()
    await c.message.edit_text('📍 <b>Позиция</b>\nВыберите линию:',reply_markup=_scout_position_keyboard(c.from_user.id))


@router.callback_query(F.data.startswith('scout:position:'))
async def cb_scout_position_pick(c:CallbackQuery):
    code=c.data.rsplit(':',1)[1]
    if code not in {'GK','DEF','MID','ATT','any'}: return await c.answer('Неизвестная позиция.',show_alert=True)
    _scout_filter_state(c.from_user.id)['position']=None if code=='any' else code
    await c.answer()
    await c.message.edit_text(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))


@router.callback_query(F.data=='scout:age')
async def cb_scout_age(c:CallbackQuery):
    await c.answer()
    await c.message.edit_text('🎂 <b>Возраст</b>\nВыберите диапазон:',reply_markup=_scout_age_keyboard(c.from_user.id))


@router.callback_query(F.data.startswith('scout:age:'))
async def cb_scout_age_pick(c:CallbackQuery):
    code=c.data.rsplit(':',1)[1]
    if code not in {'16_19','20_23','24_27','28_31','32P','any'}: return await c.answer('Неизвестный диапазон.',show_alert=True)
    _scout_filter_state(c.from_user.id)['age']=None if code=='any' else code
    await c.answer()
    await c.message.edit_text(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))


@router.callback_query(F.data=='scout:pot')
async def cb_scout_pot(c:CallbackQuery):
    await c.answer()
    await c.message.edit_text('⭐ <b>POT</b>\nВыберите диапазон:',reply_markup=_scout_pot_keyboard(c.from_user.id))


@router.callback_query(F.data.startswith('scout:pot:'))
async def cb_scout_pot_pick(c:CallbackQuery):
    code=c.data.rsplit(':',1)[1]
    if code not in {'LT70','70_79','80_89','90P','any'}: return await c.answer('Неизвестный диапазон.',show_alert=True)
    _scout_filter_state(c.from_user.id)['pot']=None if code=='any' else code
    await c.answer()
    await c.message.edit_text(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))


@router.callback_query(F.data=='scout:search')
async def cb_scout_search(c:CallbackQuery):
    _scout_filter_state(c.from_user.id)['page']=0
    await _show_scout_results(c,c.from_user.id,0)


@router.callback_query(F.data.startswith('scout:search:'))
async def cb_scout_search_page(c:CallbackQuery):
    try: page=int(c.data.rsplit(':',1)[1])
    except ValueError: page=0
    await _show_scout_results(c,c.from_user.id,max(0,page))


@router.callback_query(F.data.startswith('scout:page:'))
async def cb_scout_page(c:CallbackQuery):
    try: page=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer('Некорректная страница.',show_alert=True)
    await _show_scout_results(c,c.from_user.id,max(0,page))


@router.callback_query(F.data=='scout:reset')
async def cb_scout_reset(c:CallbackQuery):
    _SCOUT_FILTERS[c.from_user.id]={'position':None,'age':None,'pot':None,'page':0}
    await c.answer('Фильтры сброшены.')
    await c.message.edit_text(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))


@router.callback_query(F.data.startswith('scout:item:'))
async def cb_scout_item(c:CallbackQuery):
    try: pid=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:return await c.answer('Сначала создайте клуб.',show_alert=True)
        row=(await s.execute(text('''SELECT p.* FROM players p
            WHERE p.id=:p AND NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.player_id=p.id)'''),{'p':pid})).mappings().first()
    if not row:return await c.answer('Игрок уже недоступен для покупки.',show_alert=True)
    data=_card_player(row)
    card=player_card(data,out=Path('assets/visual/cards')/f'scout_player_{pid}.png')
    caption=(f'🃏 <b>{row["first_name"]} {row["last_name"]}</b>\n'
             f'{row["position"]} · {row.get("nationality") or "—"} · рейтинг <b>{data.get("overall","—")}</b>\n'
             f'⚡ PAC {row["pace"]} · SHO {row["shooting"]} · PAS {row["passing"]} · DRI {row["dribbling"]}\n'
             f'🛡 DEF {row["defending"]} · PHY {row["physical"]} · STA {row["stamina"]} · MENT {row["mental"]}\n'
             f'🎯 POT {row["potential"]} · €{int(row["market_value"]):,}')
    k=InlineKeyboardBuilder()
    k.button(text=f'💰 Купить за €{int(row["market_value"]):,}',callback_data=f'scout:buy:{pid}')
    k.button(text='⬅️ Назад к поиску',callback_data=f'scout:search:{_scout_filter_state(c.from_user.id).get("page",0)}')
    k.adjust(1)
    await c.message.answer_photo(FSInputFile(card),caption=caption,reply_markup=k.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith('scout:buy:'))
async def cb_scout_buy(c:CallbackQuery):
    try: pid=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:return await c.answer('Сначала создайте клуб.',show_alert=True)
        try:
            player,price=await buy_player(s,club['id'],pid); await s.commit()
        except ValueError as e:
            await s.rollback(); return await c.answer(str(e),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Scout buy failed: player_id=%s', pid)
            return await c.answer('❌ Не удалось выполнить операцию.', show_alert=True)
    await c.answer()
    await c.message.answer(f'✅ Куплен {player["first_name"]} {player["last_name"]} за €{price:,}.')
    await c.message.answer(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))
def league_menu_keyboard():
    k=InlineKeyboardBuilder()
    k.button(text='➕ Создать лигу', callback_data='league:create')
    k.button(text='🔗 Вступить в лигу', callback_data='league:join')
    k.button(text='▶️ Начать сезон', callback_data='league:start')
    k.button(text='⚽ Сыграть тур', callback_data='league:play')
    k.button(text='📊 Таблица', callback_data='league:table')
    k.button(text='📅 Календарь', callback_data='league:fixtures')
    k.button(text='ℹ️ Код лиги', callback_data='league:info')
    k.button(text='⬅ Назад', callback_data='menu')
    k.adjust(2,2,2,2,1)
    return k.as_markup()


@router.callback_query(F.data=='league')
async def cb_league(c:CallbackQuery):
    await c.answer()
    await c.message.answer(
        '🏆 <b>СОРЕВНОВАНИЕ</b>\n\nВыберите действие:',
        reply_markup=league_menu_keyboard()
    )


@router.callback_query(F.data=='league:menu')
async def cb_league_menu(c:CallbackQuery):
    await c.answer()
    await c.message.answer(
        '🏆 <b>СОРЕВНОВАНИЕ</b>\n\nВыберите действие:',
        reply_markup=league_menu_keyboard()
    )


@router.callback_query(F.data=='league:info')
async def cb_league_info(c:CallbackQuery):
    await c.answer()
    async with SessionLocal() as s:
        uid=await current_user(s,c.message,from_user=c.from_user)
        club=await current_club(s,uid)
        if not club:
            k=InlineKeyboardBuilder()
            k.button(text='➕ Создать лигу',callback_data='league:create')
            k.button(text='🔗 Вступить в лигу',callback_data='league:join')
            k.button(text='⬅ Назад',callback_data='menu')
            k.adjust(2,1)
            return await c.message.answer('Сначала создайте клуб.',reply_markup=k.as_markup())
        league=(await s.execute(text("""SELECT l.id,l.name,l.invite_code,l.status,l.current_round,l.max_teams,
                    l.creator_user_id,COUNT(t.club_id) AS team_count
                FROM leagues l JOIN league_teams t ON t.league_id=l.id
                WHERE t.club_id=:c
                GROUP BY l.id
                ORDER BY CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'WAITING' THEN 1 ELSE 2 END,l.id DESC
                LIMIT 1"""),{'c':club['id']})).mappings().first()
        if not league:
            k=InlineKeyboardBuilder()
            k.button(text='➕ Создать лигу',callback_data='league:create')
            k.button(text='🔗 Вступить в лигу',callback_data='league:join')
            k.button(text='⬅ Назад',callback_data='menu')
            k.adjust(2,1)
            return await c.message.answer('🏆 У вас пока нет лиги.',reply_markup=k.as_markup())
        status_label={'WAITING':'Ожидание','ACTIVE':'Активна','FINISHED':'Завершена'}.get(league['status'],league['status'])
        lines=[
            f'🏆 <b>{league["name"]}</b>',
            f'Код приглашения: <code>{league["invite_code"]}</code>',
            f'Клубы: <b>{league["team_count"]} / {league["max_teams"]}</b>',
            f'Статус: <b>{status_label}</b>',
        ]
        if league['status']=='ACTIVE':
            lines.append(f'Текущий тур: <b>{league["current_round"]}</b>')
        lines.append(f'\nОтправьте код друзьям: <code>/join {league["invite_code"]}</code>')
        k=InlineKeyboardBuilder()
        k.button(text='🔄 Обновить',callback_data='league:info:refresh')
        if league['status']=='WAITING' and league['creator_user_id']==uid:
            k.button(text='❌ Удалить лигу',callback_data='league:delete')
        k.button(text='⬅ Назад',callback_data='menu')
        k.adjust(2,1)
        await c.message.answer('\n'.join(lines),reply_markup=back_keyboard('league:menu'))


@router.callback_query(F.data=='league:info:refresh')
async def cb_league_info_refresh(c:CallbackQuery):
    await cb_league_info(c)


@router.callback_query(F.data=='league:delete')
async def cb_league_delete(c:CallbackQuery):
    await c.answer()
    async with SessionLocal() as s:
        uid=await current_user(s,c.message,from_user=c.from_user)
        club=await current_club(s,uid)
        if not club:
            return await c.message.answer('Лига недоступна.',reply_markup=league_menu_keyboard())
        league=(await s.execute(text("""SELECT l.id,l.name,l.status,l.creator_user_id
            FROM leagues l JOIN league_teams t ON t.league_id=l.id
            WHERE t.club_id=:c
            ORDER BY CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'WAITING' THEN 1 ELSE 2 END,l.id DESC
            LIMIT 1"""),{'c':club['id']})).mappings().first()
        if not league or league['creator_user_id']!=uid or league['status']!='WAITING':
            return await c.message.answer('Удалить можно только свою лигу в статусе WAITING.',reply_markup=league_menu_keyboard())
    k=InlineKeyboardBuilder()
    k.button(text='✅ Да, удалить',callback_data='league:delete:confirm')
    k.button(text='❌ Отмена',callback_data='league')
    k.adjust(2)
    await c.message.answer(f'Удалить лигу <b>{league["name"]}</b>?',reply_markup=k.as_markup())


@router.callback_query(F.data=='league:delete:confirm')
async def cb_league_delete_confirm(c:CallbackQuery):
    await c.answer()
    async with SessionLocal() as s:
        uid=await current_user(s,c.message,from_user=c.from_user)
        club=await current_club(s,uid)
        if not club:
            return await c.message.answer('Лига недоступна.',reply_markup=league_menu_keyboard())
        league=(await s.execute(text("""SELECT l.id,l.name,l.status,l.creator_user_id
            FROM leagues l JOIN league_teams t ON t.league_id=l.id
            WHERE t.club_id=:c
            ORDER BY CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'WAITING' THEN 1 ELSE 2 END,l.id DESC
            LIMIT 1"""),{'c':club['id']})).mappings().first()
        if not league or league['creator_user_id']!=uid or league['status']!='WAITING':
            return await c.message.answer('Удаление отменено: лига уже недоступна для удаления.',reply_markup=league_menu_keyboard())
        await s.execute(text('DELETE FROM league_teams WHERE league_id=:l'),{'l':league['id']})
        await s.execute(text('DELETE FROM leagues WHERE id=:l'),{'l':league['id']})
        await s.commit()
    k=InlineKeyboardBuilder()
    k.button(text='⬅ Назад',callback_data='menu')
    await c.message.answer(f'🗑 Лига <b>{league["name"]}</b> удалена.',reply_markup=k.as_markup())


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
            try:
                style=Style(st)
            except ValueError:
                return await cq.answer('Неизвестный стиль.',show_alert=True)
            tempo,pressing,width,defensive_line,aggression=STYLE_SLIDERS[style]
            await s.execute(text('''INSERT INTO club_tactics(club_id,style,tempo,pressing,width,defensive_line,aggression)
                VALUES(:c,:st,:t,:p,:w,:d,:a)
                ON CONFLICT(club_id) DO UPDATE SET style=EXCLUDED.style,tempo=EXCLUDED.tempo,pressing=EXCLUDED.pressing,width=EXCLUDED.width,defensive_line=EXCLUDED.defensive_line,aggression=EXCLUDED.aggression'''),
                {'c':c['id'],'st':style.value,'t':tempo,'p':pressing,'w':width,'d':defensive_line,'a':aggression})
            await _apply_live_tactics(s,c['id'])
        await s.commit()
        t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=await get_tactical_board(s,c['id'])
    path=tactical_board_screen(t['formation'],[dict(r) for r in rows[:11]],title=f"{c['name']} · {t['style']}")
    style_labels={'BALANCE':'Баланс','ATTACK':'Атака','COUNTER':'Контратака','DEFENSE':'Защита','POSSESSION':'Владение'}
    caption=(f'🧠 <b>{c["name"]}</b>\n{t["formation"]} · {style_labels.get(t["style"],t["style"])}\n\n'
             f'Темп {t["tempo"]} · прессинг {t["pressing"]} · ширина {t["width"]} · линия {t["defensive_line"]} · агрессия {t["aggression"]}')
    try:
        await cq.message.edit_media(InputMediaPhoto(media=FSInputFile(path),caption=caption),reply_markup=tactics_keyboard(t['formation']))
    except Exception:
        await cq.message.answer_photo(FSInputFile(path),caption=caption,reply_markup=tactics_keyboard(t['formation']))
    await cq.answer('Тактика обновлена.')

@router.callback_query(F.data=='squad')
async def cb_squad_screen(cq:CallbackQuery):
    _SQUAD_FILTERS.pop(cq.from_user.id,None)
    await cq.answer()
    await _show_squad(cq.message,cq.from_user.id)


async def _load_squad_rows(telegram_id, filter_code=None):
    async with SessionLocal() as s:
        c=await club_by_tg(s,telegram_id)
        if not c:return None,None
        rows=(await s.execute(text("""SELECT p.*,cp.club_id,cp.shirt_number,cp.fitness,cp.form,cp.is_injured
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


_SQUAD_FILTERS={}

@router.callback_query(F.data.startswith('squad:page:'))
async def cb_squad_page(cq:CallbackQuery):
    try:
        page=int(cq.data.rsplit(':',1)[1])
    except ValueError:
        return await cq.answer('Некорректная страница.',show_alert=True)
    filter_code=_SQUAD_FILTERS.get(cq.from_user.id)
    c,rows=await _load_squad_rows(cq.from_user.id,filter_code)
    if not c:
        return await cq.answer('Сначала создайте клуб.',show_alert=True)
    if not rows:
        return await cq.answer('Состав пуст.',show_alert=True)
    await cq.message.edit_reply_markup(reply_markup=squad_keyboard(rows,page))
    await cq.answer()


@router.callback_query(F.data.startswith('squad:filter:'))
async def cb_squad_filter(cq:CallbackQuery):
    code=cq.data.rsplit(':',1)[1]
    if code not in {'GK','DEF','MID','ATT'}:return await cq.answer('Неизвестный фильтр.',show_alert=True)
    _SQUAD_FILTERS[cq.from_user.id]=code
    await cq.answer(); await _show_squad(cq.message,cq.from_user.id,code,0)


@router.callback_query(F.data=='squad:contract')
async def cb_squad_contract(cq:CallbackQuery):
    pid=_SELECTED_SQUAD_PLAYERS.get(cq.from_user.id)
    if not pid:return await cq.answer('Сначала выберите игрока.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try: offer=await renew_contract(s,c['id'],pid); await s.commit()
        except ValueError as e: await s.rollback(); return await cq.answer(str(e),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Squad contract renewal failed: player_id=%s', pid)
            return await cq.answer('❌ Не удалось обновить контракт.',show_alert=True)
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
        except Exception:
            await s.rollback(); logger.exception('Squad sell failed: player_id=%s', pid)
            return await cq.answer('❌ Не удалось выполнить операцию.',show_alert=True)
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
        except Exception:
            await s.rollback(); logger.exception('Squad transfer listing failed: player_id=%s', pid)
            return await cq.answer('❌ Не удалось выполнить операцию.',show_alert=True)
    await cq.answer('Игрок выставлен на трансфер.'); await cq.message.answer(f'🔄 Игрок выставлен на трансфер. Listing #{lid}\nЦена: €{ask:,}\nМинимум: €{minimum:,}.')


@router.callback_query(F.data=='tactic:pick')
async def cb_tactic_pick(cq:CallbackQuery):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:
            return await cq.answer('Сначала создайте клуб.',show_alert=True)
        t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=(await s.execute(text('''SELECT p.*,cp.fitness,cp.form,cp.morale,cp.is_injured,cp.suspension_matches
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c
            ORDER BY p.position,p.potential DESC,p.id'''),{'c':c['id']})).mappings().all()
        saved=(await s.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'),{'c':c['id']})).scalar_one_or_none()
    assignments={i:int(pid) for i,pid in enumerate(saved or []) if i<11 and any(int(r['id'])==int(pid) for r in rows)}
    _TACTIC_ASSIGNMENTS[cq.from_user.id]=assignments
    await cq.message.answer(_lineup_text(rows,t['formation'],assignments),reply_markup=_lineup_keyboard(t['formation'],assignments))
    await cq.answer()


@router.callback_query(F.data.startswith('tactic:slot:'))
async def cb_tactic_slot(cq:CallbackQuery):
    try: slot_index=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректная позиция.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        t=(await s.execute(text('SELECT formation FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=(await s.execute(text('''SELECT p.*,cp.fitness,cp.form,cp.morale FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND cp.is_injured=false AND cp.suspension_matches=0 ORDER BY p.position,p.potential DESC,p.id'''),{'c':c['id']})).mappings().all()
    slots,labels=_slot_labels(t['formation'])
    if not 0<=slot_index<len(slots): return await cq.answer('Позиция не найдена.',show_alert=True)
    _TACTIC_ACTIVE_SLOT[cq.from_user.id]=slot_index
    assignments=_TACTIC_ASSIGNMENTS.setdefault(cq.from_user.id,{})
    await cq.message.edit_text(f'⚽ <b>{labels[slot_index]}</b> — выберите игрока',reply_markup=_player_picker_keyboard(rows,slots[slot_index]))
    await cq.answer()


@router.callback_query(F.data.startswith('tactic:player:'))
async def cb_tactic_player(cq:CallbackQuery):
    try: pid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный игрок.',show_alert=True)
    uid=cq.from_user.id; slot_index=_TACTIC_ACTIVE_SLOT.get(uid)
    if slot_index is None: return await cq.answer('Сначала выберите позицию.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,uid)
        if not c: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        t=(await s.execute(text('SELECT formation FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        row=(await s.execute(text('''SELECT p.*,cp.fitness,cp.form,cp.morale FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND p.id=:p AND cp.is_injured=false AND cp.suspension_matches=0'''),{'c':c['id'],'p':pid})).mappings().first()
        rows=(await s.execute(text('SELECT p.*,cp.fitness,cp.form,cp.morale FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.is_injured=false AND cp.suspension_matches=0 ORDER BY p.position,p.potential DESC,p.id'),{'c':c['id']})).mappings().all()
    if not row: return await cq.answer('Игрок недоступен.',show_alert=True)
    assignments=_TACTIC_ASSIGNMENTS.setdefault(uid,{})
    for idx,assigned_pid in list(assignments.items()):
        if assigned_pid==pid and idx!=slot_index: assignments.pop(idx)
    assignments[slot_index]=pid
    _TACTIC_ACTIVE_SLOT.pop(uid,None)
    await cq.message.edit_text(_lineup_text(rows,t['formation'],assignments),reply_markup=_lineup_keyboard(t['formation'],assignments))
    await cq.answer('Игрок назначен.')


@router.callback_query(F.data=='tactic:clear')
async def cb_tactic_clear(cq:CallbackQuery):
    uid=cq.from_user.id; slot_index=_TACTIC_ACTIVE_SLOT.get(uid)
    if slot_index is None: return await cq.answer('Сначала выберите позицию.',show_alert=True)
    _TACTIC_ASSIGNMENTS.setdefault(uid,{}).pop(slot_index,None); _TACTIC_ACTIVE_SLOT.pop(uid,None)
    await cb_tactic_pick(cq)


@router.callback_query(F.data=='tactic:autosquad')
async def cb_tactic_autosquad(cq:CallbackQuery):
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        t=(await s.execute(text('SELECT formation FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=(await s.execute(text('''SELECT p.*,cp.fitness,cp.form,cp.morale,cp.is_injured,cp.suspension_matches FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND cp.is_injured=false AND cp.suspension_matches=0 ORDER BY p.position,p.potential DESC,p.id'''),{'c':c['id']})).mappings().all()
    slots,_= _slot_labels(t['formation'])
    try: lineup=build_starting_lineup(rows,slots)
    except ValueError as e: return await cq.answer(str(e),show_alert=True)
    assignments={i:int(r['id']) for i,r in enumerate(lineup)}
    _TACTIC_ASSIGNMENTS[cq.from_user.id]=assignments
    await cq.message.edit_text(_lineup_text(rows,t['formation'],assignments),reply_markup=_lineup_keyboard(t['formation'],assignments))
    await cq.answer('Автосостав готов.')


@router.callback_query(F.data=='tactic:save')
async def cb_tactic_save(cq:CallbackQuery):
    uid=cq.from_user.id; assignments=_TACTIC_ASSIGNMENTS.get(uid,{})
    if len(assignments)!=11: return await cq.answer('Нужно заполнить 11/11.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,uid)
        if not c: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try:
            await set_starting_lineup(s,c['id'],[assignments[i] for i in range(11)]); await s.commit()
        except ValueError as e:
            await s.rollback(); return await cq.answer(str(e),show_alert=True)
    await cq.answer('Состав сохранён.')
    await cq.message.answer('✅ Стартовый состав сохранён.',reply_markup=tactics_keyboard((await _club_formation(cq.from_user.id))))


async def _club_formation(telegram_id):
    async with SessionLocal() as s:
        c=await club_by_tg(s,telegram_id)
        if not c: return '4-3-3'
        row=(await s.execute(text('SELECT formation FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        return row['formation'] if row else '4-3-3'


@router.callback_query(F.data.startswith('tactic:filter:'))
async def cb_tactic_filter(cq:CallbackQuery):
    parts=cq.data.split(':')
    code=parts[2] if len(parts)>2 else ''
    try: page=int(parts[3]) if len(parts)>3 else 0
    except ValueError: return await cq.answer('Некорректная страница.',show_alert=True)
    if code not in {'GK','DEF','MID','ATT'}:return await cq.answer('Неизвестный фильтр.',show_alert=True)
    c,rows=await _load_squad_rows(cq.from_user.id,code)
    if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
    if not rows:return await cq.answer('В этой группе игроков нет.',show_alert=True)
    names={'ATT':'Атака','MID':'Полузащита','DEF':'Защита','GK':'Вратари'}
    await cq.message.edit_text(f'👥 <b>Игроки · {names[code]}</b>',reply_markup=tactic_keyboard(rows,code,page,_TACTIC_PICKS[cq.from_user.id]))
    await cq.answer()


@router.callback_query(F.data.startswith('tactic:item:'))
async def cb_tactic_item(cq:CallbackQuery):
    parts=cq.data.split(':')
    try: pid=int(parts[2]); code=parts[3]; page=int(parts[4])
    except (ValueError,IndexError):return await cq.answer('Некорректный игрок.',show_alert=True)
    if code not in {'GK','DEF','MID','ATT'}:return await cq.answer('Неизвестная линия.',show_alert=True)
    c,rows=await _load_squad_rows(cq.from_user.id,code)
    if not c:
        return await cq.answer('Сначала создайте клуб.',show_alert=True)
    page_size=8
    page=max(0,page)
    page_rows=rows[page*page_size:(page+1)*page_size]
    if not any(int(r['id'])==pid for r in page_rows):
        return await cq.answer('Устаревшая кнопка. Откройте Тактику заново.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        row=(await s.execute(text("""SELECT p.*,cp.shirt_number,cp.fitness,cp.form,cp.is_injured
            FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND p.id=:pid"""),{'c':c['id'],'pid':pid})).mappings().first()
    if not row:return await cq.answer('Игрок не найден в вашем составе.',show_alert=True)
    picks=_TACTIC_PICKS[cq.from_user.id]
    if pid in picks:
        picks.remove(pid); message='Игрок убран из состава.'
    elif len(picks)>=11:
        return await cq.answer('Уже выбрано 11 игроков.',show_alert=True)
    else:
        picks.add(pid); message=f'Выбрано: {len(picks)}/11'
    _,rows=await _load_squad_rows(cq.from_user.id,code)
    await cq.message.edit_reply_markup(reply_markup=tactic_keyboard(rows,code,page,picks))
    await cq.answer(message)
    if len(picks)==11:
        async with SessionLocal() as s:
            try: await set_starting_lineup(s,c['id'],list(picks)); await s.commit()
            except ValueError as e: await s.rollback(); return await cq.answer(str(e),show_alert=True)
            except Exception:
                await s.rollback(); logger.exception('Tactic starting lineup save failed')
                return await cq.answer('❌ Не удалось сохранить состав.', show_alert=True)
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
    bot=Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher(); dp.include_router(admin_router); dp.include_router(router)
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
from app.game.positions import POSITION_LABELS_RU
