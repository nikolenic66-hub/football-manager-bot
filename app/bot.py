import asyncio
import time
import logging
import re
import json
import html
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from aiogram import BaseMiddleware
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, FSInputFile, InputMediaPhoto, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from sqlalchemy import text
from pathlib import Path
from .config import settings
from .db import SessionLocal
from .repositories import get_or_create_user, create_club
from app.game.best_xi import get_best_xi
from app.game.ai_squad import analyze_squad, build_fallback_analysis
from app.game.ai_best_xi import suggest_best_xi
from app.game.ai_coach import suggest_tactics, get_cached_tactics_recommendation
from app.game.infrastructure import upgrade_facility
from app.game.staff import upgrade_staff, staff_bonus, scouting_discovery_chance
from app.game.inbox import get_messages as inbox_get_messages, mark_read as inbox_mark_read, unread_count as inbox_unread_count
from app.game.weather import generate_weather, weather_label
from app.game.engine import sanitize_event_text

from .services import create_league, join_league, start_league, start_round_halftime, schedule_round, change_halftime_tactics, market_players, buy_player, sell_player, set_player_instruction, get_tactical_board, set_starting_lineup, plan_substitution, scout_opponent, next_opponent, set_counterplan, set_ticket_price, upgrade_stadium, financial_report, set_training_plan, train_squad, renew_contract, set_player_expectation, list_player_for_transfer, cancel_transfer_listing, make_transfer_offer, accept_transfer_offer, player_management_report, notification_list, notification_unread_count, mark_notifications_read, set_notification_preference, notification_preferences, build_starting_lineup, clean_display_name, display_player_name, training_history, training_details, _live_score_at_minute, _starting_players, _club_tactics, _club_board, record_financial_transaction
from .game.ratings import player_rating
from .game.tactics import Tactics, Style, STYLE_SLIDERS, ALLOWED_FORMATIONS, apply_formation_sliders
from .game.economy import STADIUM_CAPACITY_STEP
from .game.transfer_interest import calculate_interest
from .game.player_history import get_history, format_history, get_development_trend, format_trend
from .game.player_comparison import compare_players
from .game.shortlist import add_to_shortlist, remove_from_shortlist, get_shortlist
from app.game.packs import list_packs, open_pack, send_stars_invoice
from app.game.battle_pass import REWARDS, add_battle_pass_points, get_active_season, ensure_progress, claim_battle_pass_reward, get_season_rewards
from app.game.daily_quests import ensure_daily_quests, list_daily_quests, claim_quest, record_tactic_change
from app.game.inventory import list_inventory, item_label, use_inventory_item, add_inventory_item, USABLE_ITEMS, team_item_status, use_inventory_item_for_team
from app.game.talents import talent_label
from .game.preview import build_preview, build_post_match_analysis
from .game.board import FORMATION_SLOTS
from .handlers.admin import admin_router
from .visual import player_card, formation_board, match_center, club_crest, tactical_board_screen, squad_screen, league_table_screen, club_dashboard, market_screen, fixtures_screen, scouting_screen, match_phase_screen, dynamic_match_screen, event_replay_screen, finance_screen

router=Router()
logger=logging.getLogger(__name__)


def _user_timezone(source=None):
    for candidate in (getattr(source,'tzinfo',None), getattr(getattr(source,'from_user',None),'tzinfo',None)):
        if candidate is not None:
            return candidate
    return timezone(timedelta(hours=3))


def _local_datetime(value, source=None):
    if value is None: return None
    if not hasattr(value,'astimezone'): return value
    if value.tzinfo is None: value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(_user_timezone(source))


def _card_player(r):
    data=dict(r)
    if data.get('overall') is None:
        attrs={k:int(data.get(k) or 0) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
        pr=type('CardPlayer',(),dict(attrs,position=str(data.get('position') or 'CM'),fitness=100,form=0,morale=70))()
        data['overall']=int(round(float(player_rating(pr))))
    else:
        try:
            data['overall']=int(round(float(data['overall'])))
        except (TypeError,ValueError):
            data['overall']=None
    return data


class LeagueCreate(StatesGroup):
    choosing_size = State()
    entering_name = State()


class LeagueJoin(StatesGroup):
    entering_code = State()


class FinanceStates(StatesGroup):
    entering_ticket_price = State()


class ScoutStates(StatesGroup):
    entering_filter = State()


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, cooldown=0.75, ttl=3600):
        self.cooldown=cooldown
        self.ttl=ttl
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
            for stale_key,stamp in list(self._last.items()):
                if now-stamp>=self.ttl: self._last.pop(stale_key,None)
            if now-self._last[key] < self.cooldown:
                return None
            self._last[key]=now
        return await handler(event,data)


router.message.middleware(RateLimitMiddleware())


def menu(unread_count=0, user_id=None):
    """Minimal launcher menu; all game navigation lives in the Mini App."""
    k=InlineKeyboardBuilder()
    k.button(
        text='🎮 Начать играть',
        web_app=WebAppInfo(url='https://manager-nikmarcenko.mia0.amvera.tech'),
    )
    if user_id is not None and settings.admin_id is not None and int(user_id) == int(settings.admin_id):
        k.button(text='🛠 Админка', callback_data='admin:menu')
    k.adjust(1)
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


def fitness_indicator(value):
    fitness = max(0, min(100, int(100 if value is None else value)))
    if fitness >= 80:
        return fitness, '🟢', 'свежий'
    if fitness >= 50:
        return fitness, '🟡', 'устал'
    if fitness >= 20:
        return fitness, '🔴', 'очень устал'
    return fitness, '⚫', 'истощён'


def squad_keyboard(players, page=0, prefix='squad'):
    page_size=8
    total_pages=max(1,(len(players)+page_size-1)//page_size)
    page=max(0,min(page,total_pages-1))
    k=InlineKeyboardBuilder()
    start=page*page_size
    end=min(start+page_size,len(players))
    for i in range(start,end):
        p=players[i]
        last=display_player_name(p.get('first_name'),p.get('last_name'))[:12]
        position=POSITION_LABELS_RU.get(str(p.get('position') or ''),str(p.get('position') or '—'))
        raw_ovr=p.get('overall')
        ovr=int(round(float(raw_ovr))) if raw_ovr is not None else int(round(float(player_rating(_rating_player(p)))))
        fitness,fitness_icon,fitness_label=fitness_indicator(p.get('fitness'))
        morale=max(0,min(100,int(p.get('morale') if p.get('morale') is not None else 70)))
        k.button(text=f'{i+1}. {last} · {position} · {ovr} · {fitness_icon} FIT {fitness}% · Мораль {morale}%',callback_data=f'{prefix}:item:{int(p.get("id", 0))}')
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
        last=display_player_name(r.get('first_name'),r.get('last_name'))[:12]
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


class _BoundedUserCache(dict):
    def __init__(self,max_entries=1000,ttl=7200): super().__init__(); self.max_entries=max_entries; self.ttl=ttl; self._touched={}
    def _expire(self):
        now=time.monotonic()
        for k,t in list(self._touched.items()):
            if now-t>=self.ttl: super().pop(k,None); self._touched.pop(k,None)
    def __setitem__(self,k,v):
        self._expire(); super().__setitem__(k,v); self._touched[k]=time.monotonic()
        while len(self)>self.max_entries:
            oldest=min(self._touched,key=self._touched.get); super().pop(oldest,None); self._touched.pop(oldest,None)
    def __getitem__(self,k): self._expire(); v=super().__getitem__(k); self._touched[k]=time.monotonic(); return v
    def get(self,k,d=None): self._expire(); return super().get(k,d)
    def setdefault(self,k,d=None): self._expire(); return super().setdefault(k,d)
    def pop(self,k,*a): self._touched.pop(k,None); return super().pop(k,*a)

class _BoundedDefaultUserCache(defaultdict):
    def __init__(self,factory,max_entries=1000,ttl=7200): super().__init__(factory); self.max_entries=max_entries; self.ttl=ttl; self._touched={}
    def _expire(self):
        now=time.monotonic()
        for k,t in list(self._touched.items()):
            if now-t>=self.ttl: super().pop(k,None); self._touched.pop(k,None)
    def __setitem__(self,k,v):
        self._expire(); super().__setitem__(k,v); self._touched[k]=time.monotonic()
        while len(self)>self.max_entries:
            oldest=min(self._touched,key=self._touched.get); super().pop(oldest,None); self._touched.pop(oldest,None)
    def __getitem__(self,k): self._expire(); v=super().__getitem__(k); self._touched[k]=time.monotonic(); return v
    def get(self,k,d=None): self._expire(); return super().get(k,d)
    def setdefault(self,k,d=None): self._expire(); return super().setdefault(k,d)
    def pop(self,k,*a): self._touched.pop(k,None); return super().pop(k,*a)

_TACTIC_ASSIGNMENTS=_BoundedDefaultUserCache(dict)
_TACTIC_ACTIVE_SLOT=_BoundedUserCache()


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

def _compact_player_name(first_name, last_name, max_len=20):
    name=display_player_name(first_name,last_name)
    if len(name)<=max_len:
        return name
    first=clean_display_name(first_name)
    last=clean_display_name(last_name)
    if last:
        short=f'{first[:1]}. {last}' if first else last
        if len(short)<=max_len:
            return short
    return name[:max_len-1].rstrip()+'.'


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
            lines.append(f'{label}:  ⚠️ {display_player_name(r.get('first_name'),r.get('last_name'))} → {POSITION_LABELS_RU.get(slot,slot)} (OVR {base:.0f} → {adjusted:.0f}, −{penalty}%) · Уст {max(0,100-int(r.get('fitness') or 100))}% · Мораль {max(0,min(100,int(r.get('morale') if r.get('morale') is not None else 70)))}%')
        else:
            lines.append(f'{label}:  ✅ {display_player_name(r.get('first_name'),r.get('last_name'))} ({adjusted:.0f}) · Уст {max(0,100-int(r.get('fitness') or 100))}% · Мораль {max(0,min(100,int(r.get('morale') if r.get('morale') is not None else 70)))}%')
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


def _player_picker_keyboard(rows, slot, page=0):
    per_page=10
    total_pages=max(1,(len(rows)+per_page-1)//per_page)
    page=max(0,min(int(page),total_pages-1))
    start=page*per_page
    k=InlineKeyboardBuilder()
    for r in rows[start:start+per_page]:
        pid=int(r['id'])
        pr=_rating_player(r); overall=player_rating(pr,slot)
        label=POSITION_LABELS_RU.get(r['position'],r['position'])
        name=_compact_player_name(r.get('first_name'),r.get('last_name'))
        k.button(text=f'{name} · {label} · {overall:.0f}',callback_data=f'tactic:player:{pid}')
    if total_pages>1:
        if page>0: k.button(text='⬅️ Назад',callback_data=f'tactic:players:page:{page-1}')
        else: k.button(text='⬅️ Назад',callback_data='tactic:players:noop')
        k.button(text=f'стр. {page+1}/{total_pages}',callback_data='tactic:players:noop')
        if page<total_pages-1: k.button(text='Вперед ➡️',callback_data=f'tactic:players:page:{page+1}')
        else: k.button(text='Вперед ➡️',callback_data='tactic:players:noop')
    k.button(text='⬜ Снять игрока',callback_data='tactic:clear')
    k.button(text='⬅️ К составу',callback_data='tactic:pick')
    k.adjust(1,3,2) if total_pages>1 else k.adjust(1,2)
    return k.as_markup()


_SELECTED_SQUAD_PLAYERS=_BoundedUserCache()
_COMPARISON_TARGETS=_BoundedUserCache()
_TACTIC_PICKS=_BoundedDefaultUserCache(set)
_MARKET_VIEWS=_BoundedUserCache()
_SCOUT_FILTERS=_BoundedUserCache()

def _market_data(rows):
    data=[]
    for r in rows:
        pr=type('P',(),dict(
            r,
            name=display_player_name(r.get('first_name'),r.get('last_name')),
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
        name=display_player_name(r.get('first_name'),r.get('last_name'))
        price=r.get('asking_price') if r.get('listing_id') else r.get('market_value',0)
        label=f"{name.split()[-1] if name else 'Player'} · {r.get('position','')} · €{int(price):,}"
        k.button(text=label[:30],callback_data=f'market:item:{int(r["id"])}')
    for code,label in (('ATT','⚽ Нападающие'),('MID','🎯 Полузащита'),('DEF','🛡 Защита'),('GK','🧤 Вратари')):
        k.button(text=label,callback_data=f'market:filter:{code}')
    k.button(text='🤖 AI Советник',callback_data='transfers:ai_advice')
    k.button(text='⬅ Назад',callback_data='menu')
    # Apply layout once, after every button has been added, so the AI button
    # cannot be omitted or regrouped by a later adjust() call.
    k.adjust(5,5,2,2,1,1)
    return k.as_markup()

def _contract_keyboard(rows):
    k=InlineKeyboardBuilder()
    for i,r in enumerate(rows):
        last=display_player_name(r.get('first_name'),r.get('last_name'))[:12]
        k.button(text=f'{i+1}. {last}',callback_data=f'contract:item:{int(r["id"])}')
    k.adjust(6)
    k.button(text='⬅ Назад',callback_data='menu')
    k.adjust(6,1)
    return k.as_markup()


def _position_filter(position, code):
    groups={'GK':{'GK'},'DEF':{'LB','CB','RB'},'MID':{'DM','CM','AM'},'ATT':{'LW','RW','ST'}}
    return position in groups.get(code,set())


def tactics_keyboard(formation):
    k=InlineKeyboardBuilder()
    formations = ('5-3-2','4-5-1','4-1-4-1', '4-4-2','4-2-3-1','4-1-2-1-2','4-3-1-2', '4-3-3','3-5-2','3-4-3')
    for f in formations:
        k.button(text=('✓ ' if f==formation else '')+f, callback_data=f'tac:f:{f}')
    style_labels={'BALANCE':'Баланс','ATTACK':'Атака','DEFENSE':'Защита','COUNTER':'Контратака','POSSESSION':'Владение'}
    for st in ('BALANCE','ATTACK','COUNTER','DEFENSE','POSSESSION'):
        k.button(text=style_labels[st], callback_data=f'tac:s:{st}')
    k.button(text='🔄 Обновить доску',callback_data='tac:refresh')
    k.button(text='👥 Выбрать состав',callback_data='tactic:pick')
    k.button(text='⬅ Назад',callback_data='menu')
    k.adjust(3,4,3,2,3,1,1,1)
    return k.as_markup()

@router.message(CommandStart())
async def start(m:Message, state:FSMContext):
    await state.clear()
    # Register/update the Telegram user only. Club creation happens exclusively
    # inside the authenticated Mini App; /start must also work for clubless users.
    async with SessionLocal() as s:
        await current_user(s,m)
        await s.commit()
    greeting=(
        '🏆 <b>Добро пожаловать в Football Manager!</b>\n\n'
        'Создай свой клуб и начни футбольную карьеру.\n\n'
        'В приложении доступны состав, тактика, трансферы, матчи в реальном времени и AI-помощник.'
    )
    await m.answer(greeting, reply_markup=menu(user_id=m.from_user.id))

@router.message(Command('createleague'))
async def cmd_createleague(m:Message):
    parts=(m.text or '').split(maxsplit=2)
    if len(parts)<3:return await m.answer('Использование: /createleague 16 Friends League')
    try:n=int(parts[1])
    except:return await m.answer('Количество клубов: только 16.')
    if n != 16:return await m.answer('Количество клубов: только 16.')
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
async def cmd_startleague(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
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
async def cmd_playround(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        league=await active_league(s,c['id'])
        if not league or league['status']!='ACTIVE':return await m.answer('У клуба нет активной лиги.')
        round_no=league['current_round']
        try:
            scheduled_at,count=await schedule_round(s,league['id'],round_no,10)
            match_id=(await s.execute(text('''SELECT id FROM matches WHERE league_id=:l AND round=:r AND (home_club_id=:c OR away_club_id=:c) ORDER BY id DESC LIMIT 1'''),{'l':league['id'],'r':round_no,'c':c['id']})).scalar_one()
            await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(str(e))
    k=InlineKeyboardBuilder(); k.button(text='🔍 Обзор матча',callback_data=f'match:preview:{match_id}')
    k.adjust(1)
    await m.answer(f'📅 <b>Тур {round_no} запланирован</b>\n\n⚽ Ваш матч начнётся через <b>10 минут</b>.\n🔔 Отдельного уведомления о стартовом свистке не будет.\n\nПосле старта: 225 сек первый тайм → 60 сек перерыв → 225 сек второй тайм.\nВажные события и итог матча придут отдельными уведомлениями.',reply_markup=k.as_markup())

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
        row=(await s.execute(text("""SELECT m.id,m.home_score,m.away_score,h.name home,a.name away FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id WHERE m.status='LIVE' AND m.competition_type='LEAGUE' AND m.halftime_locked AND (m.home_club_id=:c OR m.away_club_id=:c) ORDER BY m.id DESC LIMIT 1"""),{'c':c['id']})).mappings().first()
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
    pid=int(parts[1])
    async with SessionLocal() as s:
        r=(await s.execute(text('''SELECT p.*,cp.fitness,cp.morale,cp.is_injured,cp.injury_matches FROM players p LEFT JOIN club_players cp ON cp.player_id=p.id WHERE p.id=:p'''),{'p':pid})).mappings().first()
    if not r:return await m.answer('Игрок не найден.')
    talents=r.get('talents') or []
    if isinstance(talents,str):
        import json; talents=json.loads(talents)
    async with SessionLocal() as s:
        history=await get_history(pid,s)
        trend=await get_development_trend(pid,s,30)
    caption=(f'🃏 <b>{display_player_name(r.get('first_name'),r.get('last_name'))}</b>\n{r["position"]} · {r["nationality"]} · {r["rarity"]}\n'
             f'⚡ PAC {r["pace"]} · SHO {r["shooting"]} · PAS {r["passing"]} · DRI {r["dribbling"]}\n'
             f'🛡 DEF {r["defending"]} · PHY {r["physical"]} · STA {r["stamina"]} · MENT {r["mental"]}\n'
             f'🎯 POT {r["potential"]} · €{r["market_value"]:,}\n🩹 Усталость: <b>{max(0,100-fitness_indicator(r.get("fitness"))[0])}%</b> · {fitness_indicator(r.get("fitness"))[1]} <b>{fitness_indicator(r.get("fitness"))[2]} ({fitness_indicator(r.get("fitness"))[0]}% FIT)</b> · 😊 Мораль: <b>{max(0,min(100,int(r.get("morale") if r.get("morale") is not None else 70)))}%</b>\n🔋 <b>Восстановление:</b> +15/день (без матча +20/день)\n✨ <b>Таланты:</b> {", ".join(talent_label(x) for x in talents) or "—"}')
    if history:
        caption += f'\n📈 <b>История развития:</b> {format_history(history)}'
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
    if formation not in ALLOWED_FORMATIONS:
        return await m.answer('Доступно: ' + ', '.join(ALLOWED_FORMATIONS))
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
        events=(await s.execute(text('SELECT e.minute,e.event_type,e.description,e.metadata FROM match_events e WHERE e.match_id=:m ORDER BY e.minute,e.id LIMIT 30'),{'m':row['id']})).mappings().all() if row else []
    if not row:return await m.answer('Завершённых матчей пока нет.')
    path=match_center(row['home'],row['away'],row['home_score'] or 0,row['away_score'] or 0,float(row['home_xg'] or 0),float(row['away_xg'] or 0),events)
    await m.answer_photo(FSInputFile(path),caption=f'📺 <b>{row["home"]} {row["home_score"]}:{row["away_score"]} {row["away"]}</b>')

@router.message(Command('squad'))
async def cmd_squad(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        rows=(await s.execute(text('''SELECT p.*,cp.club_id,cp.shirt_number,cp.fitness,cp.form,cp.morale,cp.is_injured
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c ORDER BY p.position,p.potential DESC,p.id'''),{'c':c['id']})).mappings().all()
    if not rows:return await m.answer('Состав пуст.')
    out=[f'👥 <b>СОСТАВ — {len(rows)} игроков</b>','']
    for r in rows:
        rating=player_rating(type('P',(),dict(r, suspended=False, name=display_player_name(r.get('first_name'),r.get('last_name'))))())
        injury=' 🩹' if r['is_injured'] else ''
        fitness,fitness_icon,fitness_label=fitness_indicator(r.get('fitness'))
        out.append(f'#{r["shirt_number"] or "-"} {display_player_name(r.get('first_name'),r.get('last_name'))} · {r["position"]} · <b>{rating}</b> · {fitness_icon} FIT {fitness}% ({fitness_label}) · FORM {r["form"]:+d}{injury}')
    k=InlineKeyboardBuilder()
    k.button(text='🤖 Best XI',callback_data='squad:bestxi')
    k.button(text='🧠 AI Best XI',callback_data='squad:bestxi_ai')
    k.button(text='📊 Анализ состава',callback_data='squad:analysis')
    k.button(text='🏟️ Инфраструктура',callback_data='infrastructure')
    k.adjust(2,2)
    await m.answer('\n'.join(out),reply_markup=k.as_markup())

@router.message(Command('squadview'))
async def cmd_squadview(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        rows=(await s.execute(text("SELECT p.*,cp.shirt_number,cp.fitness,cp.form,cp.is_injured FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c ORDER BY p.position,p.potential DESC,p.id"),{'c':c['id']})).mappings().all()
    if not rows:return await m.answer('Состав пуст.')
    data=[]
    for r in rows:
        pr=type('P',(),dict(r,suspended=False,name=display_player_name(r.get('first_name'),r.get('last_name'))))()
        data.append(dict(r,overall=player_rating(pr)))
    path=squad_screen(c['name'],data)
    await m.answer_photo(FSInputFile(path),caption=f'👥 <b>{c["name"]}</b> · {len(data)} игроков\nУст = усталость, Мораль = состояние игрока (0–100%).')

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
        pr=type('P',(),dict(r,name=display_player_name(r.get('first_name'),r.get('last_name')),fitness=100,form=0))()
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
        rows=(await s.execute(text("SELECT m.round,m.scheduled_at,h.name home,a.name away,m.home_score,m.away_score,m.status FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id WHERE m.league_id=:l ORDER BY m.round,m.id"),{'l':league['id']})).mappings().all()
    rows=[dict(r,scheduled_at=_local_datetime(r['scheduled_at'],m)) for r in rows]
    path=fixtures_screen(league['name'],rows)
    await m.answer_photo(FSInputFile(path),caption=f'📅 <b>{league["name"]}</b> · календарь',reply_markup=back_keyboard('menu:main'))

@router.message(Command('tactics'))
async def cmd_tactics(m:Message, from_user=None):
    async with SessionLocal() as s:
        uid=await current_user(s,m,from_user); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=await get_tactical_board(s,c['id'])
        await s.commit()
    path=tactical_board_screen(t['formation'],[dict(r) for r in rows[:11]],title=f"{c['name']} · {t['style']}")
    style_labels={'BALANCE':'Баланс','ATTACK':'Атака','COUNTER':'Контратака','DEFENSE':'Защита','POSSESSION':'Владение'}
    caption=(f'🧠 <b>ТАКТИЧЕСКАЯ ДОСКА</b>\n\nФормация: <b>{t["formation"]}</b> · стиль: <b>{style_labels.get(t["style"],t["style"])}</b>\n'
             f'Темп {t["tempo"]} · прессинг {t["pressing"]} · ширина {t["width"]} · линия {t["defensive_line"]} · агрессия {t["aggression"]}\n\n'
             'Выбирай схему или стиль кнопками ниже. После изменения доска перерисуется.')
    await m.answer_photo(FSInputFile(path),caption=caption,reply_markup=tactics_keyboard(t['formation']))

async def _apply_live_tactics(s, club_id):
    row=(await s.execute(text("SELECT id FROM matches WHERE status='LIVE' AND competition_type='LEAGUE' AND halftime_locked AND (home_club_id=:c OR away_club_id=:c) ORDER BY id DESC LIMIT 1"),{'c':club_id})).first()
    if not row:return False
    t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':club_id})).mappings().first()
    from .game.tactics import Tactics, Style
    await change_halftime_tactics(s,row.id,club_id,Tactics(formation=t['formation'],style=Style(t['style']),tempo=t['tempo'],pressing=t['pressing'],width=t['width'],defensive_line=t['defensive_line'],aggression=t['aggression']))
    return True

@router.message(Command('setformation'))
async def cmd_setformation(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2:return await m.answer('Использование: /setformation 4-3-3')
    if parts[1] not in ALLOWED_FORMATIONS:return await m.answer('Доступно: ' + ', '.join(ALLOWED_FORMATIONS))
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        current=(await s.execute(text('SELECT style FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).scalar_one_or_none() or 'BALANCE'
        tempo,pressing,width,defensive_line,aggression=apply_formation_sliders(Style(current),parts[1])
        await s.execute(text('''INSERT INTO club_tactics(club_id,formation,tempo,pressing,width,defensive_line,aggression)
            VALUES(:c,:f,:t,:p,:w,:d,:a) ON CONFLICT(club_id) DO UPDATE SET formation=EXCLUDED.formation,tempo=EXCLUDED.tempo,pressing=EXCLUDED.pressing,width=EXCLUDED.width,defensive_line=EXCLUDED.defensive_line,aggression=EXCLUDED.aggression'''),
            {'c':c['id'],'f':parts[1],'t':tempo,'p':pressing,'w':width,'d':defensive_line,'a':aggression})
        await _apply_live_tactics(s,c['id']); await record_tactic_change(s,int(c['id'])); await s.commit()
    await m.answer(f'✅ Формация изменена на {parts[1]}. Слайдеры: {tempo}/{pressing}/{width}/{defensive_line}/{aggression}.')

@router.message(Command('setstyle'))
async def cmd_setstyle(m:Message):
    parts=(m.text or '').split()
    if len(parts)!=2:return await m.answer('Использование: /setstyle ATTACK')
    if parts[1].upper() not in {'BALANCE','ATTACK','COUNTER','DEFENSE','POSSESSION'}:return await m.answer('Стили: BALANCE, ATTACK, COUNTER, DEFENSE, POSSESSION')
    style=parts[1].upper()
    style_enum=Style(style)
    # STYLE_SLIDERS[style_enum] remains the canonical style baseline; formation deltas are applied below.
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        formation=(await s.execute(text('SELECT formation FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).scalar_one_or_none() or '4-3-3'
        tempo,pressing,width,defensive_line,aggression=apply_formation_sliders(style_enum,formation)
        await s.execute(text('''INSERT INTO club_tactics(club_id,style,tempo,pressing,width,defensive_line,aggression)
            VALUES(:c,:st,:t,:p,:w,:d,:a) ON CONFLICT(club_id) DO UPDATE SET style=EXCLUDED.style,tempo=EXCLUDED.tempo,pressing=EXCLUDED.pressing,width=EXCLUDED.width,defensive_line=EXCLUDED.defensive_line,aggression=EXCLUDED.aggression'''),
            {'c':c['id'],'st':style,'t':tempo,'p':pressing,'w':width,'d':defensive_line,'a':aggression})
        await _apply_live_tactics(s,c['id']); await record_tactic_change(s,int(c['id'])); await s.commit()
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
            {'c':c['id'],'t':vals[0],'p':vals[1],'w':vals[2],'d':vals[3],'a':vals[4]}); await _apply_live_tactics(s,c['id']); await record_tactic_change(s,int(c['id'])); await s.commit()
    await m.answer('✅ Параметры тактики сохранены.')

@router.message(Command('board'))
async def cmd_board(m:Message):
    async with SessionLocal() as s:
        uid=await current_user(s,m); c=await current_club(s,uid)
        if not c:return await m.answer('Сначала создайте клуб.')
        rows=await get_tactical_board(s,c['id'])
        await s.commit()
    lines=['🧠 <b>ТАКТИЧЕСКАЯ ДОСКА</b>','']
    for r in rows[:11]:
        lines.append(f'#{r["shirt_number"] or "-"} {display_player_name(r.get('first_name'),r.get('last_name'))} · {r.get("slot_label",r["position"])} · {r["role"]} · {r["instruction"]} · ({r["board_x"]},{r["board_y"]})')
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
            await s.commit()
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
            WHERE m.home_club_id=:c OR m.away_club_id=:c
            ORDER BY CASE WHEN m.status='LIVE' THEN 0 WHEN m.status='SCHEDULED' THEN 1 ELSE 2 END,
                     CASE WHEN m.status='SCHEDULED' THEN m.scheduled_at END ASC NULLS LAST,
                     CASE WHEN m.status='FINISHED' THEN m.scheduled_at END DESC NULLS LAST,
                     m.id DESC
            LIMIT 1'''),{'c':c['id']})).mappings().first()
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
        events=(await s.execute(text('SELECT e.minute,e.description FROM match_events e WHERE e.match_id=:m ORDER BY e.minute,e.id LIMIT 30'),{'m':row['id']})).mappings().all() if row else []
        live_matches=(await s.execute(text('''SELECT m.id,h.name home,a.name away,m.home_score,m.away_score,m.current_minute
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            WHERE m.status='LIVE' AND (m.home_club_id=:c OR m.away_club_id=:c) ORDER BY m.id'''),{'c':c['id']})).mappings().all()
    if not row:return await m.answer('Матчей пока нет.')
    path=match_center(row['home'],row['away'],row['home_score'] or 0,row['away_score'] or 0,float(row['home_xg'] or 0),float(row['away_xg'] or 0),events)
    state='LIVE' if row['status']=='LIVE' else row['status']
    markup=None
    if live_matches:
        k=InlineKeyboardBuilder()
        for live in live_matches:
            k.button(text=f'📺 Смотреть матч · {live["home"]} {live["home_score"] or 0}:{live["away_score"] or 0} {live["away"]}',callback_data=f'match:watch:{live["id"]}')
        k.adjust(1)
        k.row(InlineKeyboardButton(text='⬅️ Назад',callback_data='menu:main'),InlineKeyboardButton(text='🏠 Главное меню',callback_data='menu:main'))
        markup=k.as_markup()
    else:
        markup=back_keyboard('menu:main')
    local_kickoff=_local_datetime(row['scheduled_at'],m) if row['scheduled_at'] else None
    kickoff=local_kickoff.strftime('%d.%m %H:%M') if local_kickoff else '—'
    await m.answer_photo(FSInputFile(path),caption=f'📺 <b>{row["home"]} {row["home_score"] or 0}:{row["away_score"] or 0} {row["away"]}</b> · {state} · {_format_live_minute(row["current_minute"] or 0)}′\n⏰ Начало: <b>{kickoff}</b>',reply_markup=markup)

@router.message(Command('cards'))
async def cmd_cards(m:Message):
    async with SessionLocal() as s:
        rows=(await s.execute(text("SELECT rarity,count(*) n FROM players WHERE is_template=TRUE GROUP BY rarity ORDER BY CASE rarity WHEN 'LEGENDARY' THEN 1 WHEN 'EPIC' THEN 2 WHEN 'RARE' THEN 3 ELSE 4 END"))).mappings().all()
        real=(await s.execute(text('SELECT count(*) FROM players WHERE real_player=true AND is_template=TRUE'))).scalar_one()
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
    # The old standalone player-scouting screen is now unified with the market.
    uid = from_user.id if from_user is not None else m.from_user.id
    await _show_market(m, uid)


def _scout_filter_state(uid):
    return _SCOUT_FILTERS.setdefault(uid, {
        'position': [], 'age': None, 'potential': None, 'value': None, 'wage': None,
        'nationality': None, 'transfer_status': None, 'preferred_foot': None,
        'attributes': {}, 'page': 0, 'sort': 'id_asc',
    })


def _scout_menu_text(uid):
    f=_scout_filter_state(uid)
    positions=f.get('position') or []
    pos=', '.join(positions) if positions else 'Любая'
    def rng(value):
        if not value:return 'Любой'
        lo=value.get('min') if isinstance(value,dict) else None; hi=value.get('max') if isinstance(value,dict) else None
        return f'{lo or "—"}–{hi or "—"}'
    attrs=', '.join(f'{k}≥{v.get("min")}' for k,v in (f.get('attributes') or {}).items()) or '—'
    return (f'🔍 <b>СКАУТИНГ</b>\n\n'
            f'📍 Позиции: <b>{pos}</b>\n'
            f'🎂 Возраст: <b>{rng(f.get("age"))}</b>\n'
            f'⭐ Потенциал: <b>{rng(f.get("potential"))}</b>\n'
            f'💰 Стоимость: <b>{rng(f.get("value"))}</b>\n'
            f'💵 Зарплата: <b>{rng(f.get("wage"))}</b>\n'
            f'🌍 Национальность: <b>{f.get("nationality") or "Любая"}</b>\n'
            f'📦 Статус: <b>{f.get("transfer_status") or "Любой"}</b>\n'
            f'🦶 Нога: <b>{f.get("preferred_foot") or "Любая"}</b>\n'
            f'⚙️ Атрибуты: <b>{attrs}</b>')


def _scout_menu_keyboard(uid):
    k=InlineKeyboardBuilder()
    k.button(text='📍 Позиции',callback_data='scout:position')
    k.button(text='🎂 Возраст',callback_data='scout:age')
    k.button(text='⭐ Потенциал',callback_data='scout:potential')
    k.button(text='💰 Стоимость',callback_data='scout:value')
    k.button(text='💵 Зарплата',callback_data='scout:wage')
    k.button(text='🌍 Национальность',callback_data='scout:nationality')
    k.button(text='📦 Статус',callback_data='scout:status')
    k.button(text='🦶 Нога',callback_data='scout:foot')
    k.button(text='⚙️ Атрибут',callback_data='scout:attribute')
    k.button(text='🔍 Найти',callback_data='scout:search')
    k.button(text='♻️ Сбросить',callback_data='scout:reset')
    k.button(text='🏠 Главное меню',callback_data='menu:main')
    k.adjust(2,2,2,2,2,2)
    return k.as_markup()


def _scout_position_keyboard(uid):
    value=set(_scout_filter_state(uid).get('position') or [])
    k=InlineKeyboardBuilder()
    for code,label in (
        ('GK','🧤 ВРТ'),('LB','🛡 ЛЗ'),('CB','🛡 ЦЗ'),('RB','🛡 ПЗ'),
        ('DM','⚙️ ОПЗ'),('CM','⚙️ ЦПЗ'),('AM','⚙️ АПЗ'),('LW','⚡ ЛП'),('RW','⚡ ПП'),('ST','⚡ ЦФ'),
    ):
        # Keyboard contract: ✓ ' if value==code
        k.button(text=('✓ ' if code in value else '')+label,callback_data=f'scout:position:{code}')
    k.button(text='Любая',callback_data='scout:position:any')
    k.button(text='⬅️ Назад',callback_data='scout')
    k.adjust(4,4,2,2,1,1)
    return k.as_markup()



def _scout_age_keyboard(uid):
    return _scout_range_keyboard('age')

def _scout_range_keyboard(field):
    k=InlineKeyboardBuilder()
    if field=='age':
        options=[('16–19',16,19),('20–23',20,23),('24–27',24,27),('28–31',28,31),('32+',32,None)]
    else:
        options=[('<70',1,69),('70–79',70,79),('80–89',80,89),('90+',90,None)]
    for label,lo,hi in options:
        k.button(text=label,callback_data=f'scout:range:{field}:{lo}:{hi or ""}')
    k.button(text='⌨️ Ввести min-max',callback_data=f'scout:input:{field}')
    k.button(text='Любой',callback_data=f'scout:range:{field}:any')
    k.button(text='⬅️ Назад',callback_data='scout')
    k.adjust(3,2,2,1)
    return k.as_markup()


def _scout_simple_keyboard(field, options):
    k=InlineKeyboardBuilder()
    for code,label in options:
        k.button(text=label,callback_data=f'scout:set:{field}:{code}')
    k.button(text='Любой',callback_data=f'scout:set:{field}:any')
    k.button(text='⬅️ Назад',callback_data='scout')
    k.adjust(2,2,1)
    return k.as_markup()


async def _scout_search(s, club_id, filters, page=0):
    # Exact-position SQL contract: conditions.append('p.position = :pos'); params['pos']=position
    from app.game.scouting import build_scout_query, build_scout_count_query
    from random import Random
    from hashlib import sha256
    query=build_scout_query(filters).limit(24).offset(max(0,int(page))*8)
    count_query=build_scout_count_query(filters)
    total=int((await s.execute(count_query)).scalar_one())
    rows=[dict(r) for r in (await s.execute(query)).mappings().all()]
    staff_level=int((await s.execute(text('SELECT staff_scouting FROM clubs WHERE id=:c'),{'c':club_id})).scalar_one_or_none() or 1)
    if staff_level>1 and rows:
        chance=scouting_discovery_chance(staff_level)
        seed=int.from_bytes(sha256(f'{club_id}:{page}:{sorted(filters.items())}'.encode()).digest()[:8],'big')
        rng=Random(seed)
        visible=rows[:8]
        reserve=rows[8:]
        good=[r for r in reserve if int(r.get('potential') or 0)>=75]
        for candidate in good:
            if rng.random()<chance and candidate not in visible:
                replace_index=rng.randrange(len(visible)) if visible else 0
                if visible:
                    visible[replace_index]=candidate
        rows=visible
    else:
        rows=rows[:8]
    return total,rows


def _scout_results_keyboard(rows, total, page):
    k=InlineKeyboardBuilder()
    for r in rows:
        name=display_player_name(r.get('first_name'),r.get('last_name'))
        if name == 'Player':
            name=f'Игрок {r["id"]}'
        if len(name)>14:
            name=name[:12]+'.'
        position=str(r.get('position') or '—')
        pr=_card_player(r).get('overall')
        k.button(text=f'{name} · {position} · {pr if pr is not None else "—"}',callback_data=f'scout:item:{r["id"]}')
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
        scouting_level=int((await s.execute(text('SELECT staff_scouting FROM clubs WHERE id=:c'),{'c':c['id']})).scalar_one_or_none() or 1)
    if not rows:
        text_msg='Никого не найдено.\nПопробуйте изменить фильтры.'
        markup=back_keyboard('scout')
    else:
        filters['page']=page
        text_msg=_scout_results_text(rows,total,page)+f'\n👔 Скауты: уровень {scouting_level} · шанс находки сильного игрока +{int(staff_bonus('scouting',scouting_level)*100-100)}%'
        markup=_scout_results_keyboard(rows,total,page)
    if isinstance(target,CallbackQuery):
        if target.message.photo:
            await target.message.edit_caption(caption=text_msg,reply_markup=markup)
        else:
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
            out.append(f'#{r["id"]} {display_player_name(r.get('first_name'),r.get('last_name'))} · {r["position"]} · €{r["asking_price"]:,} · POT {r["potential"]}\n/offer {r["listing_id"]} {r["asking_price"]}')
        else:
            out.append(f'#{r["id"]} {display_player_name(r.get('first_name'),r.get('last_name'))} · {r["position"]} · €{r["market_value"]:,} · POT {r["potential"]}\n/buy {r["id"]}')
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
    await m.answer(f'✅ Куплен {display_player_name(player.get('first_name'),player.get('last_name'))} за €{price:,}.')

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
    await m.answer(f'✅ {display_player_name(row.get('first_name'),row.get('last_name'))} продан за €{price:,}.')

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


async def _show_finance(message, telegram_id):
    async with SessionLocal() as s:
        c=await club_by_tg(s,telegram_id)
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
            local_dt=_local_datetime(dt,m); date=local_dt.strftime('%d.%m.%Y %H:%M') if hasattr(local_dt,'strftime') else str(local_dt)[:16]
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
        lines.append(f'• {display_player_name(r.get('first_name'),r.get('last_name'))} · {r["position"]} · <b>€{int(r["salary"]):,}</b>')
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
    k.button(text='📜 История тренировок', callback_data='training:history')
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


def _staff_text(club):
    return ('👔 <b>СТАФФ</b>\n\n'
            f'🏋️ Тренеры: {club["staff_training"]} · +{int(staff_bonus("training",club["staff_training"])*100-100)}% development\n'
            f'🏥 Медики: {club["staff_medical"]} · −{int((1-staff_bonus("medical",club["staff_medical"]))*100)}% времени травмы\n'
            f'🕵️ Скауты: {club["staff_scouting"]} · +{int((staff_bonus("scouting",club["staff_scouting"])-1)*100)}% шанс находки\n\n'
            f'💰 Бюджет: €{club["budget"]:,}')


def _staff_keyboard():
    k=InlineKeyboardBuilder()
    for key,label in [('training','🏋️ Тренеры'),('medical','🏥 Медики'),('scouting','🕵️ Скауты')]:
        k.button(text=f'⬆️ {label}',callback_data=f'staff:upgrade:{key}')
    k.button(text='⬅️ Назад',callback_data='menu:main'); k.button(text='🏠 Главное меню',callback_data='menu:main'); k.adjust(2,2)
    return k.as_markup()


@router.callback_query(F.data=='staff')
async def cb_staff(cq:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
    await cq.answer(); await cq.message.answer(_staff_text(club),reply_markup=_staff_keyboard())


@router.callback_query(F.data.startswith('staff:upgrade:'))
async def cb_staff_upgrade(cq:CallbackQuery):
    staff_type=cq.data.rsplit(':',1)[1]
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try:
            level,cost=await upgrade_staff(club,staff_type,s); await s.commit()
        except ValueError as e:
            await s.rollback(); return await cq.answer(str(e),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Staff upgrade failed')
            return await cq.answer('Не удалось улучшить стафф.',show_alert=True)
        club=(await s.execute(text('SELECT * FROM clubs WHERE id=:c'),{'c':club['id']})).mappings().first()
    await cq.answer(f'Уровень {level}, €{cost:,}'); await cq.message.answer(_staff_text(club),reply_markup=_staff_keyboard())


@router.callback_query(F.data=='inbox')
async def cb_inbox(cq:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=await inbox_get_messages(club['id'],s,30)
    if not rows:
        text_msg='📬 <b>ИНБОКС</b>\n\nСообщений пока нет.'
        markup=back_keyboard('menu:main')
    else:
        lines=['📬 <b>ИНБОКС</b>']
        k=InlineKeyboardBuilder()
        for r in rows:
            icon='🔵' if not r['is_read'] else '⚪'
            local_dt=_local_datetime(r['created_at'],cq.message); stamp=local_dt.strftime('%d.%m %H:%M') if local_dt else '—'; lines.append(f'{icon} {stamp} · {r["title"]}')
            k.button(text=f'{icon} {str(r["title"])[:28]}',callback_data=f'inbox:view:{r["id"]}')
        k.button(text='🏠 Главное меню',callback_data='menu:main'); k.adjust(1)
        text_msg='\n'.join(lines); markup=k.as_markup()
    await cq.answer(); await cq.message.answer(text_msg,reply_markup=markup)


@router.callback_query(F.data.startswith('inbox:view:'))
async def cb_inbox_view(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректное сообщение.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=await inbox_get_messages(club['id'],s,30)
        row=next((r for r in rows if int(r['id'])==mid),None)
        if not row: return await cq.answer('Сообщение не найдено.',show_alert=True)
        await inbox_mark_read(mid,club['id'],s); await s.commit()
    local_dt=_local_datetime(row['created_at'],cq.message); stamp=local_dt.strftime('%d.%m.%Y %H:%M') if local_dt else '—'
    text_msg=f'📬 <b>{row["title"]}</b> · {stamp}\n\n{row["body"]}'
    await cq.answer(); await cq.message.edit_text(text_msg,reply_markup=back_keyboard('inbox'))


def _infrastructure_text(club):
    return ('🏟️ <b>ИНФРАСТРУКТУРА</b>\n\n'
            f'💎 VIP: {club["vip_level"]}\n'
            f'🏥 Медицина: {club["medical_level"]}\n'
            f'🏋️ Тренировки: {club["training_level"]}\n'
            f'🎓 Академия: {club["academy_level"]}\n'
            f'\n💰 Бюджет: €{club["budget"]:,}')

def _infrastructure_keyboard():
    k=InlineKeyboardBuilder()
    for key,label in [('vip','💎 VIP'),('medical','🏥 Медицина'),('training','🏋️ Тренировки'),('academy','🎓 Академия')]:
        k.button(text=f'⬆️ {label}',callback_data=f'infrastructure:upgrade:{key}')
    k.button(text='⬅️ Назад',callback_data='squad'); k.button(text='🏠 Главное меню',callback_data='menu:main')
    k.adjust(2,2,2)
    return k.as_markup()

@router.callback_query(F.data=='infrastructure')
async def cb_infrastructure(cq:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
    await cq.answer(); await cq.message.answer(_infrastructure_text(club),reply_markup=_infrastructure_keyboard())

@router.callback_query(F.data.startswith('infrastructure:upgrade:'))
async def cb_infrastructure_upgrade(cq:CallbackQuery):
    facility=cq.data.rsplit(':',1)[1]
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        try:
            level,cost=await upgrade_facility(club,facility,s); await s.commit()
        except ValueError as e:
            await s.rollback(); return await cq.answer(str(e),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Infrastructure upgrade failed')
            return await cq.answer('Не удалось улучшить инфраструктуру.',show_alert=True)
        club=(await s.execute(text('SELECT * FROM clubs WHERE id=:c'),{'c':club['id']})).mappings().first()
    await cq.answer(f'Уровень {level}, €{cost:,}')
    await cq.message.answer(_infrastructure_text(club),reply_markup=_infrastructure_keyboard())

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
            changes,batch_id=await train_squad(s,club['id'])
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
    total=len(changes)
    await state.clear()
    await c.answer()
    details=InlineKeyboardBuilder()
    details.button(text='👥 Посмотреть подробности',callback_data=f'training:details:{batch_id}')
    details.button(text='🏋️ К тренировкам',callback_data='training')
    details.adjust(1)
    await c.message.answer(
        f'🏋️ <b>Тренировка завершена!</b>\n'
        f'Тема: <b>{focus}</b>\n'
        f'Интенсивность: <b>{intensity}</b>\n\n'
        f'📊 <b>Развитие:</b>\n'
        f'• Игроков с ростом: <b>{progressed} из {total}</b>\n'
        f'• Очков развития: <b>{points}</b>',
        reply_markup=details.as_markup()
    )


@router.callback_query(F.data=='training:history')
async def cb_training_history(c:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        rows=await training_history(s,club['id'],10)
    await c.answer()
    if not rows:
        return await c.message.edit_text('📜 <b>ИСТОРИЯ ТРЕНИРОВОК</b>\n\nИстория пока пуста.',reply_markup=back_keyboard('training'))
    lines=['📜 <b>ИСТОРИЯ ТРЕНИРОВОК</b>','']
    k=InlineKeyboardBuilder()
    for i,r in enumerate(rows,1):
        local_dt=_local_datetime(r['created_at'],c) if r['created_at'] else None; dt=local_dt.strftime('%d.%m %H:%M') if local_dt else '—'
        lines.append(f'{dt} · <b>{r["focus"]}</b> · <b>{r["intensity"] or "NORMAL"}</b>')
        lines.append(f'  Прокачано {r["progressed"]} игроков, {r["points"]} очков')
        k.button(text=f'👥 Детали #{i}',callback_data=f'training:details:{r["batch_id"]}')
    k.button(text='⬅️ Назад',callback_data='training')
    k.adjust(1)
    await c.message.edit_text('\n'.join(lines),reply_markup=k.as_markup())


@router.callback_query(F.data.startswith('training:details:'))
async def cb_training_details(c:CallbackQuery):
    batch_id=c.data.split(':',2)[2]
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        rows=await training_details(s,club['id'],batch_id)
    if not rows:
        return await c.answer('Тренировка не найдена.',show_alert=True)
    lines=[f'🏋️ <b>ТРЕНИРОВКА — детали</b>', '', f'📈 <b>Игроки с ростом ({len(rows)}):</b>', '']
    labels={'pace':'PAC','shooting':'SHO','passing':'PAS','dribbling':'DRI','defending':'DEF','physical':'PHY','stamina':'STA','mental':'MEN'}
    import json
    for r in rows:
        changes=r['changes'] if isinstance(r['changes'],dict) else json.loads(r['changes'] or '{}')
        parts=[]
        for attr,new_value in changes.items():
            label=labels.get(attr,attr.upper())
            parts.append(f'{label} +{int(new_value)}')
        lines.append(f'• <b>{display_player_name(r["first_name"],r["last_name"])}</b>')
        lines.append('  '+', '.join(parts))
    k=InlineKeyboardBuilder(); k.button(text='⬅️ Назад',callback_data='training:history'); k.adjust(1)
    await c.answer()
    await c.message.edit_text('\n'.join(lines),reply_markup=k.as_markup())


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
            await set_training_plan(s,c['id'],focus,intensity); changes,batch_id=await train_squad(s,c['id']); await s.commit()
        except ValueError as e: await s.rollback(); return await m.answer(f'❌ {e}')
        except Exception:
            await s.rollback(); logger.exception('Failed to run training via /training')
            return await m.answer('❌ Не удалось провести тренировку. Попробуйте ещё раз позже.')
    progressed=sum(1 for _,_,upd in changes if upd); points=sum(pts for _,pts,_ in changes)
    total=len(changes)
    k=InlineKeyboardBuilder(); k.button(text='👥 Посмотреть подробности',callback_data=f'training:details:{batch_id}'); k.adjust(1)
    await m.answer(f'🏋️ <b>Тренировка завершена!</b>\nТема: <b>{focus}</b>\nИнтенсивность: <b>{intensity}</b>\n\n📊 <b>Развитие:</b>\n• Игроков с ростом: <b>{progressed} из {total}</b>\n• Очков развития: <b>{points}</b>',reply_markup=k.as_markup())

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
        out.append(f'#{r["id"]} {display_player_name(r.get('first_name'),r.get('last_name'))} · €{int(r["contract_salary"] or 0):,} · morale {r["morale"]} · {r["playing_time_expectation"]} · {r["contract_until"]}')
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
    await m.answer('\n'.join(["📨 <b>ПРЕДЛОЖЕНИЯ</b>"]+[f'#{r["id"]} {display_player_name(r.get('first_name'),r.get('last_name'))} — €{r["amount"]:,} → /acceptoffer {r["id"]}' for r in rows]))

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
        local_dt=_local_datetime(r['scheduled_at'],m) if r['scheduled_at'] else None; kickoff=(local_dt.strftime('%d.%m %H:%M') if local_dt else 'дата не назначена')
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
            local_dt=_local_datetime(r['created_at'],cq.message); stamp=local_dt.strftime('%d.%m %H:%M') if local_dt else '—'; lines.append(f"<b>{r['title']}</b> · {stamp}\n{r['body']}")
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
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if club:
            await ensure_daily_quests(s, int(club['id']))
            await s.commit()
        unread=await inbox_unread_count(club['id'],s) if club else 0
    await c.answer()
    await c.message.answer('Выберите действие:',reply_markup=menu(unread, c.from_user.id))

@router.callback_query(F.data=='inventory:menu')
async def cb_inventory_menu(c:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        club_id=int(club['id'])
        rows=await list_inventory(s,club_id)
    if not rows:
        text_value='🎒 <b>Инвентарь</b>\n\nИнвентарь пуст'
    else:
        lines=['🎒 <b>Инвентарь</b>','']
        for row in rows:
            lines.append(f'• {item_label(row["item_type"])} — <b>{int(row["quantity"])}</b>')
        text_value='\n'.join(lines)
    k=InlineKeyboardBuilder()
    canonical_items=[]
    for row in rows:
        item=str(row['item_type']).upper()
        canonical='STAMINA_BOOST' if item=='FITNESS_BOOST' else item
        if item in USABLE_ITEMS and canonical not in canonical_items:
            canonical_items.append(canonical)
    team_labels={
        'MEDKIT':'🩹 Применить все аптечки к команде',
        'MORALE_BOOST':'😊 Усилить мораль всей команды',
        'STAMINA_BOOST':'⚡ Восстановить выносливость команды',
    }
    for item in canonical_items:
        k.button(text=team_labels[item],callback_data=f'inventory:team:{item}')
    k.button(text='👥 Выбрать игрока',callback_data='squad')
    k.button(text='⬅️ Назад',callback_data='menu:main')
    k.adjust(1)
    await c.message.edit_text(text_value,reply_markup=k.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith('inventory:team:'))
async def cb_inventory_team(c:CallbackQuery):
    item_type=c.data.rsplit(':',1)[-1].upper()
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        try:
            status=await team_item_status(s,int(club['id']),item_type)
        except ValueError as exc:
            return await c.answer(str(exc),show_alert=True)
    if status['needed']<=0:
        return await c.answer('Нет игроков, которым нужен этот предмет.',show_alert=True)
    if status['available']<=0:
        return await c.answer('Этого предмета нет в инвентаре.',show_alert=True)
    if status['available'] < status['needed']:
        k=InlineKeyboardBuilder()
        k.button(text=f'✅ Да, применить {status["available"]}',callback_data=f'inventory:team_confirm:{item_type}')
        k.button(text='❌ Нет',callback_data='inventory:menu')
        k.adjust(1)
        await c.message.answer(
            f'Нужно предметов: <b>{status["needed"]}</b>, в инвентаре: <b>{status["available"]}</b>.\n'
            f'Применить к {status["available"]} игрокам?',reply_markup=k.as_markup())
        return await c.answer()
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        try:
            result=await use_inventory_item_for_team(s,int(club['id']),item_type)
            await s.commit()
        except ValueError as exc:
            await s.rollback()
            return await c.answer(str(exc),show_alert=True)
        except Exception:
            await s.rollback()
            logger.exception('Team inventory use failed: club_id=%s item=%s',club['id'],item_type)
            return await c.answer('Не удалось применить предметы.',show_alert=True)
    item_label_text=item_label(item_type)
    outcome={'MEDKIT':'вылечено','STAMINA_BOOST':'восстановлено','MORALE_BOOST':'усилено'}[result['item_type']]
    await c.answer('Предметы применены.')
    await c.message.answer(f'✅ Применено: {result["applied"]} {item_label_text.lower()}, {outcome} игроков: {result["applied"]}.',reply_markup=back_keyboard('inventory:menu'))


@router.callback_query(F.data.startswith('inventory:team_confirm:'))
async def cb_inventory_team_confirm(c:CallbackQuery):
    item_type=c.data.rsplit(':',1)[-1].upper()
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        try:
            result=await use_inventory_item_for_team(s,int(club['id']),item_type)
            await s.commit()
        except ValueError as exc:
            await s.rollback()
            return await c.answer(str(exc),show_alert=True)
        except Exception:
            await s.rollback()
            logger.exception('Confirmed team inventory use failed: club_id=%s item=%s',club['id'],item_type)
            return await c.answer('Не удалось применить предметы.',show_alert=True)
    item_label_text=item_label(result['item_type'])
    outcome={'MEDKIT':'вылечено','STAMINA_BOOST':'восстановлено','MORALE_BOOST':'усилено'}[result['item_type']]
    await c.answer('Предметы применены.')
    await c.message.answer(f'✅ Применено: {result["applied"]} {item_label_text.lower()}, {outcome} игроков: {result["applied"]}.',reply_markup=back_keyboard('inventory:menu'))


@router.callback_query(F.data.startswith('inventory:use:'))
async def cb_inventory_use(c:CallbackQuery):
    try:
        player_id=int(c.data.rsplit(':',1)[1])
    except ValueError:
        return await c.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        owns=(await s.execute(text('SELECT 1 FROM club_players WHERE club_id=:c AND player_id=:p'),
                              {'c':int(club['id']),'p':player_id})).first()
        if not owns:
            return await c.answer('Игрок не найден в вашем клубе.',show_alert=True)
        rows=await list_inventory(s,int(club['id']))
    k=InlineKeyboardBuilder()
    usable_count=0
    for row in rows:
        item=str(row['item_type']).upper()
        canonical='STAMINA_BOOST' if item=='FITNESS_BOOST' else item
        if item not in USABLE_ITEMS:
            continue
        k.button(text=f'{item_label(item)} ×{int(row["quantity"])}',
                 callback_data=f'inventory:apply:{player_id}:{canonical}')
        usable_count += 1
    if not usable_count:
        return await c.answer('В инвентаре нет предметов для использования.',show_alert=True)
    k.button(text='⬅️ Назад к игроку',callback_data=f'squad:item:{player_id}')
    k.button(text='🎒 Инвентарь',callback_data='inventory:menu')
    k.adjust(1)
    await c.message.answer('🎒 <b>Выберите предмет для игрока</b>',reply_markup=k.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith('inventory:apply:'))
async def cb_inventory_apply(c:CallbackQuery):
    parts=c.data.split(':')
    if len(parts)!=4:
        return await c.answer('Некорректный предмет.',show_alert=True)
    try:
        player_id=int(parts[2]); item_type=parts[3].upper()
    except ValueError:
        return await c.answer('Некорректный игрок или предмет.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        try:
            result=await use_inventory_item(s,int(club['id']),player_id,item_type)
            await s.commit()
        except ValueError as exc:
            await s.rollback()
            return await c.answer(str(exc),show_alert=True)
        except Exception:
            await s.rollback()
            logger.exception('Inventory item use failed: club_id=%s player_id=%s item=%s',club['id'],player_id,item_type)
            return await c.answer('Не удалось использовать предмет.',show_alert=True)
    await c.answer('Предмет использован.')
    await c.message.answer(result,reply_markup=back_keyboard(f'squad:item:{player_id}'))


@router.callback_query(F.data=='packs:menu')
async def cb_packs_menu(c:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        packs=await list_packs(s)
    k=InlineKeyboardBuilder(); lines=['🛒 <b>Магазин</b>','', '📦 <b>Карточки игроков</b>']
    for p in packs:
        lines.append(f'• <b>{p["name"]}</b> — €{int(p["price_coins"]):,} · OVR {int(p["min_ovr"])}–{int(p["max_ovr"])}')
        k.button(text=f'📦 Открыть · {p["name"]}',callback_data=f'packs:open:{int(p["id"])}')
    lines.extend(['', '🧰 <b>Предметы и усилители</b>', 'Предметы добавляются в инвентарь и применяются к игрокам.'])
    shop_items = [
        ('MEDKIT', '🩹 Аптечка', 25000, 'Лечит травму игрока'),
        ('MORALE_BOOST', '😊 Усилитель морали', 18000, 'Повышает мораль на 30 пунктов на 3 матча'),
        ('STAMINA_BOOST', '⚡ Усилитель выносливости', 18000, 'Восстанавливает выносливость'),
    ]
    for item_type, label, price, description in shop_items:
        lines.append(f'• {label} — €{price:,}\n  {description}')
        k.button(text=f'🛒 Купить: {label} · €{price:,}', callback_data=f'shop:item:{item_type}')
    k.button(text='🎒 Открыть инвентарь',callback_data='inventory:menu')
    k.button(text='⬅️ Назад',callback_data='menu:main'); k.adjust(1)
    await c.message.edit_text('\n'.join(lines),reply_markup=k.as_markup()); await c.answer()


@router.callback_query(F.data.startswith('shop:item:'))
async def cb_shop_buy_item(c: CallbackQuery):
    item_type = c.data.rsplit(':', 1)[-1].upper()
    catalog = {
        'MEDKIT': (25000, 'Аптечка'),
        'MORALE_BOOST': (18000, 'Усилитель морали'),
        'STAMINA_BOOST': (18000, 'Усилитель выносливости'),
    }
    if item_type not in catalog:
        return await c.answer('Такого предмета нет в магазине.', show_alert=True)
    price, label = catalog[item_type]
    async with SessionLocal() as s:
        club = await club_by_tg(s, c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.', show_alert=True)
        try:
            await record_financial_transaction(s, int(club['id']), 'OTHER', -price, f'Покупка: {label}')
            await add_inventory_item(s, int(club['id']), item_type, 1)
            await s.commit()
        except ValueError as exc:
            await s.rollback()
            return await c.answer(str(exc), show_alert=True)
        except Exception:
            await s.rollback()
            logger.exception('Shop item purchase failed: club_id=%s item=%s', club['id'], item_type)
            return await c.answer('Покупка не выполнена. Проверьте бюджет и повторите попытку.', show_alert=True)
    await c.message.edit_text(f'✅ Куплено: <b>{label}</b> за €{price:,}. Предмет добавлен в инвентарь.', reply_markup=back_keyboard('packs:menu'))
    await c.answer('Покупка успешна!')


@router.callback_query(F.data.startswith('packs:open:'))
async def cb_packs_open(c:CallbackQuery):
    try: pack_id=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer('Некорректный пак.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club: return await c.answer('Сначала создайте клуб.',show_alert=True)
        try:
            result=await open_pack(s,club['id'],pack_id); await s.commit()
        except ValueError as exc:
            await s.rollback(); return await c.answer(str(exc),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Pack opening failed: club_id=%s pack_id=%s',club['id'],pack_id)
            return await c.answer('Не удалось открыть пак.',show_alert=True)
    await c.message.edit_text(
        f'🎉 <b>Пак открыт!</b>\n\n👤 <b>{result["name"]}</b>\n⚽ Позиция: <b>{result["position"]}</b>\n'
        f'⭐ OVR: <b>{result["ovr"]}</b>\n💎 Редкость: <b>{result["rarity"]}</b>\n\n💰 Стоимость: €{result["price_coins"]:,}',
        reply_markup=back_keyboard('packs:menu'))
    await c.answer('Игрок получен!')

@router.callback_query(F.data.startswith('packs:stars:'))
async def cb_packs_stars(c:CallbackQuery):
    await send_stars_invoice(); await c.answer('Покупка за Telegram Stars — скоро.',show_alert=True)

@router.callback_query(F.data=='quests:menu')
async def cb_quests_menu(c:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club:
            return await c.answer('Сначала создайте клуб.',show_alert=True)
        rows=await ensure_daily_quests(s, int(club['id']))
        await s.commit()
    lines=['📋 <b>Ежедневные задания</b>','']
    k=InlineKeyboardBuilder()
    for row in rows:
        reward=(row['reward'] or {}).get('label','—')
        progress=f'{int(row["progress"])}/{int(row["target"])}'
        status='✅' if row['status']=='CLAIMED' else ('🟢' if row['status']=='COMPLETED' else '⏳')
        title={'PLAY_MATCH':'Сыграй матч','SCORE_GOALS':'Забей 3 гола','CHANGE_TACTICS':'Поменяй тактику'}.get(row['quest_type'],row['quest_type'])
        lines.append(f'{status} <b>{title}</b> · {progress} · {reward}')
        if row['status']=='COMPLETED':
            k.button(text='🎁 Забрать награду',callback_data=f'quests:claim:{int(row["id"])}')
    k.button(text='⬅️ Назад',callback_data='menu:main'); k.adjust(1)
    await c.message.edit_text('\n'.join(lines),reply_markup=k.as_markup()); await c.answer()

@router.callback_query(F.data.startswith('quests:claim:'))
async def cb_quests_claim(c:CallbackQuery):
    try: quest_id=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer('Некорректное задание.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club: return await c.answer('Сначала создайте клуб.',show_alert=True)
        try:
            reward=await claim_quest(s,int(club['id']),quest_id); await s.commit()
        except ValueError as exc:
            await s.rollback(); return await c.answer(str(exc),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Daily quest claim failed: club_id=%s quest_id=%s',club['id'],quest_id)
            return await c.answer('Не удалось забрать награду.',show_alert=True)
    await cb_quests_menu(c)

@router.callback_query(F.data=='battlepass:menu')
async def cb_battlepass_menu(c:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club: return await c.answer('Сначала создайте клуб.',show_alert=True)
        league_season=(await s.execute(text("""
            SELECT id FROM seasons WHERE state='ACTIVE' ORDER BY id DESC LIMIT 1
        """))).scalar_one_or_none()
        if league_season is None:
            return await c.message.edit_text(
                '🎫 <b>Сезонный пропуск</b>\n\nОжидание старта сезона. Battle Pass активируется после начала первого матча в лиге.',
                reply_markup=back_keyboard('menu:main'),
            )
        season=await get_active_season(s)
        if not season:
            return await c.message.edit_text('🎫 <b>Сезонный пропуск</b>\n\nАктивного сезонного пропуска сейчас нет. Обратитесь к администратору для запуска.',reply_markup=back_keyboard('menu:main'))
        await add_battle_pass_points(club['id'],1,s)
        progress=await ensure_progress(s,club['id'],season['id']); await s.commit()
    k=InlineKeyboardBuilder(); lines=[f'🎫 <b>Сезонный пропуск · Сезон {season["season_id"]}</b>',f'📈 Уровень: <b>{int(progress["level"])}</b>',f'✨ Очки: <b>{int(progress["points"])}</b>/100 до следующего уровня','']
    claimed=progress['claimed_rewards'] or {}
    rewards=await get_season_rewards(s,season['id'])
    for level,reward in rewards.items():
        unlocked=int(progress['level'])>=level; taken=str(level) in claimed; mark='✅' if taken else ('🟢' if unlocked else '🔒')
        lines.append(f'{mark} Ур. {level}: {reward["label"]}')
        if unlocked and not taken: k.button(text=f'🎁 Забрать · ур. {level}',callback_data=f'battlepass:claim:{level}')
    k.button(text='⬅️ Назад',callback_data='menu:main'); k.adjust(1)
    await c.message.edit_text('\n'.join(lines),reply_markup=k.as_markup()); await c.answer()

@router.callback_query(F.data.startswith('battlepass:claim:'))
async def cb_battlepass_claim(c:CallbackQuery):
    try: level=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer('Некорректный уровень.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,c.from_user.id)
        if not club: return await c.answer('Сначала создайте клуб.',show_alert=True)
        try:
            result=await claim_battle_pass_reward(s,club['id'],level); await s.commit()
        except ValueError as exc:
            await s.rollback(); return await c.answer(str(exc),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Battle Pass reward claim failed: club_id=%s level=%s',club['id'],level)
            return await c.answer('Не удалось забрать награду.',show_alert=True)
    msg=f'🎁 <b>Награда ур. {level} получена!</b>\n{result["label"]}'
    if result.get('player'):
        p=result['player']; msg+=f'\n\n👤 <b>{p["name"]}</b> · {p["position"]} · OVR <b>{p["ovr"]}</b>'
    await c.message.edit_text(msg,reply_markup=back_keyboard('battlepass:menu')); await c.answer()

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
    try: pid=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    data=_MARKET_VIEWS.get(cq.from_user.id)
    if data is None:
        async with SessionLocal() as s:
            data=_market_data(await market_players(s,10))
        _MARKET_VIEWS[cq.from_user.id]=data
    r=next((row for row in data if int(row['id'])==pid),None)
    if r is None:
        return await cq.answer('Игрок уже недоступен.',show_alert=True)
    card=player_card(_card_player(r),out=Path('assets/visual/cards')/f'market_player_{r["id"]}.png')
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        position_count=int((await s.execute(text('SELECT count(*) FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND p.position=:pos'), {'c':club['id'],'pos':r['position']})).scalar_one()) if club else 0
    offered_salary=max(15_000,int(r.get('asking_price') or r.get('market_value') or 0)//35)
    interest=calculate_interest(r,dict(club or {},offered_salary=offered_salary,position_count=position_count,contract_years=3,release_clause=max(int(r.get('market_value') or 0)*2,1_000_000))) if club else {'interest_score':0,'verdict':'LOW','reasons':[]}
    caption=(f'🃏 <b>{display_player_name(r.get('first_name'),r.get('last_name'))}</b>\n'
             f'{r["position"]} · рейтинг <b>{r.get("overall","—")}</b> · €{int(r.get("asking_price") or r.get("market_value") or 0):,} · POT {r.get("potential","—")}\n'
             f'Интерес: <b>{interest["interest_score"]}/100 ({interest["verdict"]})</b>')
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
    await cq.message.answer(f'✅ Куплен {display_player_name(player.get('first_name'),player.get('last_name'))} за €{price:,}.')
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
        season=(await s.execute(text("SELECT state,preseason_at FROM seasons ORDER BY id DESC LIMIT 1"))).mappings().first()
        rows=await player_management_report(s,c['id'])
        if season and season['state']=='CONTRACT_WINDOW':
            rows=[r for r in rows if r.get('contract_until') and (r['contract_until']-date.today()).days <= 180]
    if not rows:
        return await cq.message.answer('📋 В контрактное окно нет игроков с истекающим контрактом.' if season and season['state']=='CONTRACT_WINDOW' else 'Состав пуст.')
    data=[dict(r) for r in rows]
    path=squad_screen(c['name'],data)
    caption='📝 <b>ИСТЕКАЮЩИЕ КОНТРАКТЫ</b>\nПродлите игроков в течение контрактного окна.' if season and season['state']=='CONTRACT_WINDOW' else '📝 <b>КОНТРАКТЫ И СОСТОЯНИЕ СОСТАВА</b>'
    await cq.message.answer_photo(FSInputFile(path),caption=caption,reply_markup=_contract_keyboard(data))

_CONTRACT_VIEWS=_BoundedUserCache()

@router.callback_query(F.data.startswith('contract:item:'))
async def cb_contract_item(cq:CallbackQuery):
    try: pid=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,cq.from_user.id)
        if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=await player_management_report(s,c['id'])
    r=next((dict(row) for row in rows if int(row['id'])==pid),None)
    if r is None:return await cq.answer('Игрок не найден.',show_alert=True)
    _CONTRACT_VIEWS[cq.from_user.id]=r
    card=player_card(_card_player(r),out=Path('assets/visual/cards')/f'contract_player_{r["id"]}.png')
    caption=(f'📝 <b>{display_player_name(r.get('first_name'),r.get('last_name'))}</b> · {r["position"]}\n'
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
    caption=(f'📝 <b>{display_player_name(row.get('first_name'),row.get('last_name'))}</b> · {row["position"]}\n'
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

# --- Dedicated live match viewer -----------------------------------------
_LIVE_SCREENS: dict[int, asyncio.Task] = {}
_LIVE_SCREEN_SIGNATURES: dict[tuple[int,int], str] = {}
_LIVE_SCREEN_LOCK = asyncio.Lock()

def _live_watch_keyboard(match_id, finished=False, halftime=False):
    k=InlineKeyboardBuilder()
    # Both controls mutate the live second-half state; they remain available
    # during the second half so managers can react to the match in progress.
    if not finished:
        k.button(text='🎛 Тактика',callback_data=f'match:watch:tactics:{match_id}')
        k.button(text='🔄 Замена',callback_data=f'match:watch:sub:{match_id}')
    k.button(text='👥 Составы',callback_data=f'match:watch:lineups:{match_id}')
    k.button(text='📊 Статистика',callback_data=f'match:watch:stats:{match_id}')
    if not finished: k.button(text='🔄 Обновить',callback_data=f'match:watch:refresh:{match_id}')
    k.button(text='⬅️ Назад',callback_data=f'match:watch:back:{match_id}')
    if finished:
        k.button(text='🏠 Главное меню',callback_data='menu:main')
    k.adjust(2,2,2,1)
    return k.as_markup()

def _live_event_icon(event_type):
    return {'GOAL':'⚽','SHOT':'🎯','YELLOW':'🟨','RED':'🟥','INJURY':'🩹','SUBSTITUTION':'🔄','ASSIST':'🅰️'}.get(str(event_type or '').upper(),'•')

async def _load_live_view(s, match_id):
    # Event-score query contract: SELECT e.minute,e.club_id
    row=(await s.execute(text("""SELECT m.id,m.status,m.current_minute,m.halftime_locked,m.home_score,m.away_score,
        m.home_xg,m.away_xg,m.home_possession,m.away_possession,m.home_shots,m.away_shots,
        m.home_shots_on_target,m.away_shots_on_target,m.home_corners,m.away_corners,
        m.motm_player_id,m.home_club_id,m.away_club_id,m.home_penalties,m.away_penalties,
        m.competition_type,m.second_half_data,h.name home,a.name away
        FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
        WHERE m.id=:m"""),{'m':match_id})).mappings().first()
    if not row: return None
    # Keep every goal visible, even when the match has more than ten events;
    # the rolling window applies only to non-goal activity.
    events=(await s.execute(text("""WITH recent_non_goals AS (
            SELECT e.id FROM match_events e WHERE e.match_id=:m AND e.event_type<>'GOAL'
            ORDER BY e.minute DESC,e.id DESC LIMIT 10
        ), visible_events AS (
            SELECT e.id FROM match_events e WHERE e.match_id=:m AND e.event_type='GOAL'
            UNION
            SELECT recent_non_goals.id FROM recent_non_goals
        )
        SELECT e.id,e.minute,e.event_type,e.description,e.metadata,e.club_id,e.player_id,e.secondary_player_id,
            p.first_name player_first,p.last_name player_last,sp.first_name secondary_first,sp.last_name secondary_last
        FROM visible_events v JOIN match_events e ON e.id=v.id
        LEFT JOIN players p ON p.id=e.player_id LEFT JOIN players sp ON sp.id=e.secondary_player_id
        ORDER BY e.minute,e.id"""),{'m':match_id})).mappings().all()
    events=[dict(row) for row in events]
    # Substitution descriptions in existing event snapshots contain the outgoing
    # player id. Resolve it here for display without changing MatchEvent storage.
    off_ids=[]
    for event in events:
        if str(event.get('event_type') or '').upper()=='SUBSTITUTION':
            match=re.search(r'#(\d+)',str(event.get('description') or ''))
            if match: off_ids.append(int(match.group(1)))
    off_names={}
    if off_ids:
        rows=(await s.execute(text('SELECT id,first_name,last_name FROM players WHERE id=ANY(:ids)'),{'ids':list(set(off_ids))})).mappings().all()
        off_names={int(r['id']):display_player_name(r.get('first_name'),r.get('last_name')) for r in rows}
    for event in events:
        if str(event.get('event_type') or '').upper()=='SUBSTITUTION':
            match=re.search(r'#(\d+)',str(event.get('description') or ''))
            if match:
                off_id=int(match.group(1)); event['_outgoing_name']=off_names.get(off_id,f'Игрок #{off_id}')
    # Fetch the goal timeline once. The previous implementation called the
    # score helper separately for every visible goal, which made opening LIVE
    # slower as the event list grew. Reconstruct all visible goal scores in
    # memory from one query instead.
    goals=(await s.execute(text("""SELECT e.id,e.minute,e.club_id
        FROM match_events e WHERE e.match_id=:m AND e.event_type='GOAL'
        ORDER BY e.minute,e.id"""),{'m':match_id})).mappings().all()
    score_at={}; gh=ga=0
    for goal in goals:
        if int(goal['club_id'])==int(row['home_club_id']): gh+=1
        elif int(goal['club_id'])==int(row['away_club_id']): ga+=1
        score_at[int(goal['id'])]=(gh,ga)
    for event in events:
        prior=[item for gid,item in score_at.items() if gid<=int(event['id'])]
        if prior: event['_score_at_event']=prior[-1]
    club_names={int(row['home_club_id']):row['home'],int(row['away_club_id']):row['away']}
    for event in events:
        event['_club_name']=club_names.get(int(event['club_id']))
        event['_secondary_club_name']=club_names.get(int(event['club_id']))
        if event.get('secondary_player_id') and str(event.get('event_type') or '').upper() in {'PRESSURE','TACKLE','INTERCEPTION'}:
            opponent_id=int(row['away_club_id']) if int(event['club_id'])==int(row['home_club_id']) else int(row['home_club_id'])
            event['_secondary_club_name']=club_names.get(opponent_id)
    state_rows=(await s.execute(text('''SELECT ml.club_id,AVG(GREATEST(0,100-COALESCE(cp.fitness,100))) fatigue,AVG(COALESCE(cp.morale,70)) morale
        FROM match_lineups ml JOIN club_players cp ON cp.club_id=ml.club_id AND cp.player_id=ml.player_id
        WHERE ml.match_id=:m AND ml.is_starting=true GROUP BY ml.club_id'''),{'m':match_id})).mappings().all()
    state_by_club={int(r['club_id']):(float(r['fatigue'] or 0),float(r['morale'] or 70)) for r in state_rows}
    row=dict(row); row['_state_home']=state_by_club.get(int(row['home_club_id']),(0.0,70.0)); row['_state_away']=state_by_club.get(int(row['away_club_id']),(0.0,70.0))
    motm=None
    if row['motm_player_id']:
        motm=(await s.execute(text("""SELECT p.first_name,p.last_name,ps.rating
            FROM players p LEFT JOIN match_player_stats ps ON ps.player_id=p.id AND ps.match_id=:m
            WHERE p.id=:p"""),{'m':match_id,'p':row['motm_player_id']})).mappings().first()
    return dict(row),events,motm

def _format_live_minute(minute):
    minute=int(minute or 0)
    return f'90+{minute-90}' if minute>90 else str(minute)


def _live_event_lines(row,events):
    # Event score display contract: f"{event_score[0]}:{event_score[1]}"
    lines=[]
    for e in reversed(events[-7:]):
        name=display_player_name(e.get('player_first'),e.get('player_last')) if e.get('player_first') or e.get('player_last') else ''
        secondary=display_player_name(e.get('secondary_first'),e.get('secondary_last')) if e.get('secondary_first') or e.get('secondary_last') else ''
        club=e.get('_club_name') or ''
        secondary_club=e.get('_secondary_club_name') or club
        desc=str(e.get('description') or '').replace(r'\n',' ')
        desc=sanitize_event_text(desc)
        if e.get('_outgoing_name'):
            desc=re.sub(r'#\d+',str(e['_outgoing_name']),desc,count=1)
        event_type=str(e.get('event_type') or '').upper()
        if event_type=='CORNER' and name:
            desc=f'🚩 Угловой ({club}, {name})'
        elif name and secondary and event_type in {'TACKLE','PRESSURE','INTERCEPTION'}:
            action={'TACKLE':'отбирает у','PRESSURE':'прессингует','INTERCEPTION':'перехватывает передачу'}[event_type]
            desc=f'{name} ({club}) {action} {secondary} ({secondary_club})'
        elif name and name not in desc:
            desc=f'{desc} ({name} · {club})'
        elif name and club and f'({club})' not in desc:
            desc=f'{name} ({club})'+desc[len(name):]
        # Normalize again after the player/club decoration is assembled.
        desc=sanitize_event_text(desc)
        metadata=e.get('metadata') or {}
        if int(e.get('minute') or 0)>90 and not metadata.get('extra_time'):
            desc=re.sub(r"^(?:⚽\s*)?90\+\d+′\s*",'',desc)
        event_score=e.get('_score_at_event')
        score=(event_score or (int(row.get('home_score') or 0),int(row.get('away_score') or 0)))
        extra=f' ({score[0]}:{score[1]})'
        if metadata.get('extra_time'):
            minute_label=str(metadata.get('display_minute') or e.get('minute',0))
        else:
            minute_label=_format_live_minute(e.get('minute',0))
        lines.append(f'• {minute_label}′ {_live_event_icon(e.get("event_type"))} {desc[:110]}{extra}')
    return lines or ['• Пока нет событий']

def _live_watch_text(row,events,motm=None):
    finished=row['status']=='FINISHED'
    score_text=f'{row["home_score"] or 0}:{row["away_score"] or 0}'
    if row.get('home_penalties') is not None and row.get('away_penalties') is not None:
        score_text += f' ({row["home_penalties"]}:{row["away_penalties"]} пен.)'
    out=[('🏁 <b>МАТЧ ЗАВЕРШЁН</b>' if finished else '📺 <b>LIVE</b>'),f'<b>{row["home"]} {score_text} {row["away"]}</b>']
    if not finished:
        minute=int(row.get('current_minute') or 0)
        phase_data=row.get('second_half_data') or {}
        reg_end=90+int(phase_data.get('injury_time') or 0)
        if row.get('competition_type')=='CUP' and phase_data.get('extra_time_played') and minute>reg_end:
            minute_label=str(min(120,90+minute-reg_end))
        else:
            minute_label=_format_live_minute(minute)
        out.append(f'⏱ {minute_label}′')
    out += ['',f'📊 xG: {float(row["home_xg"] or 0):.2f} — {float(row["away_xg"] or 0):.2f}',f'🎯 Владение: {float(row["home_possession"] or 50):.0f}% — {float(row["away_possession"] or 50):.0f}%']
    hf,hm=row.get('_state_home',(0.0,70.0)); af,am=row.get('_state_away',(0.0,70.0)); out.append(f'🧠 Состояние: {row["home"]} — уст {hf:.0f}% · мораль {hm:.0f}% | {row["away"]} — уст {af:.0f}% · мораль {am:.0f}%')
    if finished and motm:
        out += ['', '⭐ <b>ЛУЧШИЙ ИГРОК (MOTM):</b>',f'{display_player_name(motm.get("first_name"),motm.get("last_name"))} — {float(motm.get("rating") or 0):.1f}','', '📋 <b>Ключевые события:</b>']
    else: out += ['', '📋 <b>Последние события:</b>']
    out.extend(_live_event_lines(row,events)); return '\n'.join(out)

async def _edit_live_message(message,text_value,reply_markup=None):
    try:
        if getattr(message,'photo',None): await message.edit_caption(caption=text_value,reply_markup=reply_markup)
        else: await message.edit_text(text_value,reply_markup=reply_markup)
        return True
    except Exception as exc:
        logger.info('Live screen message unavailable: chat=%s message=%s error=%s',getattr(message.chat,'id',None),getattr(message,'message_id',None),exc); return False

async def _render_live_screen(message,user_id,match_id,force=False):
    async with SessionLocal() as s: loaded=await _load_live_view(s,match_id)
    if not loaded: return False,True
    row,events,motm=loaded; text_value=_live_watch_text(row,events,motm)
    signature=repr((row['status'],row['current_minute'],row['home_score'],row['away_score'],row['home_xg'],row['away_xg'],row['home_possession'],row['away_possession'],[(e['minute'],e['event_type'],e['description'],e.get('_score_at_event')) for e in events],motm['rating'] if motm else None))
    key=(user_id,match_id)
    if not force and _LIVE_SCREEN_SIGNATURES.get(key)==signature: return True,row['status']=='FINISHED'
    ok=await _edit_live_message(message,text_value,_live_watch_keyboard(match_id,row['status']=='FINISHED',bool(row.get('halftime_locked'))))
    if ok:
        _LIVE_SCREEN_SIGNATURES[key]=signature; logger.info('Live screen updated: user_id=%s match_id=%s minute=%s status=%s',user_id,match_id,row['current_minute'],row['status'])
    return ok,row['status']=='FINISHED'

async def _live_screen_loop(message,user_id,match_id):
    logger.info('Live screen opened: user_id=%s match_id=%s',user_id,match_id); key=(user_id,match_id)
    try:
        while True:
            ok,finished=await _render_live_screen(message,user_id,match_id)
            if not ok or finished: break
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        logger.info('Live screen closed: user_id=%s match_id=%s reason=cancelled',user_id,match_id); raise
    finally:
        _LIVE_SCREEN_SIGNATURES.pop(key,None)
        if _LIVE_SCREENS.get(user_id) is asyncio.current_task(): _LIVE_SCREENS.pop(user_id,None)
        logger.info('Live screen closed: user_id=%s match_id=%s',user_id,match_id)

async def _cancel_live_screen_unlocked(user_id):
    task=_LIVE_SCREENS.pop(user_id,None)
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

async def _cancel_live_screen(user_id):
    async with _LIVE_SCREEN_LOCK:
        await _cancel_live_screen_unlocked(user_id)


async def _open_live_screen(cq,match_id):
    async with _LIVE_SCREEN_LOCK:
        await _cancel_live_screen_unlocked(cq.from_user.id)
        async with SessionLocal() as s:
            loaded=await _load_live_view(s,match_id)
        if not loaded:
            return False
        row,events,motm=loaded
        text_value=_live_watch_text(row,events,motm)
        markup=_live_watch_keyboard(match_id,row['status']=='FINISHED',bool(row.get('halftime_locked')))
        try:
            live_message=await cq.bot.send_message(
                chat_id=cq.message.chat.id,
                text=text_value,
                reply_markup=markup,
            )
        except Exception:
            logger.exception('Live screen message creation failed: user_id=%s match_id=%s',cq.from_user.id,match_id)
            return False
        _LIVE_SCREEN_SIGNATURES[(cq.from_user.id,match_id)]=repr((
            row['status'],row['current_minute'],row['home_score'],row['away_score'],
            row['home_xg'],row['away_xg'],row['home_possession'],row['away_possession'],
            [(e['minute'],e['event_type'],e['description'],e.get('_score_at_event')) for e in events],
            motm['rating'] if motm else None
        ))
        if row['status']!='FINISHED':
            _LIVE_SCREENS[cq.from_user.id]=asyncio.create_task(
                _live_screen_loop(live_message,cq.from_user.id,match_id),
                name=f'live-screen-{cq.from_user.id}-{match_id}'
            )
        logger.info('Live screen opened by user: user_id=%s match_id=%s message_id=%s',cq.from_user.id,match_id,getattr(live_message,'message_id',None))
        return True

@router.callback_query(F.data.startswith('match:prep:'))
async def cb_match_prep(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        row=(await s.execute(text('''SELECT m.id,m.status,m.scheduled_at,h.name home,a.name away
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            WHERE m.id=:m AND (m.home_club_id=:c OR m.away_club_id=:c)'''),{'m':mid,'c':club['id']})).mappings().first()
    if not row: return await cq.answer('Матч не найден.',show_alert=True)
    if row['status']!='SCHEDULED': return await cq.answer('Подготовка к этому матчу уже завершена.',show_alert=True)
    k=InlineKeyboardBuilder()
    k.button(text='🔍 Обзор матча',callback_data=f'match:preview:{mid}')
    k.button(text='🧠 AI Coach',callback_data=f'match:ai_coach:{mid}')
    k.button(text='⬅️ Назад',callback_data='matchcenter')
    k.adjust(1,1)
    text_value=(f'📅 <b>ПОДГОТОВКА К МАТЧУ</b>\n\n'
                f'⚽ <b>{row["home"]}</b> — <b>{row["away"]}</b>\n'
                f'⏰ Начало: <b>{_local_datetime(row["scheduled_at"],cq.message).strftime("%d.%m %H:%M") if row["scheduled_at"] else "—"}</b>\n'
                f'🔔 Отдельного уведомления о стартовом свистке не будет.')
    await cq.message.edit_text(text_value,reply_markup=k.as_markup())
    await cq.answer()

@router.callback_query(F.data.startswith('match:ai_coach:'))
async def cb_match_ai_coach(cq:CallbackQuery):
    try:
        mid=int(cq.data.rsplit(':',1)[1])
    except ValueError:
        return await cq.answer('Некорректный матч.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club:
            return await cq.answer('Сначала создайте клуб.',show_alert=True)
        row=(await s.execute(text('''SELECT m.id,m.status,m.home_club_id,m.away_club_id,h.name home,a.name away
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            WHERE m.id=:m AND (m.home_club_id=:c OR m.away_club_id=:c)'''),{'m':mid,'c':club['id']})).mappings().first()
        if not row:
            return await cq.answer('Матч не найден.',show_alert=True)
        if row['status']!='SCHEDULED':
            return await cq.answer('AI Coach доступен до стартового свистка.',show_alert=True)
        opponent_id=int(row['away_club_id'] if int(row['home_club_id'])==int(club['id']) else row['home_club_id'])
        recommendation=await suggest_tactics(s,int(club['id']),opponent_id)
    await cq.answer()
    if recommendation is None:
        k=InlineKeyboardBuilder()
        k.button(text='⬅️ Назад',callback_data=f'match:prep:{mid}')
        k.adjust(1)
        await cq.message.answer(
            '🧠 <b>AI Coach</b>\n\nИграй в свой футбол. ИИ не выдал проверенную рекомендацию, '
            'поэтому текущая тактика останется без изменений.',reply_markup=k.as_markup())
        return
    sliders=recommendation['sliders']
    lines=[
        '🧠 <b>AI Coach — тактика на матч</b>',
        f"Матч: <b>{html.escape(str(row['home']))} — {html.escape(str(row['away']))}</b>",
        f"Формация: <b>{recommendation['formation']}</b>",
        f"Стиль: <b>{recommendation['style']}</b>",
        '',
        f"Темп: {sliders['tempo']} · Прессинг: {sliders['pressing']}",
        f"Ширина: {sliders['width']} · Линия обороны: {sliders['defensive_line']}",
        f"Агрессия: {sliders['aggression']}",
        '',
        '<b>Обоснование:</b>',html.escape(recommendation['rationale']),
        '',
        'Рекомендация пока не применена к клубу.'
    ]
    k=InlineKeyboardBuilder()
    k.button(text='✅ Применить',callback_data=f'match:ai_coach_apply:{mid}')
    k.button(text='⬅️ Назад',callback_data=f'match:prep:{mid}')
    k.adjust(1,1)
    await cq.message.answer('\n'.join(lines),reply_markup=k.as_markup())


@router.callback_query(F.data.startswith('match:ai_coach_apply:'))
async def cb_match_ai_coach_apply(cq:CallbackQuery):
    try:
        mid=int(cq.data.rsplit(':',1)[1])
    except ValueError:
        return await cq.answer('Некорректный матч.',show_alert=True)
    recommendation=get_cached_tactics_recommendation(mid)
    if not recommendation:
        return await cq.answer('Рекомендация устарела. Запросите AI Coach заново.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club:
            return await cq.answer('Сначала создайте клуб.',show_alert=True)
        row=(await s.execute(text('''SELECT id,status,home_club_id,away_club_id FROM matches
            WHERE id=:m AND (home_club_id=:c OR away_club_id=:c)'''),{'m':mid,'c':club['id']})).mappings().first()
        if not row or row['status']!='SCHEDULED':
            return await cq.answer('Матч больше не доступен для подготовки.',show_alert=True)
        expected_opponent=int(row['away_club_id'] if int(row['home_club_id'])==int(club['id']) else row['home_club_id'])
        if int(recommendation.get('club_id',-1))!=int(club['id']) or int(recommendation.get('opponent_id',-1))!=expected_opponent:
            return await cq.answer('Рекомендация не относится к этому матчу.',show_alert=True)
        style_db='BALANCE' if recommendation['style']=='BALANCED' else recommendation['style']
        sliders=recommendation['sliders']
        result=await s.execute(text('''UPDATE club_tactics SET formation=:formation,style=:style,tempo=:tempo,
            pressing=:pressing,width=:width,defensive_line=:defensive_line,aggression=:aggression WHERE club_id=:club_id'''),{
            'formation':recommendation['formation'],'style':style_db,'tempo':sliders['tempo'],
            'pressing':sliders['pressing'],'width':sliders['width'],'defensive_line':sliders['defensive_line'],
            'aggression':sliders['aggression'],'club_id':int(club['id'])})
        if not result.rowcount:
            return await cq.answer('У клуба нет сохранённой тактики. Сначала откройте экран тактики.',show_alert=True)
        await s.commit()
    await cq.answer('Тактика применена.')
    k=InlineKeyboardBuilder(); k.button(text='⬅️ Назад',callback_data=f'match:prep:{mid}'); k.adjust(1)
    await cq.message.answer('✅ <b>Тактика AI Coach применена.</b> Настройки сохранены в тактике клуба.',reply_markup=k.as_markup())


@router.callback_query(F.data.startswith('match:preview:'))
async def cb_match_preview(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        row=(await s.execute(text('''SELECT m.id,m.status,m.seed,h.id home_id,a.id away_id,h.name home,a.name away
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
            WHERE m.id=:m AND (m.home_club_id=:c OR m.away_club_id=:c)'''),{'m':mid,'c':club['id']})).mappings().first()
        if not row: return await cq.answer('Матч не найден.',show_alert=True)
        if row['status']!='SCHEDULED': return await cq.answer('Предматчевый обзор доступен до стартового свистка.',show_alert=True)
        home=await _starting_players(s,row['home_id']); away=await _starting_players(s,row['away_id'])
        ht=await _club_tactics(s,row['home_id']); at=await _club_tactics(s,row['away_id'])
        hb=await _club_board(s,row['home_id'],home,ht.formation); ab=await _club_board(s,row['away_id'],away,at.formation)
    try:
        preview=build_preview(home,away,ht,at,hb,ab)
        preview['weather']=weather_label(generate_weather(__import__('random').Random(int(row['seed'])*1009+1)))
    except Exception:
        logger.exception('Match preview build failed: user_id=%s match_id=%s',cq.from_user.id,mid)
        preview=None
    if not preview:
        lines=[
            '🔍 <b>ОБЗОР МАТЧА</b>',
            f'<b>{row["home"]}</b> — <b>{row["away"]}</b>',
            '',
            'Детальный обзор будет доступен в следующем обновлении.',
        ]
    else:
        def phase_line(key,label):
            x=preview['phases'][key]; return f'{label}: {x["relation"]} · {x["percent"]:.1f}%'
        lines=[f'🔍 <b>ОБЗОР МАТЧА</b>\n<b>{row["home"]}</b> — <b>{row["away"]}</b>','', '📈 <b>Силы по фазам</b>',phase_line('att','ATT'),phase_line('mid','MID'),phase_line('def','DEF'),'']
        lines += ['⭐ <b>Ключевые игроки</b>',row['home']+': '+', '.join(f'{x["name"]} · {x["position"]} · {x["ovr"]:.0f}' for x in preview['key_players']['home']),row['away']+': '+', '.join(f'{x["name"]} · {x["position"]} · {x["ovr"]:.0f}' for x in preview['key_players']['away']),'']
        lines += ['✨ <b>Активные таланты</b>']
        for side,label in (('home',row['home']),('away',row['away'])):
            vals=preview['active_talents'][side]
            lines.append(label+': '+(', '.join(f'{x["name"]} +{x["effect"]*100:.1f}% {x["phase"]}' for x in vals[:6]) if vals else 'нет активных талантов'))
        xr=preview['xg_range']; lines += ['',f'🎛 <b>Матчап:</b> {preview["style_matchup"]["text"]}',f'🎯 <b>xG:</b> {xr["home"][0]:.2f}–{xr["home"][1]:.2f} — {xr["away"][0]:.2f}–{xr["away"][1]:.2f}']
    k=InlineKeyboardBuilder(); k.button(text='⬅️ Назад',callback_data=f'match:prep:{mid}'); k.adjust(1)
    await cq.message.edit_text('\n'.join(lines),reply_markup=k.as_markup()); await cq.answer()

@router.callback_query(F.data.regexp(r'^match:watch:\d+$'))
async def cb_match_watch(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    async with SessionLocal() as s:
        row=(await s.execute(text('SELECT status,scheduled_at FROM matches WHERE id=:m'),{'m':mid})).mappings().first()
    if row and row['status']=='SCHEDULED':
        k=InlineKeyboardBuilder(); k.button(text='🔍 Обзор матча',callback_data=f'match:preview:{mid}'); k.adjust(1)
        await cq.message.edit_text(
            '📅 <b>ПОДГОТОВКА К МАТЧУ</b>\n\n'
            '⚽ Предматчевый экран открыт. Подготовьтесь к матчу.',
            reply_markup=k.as_markup())
        return await cq.answer()
    # Acknowledge the callback immediately so Telegram does not keep the button
    # spinner while the database query/message creation is running.
    await cq.answer('📺 Открываю live-экран…')
    opened=await _open_live_screen(cq,mid)
    if not opened:
        try:
            await cq.bot.send_message(cq.message.chat.id,'⚠️ Не удалось открыть live-экран. Попробуйте ещё раз.')
        except Exception:
            logger.exception('Could not report live screen open failure: user_id=%s match_id=%s',cq.from_user.id,mid)

@router.callback_query(F.data.startswith('match:watch:refresh:'))
async def cb_match_watch_refresh(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    ok,finished=await _render_live_screen(cq.message,cq.from_user.id,mid,force=True)
    await cq.answer('Матч завершён.' if finished else ('🔄 Обновлено' if ok else 'Матч недоступен.'),show_alert=not ok)

@router.callback_query(F.data.startswith('match:watch:lineups:'))
async def cb_match_watch_lineups(cq:CallbackQuery):
    await _cancel_live_screen(cq.from_user.id)
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    async with SessionLocal() as s:
        row=(await s.execute(text('SELECT m.id,m.home_club_id,m.away_club_id,h.name home,a.name away FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id WHERE m.id=:m'),{'m':mid})).mappings().first()
        if not row:return await cq.answer('Матч не найден.',show_alert=True)
        players=(await s.execute(text("""SELECT ml.club_id,ml.position,ml.role,ml.is_starting,p.first_name,p.last_name,cp.fitness,p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental
            FROM match_lineups ml JOIN players p ON p.id=ml.player_id LEFT JOIN club_players cp ON cp.club_id=ml.club_id AND cp.player_id=ml.player_id
            WHERE ml.match_id=:m AND ml.is_starting=true ORDER BY ml.club_id,ml.id"""),{'m':mid})).mappings().all()
    home=[x for x in players if x['club_id']==row['home_club_id']]; away=[x for x in players if x['club_id']==row['away_club_id']]
    out=['👥 <b>СОСТАВЫ</b>',f'<b>{row["home"]}</b>  |  <b>{row["away"]}</b>','']
    for i in range(max(len(home),len(away))):
        cols=[]
        for p in ((home[i] if i<len(home) else None),(away[i] if i<len(away) else None)):
            if not p: cols.append('—'); continue
            pr=type('P',(),dict(p,fitness=int(p.get('fitness') or 100),form=0,morale=70,position=p['position']))()
            cols.append(f'{display_player_name(p["first_name"],p["last_name"])} · OVR {int(round(float(player_rating(pr))))} · {p["position"]} · FIT {int(p.get("fitness") or 100)}')
        out.append(f'{cols[0]}  |  {cols[1]}')
    k=InlineKeyboardBuilder(); k.button(text='⬅️ К live-экрану',callback_data=f'match:watch:live:{mid}'); k.button(text='⬅️ Назад',callback_data=f'match:watch:back:{mid}'); k.button(text='🏠 Главное меню',callback_data='menu:main'); k.adjust(2,1)
    await _edit_live_message(cq.message,'\n'.join(out),k.as_markup()); await cq.answer()

@router.callback_query(F.data.startswith('match:watch:stats:'))
async def cb_match_watch_stats(cq:CallbackQuery):
    await _cancel_live_screen(cq.from_user.id)
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    async with SessionLocal() as s:
        row=(await s.execute(text("""SELECT m.home_club_id,m.away_club_id,m.home_score,m.away_score,m.home_xg,m.away_xg,m.home_possession,m.away_possession,m.home_shots,m.away_shots,m.home_shots_on_target,m.away_shots_on_target,m.home_corners,m.away_corners,h.name home,a.name away FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id WHERE m.id=:m"""),{'m':mid})).mappings().first()
        stats=(await s.execute(text('SELECT ps.rating,p.first_name,p.last_name,ps.goals,ps.assists FROM match_player_stats ps JOIN players p ON p.id=ps.player_id WHERE ps.match_id=:m ORDER BY ps.rating DESC LIMIT 5'),{'m':mid})).mappings().all()
        event_counts=(await s.execute(text('SELECT club_id,event_type,count(*) n FROM match_events WHERE match_id=:m GROUP BY club_id,event_type'),{'m':mid})).all()
    if not row:return await cq.answer('Матч не найден.',show_alert=True)
    counts={(int(c),str(t)):int(n) for c,t,n in event_counts}; hc,ac=row['home_club_id'],row['away_club_id']
    lines=['📊 <b>СТАТИСТИКА МАТЧА</b>',f'<b>{row["home"]} {row["home_score"] or 0}:{row["away_score"] or 0} {row["away"]}</b>','',f'🎯 Владение: {float(row["home_possession"] or 50):.0f}% — {float(row["away_possession"] or 50):.0f}%',f'🎯 Удары: {row["home_shots"] or 0} ({row["home_shots_on_target"] or 0}) — {row["away_shots"] or 0} ({row["away_shots_on_target"] or 0})',f'🚩 Угловые: {row["home_corners"] or 0} — {row["away_corners"] or 0}',f'🟨 Жёлтые: {counts.get((hc,"YELLOW"),0)} — {counts.get((ac,"YELLOW"),0)}',f'🟥 Красные: {counts.get((hc,"RED"),0)} — {counts.get((ac,"RED"),0)}',f'⚠️ Фолы: {counts.get((hc,"FOUL"),0)} — {counts.get((ac,"FOUL"),0)}',f'📊 xG: {float(row["home_xg"] or 0):.2f} — {float(row["away_xg"] or 0):.2f}','','⭐ <b>Лучшие игроки по рейтингу</b>']
    lines += [f'• {display_player_name(x["first_name"],x["last_name"])} — {float(x["rating"]):.1f}' for x in stats] or ['• Рейтинг появится после завершения матча']
    k=InlineKeyboardBuilder(); k.button(text='⬅️ К live-экрану',callback_data=f'match:watch:live:{mid}'); k.button(text='⬅️ Назад',callback_data=f'match:watch:back:{mid}'); k.adjust(2)
    await _edit_live_message(cq.message,'\n'.join(lines),k.as_markup()); await cq.answer()

@router.callback_query(F.data.startswith('match:watch:report:'))
async def cb_match_watch_report(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    await _cancel_live_screen(cq.from_user.id)
    async with SessionLocal() as s:
        row=(await s.execute(text("""SELECT m.status,m.home_score,m.away_score,m.home_xg,m.away_xg,m.home_possession,m.away_possession,
            m.home_shots,m.away_shots,m.home_shots_on_target,m.away_shots_on_target,m.home_corners,m.away_corners,
            m.home_penalties,m.away_penalties,h.id home_club_id,a.id away_club_id,h.name home,a.name away,m.motm_player_id
            FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id WHERE m.id=:m"""),{'m':mid})).mappings().first()
        if not row: return await cq.answer('Матч не найден.',show_alert=True)
        events=(await s.execute(text("""SELECT e.minute,e.event_type,e.metadata,e.club_id,e.player_id,p.first_name,p.last_name
            FROM match_events e LEFT JOIN players p ON p.id=e.player_id WHERE e.match_id=:m AND e.event_type IN ('GOAL','YELLOW','RED') ORDER BY e.minute,e.id"""),{'m':mid})).mappings().all()
        corner_counts=(await s.execute(text('''SELECT club_id,count(*) n FROM match_events WHERE match_id=:m AND event_type='CORNER' GROUP BY club_id'''),{'m':mid})).mappings().all()
        discipline_counts=(await s.execute(text("""SELECT club_id,event_type,count(*) n FROM match_events WHERE match_id=:m AND event_type IN ('FOUL','YELLOW','RED') GROUP BY club_id,event_type"""),{'m':mid})).all()
        corner_by_club={int(x['club_id']):int(x['n']) for x in corner_counts}
        ratings=(await s.execute(text("""SELECT ps.rating,ps.minutes,ps.goals,ps.assists,p.first_name,p.last_name
            FROM match_player_stats ps JOIN players p ON p.id=ps.player_id WHERE ps.match_id=:m ORDER BY ps.rating DESC,p.id LIMIT 10"""),{'m':mid})).mappings().all()
        lineups=(await s.execute(text("""SELECT ml.club_id,ml.position,p.id,p.first_name,p.last_name,p.age,p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,p.talents,ps.rating,ps.minutes
            FROM match_lineups ml JOIN players p ON p.id=ml.player_id LEFT JOIN match_player_stats ps ON ps.match_id=ml.match_id AND ps.player_id=ml.player_id
            WHERE ml.match_id=:m AND ml.is_starting=true ORDER BY ml.club_id,ml.id"""),{'m':mid})).mappings().all()
        tactics_rows=(await s.execute(text('SELECT club_id,formation,style,tempo,pressing,width,defensive_line,aggression FROM match_tactics WHERE match_id=:m'),{'m':mid})).mappings().all()
    club_names={row['home_club_id']:row['home'],row['away_club_id']:row['away']}
    discipline={(int(cid),str(kind)):int(n) for cid,kind,n in discipline_counts}
    report_score=f'{row["home_score"] or 0}:{row["away_score"] or 0}'
    if row.get('home_penalties') is not None and row.get('away_penalties') is not None:
        report_score += f' ({row["home_penalties"]}:{row["away_penalties"]} пен.)'
    out=['📊 <b>ПОЛНЫЙ ОТЧЁТ</b>',f'<b>{row["home"]} {report_score} {row["away"]}</b>','',
         f'📊 xG: {float(row["home_xg"] or 0):.2f} — {float(row["away_xg"] or 0):.2f}',
         f'🎯 Владение: {float(row["home_possession"] or 50):.0f}% — {float(row["away_possession"] or 50):.0f}%',
         f'🎯 Удары: {row["home_shots"] or 0} ({row["home_shots_on_target"] or 0}) — {row["away_shots"] or 0} ({row["away_shots_on_target"] or 0})',
         f'🚩 Угловые: {(row["home_corners"] if row["home_corners"] is not None else corner_by_club.get(int(row["home_club_id"]),0)) or corner_by_club.get(int(row["home_club_id"]),0)} — {(row["away_corners"] if row["away_corners"] is not None else corner_by_club.get(int(row["away_club_id"]),0)) or corner_by_club.get(int(row["away_club_id"]),0)}',
         f'⚠️ Фолы: {discipline.get((int(row["home_club_id"]),"FOUL"),0)} — {discipline.get((int(row["away_club_id"]),"FOUL"),0)}',
         f'🟨 Жёлтые: {discipline.get((int(row["home_club_id"]),"YELLOW"),0)} — {discipline.get((int(row["away_club_id"]),"YELLOW"),0)}',
         f'🟥 Красные: {discipline.get((int(row["home_club_id"]),"RED"),0)} — {discipline.get((int(row["away_club_id"]),"RED"),0)}','','⚽ <b>Ключевые события</b>']
    goals=[e for e in events if e['event_type']=='GOAL']
    def report_minute(event):
        metadata=event.get('metadata') or {}
        return str(metadata.get('display_minute')) if metadata.get('extra_time') else _format_live_minute(event['minute'])
    out += [f'• {report_minute(e)}′ {display_player_name(e.get("first_name"),e.get("last_name"))} ({club_names.get(e["club_id"],"Команда")})' for e in goals] or ['• Голы отсутствуют']
    yellows=[e for e in events if e['event_type']=='YELLOW']
    if yellows:
        out += ['', '🟨 <b>Жёлтые карточки</b>'] + [f'• {_format_live_minute(e["minute"])}′ {display_player_name(e.get("first_name"),e.get("last_name"))} ({club_names.get(e["club_id"],"Команда")})' for e in yellows]
    reds=[e for e in events if e['event_type']=='RED']
    if reds:
        out += ['', '🟥 <b>Удаления</b>'] + [f'• {_format_live_minute(e["minute"])}′ {display_player_name(e.get("first_name"),e.get("last_name"))} ({club_names.get(e["club_id"],"Команда")})' for e in reds]
    out += ['', '⭐ <b>Рейтинги</b>']
    out += [f'• {display_player_name(x["first_name"],x["last_name"])} — {float(x["rating"]):.1f} · {int(x["minutes"] or 0)}′ · ⚽{int(x["goals"] or 0)} 🅰️{int(x["assists"] or 0)}' for x in ratings] or ['• Нет рейтингов']
    out += ['', '👥 <b>Составы</b>']
    for cid,label in ((row['home_club_id'],row['home']),(row['away_club_id'],row['away'])):
        out.append(f'<b>{label}</b>')
        players=[x for x in lineups if x['club_id']==cid]
        out += [f'• {display_player_name(x["first_name"],x["last_name"])} · {x["position"]}' for x in players] or ['• Нет данных']
    from app.game.tactics import Tactics, Style
    tactics_by_club={int(x['club_id']):Tactics(formation=x['formation'],style=Style(x['style']),tempo=int(x['tempo']),pressing=int(x['pressing']),width=int(x['width']),defensive_line=int(x['defensive_line']),aggression=int(x['aggression'])) for x in tactics_rows}
    def report_player(x):
        return type('ReportPlayer',(),dict(x,fitness=100,form=0,morale=50,suspended=False,talents=tuple(x.get('talents') or ())))()
    home_state={'players':[report_player(x) for x in lineups if int(x['club_id'])==int(row['home_club_id'])],'tactics':tactics_by_club.get(int(row['home_club_id'])),'score':int(row['home_score'] or 0),'minute':90}
    away_state={'players':[report_player(x) for x in lineups if int(x['club_id'])==int(row['away_club_id'])],'tactics':tactics_by_club.get(int(row['away_club_id'])),'score':int(row['away_score'] or 0),'minute':90}
    if home_state['tactics'] and away_state['tactics']:
        analysis=build_post_match_analysis(home_state,away_state,events,modifiers_log=None)
        out += ['', '🧠 <b>РАЗБОР МАТЧА</b>',
                'ATT: '+analysis['phase_breakdown']['att']['relation']+' · '+str(analysis['phase_breakdown']['att']['percent'])+'%',
                'MID: '+analysis['phase_breakdown']['mid']['relation']+' · '+str(analysis['phase_breakdown']['mid']['percent'])+'%',
                'DEF: '+analysis['phase_breakdown']['def']['relation']+' · '+str(analysis['phase_breakdown']['def']['percent'])+'%']
        out.append('✨ <b>Топ-3 таланта</b>')
        out += [f'• {x["name"]} × {x["count"]}: +{x["effect"]:.1f}% {x["phase"]}' for x in analysis['top_talents']] or ['• Срабатываний не обнаружено']
        out += ['',f'🔋 Усталость: {analysis["fatigue_impact"]["home_percent"]:.0f}% — {analysis["fatigue_impact"]["away_percent"]:.0f}% (оценка по темпу/прессингу)',f'⚡ Urgency: {row["home"]} — {analysis["urgency_impact"]["home"]}; {row["away"]} — {analysis["urgency_impact"]["away"]}']
    k=InlineKeyboardBuilder(); k.button(text='⬅️ К live-экрану',callback_data=f'match:watch:live:{mid}'); k.button(text='⬅️ Назад',callback_data=f'match:watch:back:{mid}'); k.adjust(2)
    await _edit_live_message(cq.message,'\n'.join(out),k.as_markup()); await cq.answer()

_LIVE_SUB_SELECTIONS: dict[int, tuple[int,int,int]] = {}

async def _live_control_match(s, telegram_id, match_id):
    club=await club_by_tg(s,telegram_id)
    if not club:
        raise ValueError('Сначала создайте клуб.')
    row=(await s.execute(text('''SELECT m.id,m.status,m.current_minute,m.halftime_locked,m.home_club_id,m.away_club_id,
        h.name home,a.name away
        FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
        WHERE m.id=:m'''),{'m':match_id})).mappings().first()
    if not row: raise ValueError('Матч не найден.')
    if int(club['id']) not in (int(row['home_club_id']),int(row['away_club_id'])):
        raise ValueError('Управление доступно только участнику этого матча.')
    return club,row

async def _show_live_tactics(cq:CallbackQuery, match_id):
    async with SessionLocal() as s:
        try:
            club,row=await _live_control_match(s,cq.from_user.id,match_id)
        except ValueError as exc:
            return await cq.answer(str(exc),show_alert=True)
        if row['status']!='LIVE':
            return await cq.answer('Матч уже завершён.',show_alert=True)
        t=(await s.execute(text('SELECT formation,style,tempo,pressing,width,defensive_line,aggression FROM club_tactics WHERE club_id=:c'),{'c':club['id']})).mappings().first()
    if not t: return await cq.answer('Тактика клуба не найдена.',show_alert=True)
    k=InlineKeyboardBuilder()
    for f in ALLOWED_FORMATIONS:
        k.button(text=('✓ ' if f==t['formation'] else '')+f,callback_data=f'match:watch:tactic:f:{match_id}:{f}')
    labels={'BALANCE':'Баланс','ATTACK':'Атака','COUNTER':'Контратака','DEFENSE':'Защита','POSSESSION':'Владение'}
    for st in ('BALANCE','ATTACK','COUNTER','DEFENSE','POSSESSION'):
        k.button(text=('✓ ' if st==t['style'] else '')+labels[st],callback_data=f'match:watch:tactic:s:{match_id}:{st}')
    k.button(text='⬅️ К live-экрану',callback_data=f'match:watch:live:{match_id}')
    k.adjust(3,2,2,2,1)
    await _edit_live_message(cq.message,
        f'🎛 <b>ТАКТИКА LIVE</b>\n{row["home"]} — {row["away"]}\n\n'
        f'Формация: <b>{t["formation"]}</b>\nСтиль: <b>{labels.get(t["style"],t["style"])}</b>\n'
        f'Темп {t["tempo"]} · прессинг {t["pressing"]} · ширина {t["width"]} · линия {t["defensive_line"]} · агрессия {t["aggression"]}',
        k.as_markup())
    await cq.answer()

async def _show_live_substitutions(cq:CallbackQuery, match_id):
    async with SessionLocal() as s:
        try:
            club,row=await _live_control_match(s,cq.from_user.id,match_id)
        except ValueError as exc:
            return await cq.answer(str(exc),show_alert=True)
        if row['status']!='LIVE':
            return await cq.answer('Матч уже завершён.',show_alert=True)
        lineup=(await s.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'),{'c':club['id']})).scalar_one_or_none() or []
        lineup=[int(x) for x in lineup]
        phase_data=(await s.execute(text('SELECT second_half_data FROM matches WHERE id=:m'),{'m':match_id})).scalar_one_or_none() or {}
        used_off={int(x[1]) for x in (phase_data.get('substitutions') or [])}
        used_on={int(x[2]) for x in (phase_data.get('substitutions') or [])}
        players=(await s.execute(text('''SELECT p.*,cp.fitness,cp.form,cp.morale,cp.is_injured,cp.suspension_matches
            FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND cp.is_injured=false AND cp.suspension_matches=0
            ORDER BY p.position,p.potential DESC,p.id'''),{'c':club['id']})).mappings().all()
        exited=set((await s.execute(text('''SELECT player_id FROM match_events
            WHERE match_id=:m AND event_type IN ('RED','INJURY') AND minute<=45 AND player_id IS NOT NULL'''),{'m':match_id})).scalars().all())
    starters=[p for p in players if int(p['id']) in lineup and int(p['id']) not in exited and int(p['id']) not in used_off]
    bench=[p for p in players if int(p['id']) not in lineup and int(p['id']) not in used_on][:7]
    if not starters or not bench:
        return await cq.answer('Для замены сейчас недостаточно игроков.',show_alert=True)
    k=InlineKeyboardBuilder()
    for p in bench:
        pr=_rating_player(p); overall=player_rating(pr)
        label=f'{_compact_player_name(p["first_name"],p["last_name"])} · {POSITION_LABELS_RU.get(p["position"],p["position"])} · {overall:.0f}'
        k.button(text=f'⬆️ {label}',callback_data=f'match:watch:subon:{match_id}:{p["id"]}')
    k.button(text='⬅️ К live-экрану',callback_data=f'match:watch:live:{match_id}')
    k.adjust(1)
    await _edit_live_message(cq.message,'🔄 <b>ЗАМЕНА</b>\n\nСначала выберите, кого выпускаем со скамейки:',k.as_markup())
    await cq.answer()

@router.callback_query(F.data.startswith('match:watch:live:'))
async def cb_match_watch_live(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    async with _LIVE_SCREEN_LOCK:
        await _cancel_live_screen_unlocked(cq.from_user.id)
        ok,finished=await _render_live_screen(cq.message,cq.from_user.id,mid,force=True)
        if ok and not finished:
            _LIVE_SCREENS[cq.from_user.id]=asyncio.create_task(_live_screen_loop(cq.message,cq.from_user.id,mid),name=f'live-screen-{cq.from_user.id}-{mid}')
    await cq.answer('📺 Live-экран' if ok else 'Матч недоступен.',show_alert=not ok)

@router.callback_query(F.data.startswith('match:watch:tactics:'))
async def cb_match_watch_tactics(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    await _cancel_live_screen(cq.from_user.id)
    await _show_live_tactics(cq,mid)

@router.callback_query(F.data.startswith('match:watch:sub:'))
async def cb_match_watch_sub(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректный матч.',show_alert=True)
    await _cancel_live_screen(cq.from_user.id)
    await _show_live_substitutions(cq,mid)

async def _show_live_substitution_outgoing(cq:CallbackQuery, match_id:int, on_id:int):
    async with SessionLocal() as s:
        try:
            club,row=await _live_control_match(s,cq.from_user.id,match_id)
        except ValueError as exc:
            return await cq.answer(str(exc),show_alert=True)
        if row['status']!='LIVE': return await cq.answer('Матч уже завершён.',show_alert=True)
        lineup=(await s.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'),{'c':club['id']})).scalar_one_or_none() or []
        phase_data=(await s.execute(text('SELECT second_half_data FROM matches WHERE id=:m'),{'m':match_id})).scalar_one_or_none() or {}
        used_off={int(x[1]) for x in (phase_data.get('substitutions') or [])}; used_on={int(x[2]) for x in (phase_data.get('substitutions') or [])}
        exited=set((await s.execute(text('''SELECT player_id FROM match_events WHERE match_id=:m AND event_type IN ('RED','INJURY') AND minute<=45 AND player_id IS NOT NULL'''),{'m':match_id})).scalars().all())
        starters=(await s.execute(text('''SELECT p.*,cp.fitness,cp.form,cp.morale FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND p.id=ANY(:ids) AND cp.is_injured=false AND cp.suspension_matches=0 ORDER BY p.position,p.potential DESC,p.id'''),{'c':club['id'],'ids':[int(x) for x in lineup] or [0]})).mappings().all()
    starters=[p for p in starters if int(p['id']) not in exited and int(p['id']) not in used_off and int(p['id']) not in used_on]
    if not starters: return await cq.answer('На поле нет доступного игрока для замены.',show_alert=True)
    k=InlineKeyboardBuilder()
    for p in starters:
        pr=_rating_player(p); overall=player_rating(pr); label=f'{_compact_player_name(p["first_name"],p["last_name"])} · {POSITION_LABELS_RU.get(p["position"],p["position"])} · {overall:.0f}'
        k.button(text=f'⬇️ {label}',callback_data=f'match:watch:suboff:{match_id}:{on_id}:{p["id"]}')
    k.button(text='⬅️ Назад',callback_data=f'match:watch:sub:{match_id}'); k.adjust(1)
    await _edit_live_message(cq.message,'🔄 <b>ЗАМЕНА</b>\n\nТеперь выберите, кого убираем с поля:',k.as_markup()); await cq.answer()

@router.callback_query(F.data.startswith('match:watch:suboff:'))
async def cb_match_watch_suboff(cq:CallbackQuery):
    parts=cq.data.split(':')
    if len(parts)!=6 or not all(x.isdigit() for x in parts[3:6]): return await cq.answer('Некорректная замена.',show_alert=True)
    mid,on_id,off_id=map(int,parts[3:6])
    async with SessionLocal() as s:
        try: _club,row=await _live_control_match(s,cq.from_user.id,mid)
        except ValueError as exc: return await cq.answer(str(exc),show_alert=True)
        if row['status']!='LIVE': return await cq.answer('Матч уже завершён.',show_alert=True)
        current=int(row['current_minute'] or 45)
    k=InlineKeyboardBuilder(); minute_candidates=[m for m in (46,50,55,60,65,70,75,80,85) if m>current]
    for minute in minute_candidates[:6]: k.button(text=f'{minute}′',callback_data=f'match:watch:subapply:{mid}:{off_id}:{on_id}:{minute}')
    k.button(text='⬅️ Назад',callback_data=f'match:watch:sub:{mid}'); k.adjust(2,2,1)
    await _edit_live_message(cq.message,'🔄 <b>ЗАМЕНА</b>\n\nВыберите минуту выхода:',k.as_markup()); await cq.answer()

@router.callback_query(F.data.startswith('match:watch:subon:'))
async def cb_match_watch_subon(cq:CallbackQuery):
    parts=cq.data.split(':')
    if len(parts)!=5 or not parts[3].isdigit() or not parts[4].isdigit(): return await cq.answer('Некорректная замена.',show_alert=True)
    return await _show_live_substitution_outgoing(cq,int(parts[3]),int(parts[4]))

@router.callback_query(F.data.startswith('match:watch:subapply:'))
async def cb_match_watch_subapply(cq:CallbackQuery):
    parts=cq.data.split(':')
    if len(parts)!=7 or not all(x.isdigit() for x in parts[3:7]):
        return await cq.answer('Некорректная замена.',show_alert=True)
    mid,off_id,on_id,minute=map(int,parts[3:7])
    async with SessionLocal() as s:
        try:
            club,row=await _live_control_match(s,cq.from_user.id,mid)
            if row['status']!='LIVE':
                raise ValueError('Матч уже завершён.')
            current=int(row['current_minute'] or 45)
            if minute<=current or minute>90:
                raise ValueError(f'Минута замены должна быть после текущей ({current}′) и не позже 90′.')
            # Validate the player pair against the normal substitution rules.
            await plan_substitution(s,int(club['id']),minute,off_id,on_id)
            data=dict(row.get('second_half_data') or {})
            substitutions=[list(x) for x in (data.get('substitutions') or [])]
            team='HOME' if int(club['id'])==int(row['home_club_id']) else 'AWAY'
            if sum(1 for x in substitutions if str(x[3])==team)>=5:
                raise ValueError('Достигнут лимит в 5 замен.')
            if any(int(x[1])==off_id for x in substitutions):
                raise ValueError('Этот игрок уже запланирован на замену.')
            if any(int(x[2])==on_id for x in substitutions):
                raise ValueError('Этот игрок уже выпущен по плану замены.')
            substitutions.append([minute,off_id,on_id,team])
            substitutions.sort(key=lambda x:(int(x[0]),int(x[1])))
            data['substitutions']=substitutions
            await s.execute(text('UPDATE matches SET second_half_data=:data WHERE id=:m'),{'data':json.dumps(data),'m':mid})
            await s.commit()
        except ValueError as exc:
            await s.rollback()
            return await cq.answer(str(exc),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Live substitution planning failed: match_id=%s',mid)
            return await cq.answer('❌ Не удалось запланировать замену.',show_alert=True)
    _LIVE_SUB_SELECTIONS.pop(cq.from_user.id,None)
    await cq.answer(f'🔄 Замена запланирована на {minute}′.')
    await _cancel_live_screen(cq.from_user.id)
    ok,finished=await _render_live_screen(cq.message,cq.from_user.id,mid,force=True)
    if ok and not finished:
        _LIVE_SCREENS[cq.from_user.id]=asyncio.create_task(_live_screen_loop(cq.message,cq.from_user.id,mid),name=f'live-screen-{cq.from_user.id}-{mid}')

@router.callback_query(F.data.startswith('match:watch:tactic:'))
async def cb_match_watch_tactic_choice(cq:CallbackQuery):
    parts=cq.data.split(':')
    if len(parts)!=6 or parts[3] not in {'f','s'} or not parts[4].isdigit():
        return await cq.answer('Некорректная тактика.',show_alert=True)
    kind=parts[3]; mid=int(parts[4]); value=parts[5]
    async with SessionLocal() as s:
        try:
            club,row=await _live_control_match(s,cq.from_user.id,mid)
            if row['status']!='LIVE':
                raise ValueError('Матч уже завершён.')
            t=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':club['id']})).mappings().first()
            if not t: raise ValueError('Тактика клуба не найдена.')
            if kind=='f' and value not in ALLOWED_FORMATIONS:
                raise ValueError('Неизвестная формация.')
            formation=value if kind=='f' else t['formation']
            try:
                style=Style(value) if kind=='s' else Style(t['style'])
            except ValueError:
                raise ValueError('Неизвестный стиль.')
            # STYLE_SLIDERS[style] is the style baseline; the wrapper adds the current formation delta.
            sliders=apply_formation_sliders(style,formation)
            tactics=Tactics(formation=formation,style=style,tempo=int(sliders[0]),pressing=int(sliders[1]),width=int(sliders[2]),defensive_line=int(sliders[3]),aggression=int(sliders[4]))
            if row['halftime_locked']:
                await change_halftime_tactics(s,row['id'],club['id'],tactics)
            else:
                data=dict((await s.execute(text('SELECT second_half_data FROM matches WHERE id=:m'),{'m':mid})).scalar_one_or_none() or {})
                side='home_tactics' if int(club['id'])==int(row['home_club_id']) else 'away_tactics'
                data[side]={
                    'formation':tactics.formation,'style':tactics.style.value,'tempo':tactics.tempo,
                    'pressing':tactics.pressing,'width':tactics.width,'defensive_line':tactics.defensive_line,
                    'aggression':tactics.aggression,
                }
                await s.execute(text('''UPDATE club_tactics SET formation=:f,style=:s,tempo=:t,pressing=:p,width=:w,defensive_line=:d,aggression=:a WHERE club_id=:c'''),
                    {'f':tactics.formation,'s':tactics.style.value,'t':tactics.tempo,'p':tactics.pressing,'w':tactics.width,'d':tactics.defensive_line,'a':tactics.aggression,'c':club['id']})
                await s.execute(text('UPDATE matches SET second_half_data=:data WHERE id=:m'),{'data':json.dumps(data),'m':mid})
            await record_tactic_change(s,int(club['id']))
            await s.commit()
        except ValueError as exc:
            await s.rollback(); return await cq.answer(str(exc),show_alert=True)
        except Exception:
            await s.rollback(); logger.exception('Live tactic update failed: match_id=%s',mid)
            return await cq.answer('❌ Не удалось изменить тактику.',show_alert=True)
    await _show_live_tactics(cq,mid)

@router.callback_query(F.data.startswith('match:watch:back:'))
async def cb_match_watch_back(cq:CallbackQuery):
    try: mid=int(cq.data.rsplit(':',1)[1])
    except ValueError: mid=0
    await _cancel_live_screen(cq.from_user.id)
    _LIVE_SCREEN_SIGNATURES.pop((cq.from_user.id,mid),None)
    await cq.answer()
    try:
        await cq.message.delete()
    except Exception:
        await _edit_live_message(cq.message,'📺 <b>Матч-центр</b>\n\nНажмите «📺 Смотреть матч» для live-просмотра.',back_keyboard('matchcenter'))
    logger.info('Live screen closed by user: user_id=%s match_id=%s',cq.from_user.id,mid)

@router.callback_query(F.data=='table')
async def cb_table(c:CallbackQuery): await c.answer(); await cmd_tableview(c.message, c.from_user)
@router.callback_query(F.data=='matchcenter')
async def cb_matchcenter(c:CallbackQuery): await c.answer(); await cmd_matchcenter(c.message, c.from_user)
@router.callback_query(F.data=='fixtures')
async def cb_fixtures(c:CallbackQuery): await c.answer(); await cmd_fixturesview(c.message, c.from_user)
@router.callback_query(F.data=='scout')
async def cb_scout(c:CallbackQuery):
    # Legacy scout entry point now redirects to the unified transfer market.
    await c.answer()
    await _show_market(c, c.from_user.id)


@router.callback_query(F.data=='finance')
async def cb_finance(c:CallbackQuery):
    await c.answer()
    await _show_finance(c.message,c.from_user.id)


@router.callback_query(F.data=='scout:position')
async def cb_scout_position(c:CallbackQuery):
    await c.answer(); await c.message.edit_text('📍 <b>Позиции</b>\nМожно выбрать одну или несколько:',reply_markup=_scout_position_keyboard(c.from_user.id))


@router.callback_query(F.data.startswith('scout:position:'))
async def cb_scout_position_pick(c:CallbackQuery):
    code=c.data.rsplit(':',1)[1]; f=_scout_filter_state(c.from_user.id)
    if code=='any': f['position']=[]
    elif code in {'GK','LB','CB','RB','DM','CM','AM','LW','RW','ST'}:
        current=set(f.get('position') or [])
        if code in current: current.remove(code)
        else: current.add(code)
        f['position']=sorted(current)
    else: return await c.answer('Неизвестная позиция.',show_alert=True)
    await c.answer(); await c.message.edit_text('📍 <b>Позиции</b>\nМожно выбрать одну или несколько:',reply_markup=_scout_position_keyboard(c.from_user.id))


@router.callback_query(F.data.startswith('scout:age'))
async def cb_scout_age(c:CallbackQuery):
    await c.answer(); await c.message.edit_text('🎂 <b>Возраст</b>',reply_markup=_scout_range_keyboard('age'))


@router.callback_query(F.data=='scout:potential')
async def cb_scout_potential(c:CallbackQuery):
    await c.answer(); await c.message.edit_text('⭐ <b>Потенциал</b>',reply_markup=_scout_range_keyboard('potential'))


@router.callback_query(F.data=='scout:value')
async def cb_scout_value(c:CallbackQuery,state:FSMContext):
    await state.update_data(field='value'); await state.set_state(ScoutStates.entering_filter)
    await c.answer(); await c.message.answer('Введите диапазон стоимости, например: 100000-500000')


@router.callback_query(F.data=='scout:wage')
async def cb_scout_wage(c:CallbackQuery,state:FSMContext):
    await state.update_data(field='wage'); await state.set_state(ScoutStates.entering_filter)
    await c.answer(); await c.message.answer('Введите диапазон зарплаты, например: 20000-60000')


@router.callback_query(F.data=='scout:nationality')
async def cb_scout_nationality(c:CallbackQuery,state:FSMContext):
    await state.update_data(field='nationality'); await state.set_state(ScoutStates.entering_filter)
    await c.answer(); await c.message.answer('Введите 3-буквенный код национальности, например SWE')


@router.callback_query(F.data=='scout:attribute')
async def cb_scout_attribute(c:CallbackQuery,state:FSMContext):
    await state.update_data(field='attribute'); await state.set_state(ScoutStates.entering_filter)
    await c.answer(); await c.message.answer('Введите атрибут и порог, например pace>75 или shooting>80')


@router.callback_query(F.data.startswith('scout:input:'))
async def cb_scout_input(c:CallbackQuery,state:FSMContext):
    field=c.data.rsplit(':',1)[1]
    await state.update_data(field=field); await state.set_state(ScoutStates.entering_filter)
    await c.answer(); await c.message.answer('Введите диапазон min-max или одно число:')


@router.callback_query(F.data.startswith('scout:range:'))
async def cb_scout_range(c:CallbackQuery):
    parts=c.data.split(':'); field=parts[2]; f=_scout_filter_state(c.from_user.id)
    if len(parts)>=4 and parts[3]=='any': f[field]=None
    else:
        lo=int(parts[3]); hi=int(parts[4]) if len(parts)>4 and parts[4] else None
        f[field]={'min':lo,'max':hi}
    await c.answer(); await c.message.edit_text(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))


@router.callback_query(F.data.startswith('scout:set:'))
async def cb_scout_set(c:CallbackQuery):
    _,_,field,value=c.data.split(':',3); f=_scout_filter_state(c.from_user.id)
    f[field]=None if value=='any' else value
    await c.answer(); await c.message.edit_text(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))


@router.callback_query(F.data=='scout:status')
async def cb_scout_status(c:CallbackQuery):
    await c.answer(); await c.message.edit_text('📦 <b>Статус</b>',reply_markup=_scout_simple_keyboard('transfer_status',[('free','Свободен'),('listed','Выставлен'),('not_for_sale','Не продается')]))


@router.callback_query(F.data=='scout:foot')
async def cb_scout_foot(c:CallbackQuery):
    await c.answer(); await c.message.edit_text('🦶 <b>Предпочтительная нога</b>',reply_markup=_scout_simple_keyboard('preferred_foot',[('L','Левая'),('R','Правая')]))


@router.message(ScoutStates.entering_filter)
async def scout_filter_input(m:Message,state:FSMContext):
    data=await state.get_data(); field=data.get('field'); value=(m.text or '').strip()
    f=_scout_filter_state(m.from_user.id)
    try:
        if field=='nationality':
            if not re.fullmatch(r'[A-Za-z]{3}',value): raise ValueError
            f['nationality']=value.upper()
        elif field=='attribute':
            match=re.fullmatch(r'(pace|shooting|passing|dribbling|defending|physical|stamina|mental)\s*[><=]\s*(\d{1,3})',value,re.I)
            if not match: raise ValueError
            f.setdefault('attributes',{})[match.group(1).lower()]={'min':int(match.group(2))}
        elif field in {'age','potential','value','wage'}:
            match=re.fullmatch(r'(\d+)\s*(?:-\s*(\d+))?',value)
            if not match: raise ValueError
            lo=int(match.group(1)); hi=int(match.group(2)) if match.group(2) else None
            f[field]={'min':lo,'max':hi}
        else: raise ValueError
    except ValueError:
        return await m.answer('Некорректный формат. Пример: 18-23, 100000-500000, SWE или pace>75.')
    await state.clear(); f['page']=0
    await m.answer(_scout_menu_text(m.from_user.id),reply_markup=_scout_menu_keyboard(m.from_user.id))


@router.callback_query(F.data.startswith('scout:back:'))
async def cb_scout_back(c:CallbackQuery):
    try: page=int(c.data.rsplit(':',1)[1])
    except ValueError: page=0
    try: return await _show_scout_results(c,c.from_user.id,max(0,page))
    except Exception:
        logger.exception('Scout back navigation failed: user_id=%s page=%s',c.from_user.id,page)
        return await c.answer('❌ Не удалось обновить список игроков.',show_alert=True)


# @router.callback_query(F.data.startswith('scout:search:'))
@router.callback_query(F.data=='scout:search')
async def cb_scout_search(c:CallbackQuery):
    _scout_filter_state(c.from_user.id)['page']=0
    await _show_scout_results(c,c.from_user.id,0)


@router.callback_query(F.data.startswith('scout:page:'))
async def cb_scout_page(c:CallbackQuery):
    try: page=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer('Некорректная страница.',show_alert=True)
    await _show_scout_results(c,c.from_user.id,max(0,page))


@router.callback_query(F.data=='scout:reset')
async def cb_scout_reset(c:CallbackQuery):
    _SCOUT_FILTERS[c.from_user.id]={'position':[],'age':None,'potential':None,'value':None,'wage':None,'nationality':None,'transfer_status':None,'preferred_foot':None,'attributes':{},'page':0,'sort':'id_asc'}
    await c.answer('Фильтры сброшены.')
    await c.message.edit_text(_scout_menu_text(c.from_user.id),reply_markup=_scout_menu_keyboard(c.from_user.id))



async def _show_comparison_picker(cq, target_id):
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=(await s.execute(text("""SELECT p.id,p.first_name,p.last_name,p.position
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND p.id<>:p
            ORDER BY p.position,p.id"""),{'c':club['id'],'p':int(target_id)})).mappings().all()
    if not rows: return await cq.answer('Нет второго игрока для сравнения.',show_alert=True)
    k=InlineKeyboardBuilder()
    for r in rows[:30]:
        k.button(text=f'{display_player_name(r.get("first_name"),r.get("last_name"))} · {r["position"]}',callback_data=f'compare:pick:{int(target_id)}:{int(r["id"])}')
    k.button(text='🔎 Выбрать из скаутинга',callback_data=f'compare:scout:{int(target_id)}')
    k.button(text='⬅️ Назад',callback_data='squad'); k.adjust(1)
    await cq.message.answer('🔄 <b>Сравнить с игроком</b>\nВыберите второго игрока:',reply_markup=k.as_markup()); await cq.answer()


def _comparison_text(left, right, result):
    l,r,d=result['player1'],result['player2'],result['delta']
    title=f'🔄 <b>{display_player_name(left.get("first_name"),left.get("last_name"))} vs {display_player_name(right.get("first_name"),right.get("last_name"))}</b>'
    lines=[title,'']
    labels=[('ОВР','ovr'),('Потенциал','potential'),('Стоимость','market_value'),('Зарплата','salary'),('Возраст','age'),('Усталость','fitness'),('Форма','form'),('Мораль','morale')]
    for label,key in labels:
        if key=='fitness' and (int(l[key])<=0 or int(r[key])<=0):
            lines.append(f'{label}: {l[key] or "Н/Д"} vs {r[key] or "Н/Д"} (Н/Д)'); continue
        delta=d[key]; lines.append(f'{label}: {l[key]} vs {r[key]} ({"+" if delta>0 else ""}{delta})')
    lines.append('')
    for field,label in [('pace','Скорость'),('shooting','Удар'),('passing','Пас'),('dribbling','Дриблинг'),('defending','Защита'),('physical','Физика'),('stamina','Выносливость'),('mental','Ментальность')]:
        delta=d['attributes'][field]; lines.append(f'{label}: {l["attributes"][field]} vs {r["attributes"][field]} ({"+" if delta>0 else ""}{delta})')
    return '\n'.join(lines)


@router.callback_query(F.data.startswith('compare:pick:'))
async def cb_compare_pick(cq:CallbackQuery):
    try: _,_,a,b=cq.data.split(':'); a=int(a); b=int(b)
    except (ValueError,TypeError): return await cq.answer('Некорректный выбор.',show_alert=True)
    if a==b: return await _show_comparison_picker(cq,a)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=(await s.execute(text("""SELECT p.*,COALESCE(cp.fitness,100) fitness,COALESCE(cp.form,0) form,COALESCE(cp.morale,70) morale
            FROM players p LEFT JOIN club_players cp ON cp.player_id=p.id AND cp.club_id=:c
            WHERE p.id IN (:a,:b)"""),{'c':club['id'],'a':a,'b':b})).mappings().all()
    by={int(r['id']):r for r in rows}
    if a not in by or b not in by:return await cq.answer('Игрок недоступен.',show_alert=True)
    await cq.answer(); await cq.message.answer(_comparison_text(by[a],by[b],compare_players(by[a],by[b])),reply_markup=back_keyboard('squad'))


@router.callback_query(F.data.startswith('compare:scout:'))
async def cb_compare_scout(cq:CallbackQuery):
    try: target=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    _COMPARISON_TARGETS[cq.from_user.id]=target
    _SCOUT_FILTERS.setdefault(cq.from_user.id,{'position':[],'age':None,'potential':None,'value':None,'wage':None,'nationality':None,'transfer_status':None,'preferred_foot':None,'attributes':{},'page':0,'sort':'id_asc'})['page']=0
    await cq.answer(); await _show_scout_results(cq,cq.from_user.id,0)


@router.callback_query(F.data=='shortlist')
async def cb_shortlist(cq:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        rows=await get_shortlist(club['id'],s)
    if not rows:return await cq.message.answer('⭐ <b>СПИСОК ЖЕЛАНИЙ</b>\n\nСписок пока пуст.',reply_markup=back_keyboard('menu:main'))
    lines=['⭐ <b>СПИСОК ЖЕЛАНИЙ</b>']; k=InlineKeyboardBuilder()
    for r in rows:
        interest=calculate_interest(r,club)
        pr=type('ShortlistPlayer',(),r)()
        lines.append(f'{display_player_name(r.get("first_name"),r.get("last_name"))} · {r["position"]} · OVR {player_rating(pr)} · Interest {interest["interest_score"]}/100')
        k.button(text=f'👤 {display_player_name(r.get("first_name"),r.get("last_name"))[:24]}',callback_data=f'shortlist:view:{r["player_id"]}')
    k.button(text='🏠 Главное меню',callback_data='menu:main'); k.adjust(1)
    await cq.answer(); await cq.message.answer('\n'.join(lines),reply_markup=k.as_markup())


@router.callback_query(F.data.startswith('shortlist:view:'))
async def cb_shortlist_view(cq:CallbackQuery):
    try: pid=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        row=(await s.execute(text('SELECT p.*,cp.fitness,cp.form,cp.morale FROM players p LEFT JOIN club_players cp ON cp.player_id=p.id AND cp.club_id=:c WHERE p.id=:p'),{'c':club['id'],'p':pid})).mappings().first() if club else None
        trend=await get_development_trend(pid,s,30) if row else []
    if not row:return await cq.answer('Игрок не найден.',show_alert=True)
    data=_card_player(row); card=player_card(data,out=Path('assets/visual/cards')/f'shortlist_player_{pid}.png'); interest=calculate_interest(row,club)
    caption=f'🃏 <b>{display_player_name(row.get("first_name"),row.get("last_name"))}</b>\n{row["position"]} · OVR <b>{data["overall"]}</b> · 🎯 POT {row["potential"]}\n💰 €{int(row["market_value"]):,} · Interest <b>{interest["interest_score"]}/100</b> ({interest["verdict"]})'
    if trend:
        caption += f'\n📈 <b>История развития:</b> {format_trend(trend)}'
        if len(trend)>=2: caption += f'\n💰 Value: €{trend[0][2]:,} → €{trend[-1][2]:,} ({trend[-1][2]-trend[0][2]:+d})'
    k=InlineKeyboardBuilder(); k.button(text='🔄 Сравнить',callback_data=f'compare:pick:{pid}:{pid}'); k.button(text='❌ Убрать из списка',callback_data=f'shortlist:remove:{pid}'); k.button(text='⬅️ К списку',callback_data='shortlist'); k.adjust(1)
    await cq.answer(); await cq.message.answer_photo(FSInputFile(card),caption=caption,reply_markup=k.as_markup())


@router.callback_query(F.data.startswith('shortlist:remove:'))
async def cb_shortlist_remove(cq:CallbackQuery):
    try: pid=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        await remove_from_shortlist(club['id'],pid,s); await s.commit()
    await cq.answer('Удалено из списка.'); await cb_shortlist(cq)


@router.callback_query(F.data.startswith('shortlist:add:'))
async def cb_shortlist_add(cq:CallbackQuery):
    try: pid=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club:return await cq.answer('Сначала создайте клуб.',show_alert=True)
        if not (await s.execute(text('SELECT 1 FROM players WHERE id=:p'),{'p':pid})).first(): return await cq.answer('Игрок не найден.',show_alert=True)
        await add_to_shortlist(club['id'],pid,s); await s.commit()
    await cq.answer('⭐ Добавлено в список желаний.')


@router.callback_query(F.data.startswith('scout:item:'))
async def cb_scout_item(c:CallbackQuery):
    try: pid=int(c.data.rsplit(':',1)[1])
    except ValueError: return await c.answer('Некорректный игрок.',show_alert=True)
    try:
        async with SessionLocal() as s:
            club=await club_by_tg(s,c.from_user.id)
            if not club:
                return await c.answer('Сначала создайте клуб.',show_alert=True)
            row=(await s.execute(text('''SELECT p.* FROM players p
                WHERE p.id=:p AND NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.player_id=p.id)'''),{'p':pid})).mappings().first()
            history=await get_history(pid,s) if row else []
        if not row:
            return await c.answer('Игрок уже недоступен для покупки.',show_alert=True)
        target=_COMPARISON_TARGETS.pop(c.from_user.id,None)
        if target and int(target)!=pid:
            async with SessionLocal() as s2:
                pair=(await s2.execute(text('SELECT p.*,COALESCE(cp.fitness,100) fitness,COALESCE(cp.form,0) form,COALESCE(cp.morale,70) morale FROM players p LEFT JOIN club_players cp ON cp.player_id=p.id AND cp.club_id=:c WHERE p.id IN (:a,:b)'),{'c':club['id'],'a':int(target),'b':pid})).mappings().all()
            by={int(r['id']):r for r in pair}
            if int(target) in by and pid in by:
                return await c.message.answer(_comparison_text(by[int(target)],by[pid],compare_players(by[int(target)],by[pid])),reply_markup=back_keyboard('scout'))
        data=_card_player(row)
        card=player_card(data,out=Path('assets/visual/cards')/f'scout_player_{pid}.png')
        caption=(f'🃏 <b>{display_player_name(row.get("first_name"),row.get("last_name"))}</b>\n'
                 f'{row["position"]} · {row.get("nationality") or "—"} · рейтинг <b>{data.get("overall","—")}</b>\n'
                 f'⚡ PAC {row["pace"]} · SHO {row["shooting"]} · PAS {row["passing"]} · DRI {row["dribbling"]}\n'
                 f'🛡 DEF {row["defending"]} · PHY {row["physical"]} · STA {row["stamina"]} · MENT {row["mental"]}\n'
                 f'🎯 POT {row["potential"]} · €{int(row["market_value"]):,}')
        if history:
            caption += f'\n📈 <b>История развития:</b> {format_history(history)}'
        trend=await get_development_trend(pid,s,30)
        if trend: caption += f'\n📊 <b>Тренд:</b> {format_trend(trend)}'
        k=InlineKeyboardBuilder()
        k.button(text='🔄 Сравнить',callback_data=f'compare:pick:{pid}:{pid}')
        k.button(text='⭐ В список',callback_data=f'shortlist:add:{pid}')
        k.button(text=f'💰 Купить за €{int(row["market_value"]):,}',callback_data=f'scout:buy:{pid}')
        k.button(text='⬅️ Назад к поиску',callback_data=f'scout:back:{_scout_filter_state(c.from_user.id).get("page",0)}')
        k.adjust(1)
        await c.message.answer_photo(FSInputFile(card),caption=caption,reply_markup=k.as_markup())
        await c.answer()
    except Exception:
        logger.exception('Scout player card failed: user_id=%s player_id=%s',c.from_user.id,pid)
        return await c.answer('❌ Не удалось открыть игрока.',show_alert=True)


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
    await c.message.answer(f'✅ Куплен {display_player_name(player.get('first_name'),player.get('last_name'))} за €{price:,}.')
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
    k.adjust(2,2,2,2)
    return k.as_markup()


async def _competition_menu_markup(session):
    # Only the league system is active; historical cup/UCL data is not exposed.
    return league_menu_keyboard()


async def _competition_status_text(session, club):
    if not club:
        return None
    league = (await session.execute(text("""
        SELECT l.id,l.name,l.status
        FROM leagues l
        JOIN league_teams t ON t.league_id=l.id
        WHERE t.club_id=:c
        ORDER BY CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'WAITING' THEN 1 ELSE 2 END,l.id DESC
        LIMIT 1
    """), {'c': int(club['id'])})).mappings().first()
    if not league:
        return None
    if league['status'] == 'WAITING':
        return f'🏆 Вы в <b>{league["name"]}</b> лиге. Ожидаем набора'
    if league['status'] != 'ACTIVE':
        return f'🏆 Ваша лига: <b>{league["name"]}</b>. Статус: <b>{league["status"]}</b>'
    match = (await session.execute(text("""
        SELECT scheduled_at
        FROM matches
        WHERE league_id=:l AND competition_type='LEAGUE' AND status='SCHEDULED'
          AND (home_club_id=:c OR away_club_id=:c)
        ORDER BY scheduled_at NULLS LAST,id
        LIMIT 1
    """), {'l': int(league['id']), 'c': int(club['id'])})).mappings().first()
    if match and match['scheduled_at']:
        local_dt = _local_datetime(match['scheduled_at'])
        next_match = local_dt.strftime('%d.%m.%Y %H:%M')
    else:
        next_match = 'не назначен'
    return f'🏆 Ваша лига: <b>{league["name"]}</b>. Следующий матч: <b>{next_match}</b>'


@router.callback_query(F.data=='league')
async def cb_league(c:CallbackQuery):
    await c.answer()
    async with SessionLocal() as s:
        uid=await current_user(s,c.message,from_user=c.from_user)
        club=await current_club(s,uid)
        markup=await _competition_menu_markup(s)
        status=await _competition_status_text(s,club)
    text_body='🏆 <b>СОРЕВНОВАНИЕ</b>'
    if status:
        text_body += f'\n\n{status}'
    text_body += '\n\nВыберите действие:'
    await c.message.answer(text_body,reply_markup=markup)


@router.callback_query(F.data=='league:menu')
async def cb_league_menu(c:CallbackQuery):
    await c.answer()
    async with SessionLocal() as s:
        uid=await current_user(s,c.message,from_user=c.from_user)
        club=await current_club(s,uid)
        markup=await _competition_menu_markup(s)
        status=await _competition_status_text(s,club)
    text_body='🏆 <b>СОРЕВНОВАНИЕ</b>'
    if status:
        text_body += f'\n\n{status}'
    text_body += '\n\nВыберите действие:'
    await c.message.answer(text_body,reply_markup=markup)


@router.callback_query(F.data.in_({'league:cup', 'league:champions'}))
async def cb_disabled_tournament(c:CallbackQuery):
    # Keep a clear response for stale Telegram messages with old callback buttons.
    await c.answer('Доступна только система лиг. История старых турниров сохранена.', show_alert=True)


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
            f'🏆 <b>Ваша лига: {league["name"]}</b>',
            f'Клубы: <b>{league["team_count"]} / {league["max_teams"]}</b>',
            f'Статус: <b>{status_label}</b>',
        ]
        if league['status']=='ACTIVE':
            next_match=(await s.execute(text("""
                SELECT m.scheduled_at,h.name home_name,a.name away_name
                FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
                WHERE m.league_id=:l AND m.competition_type='LEAGUE' AND m.status='SCHEDULED'
                  AND (m.home_club_id=:c OR m.away_club_id=:c)
                ORDER BY m.scheduled_at NULLS LAST,m.id LIMIT 1
            """), {'l':league['id'],'c':club['id']})).mappings().first()
            if next_match and next_match['scheduled_at']:
                local_dt=_local_datetime(next_match['scheduled_at'],c.message)
                lines.append(f'Следующий матч: <b>{local_dt.strftime("%d.%m.%Y %H:%M")}</b>')
            else:
                lines.append('Следующий матч: <b>не назначен</b>')
        elif league['status']=='WAITING':
            lines.append(f'🏆 Вы в <b>{league["name"]}</b> лиге. Ожидаем набора')
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
    for n in (16,):
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
    if n != 16:
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
    n=data.get('max_teams',16)
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
    await cmd_startleague(c.message, from_user=c.from_user)


@router.callback_query(F.data=='league:play')
async def cb_league_play(c:CallbackQuery):
    await c.answer()
    await cmd_playround(c.message, from_user=c.from_user)


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
            if f not in ALLOWED_FORMATIONS:
                return await cq.answer('Неизвестная формация.',show_alert=True)
            current_style=Style((await s.execute(text('SELECT style FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).scalar_one_or_none() or 'BALANCE')
            tempo,pressing,width,defensive_line,aggression=apply_formation_sliders(current_style,f)
            await s.execute(text('''INSERT INTO club_tactics(club_id,formation,tempo,pressing,width,defensive_line,aggression)
                VALUES(:c,:f,:t,:p,:w,:d,:a) ON CONFLICT(club_id) DO UPDATE SET formation=EXCLUDED.formation,tempo=EXCLUDED.tempo,pressing=EXCLUDED.pressing,width=EXCLUDED.width,defensive_line=EXCLUDED.defensive_line,aggression=EXCLUDED.aggression'''),
                {'c':c['id'],'f':f,'t':tempo,'p':pressing,'w':width,'d':defensive_line,'a':aggression})
            await _apply_live_tactics(s,c['id'])
        elif action[1]=='s':
            st=action[2]
            try:
                style=Style(st)
            except ValueError:
                return await cq.answer('Неизвестный стиль.',show_alert=True)
            formation=(await s.execute(text('SELECT formation FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).scalar_one_or_none() or '4-3-3'
            # STYLE_SLIDERS baseline + formation delta.
            tempo,pressing,width,defensive_line,aggression=apply_formation_sliders(style,formation)
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
        rows=(await s.execute(text("""SELECT p.*,cp.club_id,cp.shirt_number,cp.fitness,cp.form,cp.morale,cp.is_injured,cp.injury_matches
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
        pr=type('P',(),dict(r,suspended=False,name=display_player_name(r.get('first_name'),r.get('last_name'))))()
        data.append(dict(r,overall=player_rating(pr)))
    path=squad_screen(c['name'],data)
    caption=f'👥 <b>{c["name"]}</b> · {len(data)} игроков\nУст = усталость, Мораль = состояние игрока (0–100%).'
    await message.answer_photo(FSInputFile(path),caption=caption,reply_markup=squad_keyboard(data,page))


@router.callback_query(F.data=='squad:bestxi')
async def cb_squad_bestxi(cq:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        formation=(await s.execute(text('SELECT formation FROM club_tactics WHERE club_id=:c'),{'c':club['id']})).scalar_one_or_none() or '4-3-3'
        rows=(await s.execute(text('''SELECT p.*,cp.fitness,cp.form,cp.morale,cp.is_injured,cp.suspension_matches
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c ORDER BY p.id'''),{'c':club['id']})).mappings().all()
    xi=get_best_xi(rows,formation)
    text_lines=[f'🤖 <b>BEST XI · {formation}</b>']
    for i,r in enumerate(xi,1):
        pr=type('P',(),dict(r,suspended=False,name=display_player_name(r.get('first_name'),r.get('last_name'))))()
        text_lines.append(f'{i}. {display_player_name(r.get("first_name"),r.get("last_name"))} · {r.get("position")} · OVR {player_rating(pr):.0f}')
    await cq.answer(); await cq.message.answer('\n'.join(text_lines),reply_markup=back_keyboard('squad'))

@router.callback_query(F.data=='squad:bestxi_ai')
async def cb_squad_bestxi_ai(cq:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club:
            return await cq.answer('Сначала создайте клуб.',show_alert=True)
        result=await suggest_best_xi(s,int(club['id']))
    await cq.answer()
    if not result:
        return await cq.message.answer('Не удалось построить стартовый состав. Попробуйте позже.',reply_markup=back_keyboard('squad'))
    lines=[f"🧠 <b>AI BEST XI · {html.escape(str(result['formation']))}</b>"]
    for i,player in enumerate(result['players'],1):
        name=html.escape(str(player.get('name') or display_player_name(player.get('first_name'),player.get('last_name'))))
        position=html.escape(str(player.get('slot_position') or player.get('position') or '?'))
        lines.append(f"{i}. {name} · {position} · OVR {float(player.get('overall') or 0):.1f}")
    lines.extend(['', '<b>Обоснование:</b>', html.escape(str(result.get('reason') or 'Состав подобран по доступности и рейтингу игроков.'))])
    k=InlineKeyboardBuilder()
    k.button(text='⬅️ Назад',callback_data='squad')
    k.button(text='🔄 Обновить',callback_data='squad:bestxi_ai')
    k.adjust(2)
    await cq.message.answer('\n'.join(lines),reply_markup=k.as_markup())


@router.callback_query(F.data=='squad:analysis')
async def cb_squad_analysis(cq:CallbackQuery):
    async with SessionLocal() as s:
        club=await club_by_tg(s,cq.from_user.id)
        if not club:
            return await cq.answer('Сначала создайте клуб.',show_alert=True)
        await cq.answer()
        club_id=int(club['id'])
        result=await analyze_squad(s,club_id)
        if result:
            heading='🤖 <b>Анализ состава:</b>'
        else:
            result=await build_fallback_analysis(s,club_id)
            heading='📊 <b>Анализ состава:</b>'
    keyboard=InlineKeyboardBuilder()
    keyboard.button(text='⬅️ Назад',callback_data='squad')
    keyboard.button(text='🔄 Обновить',callback_data='squad:analysis')
    keyboard.adjust(2)
    await cq.message.answer(f'{heading}\n{html.escape(result)}',reply_markup=keyboard.as_markup())

@router.callback_query(F.data.startswith('squad:item:'))
async def cb_squad_item(cq:CallbackQuery):
    try: pid=int(cq.data.rsplit(':',1)[1])
    except ValueError:return await cq.answer('Некорректный игрок.',show_alert=True)
    c,rows=await _load_squad_rows(cq.from_user.id)
    if not c:return await cq.answer('Сначала создайте клуб.',show_alert=True)
    r=next((row for row in rows if int(row['id'])==pid),None)
    if r is None:return await cq.answer('Игрок не найден.',show_alert=True)
    _SELECTED_SQUAD_PLAYERS[cq.from_user.id]=pid
    talents=r.get('talents') or []
    if isinstance(talents,str):
        import json; talents=json.loads(talents)
    async with SessionLocal() as s:
        history=await get_history(pid,s)
        trend=await get_development_trend(pid,s,30)
    injury_matches=int(r.get('injury_matches') or 0)
    injury_text=f'\n🚑 Травма: ещё {injury_matches} матч(а/ей)' if r.get('is_injured') and injury_matches>0 else ''
    caption=(f'🃏 <b>{display_player_name(r.get('first_name'),r.get('last_name'))}</b>\n{r["position"]} · {r["nationality"]} · {r["rarity"]}\n'
             f'⚡ PAC {r["pace"]} · SHO {r["shooting"]} · PAS {r["passing"]} · DRI {r["dribbling"]}\n'
             f'🛡 DEF {r["defending"]} · PHY {r["physical"]} · STA {r["stamina"]} · MENT {r["mental"]}\n'
             f'🎯 POT {r["potential"]} · €{r["market_value"]:,}\n🩹 Усталость: <b>{max(0,100-fitness_indicator(r.get("fitness"))[0])}%</b> · {fitness_indicator(r.get("fitness"))[1]} <b>{fitness_indicator(r.get("fitness"))[2]} ({fitness_indicator(r.get("fitness"))[0]}% FIT)</b> · 😊 Мораль: <b>{max(0,min(100,int(r.get("morale") if r.get("morale") is not None else 70)))}%</b>{injury_text}\n🔋 <b>Восстановление:</b> +15/день (без матча +20/день)\n✨ <b>Таланты:</b> {", ".join(talent_label(x) for x in talents) or "—"}')
    if history:
        caption += f'\n📈 <b>История развития:</b> {format_history(history)}'
    if trend:
        caption += f'\n📊 <b>Тренд:</b> {format_trend(trend)}'
        if len(trend)>=2: caption += f'\n💰 Value: €{trend[0][2]:,} → €{trend[-1][2]:,} ({trend[-1][2]-trend[0][2]:+d})'
    portrait=r.get('portrait_url'); portrait_path=None
    if portrait:
        candidate=Path(str(portrait)); portrait_path=candidate if candidate.is_absolute() else Path(__file__).resolve().parents[1]/candidate
    card=player_card(dict(r,portrait_path=portrait_path),out=Path('assets/visual/cards')/f'player_{r["id"]}.png')
    async with SessionLocal() as s:
        inventory_rows=await list_inventory(s,int(c['id']))
    k=InlineKeyboardBuilder(); k.button(text='🔄 Сравнить',callback_data=f'compare:pick:{pid}:{pid}'); k.button(text='⭐ В список',callback_data=f'shortlist:add:{pid}'); k.button(text='📝 Контракт',callback_data='squad:contract'); k.button(text='💸 Продать',callback_data='squad:sell'); k.button(text='🔄 Трансфер',callback_data='squad:transfer')
    if inventory_rows:
        k.button(text='🎒 Использовать предмет',callback_data=f'inventory:use:{pid}')
    k.button(text='⬅ К составу',callback_data='squad'); k.adjust(2,2,2,1)
    await cq.message.answer_photo(FSInputFile(card),caption=caption,reply_markup=k.as_markup()); await cq.answer()


_SQUAD_FILTERS=_BoundedUserCache()

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
    await cq.answer('Игрок продан.'); await cq.message.answer(f'💸 {display_player_name(row.get('first_name'),row.get('last_name'))} продан за <b>€{price:,}</b>.',reply_markup=squad_keyboard([]))


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
    await cq.message.edit_text(f'⚽ <b>{labels[slot_index]}</b> — выберите игрока',reply_markup=_player_picker_keyboard(rows,slots[slot_index],0))
    await cq.answer()


@router.callback_query(F.data.startswith('tactic:players:page:'))
async def cb_tactic_players_page(cq:CallbackQuery):
    try: page=int(cq.data.rsplit(':',1)[1])
    except ValueError: return await cq.answer('Некорректная страница.',show_alert=True)
    uid=cq.from_user.id; slot_index=_TACTIC_ACTIVE_SLOT.get(uid)
    if slot_index is None: return await cq.answer('Сначала выберите позицию.',show_alert=True)
    async with SessionLocal() as s:
        c=await club_by_tg(s,uid)
        if not c: return await cq.answer('Сначала создайте клуб.',show_alert=True)
        t=(await s.execute(text('SELECT formation FROM club_tactics WHERE club_id=:c'),{'c':c['id']})).mappings().first()
        rows=(await s.execute(text('''SELECT p.*,cp.fitness,cp.form,cp.morale
            FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND cp.is_injured=false AND cp.suspension_matches=0
            ORDER BY p.position,p.potential DESC,p.id'''),{'c':c['id']})).mappings().all()
    slots,labels=_slot_labels(t['formation'])
    if not 0<=slot_index<len(slots): return await cq.answer('Позиция не найдена.',show_alert=True)
    await cq.message.edit_reply_markup(reply_markup=_player_picker_keyboard(rows,slots[slot_index],page))
    await cq.answer()


@router.callback_query(F.data=='tactic:players:noop')
async def cb_tactic_players_noop(cq:CallbackQuery):
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
    if len(parts)!=5:
        return await cq.answer('Устаревшая кнопка. Откройте Тактику заново.',show_alert=True)
    try:
        pid=int(parts[2]); code=parts[3]; page=int(parts[4])
    except (ValueError,IndexError):
        return await cq.answer('Устаревшая кнопка. Откройте Тактику заново.',show_alert=True)
    if code not in {'GK','DEF','MID','ATT'} or page<0:
        return await cq.answer('Устаревшая кнопка. Откройте Тактику заново.',show_alert=True)
    c,rows=await _load_squad_rows(cq.from_user.id,code)
    if not c:
        return await cq.answer('Сначала создайте клуб.',show_alert=True)
    # The callback contains the real player id. Never map it back through a list index.
    if not any(int(r['id'])==pid for r in rows):
        return await cq.answer('Игрок недоступен в этой линии.',show_alert=True)
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
        pr=type('P',(),dict(r,suspended=False,name=display_player_name(r.get('first_name'),r.get('last_name'))))()
        data.append(dict(r,overall=player_rating(pr)))
    path=squad_screen(c['name'],data)
    await cq.message.answer_photo(FSInputFile(path),caption=f'👥 <b>{c["name"]}</b> · {len(data)} игроков\nУст = усталость, Мораль = состояние игрока (0–100%).'); await cq.answer()

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
    async with SessionLocal() as s:
        club=await club_by_tg(s,m.from_user.id); unread=await inbox_unread_count(club['id'],s) if club else 0
    await m.answer(f'🏟 <b>{name}</b> создан! Вы получили стартовый состав.',reply_markup=menu(unread))

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
        events=(await s.execute(text('SELECT e.minute,e.event_type,e.description,e.metadata FROM match_events e WHERE e.match_id=:m ORDER BY e.minute,e.id LIMIT 30'),{'m':row['id']})).mappings().all() if row else []
    if not row: return await m.answer('Матчей пока нет.')
    hp=row['home_pressing'] or 50; ap=row['away_pressing'] or 50
    phase='PRESSING' if hp-ap>=12 else ('COUNTER' if ap-hp>=12 else 'POSSESSION')
    control=max(.35,min(.65,.5+(hp-ap)/200))
    path=dynamic_match_screen(row['home'],row['away'],row['home_formation'] or '4-3-3',row['away_formation'] or '4-3-3',phase=phase,minute=row['current_minute'] or 0,home_control=control,pressure_home=hp,pressure_away=ap,events=events,home_score=row['home_score'] or 0,away_score=row['away_score'] or 0)
    await m.answer_photo(FSInputFile(path),caption=f'🎥 <b>Динамический Match Center</b> · {phase} · {_format_live_minute(row["current_minute"] or 0)}′')


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
            p.first_name player_first,p.last_name player_last,
            sp.first_name secondary_first,sp.last_name secondary_last
            FROM match_events e
            LEFT JOIN players p ON p.id=e.player_id
            LEFT JOIN players sp ON sp.id=e.secondary_player_id
            WHERE e.match_id=:m AND e.event_type IN ('PASS','CARRY','PRESSURE','TACKLE','INTERCEPTION','SHOT','GOAL')
            ORDER BY e.minute,e.id'''),{'m':row['id']})).mappings().all() if row else []
    if not row: return await m.answer('Матчей пока нет.')
    key_events=[]
    for r in raw:
        if not (r.get('metadata') or {}).get('visual_type'):
            continue
        item=dict(r)
        item['player']=display_player_name(item.pop('player_first', ''),item.pop('player_last', ''))
        item['secondary_player']=display_player_name(item.pop('secondary_first', ''),item.pop('secondary_last', '')) if item.get('secondary_first') or item.get('secondary_last') else None
        key_events.append(item)
    if not key_events: return await m.answer('В этом матче ещё нет визуализируемых событий.')
    index=requested or len(key_events)
    index=min(index,len(key_events))
    event=key_events[index-1]
    event['team']='HOME' if event.get('club_id')==row['home_club_id'] else 'AWAY'
    event['description']=re.sub(r'(\))\s*(?:en|rom|ru|eng|рус|[a-z]{2,5})\s*', r'\1 ', str(event.get('description') or ''), flags=re.IGNORECASE).strip()
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
        events=(await s.execute(text('SELECT e.minute,e.description FROM match_events e WHERE e.match_id=:m ORDER BY e.minute,e.id LIMIT 20'),{'m':row['id']})).mappings().all() if row else []
    if not row:
        return await m.answer('Матчей пока нет.')
    home_control=.5
    if row['home_formation']=='4-2-3-1': home_control=.53
    elif row['home_formation']=='3-5-2': home_control=.55
    phase='POSSESSION' if (row['home_pressing'] or 50) >= (row['away_pressing'] or 50) else 'TRANSITION'
    path=match_phase_screen(row['home'],row['away'],row['home_formation'] or '4-3-3',row['away_formation'] or '4-3-3',phase,home_control,row['home_pressing'] or 50,row['away_pressing'] or 50,events)
    await m.answer_photo(FSInputFile(path),caption=f'🗺 <b>Карта матча</b> · {phase} · {_format_live_minute(row["current_minute"] or 0)}′')
from app.game.positions import POSITION_LABELS_RU
