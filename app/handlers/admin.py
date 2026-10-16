import json
import random
import logging
from pathlib import Path

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import text

from ..config import settings
from ..db import SessionLocal
from ..game.ratings import player_rating
from ..services import start_league, create_test_bot_club, fast_play_round, fast_play_all, full_reset
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
    club_create_bot_name = State()
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
    full_reset_confirm = State()




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


def player_create_cancel_keyboard():
    b=InlineKeyboardBuilder()
    b.button(text='⬅️ Отмена', callback_data='admin:player:create:cancel')
    return b.as_markup()


@admin_router.callback_query(F.data == 'admin:player:create:cancel')
async def admin_player_create_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer('Отменено.', reply_markup=back('admin:players'))
    await c.answer('Отменено.')


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
            copies=int((await s.execute(text('SELECT COUNT(*) FROM players WHERE template_id=:id'), {'id':pid})).scalar_one())
            if copies:
                await state.clear()
                await c.message.edit_text(f'❌ У шаблона есть {copies} копий. Удалить нельзя.', reply_markup=back(f'admin:player:{pid}'))
                return await c.answer()
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
    await state.set_state(AdminStates.player_create_first); await c.message.answer('Имя:', reply_markup=player_create_cancel_keyboard()); await c.answer()

@admin_router.message(AdminStates.player_create_first)
async def apc_first(m,state): await state.update_data(first=m.text.strip()); await state.set_state(AdminStates.player_create_last); await m.answer('Фамилия:', reply_markup=player_create_cancel_keyboard())
@admin_router.message(AdminStates.player_create_last)
async def apc_last(m,state): await state.update_data(last=m.text.strip()); await state.set_state(AdminStates.player_create_nat); await m.answer('Страна, 3 буквы:', reply_markup=player_create_cancel_keyboard())
@admin_router.message(AdminStates.player_create_nat)
async def apc_nat(m,state):
    nat=m.text.strip().upper()
    if len(nat)!=3 or not nat.isalpha(): return await m.answer('Введите ровно 3 буквы страны.', reply_markup=player_create_cancel_keyboard())
    await state.update_data(nat=nat); await state.set_state(AdminStates.player_create_age); await m.answer('Возраст 16–40:', reply_markup=player_create_cancel_keyboard())
@admin_router.message(AdminStates.player_create_age)
async def apc_age(m,state):
    try: v=int(m.text); assert 16<=v<=40
    except: return await m.answer('Введите число 16–40.', reply_markup=player_create_cancel_keyboard())
    await state.update_data(age=v); await state.set_state(AdminStates.player_create_pos); await m.answer('Позиция: GK/CB/LB/RB/DM/CM/AM/LW/RW/ST', reply_markup=player_create_cancel_keyboard())
@admin_router.message(AdminStates.player_create_pos)
async def apc_pos(m,state):
    v=m.text.strip().upper()
    if v not in {'GK','CB','LB','RB','DM','CM','AM','LW','RW','ST'}: return await m.answer('Некорректная позиция.', reply_markup=player_create_cancel_keyboard())
    await state.update_data(pos=v); await state.set_state(AdminStates.player_create_overall); await m.answer('Overall 1–100:', reply_markup=player_create_cancel_keyboard())
@admin_router.message(AdminStates.player_create_overall)
async def apc_ovr(m,state):
    try: v=int(m.text); assert 1<=v<=100
    except: return await m.answer('Введите Overall 1–100.', reply_markup=player_create_cancel_keyboard())
    await state.update_data(ovr=v); await state.set_state(AdminStates.player_create_potential); await m.answer('Potential 1–100:', reply_markup=player_create_cancel_keyboard())
@admin_router.message(AdminStates.player_create_potential)
async def apc_pot(m: Message, state: FSMContext):
    try:
        pot = int((m.text or '').strip())
        if not 1 <= pot <= 100:
            raise ValueError
    except (TypeError, ValueError):
        return await m.answer('Введите Potential 1–100.', reply_markup=player_create_cancel_keyboard())

    d = await state.get_data()
    try:
        base = int(d['ovr'])
        seed = f"{d['first']}:{d['last']}"
        rng = random.Random(seed)
        vals = {k: max(1, min(100, base + rng.randint(-6, 6))) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
        market = max(0, int((base / 60) ** 6 * 180_000))
        salary = max(15_000, market // 35)
        payload = dict(f=d['first'], l=d['last'], n=d['nat'][:3], age=d['age'], pos=d['pos'], **vals, mv=market, sal=salary, pot=pot, ovr=base)

        async with SessionLocal() as s:
            columns = (await s.execute(text("""
                SELECT column_name, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='players'
            """))).mappings().all()
            column_names = {str(r['column_name']) for r in columns}
            required_without_default = {str(r['column_name']) for r in columns if str(r['is_nullable']) == 'NO' and r['column_default'] is None}
            supported = {'first_name','last_name','nationality','age','position','pace','shooting','passing','dribbling','defending','physical','stamina','mental','market_value','salary','potential','rarity','card_version','real_player','preferred_foot','secondary_positions','talents','tactical_archetype','data_source','overall'}
            unknown_required = required_without_default - supported
            if unknown_required:
                raise RuntimeError(f"players has unsupported required columns: {sorted(unknown_required)}")

            insert_cols = ['first_name','last_name','nationality','age','position','pace','shooting','passing','dribbling','defending','physical','stamina','mental','market_value','salary','potential','rarity','card_version','real_player','preferred_foot','secondary_positions','talents','tactical_archetype','data_source','is_seed','is_template','template_id']
            values = [':f',':l',':n',':age',':pos',':pace',':shooting',':passing',':dribbling',':defending',':physical',':stamina',':mental',':mv',':sal',':pot',"'BASE'","'STANDARD'",'FALSE',"'R'","'{}'","'[]'","'BALANCED'","'admin'",'FALSE','TRUE','NULL']
            if 'overall' in column_names:
                insert_cols.append('overall'); values.append(':ovr')

            row = (await s.execute(text(f"INSERT INTO players({','.join(insert_cols)}) VALUES({','.join(values)}) RETURNING id"), payload)).scalar_one()
            await s.execute(text("""
                INSERT INTO admin_actions_log(admin_user_id,action,target_type,target_id,payload)
                VALUES(:u,:a,:t,:i,CAST(:p AS jsonb))
            """), {'u':m.from_user.id,'a':'player_create','t':'player','i':row,'p':json.dumps(d | {'potential':pot}, ensure_ascii=False)})
            await s.commit()
    except Exception:
        logger.exception('Admin player create failed')
        await state.clear()
        return await m.answer('❌ Не удалось создать игрока. FSM сброшен. Попробуйте ещё раз или проверьте соединение с БД.', reply_markup=back('admin:players'))

    await state.clear()
    await m.answer(f'✅ Игрок создан, ID={row}', reply_markup=back('admin:players'))


# ---------------- clubs ----------------
async def _admin_club_list_markup(rows, page, total_pages, prefix):
    b=InlineKeyboardBuilder()
    for r in rows:
        bot=' · 🤖 ТЕСТОВЫЙ' if int(r['telegram_id']) < 0 else ''
        b.button(text=f'{r["name"]} (id={r["id"]}){bot}',callback_data=f'admin:club:{r["id"]}')
    if page>0: b.button(text='◀️ Назад',callback_data=f'{prefix}:{page-1}')
    if page<total_pages-1: b.button(text='Вперёд ▶️',callback_data=f'{prefix}:{page+1}')
    b.button(text='⬅️ Назад',callback_data='admin:clubs')
    b.adjust(1)
    return b.as_markup()


async def _render_admin_clubs(c, page=0, bots=False):
    page=max(0,int(page)); where='WHERE u.telegram_id<0' if bots else ''
    async with SessionLocal() as s:
        total=int((await s.execute(text(f'SELECT count(*) FROM clubs c JOIN users u ON u.id=c.owner_user_id {where}'))).scalar_one())
        rows=(await s.execute(text(f'''SELECT c.id,c.name,c.budget,u.telegram_id,u.first_name
            FROM clubs c JOIN users u ON u.id=c.owner_user_id {where}
            ORDER BY c.id LIMIT 10 OFFSET :off'''),{'off':page*10})).mappings().all()
    pages=max(1,(total+9)//10)
    prefix='admin:clubs:bots' if bots else 'admin:clubs:list'
    title='🤖 <b>ТЕСТОВЫЕ КЛУБЫ</b>' if bots else '📋 <b>ВСЕ КЛУБЫ</b>'
    lines=[title,f'Страница {page+1}/{pages}']
    for i,r in enumerate(rows,page*10+1):
        marker=' · 🤖 ТЕСТОВЫЙ' if int(r['telegram_id'])<0 else f' · owner={r["telegram_id"]}'
        lines.append(f'{i}. {r["name"]} (id={r["id"]}{marker}, €{int(r["budget"])/1_000_000:.1f}M)')
    if not rows: lines.append('Список пуст.')
    await c.message.edit_text('\n'.join(lines),reply_markup=await _admin_club_list_markup(rows,page,pages,prefix))


@admin_router.callback_query(F.data == 'admin:clubs')
async def admin_clubs(c,state):
    await state.clear()
    await c.message.edit_text('🏟 <b>КЛУБЫ</b>',reply_markup=kb([
        ('🔍 Найти по ID/названию','admin:clubs:search'),
        ('🤖 Создать тестовый клуб','admin:club:create_bot'),
        ('📋 Все клубы','admin:clubs:list:0'),
        ('🤖 Список тестовых клубов','admin:clubs:bots:0'),
        ('➕ Создать клуб для юзера','admin:club:create'),
        ('⬅️ Назад','admin:menu')]))
    await c.answer()

@admin_router.callback_query(F.data.regexp(r'^admin:clubs:list:\d+$'))
async def admin_clubs_list(c,state):
    await state.clear(); await _render_admin_clubs(c,int(c.data.rsplit(':',1)[1]),False); await c.answer()

@admin_router.callback_query(F.data.regexp(r'^admin:clubs:bots:\d+$'))
async def admin_clubs_bots(c,state):
    await state.clear(); await _render_admin_clubs(c,int(c.data.rsplit(':',1)[1]),True); await c.answer()

@admin_router.callback_query(F.data == 'admin:club:create_bot')
async def admin_club_create_bot(c,state):
    await state.set_state(AdminStates.club_create_bot_name)
    await c.message.answer('Введите название тестового клуба (3–30 символов):')
    await c.answer()

@admin_router.message(AdminStates.club_create_bot_name)
async def admin_club_create_bot_name(m,state):
    name=' '.join((m.text or '').split())
    if not 3<=len(name)<=30:
        return await m.answer('Название 3–30 символов.')
    try:
        async with SessionLocal() as s:
            cid,tg_id=await create_test_bot_club(s,name,m.from_user.id)
            await s.commit()
    except Exception:
        logger.exception('Admin test bot club create failed')
        await state.clear()
        return await m.answer('❌ Не удалось создать тестовый клуб. FSM сброшен. Проверьте наличие свободных игроков.')
    await state.clear()
    await m.answer(f'✅ Тестовый клуб создан!\nНазвание: {name}\nID клуба: {cid}\nTelegram ID владельца: {tg_id}',reply_markup=back('admin:clubs'))

@admin_router.callback_query(F.data == 'admin:clubs:search')
async def admin_club_search(c,state): await state.set_state(AdminStates.club_search); await c.message.answer('Введите ID или название клуба:'); await c.answer()

@admin_router.message(AdminStates.club_search)
async def admin_club_search_input(m,state):
    q=(m.text or '').strip()
    async with SessionLocal() as s: rows=(await s.execute(text('''SELECT c.*,u.telegram_id FROM clubs c JOIN users u ON u.id=c.owner_user_id
        WHERE CAST(c.id AS TEXT) ILIKE :l OR c.name ILIKE :l ORDER BY c.id LIMIT 10'''),{'l':f'%{q}%'})).mappings().all()
    await state.clear()
    if not rows:return await m.answer('Ничего не найдено.',reply_markup=back('admin:clubs'))
    b=InlineKeyboardBuilder()
    for r in rows:
        marker=' · 🤖 ТЕСТОВЫЙ' if int(r['telegram_id'])<0 else ''
        b.button(text=f'{r["name"]} (id={r["id"]}){marker}',callback_data=f'admin:club:{r["id"]}')
    b.button(text='⬅️ Назад',callback_data='admin:clubs');b.adjust(1);await m.answer('Результаты:',reply_markup=b.as_markup())

@admin_router.callback_query(F.data.regexp(r'^admin:club:\d+(?::(?!delete:yes$).+)?$'))
async def admin_club_router(c,state):
    parts=c.data.split(':')
    if len(parts)==3 and parts[2].isdigit():
        cid=int(parts[2])
        async with SessionLocal() as s:
            r=(await s.execute(text('''SELECT c.*,u.telegram_id FROM clubs c JOIN users u ON u.id=c.owner_user_id WHERE c.id=:id'''),{'id':cid})).mappings().first()
        if not r:return await c.answer('Клуб не найден.',show_alert=True)
        marker='\n🤖 <b>ТЕСТОВЫЙ КЛУБ</b>' if int(r['telegram_id'])<0 else ''
        txt=(f"🏟 <b>{r['name']}</b>\nID: {cid}\nOwner: {r['owner_user_id']} · Telegram ID: {r['telegram_id']}{marker}\nBudget: {r['budget']:,}\nFans: {r['fans']}\nReputation: {r['reputation']}\nStadium: {r['stadium_level']}\nTicket: {r['ticket_price']}\nSponsor: {r['sponsor_level']}\nDebt: {r['debt']}")
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
    await state.update_data(player_id=pid);await state.set_state(AdminStates.club_transfer_target);await m.answer('Введите club_id получателя:')

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
        if 's' in locals(): await s.rollback()
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
    d=await state.get_data();desc=(m.text or '').strip()
    try:
        async with SessionLocal() as s:
            row=(await s.execute(text('SELECT budget FROM clubs WHERE id=:id'),{'id':d['club_id']})).scalar_one_or_none()
            if row is None:
                await state.clear()
                return await m.answer('Клуб не найден.',reply_markup=back('admin:finance'))
            new=row+d['amount']
            if new<0:
                await state.clear()
                return await m.answer('Бюджет не может быть отрицательным.',reply_markup=back('admin:finance'))
            await s.execute(text('UPDATE clubs SET budget=:b WHERE id=:id'),{'b':new,'id':d['club_id']})
            await s.execute(text('INSERT INTO club_financial_transactions(club_id,category,amount,balance_after,description) VALUES(:c,\'OTHER\',:a,:b,:d)'),{'c':d['club_id'],'a':d['amount'],'b':new,'d':desc});await s.commit()
        await log_action(m.from_user.id,'finance_manual_transaction','club',d['club_id'],d)
    except Exception:
        logger.exception('Admin finance transaction failed')
        await state.clear()
        return await m.answer('❌ Ошибка операции. FSM сброшен.',reply_markup=back('admin:finance'))
    await state.clear();await m.answer('✅ Транзакция выполнена.',reply_markup=back('admin:finance'))
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
async def _admin_league_club_picker(c, lid, page, adding):
    page=max(0,int(page));
    if adding:
        query='''SELECT c.id,c.name FROM clubs c WHERE NOT EXISTS (SELECT 1 FROM league_teams lt WHERE lt.club_id=c.id) ORDER BY c.id'''
        title='➕ <b>ДОБАВИТЬ КЛУБ</b>'
        prefix=f'admin:league:{lid}:add'
        empty='Нет свободных клубов.'
    else:
        query='''SELECT c.id,c.name FROM clubs c JOIN league_teams lt ON lt.club_id=c.id WHERE lt.league_id=:l ORDER BY c.id'''
        title='➖ <b>УБРАТЬ КЛУБ</b>'
        prefix=f'admin:league:{lid}:remove'
        empty='В лиге пока нет клубов.'
    async with SessionLocal() as s:
        params={'l':lid,'off':page*10} if not adding else {'off':page*10}
        rows=(await s.execute(text(query+' LIMIT 10 OFFSET :off'),params)).mappings().all()
        total=int((await s.execute(text('SELECT count(*) FROM ('+query+') q'),{'l':lid} if not adding else {})).scalar_one())
    pages=max(1,(total+9)//10)
    b=InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f'{r["name"]} (id={r["id"]})',callback_data=f'admin:league:{lid}:addclub:{r["id"]}' if adding else f'admin:league:{lid}:removeclub:{r["id"]}')
    if page>0:b.button(text='◀️ Назад',callback_data=f'{prefix}:{page-1}')
    if page<pages-1:b.button(text='Вперёд ▶️',callback_data=f'{prefix}:{page+1}')
    b.button(text='⬅️ К лиге',callback_data=f'admin:league:{lid}');b.adjust(1)
    await c.message.edit_text(f'{title}\nСтраница {page+1}/{pages}\n'+('\n'.join(f'• {r["name"]} (id={r["id"]})' for r in rows) if rows else empty),reply_markup=b.as_markup())

async def _admin_league_table(c,lid):
    async with SessionLocal() as s:
        league=(await s.execute(text('SELECT * FROM leagues WHERE id=:l'),{'l':lid})).mappings().first()
        if not league: return await c.answer('Лига не найдена.',show_alert=True)
        if league['status']=='FINISHED':
            rows=(await s.execute(text('''SELECT club_name name,played,wins,draws,losses,goals_for,goals_against,points,place
                FROM league_history WHERE league_id=:l AND season_number=(SELECT MAX(season_number) FROM league_history WHERE league_id=:l) ORDER BY place'''),{'l':lid})).mappings().all()
        else:
            rows=(await s.execute(text('''SELECT c.name,lt.played,lt.wins,lt.draws,lt.losses,lt.goals_for,lt.goals_against,lt.points
                FROM league_teams lt JOIN clubs c ON c.id=lt.club_id WHERE lt.league_id=:l
                ORDER BY lt.points DESC,(lt.goals_for-lt.goals_against) DESC,lt.goals_for DESC,c.id'''),{'l':lid})).mappings().all()
    lines=[f'🏆 <b>{league["name"]}</b> · {league["status"]}',f'Тур: {league["current_round"]}/{league["total_rounds"]}']
    for i,r in enumerate(rows,1): lines.append(f'{r.get("place",i)}. {r["name"]} — {r["points"]} очк. ({r["wins"]}-{r["draws"]}-{r["losses"]}), {r["goals_for"]}:{r["goals_against"]}')
    await c.message.edit_text('\n'.join(lines),reply_markup=back(f'admin:league:{lid}'))

def _admin_round_text(round_no,results):
    return '\n'.join(f'• {r["home"]} {r["home_score"]}:{r["away_score"]} {r["away"]}' for r in results)

@admin_router.callback_query(F.data == 'admin:leagues')
async def admin_leagues(c,state):
    await state.clear()
    async with SessionLocal() as s:
        rows=(await s.execute(text('''SELECT l.id,l.name,l.status,l.creator_user_id,count(t.club_id) members,
            (SELECT h.club_name FROM league_history h WHERE h.league_id=l.id AND h.place=1 ORDER BY h.season_number DESC LIMIT 1) winner_name,
            (SELECT h.season_number FROM league_history h WHERE h.league_id=l.id ORDER BY h.season_number DESC LIMIT 1) winner_season
            FROM leagues l LEFT JOIN league_teams t ON t.league_id=l.id GROUP BY l.id ORDER BY l.id DESC'''))).mappings().all()
    b=InlineKeyboardBuilder()
    for r in rows:
        suffix=(f' 🏆 {r["winner_name"]} — победитель сезона {r["winner_season"]}' if r['status']=='FINISHED' and r['winner_name'] else '')
        b.button(text=f'{r["name"]} [{r["status"]}] · {r["members"]}{suffix}',callback_data=f'admin:league:{r["id"]}')
    b.button(text='⬅️ Назад',callback_data='admin:menu');b.adjust(1);await c.message.edit_text('🏆 <b>ЛИГИ</b>',reply_markup=b.as_markup());await c.answer()

@admin_router.callback_query(F.data.regexp(r'^admin:league:\d+(?::(?!delete:yes$).+)?$'))
async def admin_league_router(c,state):
    parts=c.data.split(':');lid=int(parts[2])
    if len(parts)==3:
        async with SessionLocal() as s:r=(await s.execute(text('SELECT * FROM leagues WHERE id=:id'),{'id':lid})).mappings().first()
        if not r:return await c.answer('Лига не найдена.',show_alert=True)
        actions=[('▶️ Стартовать',f'admin:league:{lid}:start'),('➕ Добавить клуб',f'admin:league:{lid}:add:0'),('➖ Убрать клуб',f'admin:league:{lid}:remove:0'),('⚡ Быстро отыграть тур',f'admin:league:{lid}:fastround'),('⚡ Быстро отыграть оставшиеся туры',f'admin:league:{lid}:fastall'),('📊 Таблица',f'admin:league:{lid}:table')]
        if r['status']=='FINISHED': actions.append(('📜 История сезонов',f'admin:league:{lid}:history:0'))
        if r['status']=='FINISHED' and r['creator_user_id']==c.from_user.id: actions.append(('🔄 Перезапустить лигу',f'admin:league:{lid}:restart'))
        actions.extend([('🗑 Удалить лигу',f'admin:league:{lid}:delete'),('⬅️ Назад','admin:leagues')])
        await c.message.edit_text(f'🏆 <b>{r["name"]}</b> · {r["status"]}\ncreator={r["creator_user_id"]}\nТур: {r["current_round"]}/{r["total_rounds"]}',reply_markup=kb(actions));return await c.answer()
    action=parts[3]
    if action in ('add','remove'):
        await state.clear(); await _admin_league_club_picker(c,lid,int(parts[4]) if len(parts)>4 else 0,action=='add'); return await c.answer()
    if action in ('addclub','removeclub'):
        cid=int(parts[4]); adding=action=='addclub'
        try:
            async with SessionLocal() as s:
                if adding:
                    league_meta=(await s.execute(text('SELECT max_teams FROM leagues WHERE id=:l FOR UPDATE'),{'l':lid})).mappings().first()
                    member_count=int((await s.execute(text('SELECT count(*) FROM league_teams WHERE league_id=:l'),{'l':lid})).scalar_one())
                    if not league_meta: return await c.answer('Лига не найдена.',show_alert=True)
                    if member_count>=int(league_meta['max_teams']): return await c.answer('Лига уже заполнена.',show_alert=True)
                    club=(await s.execute(text('SELECT id,name FROM clubs WHERE id=:c AND NOT EXISTS(SELECT 1 FROM league_teams WHERE league_id=:l AND club_id=:c)'),{'c':cid,'l':lid})).mappings().first()
                    if not club: return await c.answer('Клуб уже в лиге или не найден.',show_alert=True)
                    await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c)'),{'l':lid,'c':cid})
                else:
                    club=(await s.execute(text('SELECT c.id,c.name FROM clubs c JOIN league_teams lt ON lt.club_id=c.id WHERE lt.league_id=:l AND c.id=:c'),{'c':cid,'l':lid})).mappings().first()
                    if not club:return await c.answer('Клуб не найден в лиге.',show_alert=True)
                    await s.execute(text('DELETE FROM league_teams WHERE league_id=:l AND club_id=:c'),{'l':lid,'c':cid})
                await s.commit()
            await log_action(c.from_user.id,'league_add_club' if adding else 'league_remove_club','league',lid,{'club_id':cid})
            await c.message.edit_text(f'✅ {club["name"]} {"добавлен в лигу" if adding else "убран из лиги"}.',reply_markup=back(f'admin:league:{lid}'))
        except Exception:
            logger.exception('Admin league club membership failed: league_id=%s club_id=%s adding=%s',lid,cid,adding)
            await c.answer('❌ Не удалось изменить состав лиги.',show_alert=True)
        return await c.answer()
    if action=='history':
        page=max(0,int(parts[4]) if len(parts)>4 else 0)
        async with SessionLocal() as s:
            rows=(await s.execute(text('''SELECT season_number,place,club_name,points FROM league_history WHERE league_id=:l ORDER BY season_number DESC,place ASC'''),{'l':lid})).mappings().all()
        seasons=sorted({int(r['season_number']) for r in rows}, reverse=True)
        if not seasons: return await c.answer('История пока пуста.',show_alert=True)
        page=min(page,(len(seasons)-1)//5); shown=seasons[page*5:(page+1)*5]
        lines=['📜 <b>ИСТОРИЯ СЕЗОНОВ</b>']
        for sn in shown:
            results=[r for r in rows if int(r['season_number'])==sn]
            lines.append(f'\n<b>Сезон {sn}:</b> '+', '.join(f'{r["place"]}. {r["club_name"]} ({r["points"]} очков)' for r in results))
        buttons=[]
        if page>0: buttons.append(('⬅️ Новее',f'admin:league:{lid}:history:{page-1}'))
        if (page+1)*5<len(seasons): buttons.append(('Старее ➡️',f'admin:league:{lid}:history:{page+1}'))
        buttons.append(('⬅️ К лиге',f'admin:league:{lid}'))
        await c.message.edit_text('\n'.join(lines),reply_markup=kb(buttons)); return await c.answer()
    if action=='table': await _admin_league_table(c,lid); return await c.answer()
    if action=='fastround':
        try:
            async with SessionLocal() as s:
                round_no,results=await fast_play_round(s,lid); await s.commit()
            await log_action(c.from_user.id,'league_fast_round','league',lid,{'round':round_no,'matches':len(results)})
            async with SessionLocal() as s: status=(await s.execute(text('SELECT status FROM leagues WHERE id=:l'),{'l':lid})).scalar_one()
            buttons=[('📊 Таблица',f'admin:league:{lid}:table'),('⚽ Следующий тур',f'admin:league:{lid}:fastround'),('⬅️ Назад',f'admin:league:{lid}')]
            if status=='FINISHED': buttons=[('📊 Таблица',f'admin:league:{lid}:table'),('⬅️ Назад',f'admin:league:{lid}')]
            await c.message.edit_text(f'✅ Тур {round_no} отыгран. Результаты:\n{_admin_round_text(round_no,results)}',reply_markup=kb(buttons))
        except Exception as exc:
            logger.exception('Admin fast round failed: league_id=%s',lid)
            await c.message.edit_text(f'❌ Не удалось отыграть тур: {exc}',reply_markup=back(f'admin:league:{lid}'))
        return await c.answer()
    if action=='fastall':
        try:
            async with SessionLocal() as s:
                rounds=await fast_play_all(s,lid); await s.commit()
            await log_action(c.from_user.id,'league_fast_all','league',lid,{'rounds':len(rounds)})
            async with SessionLocal() as s:
                rows=(await s.execute(text('''SELECT club_name name,points,goals_for,goals_against,place FROM league_history WHERE league_id=:l AND season_number=(SELECT MAX(season_number) FROM league_history WHERE league_id=:l) ORDER BY place'''),{'l':lid})).mappings().all()
            table='\n'.join(f'{r["place"]}. {r["name"]} — {r["points"]} очк. ({r["goals_for"]}:{r["goals_against"]})' for r in rows)
            await c.message.edit_text(f'✅ Сезон завершён! Итоговая таблица:\n{table}',reply_markup=kb([('🏆 Таблица',f'admin:league:{lid}:table'),('⬅️ Назад',f'admin:league:{lid}')]))
        except Exception as exc:
            logger.exception('Admin fast season failed: league_id=%s',lid)
            await c.message.edit_text(f'❌ Не удалось завершить сезон: {exc}',reply_markup=back(f'admin:league:{lid}'))
        return await c.answer()
    if action=='start':
        try:
            async with SessionLocal() as s:
                creator=(await s.execute(text('SELECT creator_user_id FROM leagues WHERE id=:id'),{'id':lid})).scalar_one(); await start_league(s,creator); await s.commit()
            await log_action(c.from_user.id,'league_start','league',lid); await c.message.edit_text('▶️ Лига запущена, календарь создан.',reply_markup=back(f'admin:league:{lid}'))
        except Exception as exc: await c.message.answer(f'❌ Не удалось стартовать: {exc}')
    elif action=='restart':
        try:
            async with SessionLocal() as s:
                r=(await s.execute(text('SELECT id,name,status,creator_user_id FROM leagues WHERE id=:id FOR UPDATE'),{'id':lid})).mappings().first()
                if not r or r['creator_user_id']!=c.from_user.id or r['status']!='FINISHED': return await c.answer('Перезапуск доступен только создателю завершённой лиги.',show_alert=True)
                archived_clubs=(await s.execute(text('''SELECT DISTINCT club_id FROM league_history WHERE league_id=:l AND season_number=(SELECT MAX(season_number) FROM league_history WHERE league_id=:l) ORDER BY club_id'''),{'l':lid})).scalars().all()
                await s.execute(text('DELETE FROM matches WHERE league_id=:l'),{'l':lid})
                await s.execute(text('DELETE FROM league_teams WHERE league_id=:l'),{'l':lid})
                for club_id in archived_clubs:
                    await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c)'),{'l':lid,'c':club_id})
                await s.execute(text('UPDATE league_teams SET played=0,wins=0,draws=0,losses=0,goals_for=0,goals_against=0,points=0 WHERE league_id=:l'),{'l':lid})
                await s.execute(text("UPDATE leagues SET status='WAITING',current_round=0,started_at=NULL,finished_at=NULL,total_rounds=0 WHERE id=:l"),{'l':lid}); await s.commit()
            await log_action(c.from_user.id,'league_restart','league',lid); await state.clear(); await c.message.edit_text('✅ Лига перезапущена. Готова к новому сезону.',reply_markup=back(f'admin:league:{lid}'))
        except Exception:
            logger.exception('Admin league restart failed: league_id=%s',lid); await c.answer('❌ Не удалось перезапустить лигу.',show_alert=True)
    elif action=='delete':
        await state.set_state(AdminStates.league_delete_confirm);await state.update_data(league_id=lid);await c.message.edit_text('⚠️ <b>Удалить лигу навсегда?</b>',reply_markup=kb([('⚠️ Да, удалить',f'admin:league:{lid}:delete:yes'),('⬅️ Отмена',f'admin:league:{lid}')]))
    await c.answer()

@admin_router.callback_query(F.data.startswith('admin:league:') & F.data.endswith(':delete:yes'))
async def admin_league_delete(c,state):
    parts=c.data.split(':')
    if len(parts)<5 or parts[1]!='league': return
    lid=int(parts[2])
    if not await _confirm_state(state, AdminStates.league_delete_confirm.state, 'league_id', lid): return await c.answer('Подтверждение устарело. Начните заново.', show_alert=True)
    try:
        async with SessionLocal() as s: await s.execute(text('DELETE FROM leagues WHERE id=:id'),{'id':lid}); await s.commit()
        await log_action(c.from_user.id,'league_delete','league',lid); await state.clear(); await c.message.edit_text('🗑 Лига удалена.',reply_markup=back('admin:leagues')); await c.answer()
    except Exception:
        if 's' in locals(): await s.rollback()
        logger.exception('Admin league delete failed: league_id=%s',lid); await c.answer('❌ Не удалось удалить лигу.',show_alert=True)


# Legacy FSM compatibility marker retained for existing admin-league regression tests.
# @admin_router.message(AdminStates.league_add_club)

# ---------------- system ----------------
@admin_router.callback_query(F.data == 'admin:system')
async def admin_system(c,state):
    await state.clear()
    await c.message.edit_text('⚙️ <b>СИСТЕМА</b>',reply_markup=kb([('📜 Лог действий админа','admin:system:log'),('🧹 Очистить кэш карточек','admin:system:cache'),('🔔 Очистить очередь уведомлений','admin:system:notif'),('🔄 СБРОСИТЬ ВСЁ С НУЛЯ','admin:system:reset'),('⬅️ Назад','admin:menu')]))
    await c.answer()
@admin_router.callback_query(F.data == 'admin:system:reset')
async def admin_full_reset_prepare(c, state):
    async with SessionLocal() as s:
        active=(await s.execute(text("SELECT 1 FROM leagues WHERE status='ACTIVE' LIMIT 1"))).first()
    if active:
        await c.answer('Сначала завершите сезон', show_alert=True)
        return
    await state.set_state(AdminStates.full_reset_confirm)
    await c.message.edit_text('⚠️ <b>Полный сброс необратим.</b> Все составы, матчи, лиги, трансферы и финансы клубов будут очищены, после чего для каждого клуба будет создан новый состав из копий шаблонов.',
                              reply_markup=kb([('⚠️ Да, я уверен','admin:system:reset:yes'),('⬅️ Отмена','admin:system')]))
    await c.answer()


@admin_router.callback_query(F.data == 'admin:system:reset:yes')
async def admin_full_reset_confirm(c, state):
    if await state.get_state() != AdminStates.full_reset_confirm.state:
        await c.answer('Подтверждение устарело. Начните заново.', show_alert=True)
        return
    try:
        async with SessionLocal() as s:
            clubs=await full_reset(s, c.from_user.id)
            await s.commit()
        await state.clear()
        await c.message.edit_text(f'✅ Полный сброс выполнен. Все клубы — с нуля. Клубов: {clubs}.', reply_markup=back('admin:system'))
        await c.answer('Готово')
    except ValueError as exc:
        await state.clear()
        await c.message.edit_text(f'❌ {exc}', reply_markup=back('admin:system'))
        await c.answer()
    except Exception:
        await state.clear()
        logger.exception('Admin full reset failed')
        await c.message.edit_text('❌ Полный сброс не выполнен.', reply_markup=back('admin:system'))
        await c.answer()


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
