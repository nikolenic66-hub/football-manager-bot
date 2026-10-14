import json
import random
import logging
from pathlib import Path

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import text

from ..config import settings
from ..db import SessionLocal
from ..game.ratings import player_rating
from ..services import start_league
from app.services import clean_display_name, display_player_name

admin_router = Router()
logger = logging.getLogger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(getattr(event, 'from_user', None), 'id', None)
        if user != settings.admin_id:
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None
        return await handler(event, data)


admin_router.message.middleware(AdminOnlyMiddleware())
admin_router.callback_query.middleware(AdminOnlyMiddleware())


class AdminStates(StatesGroup):
    player_search = State()
    player_create_first = State()
    player_create_last = State()
    player_create_nat = State()
    player_create_age = State()
    player_create_pos = State()
    player_create_overall = State()
    player_create_potential = State()
    player_edit = State()
    player_delete_confirm = State()
    club_search = State()
    club_create_owner = State()
    club_create_name = State()
    club_edit = State()
    club_transfer_player = State()
    club_transfer_target = State()
    club_delete_confirm = State()
    finance_club = State()
    finance_amount = State()
    finance_description = State()
    training_club = State()
    training_all_confirm = State()
    league_add_club = State()
    league_remove_club = State()
    league_delete_confirm = State()




async def _confirm_state(state: FSMContext, expected_state: str, key: str, value: int) -> bool:
    current = await state.get_state()
    data = await state.get_data()
    return current == expected_state and data.get(key) == value


def kb(rows):
    b = InlineKeyboardBuilder()
    for label, cb in rows:
        b.button(text=label, callback_data=cb)
    b.adjust(2)
    return b.as_markup()


def back(cb='admin:menu'):
    return kb([('⬅️ Назад', cb), ('🏠 Главное меню', 'menu:main')])


async def log_action(admin_id, action, target_type=None, target_id=None, payload=None):
    async with SessionLocal() as s:
        await s.execute(text('''INSERT INTO admin_actions_log
            (admin_user_id,action,target_type,target_id,payload)
            VALUES(:u,:a,:t,:i,:p)'''), {
            'u': admin_id, 'a': action, 't': target_type, 'i': target_id,
            'p': json.dumps(payload or {}, ensure_ascii=False),
        })
        await s.commit()


def ovr(row):
    attrs = {k: int(row.get(k) or 0) for k in
             ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
    p = type('P', (), dict(attrs, position=str(row.get('position') or 'CM'), fitness=100, form=0, morale=70))()
    return player_rating(p)


def player_card_text(p):
    talents = p.get('talents') or '[]'
    if isinstance(talents, str):
        try: talents = json.loads(talents)
        except Exception: talents = [talents]
    stats = ' / '.join(f'{k[:3].upper()} {p[k]}' for k in
                       ('pace','shooting','passing','dribbling','defending','physical','stamina','mental'))
    return (f"👤 <b>{display_player_name(p.get('first_name'),p.get('last_name'))}</b>\n"
            f"ID: <code>{p['id']}</code> · OVR: <b>{ovr(p):g}</b> · POS: <b>{p['position']}</b>\n"
            f"Возраст: {p['age']} · Страна: {p['nationality']} · Rarity: {p['rarity']}\n"
            f"Potential: {p['potential']} · Real: {p['real_player']}\n"
            f"Stats: {stats}\n"
            f"Market value: {p['market_value']:,} · Salary: {p['salary']:,}\n"
            f"Talents: {', '.join(talents) if talents else '—'}")


@admin_router.message(Command('admin'))
async def admin_command(m: Message, state: FSMContext):
    await state.clear()
    await m.answer('👑 <b>АДМИН-ПАНЕЛЬ</b>', reply_markup=admin_menu())


def admin_menu():
    return kb([
        ('👥 Игроки', 'admin:players'), ('🏟 Клубы', 'admin:clubs'),
        ('💰 Финансы', 'admin:finance'), ('⏱ Тренировки', 'admin:training'),
        ('🏆 Лиги', 'admin:leagues'), ('⚙️ Система', 'admin:system'),
        ('🏠 Главное меню', 'menu:main'),
    ])


@admin_router.callback_query(F.data == 'admin:menu')
async def admin_menu_cb(c: CallbackQuery, state: FSMContext):
    await state.clear(); await c.message.edit_text('👑 <b>АДМИН-ПАНЕЛЬ</b>', reply_markup=admin_menu()); await c.answer()


# ---------------- players ----------------
@admin_router.callback_query(F.data == 'admin:players')
async def admin_players(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text('👥 <b>ИГРОКИ</b>', reply_markup=kb([
        ('🔍 Найти по ID/имени', 'admin:players:search'), ('➕ Создать игрока', 'admin:player:create'),
        ('⬅️ Назад', 'admin:menu')]))
    await c.answer()


@admin_router.callback_query(F.data == 'admin:players:search')
async def admin_player_search_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.player_search)
    await c.message.answer('Введите ID, имя или фамилию игрока:')
    await c.answer()


@admin_router.message(AdminStates.player_search)
async def admin_player_search_input(m: Message, state: FSMContext):
    q = (m.text or '').strip()
    async with SessionLocal() as s:
        rows = (await s.execute(text('''SELECT * FROM players
          WHERE CAST(id AS TEXT) ILIKE :like OR first_name ILIKE :like OR last_name ILIKE :like
          ORDER BY id LIMIT 10'''), {'like': f'%{q}%'})).mappings().all()
    await state.clear()
    if not rows: return await m.answer('Ничего не найдено.', reply_markup=back('admin:players'))
    b = InlineKeyboardBuilder()
    for i, p in enumerate(rows, 1):
        b.button(text=f'{i}. {display_player_name(p.get("first_name"),p.get("last_name"))} (id={p["id"]}, OVR={ovr(p):g}, {p["position"]})', callback_data=f'admin:player:{p["id"]}')
    b.button(text='⬅️ Назад', callback_data='admin:players'); b.adjust(1)
    await m.answer('Результаты:', reply_markup=b.as_markup())


@admin_router.callback_query(F.data.regexp(r'^admin:player:\d+(?::(?!delete:yes$).+)?$'))
async def admin_player_router(c: CallbackQuery, state: FSMContext):
    parts = c.data.split(':')
    if len(parts) == 3 and parts[2].isdigit():
        pid = int(parts[2])
        async with SessionLocal() as s:
            p = (await s.execute(text('SELECT * FROM players WHERE id=:id'), {'id': pid})).mappings().first()
        if not p: return await c.answer('Игрок не найден.', show_alert=True)
        await c.message.edit_text(player_card_text(p), reply_markup=kb([
            ('✏️ Статы', f'admin:player:{pid}:stats'), ('✏️ Возраст', f'admin:player:{pid}:age'),
            ('✏️ Overall/Potential', f'admin:player:{pid}:ovr'), ('✏️ Позиция', f'admin:player:{pid}:pos'),
            ('✏️ Стоимость/Зарплата', f'admin:player:{pid}:money'), ('✏️ Имя/Фамилия/Национальность', f'admin:player:{pid}:identity'),
            ('🗑 Удалить игрока', f'admin:player:{pid}:delete'), ('⬅️ Назад', 'admin:players')]))
        return await c.answer()
    if len(parts) < 4: return await c.answer('Некорректная кнопка.', show_alert=True)
    pid = int(parts[2]); action = parts[3]
    if action == 'delete':
        await state.set_state(AdminStates.player_delete_confirm); await state.update_data(player_id=pid)
        await c.message.edit_text('⚠️ <b>Удалить игрока навсегда?</b>', reply_markup=kb([('⚠️ Да, удалить навсегда', f'admin:player:{pid}:delete:yes'), ('⬅️ Отмена', f'admin:player:{pid}')]))
    else:
        await state.set_state(AdminStates.player_edit); await state.update_data(player_id=pid, field=action)
        prompts={'stats':'Введите 8 статов через пробел: pace shooting passing dribbling defending physical stamina mental', 'age':'Введите возраст 16–40:', 'ovr':'Введите Overall и Potential через пробел:', 'pos':'Введите позицию (GK/CB/LB/RB/DM/CM/AM/LW/RW/ST):', 'money':'Введите market_value и salary через пробел:', 'identity':'Введите имя, фамилию и страну (3 буквы) через пробел:'}
        await c.message.answer(prompts.get(action, 'Введите новое значение:'))
    await c.answer()


@admin_router.message(AdminStates.player_edit)
async def admin_player_edit_input(m: Message, state: FSMContext):
    d = await state.get_data(); pid = d['player_id']; field = d['field']; raw=(m.text or '').strip()
    try:
        async with SessionLocal() as s:
            p=(await s.execute(text('SELECT * FROM players WHERE id=:id'),{'id':pid})).mappings().first()
            if not p: raise ValueError('Игрок не найден.')
            if field == 'stats':
                vals=list(map(int,raw.split())); assert len(vals)==8 and all(1<=x<=100 for x in vals)
                cols='pace,shooting,passing,dribbling,defending,physical,stamina,mental'; params=dict(zip(cols.split(','),vals))
                await s.execute(text('UPDATE players SET pace=:pace,shooting=:shooting,passing=:passing,dribbling=:dribbling,defending=:defending,physical=:physical,stamina=:stamina,mental=:mental WHERE id=:id'),dict(params,id=pid))
            elif field=='age':
                v=int(raw); assert 16<=v<=40; await s.execute(text('UPDATE players SET age=:v WHERE id=:id'),{'v':v,'id':pid})
            elif field=='ovr':
                o,pot=map(int,raw.split()); assert 1<=o<=100 and 1<=pot<=100
                ratio=o/max(float(ovr(p)),1); vals={k:max(1,min(100,round(int(p[k])*ratio))) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
                await s.execute(text('''UPDATE players SET pace=:pace,shooting=:shooting,passing=:passing,dribbling=:dribbling,defending=:defending,physical=:physical,stamina=:stamina,mental=:mental,potential=:pot WHERE id=:id'''),dict(vals,pot=pot,id=pid))
            elif field=='pos':
                assert raw in {'GK','CB','LB','RB','DM','CM','AM','LW','RW','ST'}; await s.execute(text('UPDATE players SET position=:v WHERE id=:id'),{'v':raw,'id':pid})
            elif field=='money':
                mv,sal=map(int,raw.split()); assert mv>=0 and sal>=0; await s.execute(text('UPDATE players SET market_value=:mv,salary=:sal WHERE id=:id'),{'mv':mv,'sal':sal,'id':pid})
            elif field=='identity':
                first,last,nat=raw.split(maxsplit=2); assert first and last and len(nat)==3; await s.execute(text('UPDATE players SET first_name=:f,last_name=:l,nationality=:n WHERE id=:id'),{'f':first,'l':last,'n':nat.upper(),'id':pid})
            await s.commit()
        await log_action(m.from_user.id,f'player_edit_{field}','player',pid,{'input':raw})
        await state.clear(); await m.answer('✅ Изменено.',reply_markup=back(f'admin:player:{pid}'))
    except Exception:
        await m.answer('❌ Некорректное значение. Попробуйте ещё раз.')


@admin_router.callback_query(F.data.startswith('admin:player:') & F.data.endswith(':delete:yes'))
async def admin_player_delete(c: CallbackQuery, state: FSMContext):
    pid=int(c.data.split(':')[2])
    if not await _confirm_state(state, AdminStates.player_delete_confirm.state, 'player_id', pid):
        return await c.answer('Подтверждение устарело. Начните заново.', show_alert=True)
    try:
        async with SessionLocal() as s:
            await s.execute(text('UPDATE matches SET motm_player_id=NULL WHERE motm_player_id=:id'),{'id':pid})
            for table,col in [('match_events','player_id'),('match_events','secondary_player_id')]: await s.execute(text(f'UPDATE {table} SET {col}=NULL WHERE {col}=:id'),{'id':pid})
            await s.execute(text('UPDATE club_lineups SET player_ids=array_remove(player_ids,:id)'),{'id':pid})
            await s.execute(text('DELETE FROM club_player_instructions WHERE player_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM match_player_instructions WHERE player_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM match_substitutions WHERE player_off_id=:id OR player_on_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM club_substitution_plans WHERE player_off_id=:id OR player_on_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM match_player_stats WHERE player_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM match_lineups WHERE player_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM transfers WHERE player_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM transfer_offers WHERE listing_id IN (SELECT id FROM transfer_listings WHERE player_id=:id)'),{'id':pid})
            await s.execute(text('DELETE FROM transfer_listings WHERE player_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM player_development_log WHERE player_id=:id'),{'id':pid})
            await s.execute(text('DELETE FROM club_players WHERE player_id=:id'),{'id':pid})
            res=await s.execute(text('DELETE FROM players WHERE id=:id'),{'id':pid})
            await s.commit()
        if not res.rowcount:
            await state.clear()
            return await c.answer('Игрок уже удалён.',show_alert=True)
        await log_action(c.from_user.id,'player_delete','player',pid)
        await state.clear(); await c.message.edit_text('🗑 Игрок удалён.',reply_markup=back('admin:players')); await c.answer()
    except Exception:
        if 's' in locals():
            await s.rollback()
        logger.exception('Admin player delete failed: player_id=%s', pid)
        await c.answer('❌ Не удалось удалить игрока.', show_alert=True)


@admin_router.callback_query(F.data == 'admin:player:create')
async def admin_player_create(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.player_create_first); await c.message.answer('Имя:'); await c.answer()

@admin_router.message(AdminStates.player_create_first)
async def apc_first(m,state): await state.update_data(first=m.text.strip()); await state.set_state(AdminStates.player_create_last); await m.answer('Фамилия:')
@admin_router.message(AdminStates.player_create_last)
async def apc_last(m,state): await state.update_data(last=m.text.strip()); await state.set_state(AdminStates.player_create_nat); await m.answer('Страна, 3 буквы:')
@admin_router.message(AdminStates.player_create_nat)
async def apc_nat(m,state):
    nat=m.text.strip().upper()
    if len(nat)!=3 or not nat.isalpha(): return await m.answer('Введите ровно 3 буквы страны.')
    await state.update_data(nat=nat); await state.set_state(AdminStates.player_create_age); await m.answer('Возраст 16–40:')
@admin_router.message(AdminStates.player_create_age)
async def apc_age(m,state):
    try: v=int(m.text); assert 16<=v<=40
    except: return await m.answer('Введите число 16–40.')
    await state.update_data(age=v); await state.set_state(AdminStates.player_create_pos); await m.answer('Позиция: GK/CB/LB/RB/DM/CM/AM/LW/RW/ST')
@admin_router.message(AdminStates.player_create_pos)
async def apc_pos(m,state):
    v=m.text.strip().upper()
    if v not in {'GK','CB','LB','RB','DM','CM','AM','LW','RW','ST'}: return await m.answer('Некорректная позиция.')
    await state.update_data(pos=v); await state.set_state(AdminStates.player_create_overall); await m.answer('Overall 1–100:')
@admin_router.message(AdminStates.player_create_overall)
async def apc_ovr(m,state):
    try: v=int(m.text); assert 1<=v<=100
    except: return await m.answer('Введите Overall 1–100.')
    await state.update_data(ovr=v); await state.set_state(AdminStates.player_create_potential); await m.answer('Potential 1–100:')
@admin_router.message(AdminStates.player_create_potential)
async def apc_pot(m,state):
    try: pot=int(m.text); assert 1<=pot<=100
    except: return await m.answer('Введите Potential 1–100.')
    d=await state.get_data(); base=d['ovr']; rng=random.Random((d['first']+d['last']).__hash__() & 0xffffffff)
    vals={k:max(1,min(100,base+rng.randint(-6,6))) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
    market=max(0,int((base/60)**6*180_000)); salary=max(15_000,market//35)
    async with SessionLocal() as s:
        row=await s.execute(text('''INSERT INTO players(first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,real_player,preferred_foot,secondary_positions,talents,tactical_archetype,data_source)
        VALUES(:f,:l,:n,:age,:pos,:pace,:shooting,:passing,:dribbling,:defending,:physical,:stamina,:mental,:mv,:sal,:pot,'BASE','STANDARD',FALSE,'R','{}','[]','BALANCED','generated') RETURNING id'''),dict(f=d['first'],l=d['last'],n=d['nat'][:3],age=d['age'],pos=d['pos'],**vals,mv=market,sal=salary,pot=pot)).scalar_one()
        await s.commit()
    await log_action(m.from_user.id,'player_create','player',row,d)
    await state.clear(); await m.answer(f'✅ Игрок создан, ID={row}',reply_markup=back('admin:players'))


# ---------------- clubs ----------------
@admin_router.callback_query(F.data == 'admin:clubs')
async def admin_clubs(c,state):
    await state.clear(); await c.message.edit_text('🏟 <b>КЛУБЫ</b>',reply_markup=kb([('🔍 Найти по ID/названию','admin:clubs:search'),('➕ Создать клуб для юзера','admin:club:create'),('⬅️ Назад','admin:menu')])); await c.answer()

@admin_router.callback_query(F.data == 'admin:clubs:search')
async def admin_club_search(c,state): await state.set_state(AdminStates.club_search); await c.message.answer('Введите ID или название клуба:'); await c.answer()

@admin_router.message(AdminStates.club_search)
async def admin_club_search_input(m,state):
    q=(m.text or '').strip()
    async with SessionLocal() as s: rows=(await s.execute(text('SELECT * FROM clubs WHERE CAST(id AS TEXT) ILIKE :l OR name ILIKE :l ORDER BY id LIMIT 10'),{'l':f'%{q}%'})).mappings().all()
    await state.clear()
    if not rows:return await m.answer('Ничего не найдено.',reply_markup=back('admin:clubs'))
    b=InlineKeyboardBuilder()
    for r in rows:b.button(text=f'{r["name"]} (id={r["id"]})',callback_data=f'admin:club:{r["id"]}')
    b.button(text='⬅️ Назад',callback_data='admin:clubs');b.adjust(1);await m.answer('Результаты:',reply_markup=b.as_markup())

@admin_router.callback_query(F.data.regexp(r'^admin:club:\d+(?::(?!delete:yes$).+)?$'))
async def admin_club_router(c,state):
    parts=c.data.split(':')
    if len(parts)==3 and parts[2].isdigit():
        cid=int(parts[2]);
        async with SessionLocal() as s:r=(await s.execute(text('SELECT * FROM clubs WHERE id=:id'),{'id':cid})).mappings().first()
        if not r:return await c.answer('Клуб не найден.',show_alert=True)
        txt=(f"🏟 <b>{r['name']}</b>\nID: {cid}\nOwner: {r['owner_user_id']}\nBudget: {r['budget']:,}\nFans: {r['fans']}\nReputation: {r['reputation']}\nStadium: {r['stadium_level']}\nTicket: {r['ticket_price']}\nSponsor: {r['sponsor_level']}\nDebt: {r['debt']}")
        await c.message.edit_text(txt,reply_markup=kb([('💰 Изменить бюджет',f'admin:club:{cid}:budget'),('👥 Изменить фанатов',f'admin:club:{cid}:fans'),('⭐ Изменить репутацию',f'admin:club:{cid}:reputation'),('🏟 Стадион',f'admin:club:{cid}:stadium'),('🎟 Цена билета',f'admin:club:{cid}:ticket'),('🔄 Перевести игрока из этого клуба',f'admin:club:{cid}:transfer'),('🗑 Удалить клуб',f'admin:club:{cid}:delete'),('⬅️ Назад','admin:clubs')]))
        return await c.answer()
    if len(parts)<4:return await c.answer()
    cid=int(parts[2]);action=parts[3]
    if action=='delete':
        await state.set_state(AdminStates.club_delete_confirm);await state.update_data(club_id=cid);await c.message.edit_text('⚠️ <b>Удалить клуб навсегда?</b>',reply_markup=kb([('⚠️ Да, удалить навсегда',f'admin:club:{cid}:delete:yes'),('⬅️ Отмена',f'admin:club:{cid}')]))
    elif action=='transfer':
        await state.set_state(AdminStates.club_transfer_player);await state.update_data(club_id=cid);await c.message.answer('Введите player_id:')
    else:
        await state.set_state(AdminStates.club_edit);await state.update_data(club_id=cid,field=action);await c.message.answer({'budget':'Новое значение бюджета (можно отрицательное):','fans':'Новое число фанатов:','reputation':'Новая репутация:','stadium':'Новый уровень стадиона 1–20:','ticket':'Новая цена билета 5–120:'}[action])
    await c.answer()

@admin_router.message(AdminStates.club_edit)
async def admin_club_edit(m,state):
    d=await state.get_data();cid=d['club_id'];field=d['field']
    try:
        v=int(m.text); limits={'fans':(0,None),'reputation':(1,None),'stadium':(1,20),'ticket':(5,120)}
        if field!='budget' and not (limits[field][0]<=v<=limits[field][1]):raise ValueError
        col={'budget':'budget','fans':'fans','reputation':'reputation','stadium':'stadium_level','ticket':'ticket_price'}[field]
        async with SessionLocal() as s: await s.execute(text(f'UPDATE clubs SET {col}=:v WHERE id=:id'),{'v':v,'id':cid}); await s.commit()
        await log_action(m.from_user.id,f'club_edit_{field}','club',cid,{'value':v});await state.clear();await m.answer('✅ Изменено.',reply_markup=back(f'admin:club:{cid}'))
    except Exception:await m.answer('❌ Некорректное число.')

@admin_router.message(AdminStates.club_transfer_player)
async def admin_transfer_player(m,state):
    try:pid=int(m.text)
    except:return await m.answer('Введите player_id числом.')
    d=await state.get_data();await state.update_data(player_id=pid);await state.set_state(AdminStates.club_transfer_target);await m.answer('Введите club_id получателя:')

@admin_router.message(AdminStates.club_transfer_target)
async def admin_transfer_target(m,state):
    try:target=int(m.text)
    except:return await m.answer('Введите club_id числом.')
    d=await state.get_data();cid=d['club_id'];pid=d['player_id']
    async with SessionLocal() as s:
        owner=(await s.execute(text('SELECT club_id FROM club_players WHERE player_id=:p'),{'p':pid})).scalar_one_or_none()
        exists=(await s.execute(text('SELECT 1 FROM clubs WHERE id=:id'),{'id':target})).scalar_one_or_none()
        if owner!=cid:return await m.answer('Игрок не принадлежит этому клубу.')
        if not exists:return await m.answer('Клуб-получатель не найден.')
        await s.execute(text('UPDATE club_players SET club_id=:to WHERE player_id=:p AND club_id=:from'),{'to':target,'p':pid,'from':cid});await s.commit()
    await log_action(m.from_user.id,'club_transfer_player','player',pid,{'from_club':cid,'to_club':target});await state.clear();await m.answer('✅ Игрок переведён.',reply_markup=back(f'admin:club:{cid}'))

@admin_router.callback_query(F.data.startswith('admin:club:') & F.data.endswith(':delete:yes'))
async def admin_club_delete(c,state):
    parts=c.data.split(':')
    if len(parts)<5 or parts[1]!='club': return
    cid=int(parts[2])
    if not await _confirm_state(state, AdminStates.club_delete_confirm.state, 'club_id', cid):
        return await c.answer('Подтверждение устарело. Начните заново.', show_alert=True)
    try:
        async with SessionLocal() as s:
            await s.execute(text('DELETE FROM matches WHERE home_club_id=:id OR away_club_id=:id'), {'id': cid})
            for q in ['DELETE FROM club_players WHERE club_id=:id','DELETE FROM club_tactics WHERE club_id=:id','DELETE FROM club_lineups WHERE club_id=:id','DELETE FROM club_training WHERE club_id=:id','DELETE FROM club_player_instructions WHERE club_id=:id','DELETE FROM club_substitution_plans WHERE club_id=:id','DELETE FROM club_counterplans WHERE club_id=:id','DELETE FROM club_financial_transactions WHERE club_id=:id','DELETE FROM club_season_finances WHERE club_id=:id','DELETE FROM league_teams WHERE club_id=:id']:
                await s.execute(text(q),{'id':cid})
            await s.execute(text('DELETE FROM clubs WHERE id=:id'),{'id':cid}); await s.commit()
        await log_action(c.from_user.id,'club_delete','club',cid); await state.clear(); await c.message.edit_text('🗑 Клуб удалён.',reply_markup=back('admin:clubs')); await c.answer()
    except Exception:
        if 's' in locals():
            await s.rollback()
        logger.exception('Admin club delete failed: club_id=%s', cid)
        await c.answer('❌ Не удалось удалить клуб.', show_alert=True)


@admin_router.callback_query(F.data == 'admin:club:create')
async def admin_club_create(c,state):await state.set_state(AdminStates.club_create_owner);await c.message.answer('Введите Telegram user_id владельца:');await c.answer()
@admin_router.message(AdminStates.club_create_owner)
async def acc_owner(m,state):
    try:v=int(m.text)
    except:return await m.answer('Введите числовой user_id.')
    await state.update_data(owner=v);await state.set_state(AdminStates.club_create_name);await m.answer('Название клуба:')
@admin_router.message(AdminStates.club_create_name)
async def acc_name(m,state):
    d=await state.get_data();name=m.text.strip()
    if not 3<=len(name)<=30:return await m.answer('Название 3–30 символов.')
    async with SessionLocal() as s:
        user=(await s.execute(text('SELECT id FROM users WHERE telegram_id=:t'),{'t':d['owner']})).scalar_one_or_none()
        if not user:return await m.answer('Пользователь не найден в users.')
        exists=(await s.execute(text('SELECT 1 FROM clubs WHERE owner_user_id=:u'),{'u':user})).scalar_one_or_none()
        if exists:return await m.answer('У пользователя уже есть клуб.')
        cid=await s.execute(text('''INSERT INTO clubs(owner_user_id,name,short_name,stadium_name) VALUES(:u,:n,:s,:st) RETURNING id'''),{'u':user,'n':name,'s':name[:8].upper(),'st':f'{name} Stadium'});cid=cid.scalar_one();await s.commit()
    await log_action(m.from_user.id,'club_create','club',cid,{'owner_user_id':d['owner'],'name':name});await state.clear();await m.answer(f'✅ Клуб создан, ID={cid}',reply_markup=back('admin:clubs'))


# ---------------- finance ----------------
@admin_router.callback_query(F.data == 'admin:finance')
async def admin_finance(c,state):
    await state.clear();await c.message.edit_text('💰 <b>ФИНАНСЫ</b>',reply_markup=kb([('📊 Топ-10 богатых клубов','admin:finance:rich'),('💸 Ручная транзакция','admin:finance:tx'),('📜 История транзакций клуба','admin:finance:history'),('⬅️ Назад','admin:menu')]));await c.answer()
@admin_router.callback_query(F.data == 'admin:finance:rich')
async def admin_rich(c):
    async with SessionLocal() as s:rows=(await s.execute(text('SELECT id,name,budget FROM clubs ORDER BY budget DESC LIMIT 10'))).mappings().all()
    await c.message.edit_text('📊 <b>Топ-10</b>\n'+'\n'.join(f'{i}. {r["name"]} #{r["id"]} — {r["budget"]:,}' for i,r in enumerate(rows,1)),reply_markup=back('admin:finance'));await c.answer()
@admin_router.callback_query(F.data == 'admin:finance:tx')
async def admin_fin_tx(c,state):await state.set_state(AdminStates.finance_club);await c.message.answer('club_id:');await c.answer()
@admin_router.message(AdminStates.finance_club)
async def aft_club(m,state):
    try:v=int(m.text)
    except:return await m.answer('Число.')
    d=await state.get_data()
    if d.get('mode') == 'history':
        async with SessionLocal() as s:
            rows=(await s.execute(text('SELECT created_at,category,amount,balance_after,description FROM club_financial_transactions WHERE club_id=:c ORDER BY id DESC LIMIT 50'),{'c':v})).mappings().all()
        await state.clear()
        if not rows:return await m.answer('История пуста.',reply_markup=back('admin:finance'))
        txt='📜 <b>История</b>\n'+'\n'.join(f'{r["created_at"]:%Y-%m-%d %H:%M} {r["category"]}: {r["amount"]:+,} → {r["balance_after"]:,} · {r["description"]}' for r in rows)
        return await m.answer(txt,reply_markup=back('admin:finance'))
    await state.update_data(club_id=v);await state.set_state(AdminStates.finance_amount);await m.answer('Сумма (+/-):')
@admin_router.message(AdminStates.finance_amount)
async def aft_amount(m,state):
    try:v=int(m.text)
    except:return await m.answer('Число.')
    await state.update_data(amount=v);await state.set_state(AdminStates.finance_description);await m.answer('Описание:')
@admin_router.message(AdminStates.finance_description)
async def aft_desc(m,state):
    d=await state.get_data();desc=m.text.strip()
    async with SessionLocal() as s:
        row=(await s.execute(text('SELECT budget FROM clubs WHERE id=:id'),{'id':d['club_id']})).scalar_one_or_none()
        if row is None:return await m.answer('Клуб не найден.')
        new=row+d['amount']
        if new<0:return await m.answer('Бюджет не может быть отрицательным.')
        await s.execute(text('UPDATE clubs SET budget=:b WHERE id=:id'),{'b':new,'id':d['club_id']})
        await s.execute(text('INSERT INTO club_financial_transactions(club_id,category,amount,balance_after,description) VALUES(:c,\'OTHER\',:a,:b,:d)'),{'c':d['club_id'],'a':d['amount'],'b':new,'d':desc});await s.commit()
    await log_action(m.from_user.id,'finance_manual_transaction','club',d['club_id'],d);await state.clear();await m.answer('✅ Транзакция выполнена.',reply_markup=back('admin:finance'))
@admin_router.callback_query(F.data == 'admin:finance:history')
async def admin_fin_history_start(c,state):
    await state.set_state(AdminStates.finance_club)
    await state.update_data(mode='history')
    await c.message.answer('club_id:')
    await c.answer()


# ---------------- training ----------------
@admin_router.callback_query(F.data == 'admin:training')
async def admin_training(c,state):await state.clear();await c.message.edit_text('⏱ <b>ТРЕНИРОВКИ</b>',reply_markup=kb([('🔓 Сбросить кулдаун клуба','admin:training:club'),('🔓 Сбросить кулдаун ВСЕХ клубов','admin:training:all'),('⬅️ Назад','admin:menu')]));await c.answer()
@admin_router.callback_query(F.data == 'admin:training:club')
async def admin_training_club(c,state):await state.set_state(AdminStates.training_club);await c.message.answer('club_id:');await c.answer()
@admin_router.message(AdminStates.training_club)
async def admin_training_club_input(m,state):
    try:cid=int(m.text)
    except:return await m.answer('Число.')
    async with SessionLocal() as s:await s.execute(text('UPDATE club_players SET last_training_at=NULL WHERE club_id=:c'),{'c':cid});await s.commit()
    await log_action(m.from_user.id,'training_cooldown_reset','club',cid);await state.clear();await m.answer('✅ Кулдаун сброшен.',reply_markup=back('admin:training'))
@admin_router.callback_query(F.data == 'admin:training:all')
async def admin_training_all(c,state):await state.set_state(AdminStates.training_all_confirm);await c.message.edit_text('⚠️ Сбросить кулдаун у всех клубов?',reply_markup=kb([('⚠️ Да, сбросить всех','admin:training:all:yes'),('⬅️ Отмена','admin:training')]));await c.answer()
@admin_router.callback_query(F.data == 'admin:training:all:yes')
async def admin_training_all_yes(c,state):
    current = await state.get_state()
    if current != AdminStates.training_all_confirm.state:
        return await c.answer('Подтверждение устарело. Начните заново.', show_alert=True)
    try:
        async with SessionLocal() as s:
            await s.execute(text('UPDATE club_players SET last_training_at=NULL')); await s.commit()
        await log_action(c.from_user.id,'training_cooldown_reset_all','club',None); await state.clear(); await c.message.edit_text('✅ Кулдаун всех клубов сброшен.',reply_markup=back('admin:training')); await c.answer()
    except Exception:
        if 's' in locals():
            await s.rollback()
        logger.exception('Admin training cooldown reset all failed')
        await c.answer('❌ Не удалось сбросить кулдаун.', show_alert=True)


# ---------------- leagues ----------------
@admin_router.callback_query(F.data == 'admin:leagues')
async def admin_leagues(c,state):
    async with SessionLocal() as s:rows=(await s.execute(text('''SELECT l.id,l.name,l.status,l.creator_user_id,count(t.club_id) members FROM leagues l LEFT JOIN league_teams t ON t.league_id=l.id GROUP BY l.id ORDER BY l.id DESC'''))).mappings().all()
    b=InlineKeyboardBuilder()
    for r in rows:b.button(text=f'{r["name"]} [{r["status"]}] · {r["members"]} · creator {r["creator_user_id"]}',callback_data=f'admin:league:{r["id"]}')
    b.button(text='⬅️ Назад',callback_data='admin:menu');b.adjust(1);await c.message.edit_text('🏆 <b>ЛИГИ</b>',reply_markup=b.as_markup());await c.answer()
@admin_router.callback_query(F.data.regexp(r'^admin:league:\d+(?::(?!delete:yes$).+)?$'))
async def admin_league_router(c,state):
    parts=c.data.split(':');lid=int(parts[2])
    if len(parts)==3:
        async with SessionLocal() as s:r=(await s.execute(text('SELECT * FROM leagues WHERE id=:id'),{'id':lid})).mappings().first()
        if not r:return await c.answer('Лига не найдена.',show_alert=True)
        await c.message.edit_text(f'🏆 <b>{r["name"]}</b> · {r["status"]}\ncreator={r["creator_user_id"]}',reply_markup=kb([('▶️ Стартовать',f'admin:league:{lid}:start'),('🔄 Добавить клуб',f'admin:league:{lid}:add'),('🔄 Убрать клуб',f'admin:league:{lid}:remove'),('🗑 Удалить лигу',f'admin:league:{lid}:delete'),('⬅️ Назад','admin:leagues')]));return await c.answer()
    action=parts[3]
    if action=='start':
        try:
            async with SessionLocal() as s:
                creator=(await s.execute(text('SELECT creator_user_id FROM leagues WHERE id=:id'),{'id':lid})).scalar_one()
                await start_league(s, creator)
                await s.commit()
            await log_action(c.from_user.id,'league_start','league',lid)
            await c.message.edit_text('▶️ Лига запущена, календарь создан.',reply_markup=back(f'admin:league:{lid}'))
        except Exception as exc:
            await c.message.answer(f'❌ Не удалось стартовать: {exc}')
    elif action in ('add','remove'):
        await state.set_state(AdminStates.league_add_club if action=='add' else AdminStates.league_remove_club);await state.update_data(league_id=lid);await c.message.answer('Введите club_id:')
    elif action=='delete':
        await state.set_state(AdminStates.league_delete_confirm);await state.update_data(league_id=lid);await c.message.edit_text('⚠️ <b>Удалить лигу навсегда?</b>',reply_markup=kb([('⚠️ Да, удалить',f'admin:league:{lid}:delete:yes'),('⬅️ Отмена',f'admin:league:{lid}')]))
    await c.answer()
@admin_router.message(AdminStates.league_add_club)
async def admin_league_add(m,state):
    try:cid=int(m.text)
    except:return await m.answer('Число.')
    d=await state.get_data()
    async with SessionLocal() as s:
        await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c) ON CONFLICT DO NOTHING'),{'l':d['league_id'],'c':cid});await s.commit()
    await log_action(m.from_user.id,'league_add_club','league',d['league_id'],{'club_id':cid});await state.clear();await m.answer('✅ Клуб добавлен.',reply_markup=back(f'admin:league:{d["league_id"]}'))
@admin_router.message(AdminStates.league_remove_club)
async def admin_league_remove(m,state):
    try:cid=int(m.text)
    except:return await m.answer('Число.')
    d=await state.get_data()
    async with SessionLocal() as s:await s.execute(text('DELETE FROM league_teams WHERE league_id=:l AND club_id=:c'),{'l':d['league_id'],'c':cid});await s.commit()
    await log_action(m.from_user.id,'league_remove_club','league',d['league_id'],{'club_id':cid});await state.clear();await m.answer('✅ Клуб убран.',reply_markup=back(f'admin:league:{d["league_id"]}'))
@admin_router.callback_query(F.data.startswith('admin:league:') & F.data.endswith(':delete:yes'))
async def admin_league_delete(c,state):
    parts=c.data.split(':')
    if len(parts)<5 or parts[1]!='league': return
    lid=int(parts[2])
    if not await _confirm_state(state, AdminStates.league_delete_confirm.state, 'league_id', lid):
        return await c.answer('Подтверждение устарело. Начните заново.', show_alert=True)
    try:
        async with SessionLocal() as s:
            await s.execute(text('DELETE FROM leagues WHERE id=:id'),{'id':lid}); await s.commit()
        await log_action(c.from_user.id,'league_delete','league',lid); await state.clear(); await c.message.edit_text('🗑 Лига удалена.',reply_markup=back('admin:leagues')); await c.answer()
    except Exception:
        if 's' in locals():
            await s.rollback()
        logger.exception('Admin league delete failed: league_id=%s', lid)
        await c.answer('❌ Не удалось удалить лигу.', show_alert=True)


# ---------------- system ----------------
@admin_router.callback_query(F.data == 'admin:system')
async def admin_system(c,state):await state.clear();await c.message.edit_text('⚙️ <b>СИСТЕМА</b>',reply_markup=kb([('📜 Лог действий админа','admin:system:log'),('🧹 Очистить кэш карточек','admin:system:cache'),('🔔 Очистить очередь уведомлений','admin:system:notif'),('⬅️ Назад','admin:menu')]));await c.answer()
@admin_router.callback_query(F.data == 'admin:system:log')
async def admin_log(c):
    async with SessionLocal() as s:rows=(await s.execute(text('SELECT id,action,target_type,target_id,created_at FROM admin_actions_log ORDER BY id DESC LIMIT 50'))).mappings().all()
    txt='📜 <b>Последние 50 действий</b>\n'+'\n'.join(f'#{r["id"]} {r["created_at"]:%Y-%m-%d %H:%M} {r["action"]} {r["target_type"] or ""}:{r["target_id"] or ""}' for r in rows)
    await c.message.edit_text(txt or 'Лог пуст.',reply_markup=back('admin:system'));await c.answer()
@admin_router.callback_query(F.data == 'admin:system:cache')
async def admin_cache(c):
    root=Path(__file__).resolve().parents[2]/'assets/visual/cards';count=0
    for p in root.glob('*.png'):
        try:p.unlink();count+=1
        except OSError:pass
    await log_action(c.from_user.id,'clear_card_cache',payload={'deleted':count});await c.message.edit_text(f'🧹 Удалено карточек: {count}',reply_markup=back('admin:system'));await c.answer()
@admin_router.callback_query(F.data == 'admin:system:notif')
async def admin_notif(c):
    async with SessionLocal() as s:res=await s.execute(text('DELETE FROM notifications WHERE sent_at IS NULL'));await s.commit();count=res.rowcount
    await log_action(c.from_user.id,'clear_notification_queue',payload={'deleted':count});await c.message.edit_text(f'🔔 Очередь очищена: {count}',reply_markup=back('admin:system'));await c.answer()
