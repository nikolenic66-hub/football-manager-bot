from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import text
from pathlib import Path

from .config import settings
from .db import SessionLocal
from .game.ratings import player_rating
from .repositories import create_club, get_or_create_user
from .services import (
    accept_transfer_offer,
    buy_player,
    change_halftime_tactics,
    create_league,
    financial_report,
    get_tactical_board,
    join_league,
    list_player_for_transfer,
    make_transfer_offer,
    market_players,
    next_opponent,
    notification_list,
    notification_preferences,
    notification_unread_count,
    mark_notifications_read,
    player_management_report,
    plan_substitution,
    renew_contract,
    schedule_round,
    scout_opponent,
    sell_player,
    set_counterplan,
    set_notification_preference,
    set_player_expectation,
    set_player_instruction,
    set_starting_lineup,
    set_ticket_price,
    start_league,
    start_round_halftime,
    train_squad,
    upgrade_stadium,
    set_training_plan,
)
from .visual import (
    club_crest,
    club_dashboard,
    dynamic_match_screen,
    event_replay_screen,
    finance_screen,
    fixtures_screen,
    formation_board,
    league_table_screen,
    market_screen,
    match_center,
    match_phase_screen,
    player_card,
    scouting_screen,
    squad_screen,
    tactical_board_screen,
)

router = Router()


def menu():
    k = InlineKeyboardBuilder()
    for t, d in [
        ("🏟 Клуб", "club"),
        ("👥 Состав", "visual:squad"),
        ("📋 Тактика", "tactics"),
        ("📺 Матч", "matchcenter"),
        ("🏆 Лига", "league"),
        ("🔄 Рынок", "market"),
        ("💰 Финансы", "finance"),
        ("🏋️ Тренировки", "training"),
        ("📊 Таблица", "table"),
        ("📅 Календарь", "fixtures"),
        ("🕵️ Скаутинг", "scout"),
        ("🔔 Уведомления", "notifications"),
    ]:
        k.button(text=t, callback_data=d)
    k.adjust(2, 2, 2)
    return k.as_markup()


async def current_user(s, m: Message):
    return await get_or_create_user(s, m.from_user.id, m.from_user.username, m.from_user.first_name)


async def current_club(s, uid):
    return (
        await s.execute(
            text("SELECT * FROM clubs WHERE owner_user_id=:u ORDER BY id LIMIT 1"),
            {"u": uid},
        )
    ).mappings().first()


async def active_league(s, club_id):
    return (
        await s.execute(
            text(
                """SELECT l.id,l.name,l.status,l.current_round,l.total_rounds
                FROM leagues l JOIN league_teams t ON t.league_id=l.id
                WHERE t.club_id=:c
                ORDER BY CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'WAITING' THEN 1 ELSE 2 END, l.id DESC
                LIMIT 1"""
            ),
            {"c": club_id},
        )
    ).mappings().first()


async def club_by_tg(s, telegram_id):
    return (
        await s.execute(
            text(
                "SELECT c.* FROM users u JOIN clubs c ON c.owner_user_id=u.id WHERE u.telegram_id=:t ORDER BY c.id LIMIT 1"
            ),
            {"t": telegram_id},
        )
    ).mappings().first()


def tactics_keyboard(formation):
    k = InlineKeyboardBuilder()
    for f in ("4-3-3", "4-2-3-1", "4-4-2", "3-5-2", "5-3-2"):
        k.button(text=("✓ " if f == formation else "") + f, callback_data=f"tac:f:{f}")
    for st in ("BALANCE", "ATTACK", "COUNTER", "DEFENSE", "POSSESSION"):
        k.button(text=st, callback_data=f"tac:s:{st}")
    k.button(text="🔄 Обновить доску", callback_data="tac:refresh")
    k.adjust(3, 2, 1)
    return k.as_markup()


@router.message(CommandStart())
async def start(m: Message):
    async with SessionLocal() as s:
        uid = await current_user(s, m)
        c = await current_club(s, uid)
        await s.commit()
    if c:
        crest = Path("assets/visual/clubs") / f"club_{c['id']}.png"
        if not crest.exists():
            crest = Path(club_crest(c["name"], c["id"]))
        await m.answer_photo(
            FSInputFile(crest),
            caption=(
                f'⚽ <b>{c["name"]}</b>\n'
                f'💰 €{c["budget"]:,}\n'
                f'🏟 {c["stadium_name"]} · LVL {c["stadium_level"]}\n'
                f'🎟 €{c["ticket_price"]} · sponsor LVL {c["sponsor_level"]}'
            ),
        )
        await m.answer("Выберите действие:", reply_markup=menu())
    else:
        k = InlineKeyboardBuilder()
        k.button(text="🏟 Создать клуб", callback_data="create")
        await m.answer(
            "⚽ <b>FOOTBALL MANAGER</b>\n\nСоздайте клуб, вступите в лигу и играйте с друзьями.",
            reply_markup=k.as_markup(),
        )


@router.message(Command("createleague"))
async def cmd_createleague(m: Message):
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 3:
        return await m.answer("Использование: /createleague 8 Friends League")
    try:
        n = int(parts[1])
    except ValueError:
        return await m.answer("Количество клубов: 4, 6, 8, 10 или 12.")
    if n not in {4, 6, 8, 10, 12}:
        return await m.answer("Количество клубов: 4, 6, 8, 10 или 12.")
    async with SessionLocal() as s:
        uid = await current_user(s, m)
        c = await current_club(s, uid)
        if not c:
            return await m.answer("Сначала создайте клуб.")
        try:
            league_id, code = await create_league(s, uid, parts[2], n)
            await s.commit()
        except Exception as e:
            await s.rollback()
            return await m.answer(f"Не удалось создать лигу: {e}")
    await m.answer(f"🏆 Лига <b>{parts[2]}</b> создана.\nКод приглашения: <code>{code}</code>\n\nДрузья могут: /join {code}")


@router.message(Command("join"))
async def cmd_join(m: Message):
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await m.answer("Использование: /join КОД")
    code = parts[1].strip().upper()
    async with SessionLocal() as s:
        uid = await current_user(s, m)
        c = await current_club(s, uid)
        if not c:
            return await m.answer("Сначала создайте клуб.")
        league = (await s.execute(text("SELECT id,name FROM leagues WHERE invite_code=:c"), {"c": code})).first()
        if not league:
            return await m.answer("Лига не найдена.")
        try:
            await join_league(s, uid, code)
            await s.commit()
        except ValueError as e:
            await s.rollback()
            return await m.answer(str(e))
    await m.answer(f"✅ Вы вступили в лигу <b>{league.name}</b>.")


@router.message(Command("startleague"))
async def cmd_startleague(m: Message):
    async with SessionLocal() as s:
        uid = await current_user(s, m)
        c = await current_club(s, uid)
        lt = (
            await s.execute(
                text(
                    """SELECT l.id,l.name,l.creator_user_id FROM leagues l JOIN league_teams t ON t.league_id=l.id
                    WHERE t.club_id=:c AND l.status='WAITING' ORDER BY l.id DESC LIMIT 1"""
                ),
                {"c": c["id"]},
            )
        ).first() if c else None
        if not lt:
            return await m.answer("Не найдена ожидающая лига.")
        if lt.creator_user_id != uid:
            return await m.answer("Начать сезон может создатель лиги.")
        try:
            await start_league(s, uid)
            await s.commit()
        except ValueError as e:
            await s.rollback()
            return await m.answer(str(e))
    await m.answer(f"🚦 Сезон <b>{lt.name}</b> начался! Теперь можно использовать /playround.")


@router.message(Command("playround"))
async def cmd_playround(m: Message):
    async with SessionLocal() as s:
        uid = await current_user(s, m)
        c = await current_club(s, uid)
        if not c:
            return await m.answer("Сначала создайте клуб.")
        league = await active_league(s, c["id"])
        if not league or league["status"] != "ACTIVE":
            return await m.answer("У клуба нет активной лиги.")
        round_no = league["current_round"]
        try:
            scheduled_at, count = await schedule_round(s, league["id"], round_no, 10)
            await s.commit()
        except ValueError as e:
            await s.rollback()
            return await m.answer(str(e))
    await m.answer(
        f"📅 <b>Тур {round_no} запланирован</b>\n\n"
        f"⚽ Ваш матч начнётся через <b>10 минут</b>.\n"
        "🔔 Отдельного уведомления о старте не будет."
    )


@router.message(Command("text_router"))
async def text_router(m: Message):
    name = (m.text or "").strip()
    if not 3 <= len(name) <= 30:
        return
    async with SessionLocal() as s:
        uid = await current_user(s, m)
        exists = await current_club(s, uid)
        if exists:
            return
        try:
            await create_club(s, uid, name)
            await s.commit()
        except Exception:
            await s.rollback()
            return await m.answer("Не удалось создать клуб. Возможно, такое название уже занято.")
    await m.answer(f"🏟 <b>{name}</b> создан! Вы получили стартовый состав.", reply_markup=menu())


def create_bot():
    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    live_task = None

    async def on_startup():
        nonlocal live_task
        from .live_worker import live_worker
        live_task = asyncio.create_task(live_worker(bot), name="football-manager-live-worker")

    async def on_shutdown():
        nonlocal live_task
        if live_task:
            live_task.cancel()
            try:
                await live_task
            except asyncio.CancelledError:
                pass

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    return bot, dp


@router.message(Command("dynamicmatch"))
async def cmd_dynamicmatch(m: Message):
    async with SessionLocal() as s:
        uid = await current_user(s, m)
        c = await current_club(s, uid)
        if not c:
            return await m.answer("Сначала создайте клуб.")
        row = (
            await s.execute(
                text(
                    """SELECT m.id,m.current_minute,m.home_score,m.away_score,h.name home,a.name away,
                    th.formation home_formation,ta.formation away_formation,th.pressing home_pressing,ta.pressing away_pressing
                    FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
                    LEFT JOIN match_tactics th ON th.match_id=m.id AND th.club_id=m.home_club_id
                    LEFT JOIN match_tactics ta ON ta.match_id=m.id AND ta.club_id=m.away_club_id
                    WHERE m.home_club_id=:c OR m.away_club_id=:c ORDER BY m.id DESC LIMIT 1"""
                ),
                {"c": c["id"]},
            )
        ).mappings().first()
        events = (
            await s.execute(
                text("SELECT minute,event_type,description,metadata FROM match_events WHERE match_id=:m ORDER BY minute,id LIMIT 30"),
                {"m": row["id"]},
            )
        ).mappings().all() if row else []
    if not row:
        return await m.answer("Матчей пока нет.")
    hp = row["home_pressing"] or 50
    ap = row["away_pressing"] or 50
    phase = "PRESSING" if hp - ap >= 12 else ("COUNTER" if ap - hp >= 12 else "POSSESSION")
    control = max(0.35, min(0.65, 0.5 + (hp - ap) / 200))
    path = dynamic_match_screen(
        row["home"],
        row["away"],
        row["home_formation"] or "4-3-3",
        row["away_formation"] or "4-3-3",
        phase=phase,
        minute=row["current_minute"] or 0,
        home_control=control,
        pressure=(hp, ap),
        events=events,
    )
    await m.answer_photo(FSInputFile(path), caption=f'🎥 <b>Динамический Match Center</b> · {phase} · {row["current_minute"] or 0}′')


@router.message()
async def catch_all(m: Message):
    if m.text and m.text.startswith("/"):
        return await m.answer("Неизвестная команда. Используйте меню или /start.")
    if not m.text:
        return
    name = m.text.strip()
    if not 3 <= len(name) <= 30:
        return
    async with SessionLocal() as s:
        uid = await current_user(s, m)
        exists = await current_club(s, uid)
        if exists:
            return
        try:
            await create_club(s, uid, name)
            await s.commit()
        except Exception:
            await s.rollback()
            return await m.answer("Не удалось создать клуб. Возможно, такое название уже занято.")
    await m.answer(f"🏟 <b>{name}</b> создан! Вы получили стартовый состав.", reply_markup=menu())


# NOTE: the rest of the route definitions in this file are intentionally omitted for brevity.
# This fix specifically resolves the invalid command-handling flow that caused the command name error.
