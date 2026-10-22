"""FastAPI endpoints for the Telegram Football Manager Mini App."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from functools import wraps
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InterfaceError, SQLAlchemyError

from .config import settings
from .db import SessionLocal, init_db
from .services import buy_player, market_players, make_transfer_offer, accept_transfer_offer, train_squad, upgrade_stadium, add_copy_to_club
from .game.packs import list_packs, open_pack
from .game.economy import stadium_capacity
from .repositories import create_club
from .game.best_xi import get_best_xi
from .game.ai_best_xi import suggest_best_xi
from .telegram_auth import TelegramInitDataError, validate_telegram_init_data

log = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "mini_app" / "dist"
app = FastAPI(title="Football Manager API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])


@app.on_event("startup")
async def initialize_database_schema():
    # The API may start before the bot process; make sure all migrations are applied.
    await init_db()


class TacticsPayload(BaseModel):
    formation: str = Field(default="4-3-3", min_length=3, max_length=10)
    style: str = Field(default="BALANCE", min_length=3, max_length=20)
    tempo: int = Field(default=50, ge=0, le=100)
    pressing: int = Field(default=50, ge=0, le=100)
    width: int = Field(default=50, ge=0, le=100)
    defensive_line: int = Field(default=50, ge=0, le=100)
    aggression: int = Field(default=50, ge=0, le=100)


class BidPayload(BaseModel):
    player_id: int = Field(gt=0)


class SellPlayerPayload(BaseModel):
    player_id: int = Field(gt=0)
    price: int = Field(gt=1, le=2_000_000_000)


class TrainingPayload(BaseModel):
    focus: str = Field(default="BALANCED", min_length=3, max_length=20)
    intensity: str = Field(default="NORMAL", min_length=3, max_length=10)


class CreateClubPayload(BaseModel):
    name: str = Field(min_length=3, max_length=20)
    emblem_shape: str
    emblem_color: str
    emblem_symbol: str


class EmblemPayload(BaseModel):
    emblem_shape: str
    emblem_color: str
    emblem_symbol: str


class ManagerProfilePayload(BaseModel):
    manager_name: str = Field(min_length=2, max_length=32)


class ClubNamePayload(BaseModel):
    name: str = Field(min_length=3, max_length=24)


class ManagerAvatarPayload(BaseModel):
    avatar_id: int = Field(ge=1, le=8)


class LineupPayload(BaseModel):
    player_ids: list[int] = Field(min_length=11, max_length=11)
    formation: str | None = Field(default=None, min_length=3, max_length=10)
    bench_ids: list[int] = Field(default_factory=list, max_length=7)


class LiveSubstitutionPayload(BaseModel):
    player_out_id: int = Field(gt=0)
    player_in_id: int = Field(gt=0)


EMBLEM_SHAPES = {"shield", "circle", "diamond", "triangle", "star", "square"}
EMBLEM_COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "black", "white"}
EMBLEM_SYMBOLS = {"ball", "lion", "eagle", "wolf", "lightning", "crown", "star"}
DIVISION_NAMES = {
    "ROOKIE": "Новичок",
    "BRONZE": "Бронза",
    "SILVER": "Серебро",
    "GOLD": "Золото",
    "DIAMOND": "Алмаз",
    "ELITE": "Элита",
    "LEGEND": "Легенда",
}


def division_name(tier: Any) -> str | None:
    """Translate the persisted league tier into a stable UI label."""
    if tier is None:
        return None
    code = str(tier).strip().upper()
    return DIVISION_NAMES.get(code, code.title() if code else None)


def retry_on_interface_error(endpoint):
    """Retry an HTTP endpoint once if its DB connection was closed unexpectedly."""
    @wraps(endpoint)
    async def wrapped(*args, **kwargs):
        try:
            return await endpoint(*args, **kwargs)
        except InterfaceError:
            log.warning("DB connection closed, retrying...")
            return await endpoint(*args, **kwargs)
    return wrapped


async def authenticated_telegram_user(x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    """Validate signed Telegram initData; never accept unsigned user IDs."""
    init_data = x_telegram_init_data.strip() if isinstance(x_telegram_init_data, str) else ""
    try:
        return validate_telegram_init_data(init_data, settings.bot_token)
    except TelegramInitDataError as exc:
        # Log only a reason code. Never log the raw initData, hash, token, or user payload.
        reason = str(exc)
        log.warning("Telegram Mini App authentication rejected (reason=%s)", reason)
        if reason == "missing":
            detail = "Откройте приложение из Telegram: initData отсутствует."
        elif reason == "bot_token_missing":
            detail = "На сервере не настроен BOT_TOKEN."
        elif reason == "signature_mismatch":
            detail = "Invalid Telegram initData signature. Проверьте, что BOT_TOKEN в Amvera совпадает с токеном этого бота."
        elif reason == "expired":
            detail = "Telegram initData устарел. Полностью закройте и снова откройте Mini App из Telegram."
        else:
            detail = "Invalid Telegram initData (reason: %s). Откройте Mini App заново из Telegram." % reason
        raise HTTPException(status_code=401, detail=detail) from exc


async def user_and_club(user: dict[str, Any], session):
    row = (await session.execute(text("""SELECT u.id AS user_id, c.* FROM users u
        LEFT JOIN clubs c ON c.owner_user_id=u.id
        WHERE u.telegram_id=:telegram_id ORDER BY c.is_test ASC, c.id LIMIT 1"""),
        {"telegram_id": user["telegram_id"]})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="User or club not found; create a club first")
    if row.get("id") is None:
        raise HTTPException(status_code=404, detail="Club not found; create a club first")
    return row


@app.get("/api/health")
async def health():
    """Liveness probe: the API process is responsive."""
    return {"status": "ok"}


@app.get("/api/ready")
async def readiness():
    """Readiness probe: report ready only when PostgreSQL is reachable."""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.exception("Readiness check failed: PostgreSQL unavailable")
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})
    return {"status": "ready", "database": "ok"}


@app.get("/api/me")
@retry_on_interface_error
async def get_me(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        row = (await session.execute(text("""SELECT u.id AS user_id, COALESCE(u.manager_name,u.first_name) AS manager_name,
                COALESCE(u.avatar_id,1) AS avatar_id, c.id AS club_id, c.name AS club_name
            FROM users u LEFT JOIN clubs c ON c.owner_user_id=u.id
            WHERE u.telegram_id=:telegram_id ORDER BY c.is_test ASC, c.id LIMIT 1"""),
            {"telegram_id": user["telegram_id"]})).mappings().first()
        if not row:
            user_id = (await session.execute(text("""INSERT INTO users(telegram_id,username,first_name)
                VALUES(:t,:u,:f) ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username,
                first_name=EXCLUDED.first_name RETURNING id"""),
                {"t": user["telegram_id"], "u": user.get("username"), "f": user.get("first_name") or "Manager"})).scalar_one()
            await session.commit()
            return {"telegram_id": user["telegram_id"], "user_id": int(user_id), "club_id": None, "club_name": None,
                    "first_name": user.get("first_name") or "Менеджер", "username": user.get("username"), "avatar_id": 1}
        return {"telegram_id": user["telegram_id"], "user_id": int(row["user_id"]),
                "club_id": int(row["club_id"]) if row["club_id"] is not None else None,
                "club_name": row["club_name"], "first_name": row["manager_name"] or user.get("first_name") or "Менеджер",
                "username": user.get("username"), "avatar_id": int(row["avatar_id"] or 1)}


@app.post("/api/manager/profile")
@retry_on_interface_error
async def update_manager_profile(payload: ManagerProfilePayload, user=Depends(authenticated_telegram_user)):
    name = " ".join(payload.manager_name.split()).strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Имя менеджера должно содержать минимум 2 символа.")
    async with SessionLocal() as session:
        await session.execute(text("UPDATE users SET manager_name=:name WHERE telegram_id=:tg"),
                              {"name": name, "tg": int(user["telegram_id"])})
        await session.commit()
    return {"status": "updated", "manager_name": name}


@app.post("/api/club/name")
@retry_on_interface_error
async def update_club_name(payload: ClubNamePayload, user=Depends(authenticated_telegram_user)):
    name = " ".join(payload.name.split()).strip()
    if len(name) < 3:
        raise HTTPException(status_code=400, detail="Название клуба должно содержать минимум 3 символа.")
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        duplicate = (await session.execute(text("SELECT id FROM clubs WHERE lower(name)=lower(:n) AND id<>:c LIMIT 1"),
            {"n": name, "c": int(club["id"])})).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Клуб с таким названием уже существует.")
        try:
            await session.execute(text("UPDATE clubs SET name=:n,short_name=:sn WHERE id=:c"), {"n": name, "sn": name[:4].upper(), "c": int(club["id"])})
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Клуб с таким названием уже существует.") from exc
    return {"status": "updated", "club_id": int(club["id"]), "name": name}


@app.post("/api/manager/avatar")
@retry_on_interface_error
async def update_manager_avatar(payload: ManagerAvatarPayload, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        result = await session.execute(text("UPDATE users SET avatar_id=:avatar WHERE telegram_id=:tg RETURNING avatar_id"),
            {"avatar": payload.avatar_id, "tg": int(user["telegram_id"])})
        avatar_id = result.scalar_one_or_none()
        if avatar_id is None:
            raise HTTPException(status_code=404, detail="Профиль менеджера не найден.")
        await session.commit()
    return {"status": "updated", "avatar_id": int(avatar_id)}


@app.post("/api/club/emblem")
@retry_on_interface_error
async def update_club_emblem(payload: EmblemPayload, user=Depends(authenticated_telegram_user)):
    if payload.emblem_shape not in EMBLEM_SHAPES:
        raise HTTPException(status_code=400, detail="Неизвестная форма эмблемы.")
    if payload.emblem_color not in EMBLEM_COLORS:
        raise HTTPException(status_code=400, detail="Неизвестный цвет эмблемы.")
    if payload.emblem_symbol not in EMBLEM_SYMBOLS:
        raise HTTPException(status_code=400, detail="Неизвестный символ эмблемы.")
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        await session.execute(text("UPDATE clubs SET emblem_shape=:shape,emblem_color=:color,emblem_symbol=:symbol WHERE id=:club"),
            {"shape": payload.emblem_shape, "color": payload.emblem_color, "symbol": payload.emblem_symbol, "club": int(club["id"])})
        await session.commit()
    return {"status": "updated", "emblem_shape": payload.emblem_shape,
            "emblem_color": payload.emblem_color, "emblem_symbol": payload.emblem_symbol}


@app.get("/api/lineup")
@retry_on_interface_error
async def get_starting_lineup(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        tactics = (await session.execute(text("SELECT formation FROM club_tactics WHERE club_id=:c"), {"c": int(club["id"])})).mappings().first()
        formation = str(tactics["formation"] if tactics else "4-3-3")
        rows = (await session.execute(text("""SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.portrait_url,
                ROUND((p.pace+p.shooting+p.passing+p.dribbling+p.defending+p.physical+p.stamina+p.mental)/8.0)::int AS overall,
                cp.fitness,cp.form,cp.morale,cp.is_injured,COALESCE(cp.suspension_matches,0) AS suspension_matches,
                p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c ORDER BY p.id"""),
            {"c": int(club["id"])})).mappings().all()
        saved = (await session.execute(text("SELECT player_ids,bench_ids FROM club_lineups WHERE club_id=:c"), {"c": int(club["id"])})).mappings().first()
        player_ids = {int(r["id"]) for r in rows}
        saved_ids = [int(pid) for pid in (saved['player_ids'] if saved else []) if int(pid) in player_ids][:11]
        bench_ids = [int(pid) for pid in (saved['bench_ids'] if saved else [])
                     if int(pid) in player_ids and int(pid) not in saved_ids][:7]
        from .game.board import FORMATION_SLOTS
        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-3-3"])
        return {"formation": formation, "formation_slots": [
                    {"slot": role, "x": int(x), "y": int(y)} for role, x, y in slots
                ], "players": [dict(r) for r in rows], "starting_ids": saved_ids,
                "bench_ids": bench_ids}


@app.post("/api/lineup/autoselect")
@retry_on_interface_error
async def auto_select_starting_lineup(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        tactics = (await session.execute(text("SELECT formation FROM club_tactics WHERE club_id=:c"), {"c": int(club["id"])})).mappings().first()
        formation = str(tactics["formation"] if tactics else "4-3-3")
        rows = (await session.execute(text("""SELECT p.*,cp.fitness,cp.form,cp.morale,cp.is_injured,
                COALESCE(cp.suspension_matches,0) AS suspension_matches
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c
            ORDER BY p.id"""), {"c": int(club["id"])})).mappings().all()
        try:
            selected = get_best_xi(rows, formation)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if len(selected) != 11:
            raise HTTPException(status_code=400, detail="Для стартового состава нужны 11 доступных игроков.")
        ids = [int(row["id"]) for row in selected]
        saved_bench = (await session.execute(text("SELECT bench_ids FROM club_lineups WHERE club_id=:c"),
            {"c": int(club["id"])})).scalar_one_or_none() or []
        eligible_ids = {int(row["id"]) for row in rows
                        if not row.get("is_injured") and int(row.get("suspension_matches") or 0) == 0}
        bench_ids = [int(pid) for pid in saved_bench if int(pid) in eligible_ids and int(pid) not in ids][:7]
        await session.execute(text("""INSERT INTO club_lineups(club_id,player_ids,bench_ids) VALUES(:c,:ids,:bench)
            ON CONFLICT(club_id) DO UPDATE SET player_ids=EXCLUDED.player_ids,bench_ids=EXCLUDED.bench_ids"""),
            {"c": int(club["id"]), "ids": ids, "bench": bench_ids})
        await session.commit()
        return {"status": "saved", "formation": formation, "starting_ids": ids, "bench_ids": bench_ids}


@app.get("/api/lineup/recommendation")
@retry_on_interface_error
async def recommend_starting_lineup(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        result = await suggest_best_xi(session, int(club["id"]))
        if not result:
            raise HTTPException(status_code=503, detail="Не удалось рассчитать рекомендацию состава.")
        formation = str(result.get("formation") or "4-3-3")
        from .game.board import FORMATION_SLOTS
        return {"formation": formation,
                "formation_slots": [{"slot": role, "x": int(x), "y": int(y)} for role, x, y in FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-3-3"])],
                "starting_ids": [int(p["id"]) for p in result.get("players", []) if p.get("id") is not None],
                "reason": str(result.get("reason") or "Состав рекомендован по доступности и рейтингу игроков."),
                "source": str(result.get("source") or "fallback")}


@app.post("/api/lineup")
@retry_on_interface_error
async def save_starting_lineup(payload: LineupPayload, user=Depends(authenticated_telegram_user)):
    ids = [int(pid) for pid in payload.player_ids]
    if len(set(ids)) != 11:
        raise HTTPException(status_code=400, detail="В стартовом составе должно быть 11 разных игроков.")
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        from .game.board import FORMATION_SLOTS
        from .game.best_xi import get_best_xi
        tactics_row = (await session.execute(text("SELECT formation FROM club_tactics WHERE club_id=:c"),
            {"c": int(club["id"])})).mappings().first()
        formation = payload.formation or str(tactics_row["formation"] if tactics_row else "4-3-3")
        if formation not in FORMATION_SLOTS:
            raise HTTPException(status_code=400, detail="Неизвестная схема расстановки.")
        requested_bench = [int(pid) for pid in payload.bench_ids]
        if len(set(requested_bench)) != len(requested_bench):
            raise HTTPException(status_code=400, detail="В списке запасных не должно быть повторяющихся игроков.")
        if set(requested_bench) & set(ids):
            raise HTTPException(status_code=400, detail="Игрок не может одновременно находиться в основе и на лавке.")
        all_requested_ids = ids + requested_bench
        all_rows = (await session.execute(text("""SELECT p.*,cp.is_injured,COALESCE(cp.suspension_matches,0) AS suspension_matches,
                cp.fitness,cp.form,cp.morale
            FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND p.id=ANY(:ids)"""), {"c": int(club["id"]), "ids": all_requested_ids})).mappings().all()
        if len(all_rows) != len(all_requested_ids):
            raise HTTPException(status_code=400, detail="Все выбранные игроки должны принадлежать вашему клубу.")
        all_rows_by_id = {int(row["id"]): row for row in all_rows}
        rows_by_id = all_rows_by_id
        # Reconstruct starters in the exact order submitted by the manager.
        rows = [all_rows_by_id[pid] for pid in ids]
        starting_rows = [dict(r) for r in rows]
        unavailable_starting = [r for r in starting_rows if r["is_injured"] or int(r["suspension_matches"] or 0) > 0]
        if unavailable_starting:
            raise HTTPException(status_code=400, detail="Травмированных и дисквалифицированных игроков нельзя поставить в состав.")
        unavailable_bench = [rows_by_id[pid] for pid in requested_bench
                             if rows_by_id[pid]["is_injured"] or int(rows_by_id[pid]["suspension_matches"] or 0) > 0]
        if unavailable_bench:
            raise HTTPException(status_code=400, detail="Травмированных и дисквалифицированных игроков нельзя включить в лавку.")
        # Store the XI in the same slot order used by the engine and formation renderer.
        ordered = get_best_xi([dict(r) for r in rows], formation)
        ordered_ids = [int(row["id"]) for row in ordered]
        if len(ordered_ids) != 11 or set(ordered_ids) != set(ids):
            raise HTTPException(status_code=400, detail="Не удалось распределить выбранных игроков по схеме.")
        bench_ids = requested_bench[:7]
        await session.execute(text("""INSERT INTO club_lineups(club_id,player_ids,bench_ids) VALUES(:c,:ids,:bench)
            ON CONFLICT(club_id) DO UPDATE SET player_ids=EXCLUDED.player_ids,bench_ids=EXCLUDED.bench_ids"""),
            {"c": int(club["id"]), "ids": ordered_ids, "bench": bench_ids})
        await session.execute(text("""INSERT INTO club_tactics(club_id,formation) VALUES(:c,:f)
            ON CONFLICT(club_id) DO UPDATE SET formation=EXCLUDED.formation"""),
            {"c": int(club["id"]), "f": formation})
        await session.commit()
    return {"status": "saved", "starting_ids": ordered_ids, "bench_ids": bench_ids, "formation": formation}


@app.post("/api/club/create")
async def create_club_endpoint(payload: CreateClubPayload, user=Depends(authenticated_telegram_user)):
    name = payload.name.strip()
    if not 3 <= len(name) <= 20:
        raise HTTPException(status_code=422, detail="Название клуба должно содержать 3–20 символов")
    if payload.emblem_shape not in EMBLEM_SHAPES:
        raise HTTPException(status_code=422, detail="Недопустимая форма эмблемы")
    if payload.emblem_color not in EMBLEM_COLORS:
        raise HTTPException(status_code=422, detail="Недопустимый цвет эмблемы")
    if payload.emblem_symbol not in EMBLEM_SYMBOLS:
        raise HTTPException(status_code=422, detail="Недопустимый символ эмблемы")
    async with SessionLocal() as session:
        user_id = (await session.execute(text("SELECT id FROM users WHERE telegram_id=:t"),
            {"t": user["telegram_id"]})).scalar_one_or_none()
        if user_id is None:
            user_id = (await session.execute(text("""INSERT INTO users(telegram_id,username,first_name)
                VALUES(:t,:u,:f) ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username,
                first_name=EXCLUDED.first_name RETURNING id"""),
                {"t": user["telegram_id"], "u": user.get("username"), "f": user.get("first_name") or "Manager"})).scalar_one()
        try:
            club_id = await create_club(session, int(user_id), name)
            await session.execute(text("""UPDATE clubs SET emblem_shape=:shape, emblem_color=:color,
                emblem_symbol=:symbol WHERE id=:id"""),
                {"shape": payload.emblem_shape, "color": payload.emblem_color,
                 "symbol": payload.emblem_symbol, "id": club_id})
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InterfaceError:
            await session.rollback()
            raise
        except Exception as exc:
            await session.rollback()
            log.exception("Club creation failed")
            raise HTTPException(status_code=500, detail="Не удалось создать клуб. Попробуйте ещё раз.") from exc
    return {"status": "created", "club_id": int(club_id), "name": name,
            "emblem_shape": payload.emblem_shape, "emblem_color": payload.emblem_color,
            "emblem_symbol": payload.emblem_symbol}


@app.get("/api/club/{club_id}")
@retry_on_interface_error
async def get_club(club_id: int, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        if int(club["id"]) != club_id:
            raise HTTPException(status_code=403, detail="This club does not belong to you")
        count = (await session.execute(text("SELECT count(*) FROM club_players WHERE club_id=:c"), {"c": club_id})).scalar_one()
        division = (await session.execute(text("""SELECT l.id AS league_id,l.name AS league_name,l.tier
            FROM league_teams lt JOIN leagues l ON l.id=lt.league_id
            WHERE lt.club_id=:c
            ORDER BY CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'WAITING' THEN 1 ELSE 2 END,
                     l.season_id DESC NULLS LAST,l.id DESC
            LIMIT 1"""), {"c": club_id})).mappings().first()
        return {"id": club_id, "name": club["name"], "short_name": club["short_name"],
                "budget": int(club["budget"]), "stadium_name": club["stadium_name"],
                "stadium_level": club["stadium_level"], "fans": club["fans"], "reputation": club["reputation"],
                "squad_size": int(count), "emblem_shape": club.get("emblem_shape") or "shield",
                "emblem_color": club.get("emblem_color") or "blue", "emblem_symbol": "ball" if club.get("emblem_symbol") == "sword" else (club.get("emblem_symbol") or "ball"),
                "league_id": int(division["league_id"]) if division else None,
                "league_name": division["league_name"] if division else None,
                "division_code": str(division["tier"]).upper() if division and division["tier"] else None,
                "division_name": division_name(division["tier"]) if division else None}


@app.get("/api/squad/{club_id}")
@retry_on_interface_error
async def get_squad(club_id: int, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        if int(club["id"]) != club_id:
            raise HTTPException(status_code=403, detail="This club does not belong to you")
        rows = (await session.execute(text("""SELECT p.id,p.first_name,p.last_name,p.position,
            ROUND((p.pace+p.shooting+p.passing+p.dribbling+p.defending+p.physical+p.stamina+p.mental)/8.0)::int AS overall,
            cp.fitness,cp.morale,cp.is_injured,p.portrait_url,p.age,cp.shirt_number
            FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c
            ORDER BY cp.shirt_number NULLS LAST,p.position,p.id"""), {"c": club_id})).mappings().all()
        return {"players": [dict(r) for r in rows]}


@app.get("/api/matches/live")
@retry_on_interface_error
async def live_matches(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        rows = (await session.execute(text("""SELECT m.id,m.home_club_id,m.away_club_id,
            hc.name AS home_name,ac.name AS away_name,COALESCE(m.home_score,0) home_score,
            COALESCE(m.away_score,0) away_score,m.status,m.started_at
            FROM matches m JOIN clubs hc ON hc.id=m.home_club_id JOIN clubs ac ON ac.id=m.away_club_id
            WHERE m.status='LIVE' AND (m.home_club_id=:c OR m.away_club_id=:c)
              AND UPPER(COALESCE(m.competition_type,'LEAGUE')) NOT IN ('CUP','CHAMPIONS','CHAMPIONS_LEAGUE','UCL')
              AND NOT EXISTS (SELECT 1 FROM cup_matches cm WHERE cm.match_id=m.id)
              AND NOT EXISTS (SELECT 1 FROM champions_matches clm WHERE clm.match_id=m.id) ORDER BY m.id"""), {"c": club["id"]})).mappings().all()
        result=[]
        for row in rows:
            events=(await session.execute(text("SELECT id,minute,event_type,club_id,player_id,description,metadata FROM match_events WHERE match_id=:m ORDER BY minute,id"), {"m":row["id"]})).mappings().all()
            item=dict(row); item["events"]=[dict(e) for e in events]; result.append(item)
        return {"matches": result}


@app.get("/api/matches/last")
@retry_on_interface_error
async def last_match(user=Depends(authenticated_telegram_user)):
    """Return the most recent completed match for the authenticated user's club."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        row = (await session.execute(text("""SELECT m.id,m.home_club_id,m.away_club_id,
                hc.name AS home_name,ac.name AS away_name,m.home_score,m.away_score,
                m.status,m.current_minute,m.started_at,m.finished_at,m.scheduled_at,
                m.competition_type,m.is_test,
                CASE WHEN UPPER(COALESCE(m.competition_type,''))='FRIENDLY' THEN
                     CASE WHEN COALESCE(m.is_test,FALSE) THEN 'Тестовый матч' ELSE 'Товарищеский матч' END
                     ELSE COALESCE(l.name,'Матч') END AS competition_name
            FROM matches m
            JOIN clubs hc ON hc.id=m.home_club_id
            JOIN clubs ac ON ac.id=m.away_club_id
            LEFT JOIN leagues l ON l.id=m.league_id
            WHERE UPPER(COALESCE(m.status,''))='FINISHED'
              AND (m.home_club_id=:c OR m.away_club_id=:c)
              AND UPPER(COALESCE(m.competition_type,'LEAGUE')) NOT IN ('CUP','CHAMPIONS','CHAMPIONS_LEAGUE','UCL')
              AND NOT EXISTS (SELECT 1 FROM cup_matches cm WHERE cm.match_id=m.id)
              AND NOT EXISTS (SELECT 1 FROM champions_matches clm WHERE clm.match_id=m.id)
            ORDER BY COALESCE(m.finished_at,m.started_at,m.scheduled_at) DESC NULLS LAST,m.id DESC
            LIMIT 1"""), {"c": int(club["id"])})).mappings().first()
        return {"match": dict(row) if row else None}


@app.get("/api/matches/next")
@retry_on_interface_error
async def next_match(user=Depends(authenticated_telegram_user)):
    """Return the next scheduled or currently live match for the authenticated user's club."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        row = (await session.execute(text("""SELECT m.id, m.home_club_id, m.away_club_id,
                hc.name AS home_name, ac.name AS away_name,
                COALESCE(m.home_score, 0) AS home_score, COALESCE(m.away_score, 0) AS away_score,
                m.status, m.current_minute, m.started_at, m.scheduled_at,
                m.competition_type, m.is_test,
                CASE UPPER(COALESCE(m.competition_type,''))
                     WHEN 'FRIENDLY' THEN CASE WHEN COALESCE(m.is_test,FALSE)
                         THEN 'Тестовый матч' ELSE 'Товарищеский матч' END
                     WHEN 'LEAGUE' THEN COALESCE(l.name,'Чемпионат')
                     ELSE COALESCE(m.competition_type,'Матч') END AS competition_name
            FROM matches m
            JOIN clubs hc ON hc.id = m.home_club_id
            JOIN clubs ac ON ac.id = m.away_club_id
            LEFT JOIN leagues l ON l.id = m.league_id
            WHERE (m.home_club_id = :club_id OR m.away_club_id = :club_id)
              AND UPPER(COALESCE(m.competition_type,'LEAGUE')) NOT IN ('CUP','CHAMPIONS','CHAMPIONS_LEAGUE','UCL')
              AND NOT EXISTS (SELECT 1 FROM cup_matches cm WHERE cm.match_id=m.id)
              AND NOT EXISTS (SELECT 1 FROM champions_matches clm WHERE clm.match_id=m.id)
              AND (m.status='LIVE' OR (m.status='SCHEDULED' AND (m.scheduled_at IS NULL OR m.scheduled_at >= now())))
            ORDER BY CASE WHEN m.status='LIVE' THEN 0 ELSE 1 END,
                     m.scheduled_at ASC NULLS LAST,
                     m.id ASC
            LIMIT 1"""), {"club_id": int(club["id"])})).mappings().first()
        if row is None:
            return {"match": None}
        match = dict(row)
        if match["status"] == "LIVE" or (match.get("is_test") and match.get("competition_type") == "FRIENDLY"):
            events = (await session.execute(text("""SELECT id, minute, event_type, club_id,
                    player_id, description, metadata
                FROM match_events WHERE match_id = :match_id ORDER BY minute, id"""),
                {"match_id": int(match["id"])})).mappings().all()
            match["events"] = [dict(event) for event in events]
        else:
            match["events"] = []
        return {"match": match}


class FriendlyMatchPayload(BaseModel):
    mode: str = Field(default="friendly", pattern="^(friendly|test)$")


async def _run_user_friendly_match(payload: FriendlyMatchPayload, user):
    """Run an isolated paced LIVE match without affecting league standings."""
    from .services import create_user_friendly_match
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        try:
            result = await create_user_friendly_match(session, int(club["id"]), mode=payload.mode)
            await session.commit()
            return result
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            log.exception("Friendly/test match failed for club_id=%s mode=%s", int(club["id"]), payload.mode)
            raise HTTPException(status_code=500, detail="Не удалось запустить матч. Попробуйте ещё раз.")


@app.post("/api/matches/friendly")
@retry_on_interface_error
async def play_friendly_match(payload: FriendlyMatchPayload = FriendlyMatchPayload(), user=Depends(authenticated_telegram_user)):
    return await _run_user_friendly_match(payload, user)


@app.post("/api/matches/test")
@retry_on_interface_error
async def play_test_match(user=Depends(authenticated_telegram_user)):
    # Route itself defines the mode; it works even when called with an empty body.
    return await _run_user_friendly_match(FriendlyMatchPayload(mode="test"), user)


@app.get("/api/matches/{match_id}/lineup")
@retry_on_interface_error
async def get_live_match_lineup(match_id: int, user=Depends(authenticated_telegram_user)):
    """Return this club's current on-pitch XI and unused eligible substitutes."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        club_id = int(club["id"])
        match = (await session.execute(text("""SELECT id,home_club_id,away_club_id,status
            FROM matches WHERE id=:m"""), {"m": match_id})).mappings().first()
        if not match:
            raise HTTPException(status_code=404, detail="Матч не найден.")
        if club_id not in (int(match["home_club_id"]), int(match["away_club_id"])):
            raise HTTPException(status_code=403, detail="Вы не управляете ни одной из команд этого матча.")
        if str(match["status"]).upper() != "LIVE":
            raise HTTPException(status_code=409, detail="Управление составом доступно только во время LIVE-матча.")
        active_ids = [int(row[0]) for row in (await session.execute(text("""SELECT player_id
            FROM match_lineups WHERE match_id=:m AND club_id=:c AND is_starting=TRUE ORDER BY id"""),
            {"m": match_id, "c": club_id})).all()]
        used_ids = [int(row[0]) for row in (await session.execute(text("""SELECT DISTINCT player_id
            FROM match_lineups WHERE match_id=:m AND club_id=:c"""),
            {"m": match_id, "c": club_id})).all()]
        saved_bench = (await session.execute(text("SELECT bench_ids FROM club_lineups WHERE club_id=:c"),
            {"c": club_id})).scalar_one_or_none() or []
        eligible_ids = [int(row[0]) for row in (await session.execute(text("""SELECT cp.player_id
            FROM club_players cp WHERE cp.club_id=:c AND cp.is_injured=FALSE
              AND COALESCE(cp.suspension_matches,0)=0 ORDER BY cp.player_id"""),
            {"c": club_id})).all()]
        base_bench = [int(pid) for pid in saved_bench] if saved_bench else eligible_ids
        bench_ids = [pid for pid in base_bench if pid in eligible_ids and pid not in used_ids and pid not in active_ids][:7]
        return {"match_id": match_id, "club_id": club_id,
                "starting_ids": active_ids, "bench_ids": bench_ids}


@app.post("/api/matches/{match_id}/substitute")
@retry_on_interface_error
async def substitute_live_player(
    match_id: int,
    payload: LiveSubstitutionPayload,
    user=Depends(authenticated_telegram_user),
):
    """Apply an immediate, persisted substitution to the user's LIVE match."""
    if payload.player_out_id == payload.player_in_id:
        raise HTTPException(status_code=400, detail="Нужно выбрать двух разных игроков.")

    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        club_id = int(club["id"])
        match = (await session.execute(text("""SELECT id,home_club_id,away_club_id,status,live_phase,
                COALESCE(current_minute,0) AS current_minute,
                COALESCE(home_subs_used,0) AS home_subs_used,
                COALESCE(away_subs_used,0) AS away_subs_used,
                halftime_data,second_half_data
            FROM matches WHERE id=:m FOR UPDATE"""), {"m": match_id})).mappings().first()
        if not match:
            raise HTTPException(status_code=404, detail="Матч не найден.")
        if club_id not in (int(match["home_club_id"]), int(match["away_club_id"])):
            raise HTTPException(status_code=403, detail="Вы не управляете ни одной из команд этого матча.")
        if str(match["status"]).upper() != "LIVE":
            raise HTTPException(status_code=409, detail="Замены доступны только во время LIVE-матча.")

        used = (await session.execute(text("""SELECT count(*) FROM match_substitutions
            WHERE match_id=:m AND club_id=:c"""), {"m": match_id, "c": club_id})).scalar_one()
        if int(used or 0) >= 5:
            raise HTTPException(status_code=400, detail="Лимит замен для этой команды уже использован.")

        outgoing = (await session.execute(text("""SELECT ml.player_id,ml.position,ml.role,
                p.first_name,p.last_name
            FROM match_lineups ml JOIN players p ON p.id=ml.player_id
            WHERE ml.match_id=:m AND ml.club_id=:c AND ml.player_id=:p AND ml.is_starting=TRUE
            LIMIT 1 FOR UPDATE OF ml"""),
            {"m": match_id, "c": club_id, "p": payload.player_out_id})).mappings().first()
        if not outgoing:
            raise HTTPException(status_code=400, detail="Игрок, которого нужно заменить, не находится на поле.")

        incoming = (await session.execute(text("""SELECT p.id,p.position,p.first_name,p.last_name,
                cp.is_injured,COALESCE(cp.suspension_matches,0) AS suspension_matches
            FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND p.id=:p FOR UPDATE OF cp"""),
            {"c": club_id, "p": payload.player_in_id})).mappings().first()
        if not incoming:
            raise HTTPException(status_code=400, detail="Запасной игрок не принадлежит вашему клубу.")
        if incoming["is_injured"] or int(incoming["suspension_matches"] or 0) > 0:
            raise HTTPException(status_code=400, detail="Травмированного или дисквалифицированного игрока нельзя выпустить.")

        already_active = (await session.execute(text("""SELECT 1 FROM match_lineups
            WHERE match_id=:m AND club_id=:c AND player_id=:p AND is_starting=TRUE LIMIT 1"""),
            {"m": match_id, "c": club_id, "p": payload.player_in_id})).first()
        if already_active:
            raise HTTPException(status_code=400, detail="Этот игрок уже находится на поле.")

        lineup = (await session.execute(text("SELECT bench_ids FROM club_lineups WHERE club_id=:c"),
            {"c": club_id})).scalar_one_or_none() or []
        saved_bench = [int(pid) for pid in lineup]
        if saved_bench and payload.player_in_id not in saved_bench:
            raise HTTPException(status_code=400, detail="Выбранный игрок не входит в сохранённую лавку запасных.")

        minute = max(0, int(match["current_minute"] or 0))
        await session.execute(text("""UPDATE match_lineups SET is_starting=FALSE,role='SUBSTITUTED_OUT'
            WHERE match_id=:m AND club_id=:c AND player_id=:p AND is_starting=TRUE"""),
            {"m": match_id, "c": club_id, "p": payload.player_out_id})

        existing_incoming = (await session.execute(text("""SELECT id FROM match_lineups
            WHERE match_id=:m AND club_id=:c AND player_id=:p ORDER BY id LIMIT 1 FOR UPDATE"""),
            {"m": match_id, "c": club_id, "p": payload.player_in_id})).first()
        if existing_incoming:
            await session.execute(text("""UPDATE match_lineups SET is_starting=TRUE,position=:pos,role=:role
                WHERE id=:id"""), {"pos": outgoing["position"], "role": outgoing["role"], "id": existing_incoming.id})
        else:
            await session.execute(text("""INSERT INTO match_lineups
                (match_id,club_id,player_id,position,role,is_starting)
                VALUES(:m,:c,:p,:pos,:role,TRUE)"""),
                {"m": match_id, "c": club_id, "p": payload.player_in_id,
                 "pos": outgoing["position"], "role": outgoing["role"]})

        await session.execute(text("""INSERT INTO match_substitutions
            (match_id,club_id,minute,player_off_id,player_on_id)
            VALUES(:m,:c,:minute,:off,:on)"""),
            {"m": match_id, "c": club_id, "minute": minute,
             "off": payload.player_out_id, "on": payload.player_in_id})
        if club_id == int(match["home_club_id"]):
            await session.execute(text("UPDATE matches SET home_subs_used=COALESCE(home_subs_used,0)+1 WHERE id=:m"), {"m": match_id})
        else:
            await session.execute(text("UPDATE matches SET away_subs_used=COALESCE(away_subs_used,0)+1 WHERE id=:m"), {"m": match_id})

        out_name = f"{outgoing['first_name']} {outgoing['last_name']}".strip()
        in_name = f"{incoming['first_name']} {incoming['last_name']}".strip()
        description = f"Замена: {in_name} вместо {out_name}"
        await session.execute(text("""INSERT INTO match_events
            (match_id,minute,event_type,club_id,player_id,secondary_player_id,description,metadata)
            VALUES(:m,:minute,'SUBSTITUTION',:c,:off,:on,:description,CAST(:metadata AS jsonb))"""),
            {"m": match_id, "minute": minute, "c": club_id, "off": payload.player_out_id,
             "on": payload.player_in_id, "description": description,
             "metadata": json.dumps({"player_out_id": payload.player_out_id,
                                     "player_in_id": payload.player_in_id})})
        # The live engine stores future events as a JSON phase timeline. Update
        # not-yet-materialized events too, so later play-by-play reflects the
        # player who actually came onto the pitch.
        live_phase = str(match.get("live_phase") or "").upper()
        phase_column = "halftime_data" if live_phase == "FIRST_HALF" else (
            "second_half_data" if live_phase == "SECOND_HALF" else None
        )
        if phase_column:
            phase_data = match.get(phase_column) or {}
            if isinstance(phase_data, str):
                try:
                    phase_data = json.loads(phase_data)
                except (TypeError, json.JSONDecodeError):
                    phase_data = {}
            phase_data = dict(phase_data)
            team_code = "HOME" if club_id == int(match["home_club_id"]) else "AWAY"
            for future_event in phase_data.get("events", []) or []:
                if str(future_event.get("team") or "").upper() != team_code:
                    continue
                try:
                    if int(future_event.get("minute") or 0) <= minute:
                        continue
                except (TypeError, ValueError):
                    continue
                if int(future_event.get("player_id") or 0) == payload.player_out_id:
                    future_event["player_id"] = payload.player_in_id
                    future_event["player"] = in_name
                    if isinstance(future_event.get("text"), str):
                        future_event["text"] = future_event["text"].replace(out_name, in_name)
                if int(future_event.get("secondary_player_id") or 0) == payload.player_out_id:
                    future_event["secondary_player_id"] = payload.player_in_id
                    if isinstance(future_event.get("secondary_player"), str):
                        future_event["secondary_player"] = future_event["secondary_player"].replace(out_name, in_name)
            roster_key = "home_players" if team_code == "HOME" else "away_players"
            roster = [int(pid) for pid in phase_data.get(roster_key, []) if pid is not None]
            if payload.player_in_id not in roster:
                roster.append(payload.player_in_id)
            phase_data[roster_key] = roster
            # Post-half simulation statistics use this timeline to calculate
            # appearances and minutes for substitutes actually used by a manager.
            if phase_column == "second_half_data":
                substitutions = list(phase_data.get("substitutions") or [])
                substitution_record = [minute, payload.player_out_id, payload.player_in_id, team_code]
                if substitution_record not in substitutions:
                    substitutions.append(substitution_record)
                phase_data["substitutions"] = substitutions
            await session.execute(text(f"UPDATE matches SET {phase_column}=CAST(:data AS jsonb) WHERE id=:m"),
                {"data": json.dumps(phase_data, ensure_ascii=False, default=str), "m": match_id})

        await session.commit()
        return {"status": "substituted", "match_id": match_id, "minute": minute,
                "player_out_id": payload.player_out_id, "player_in_id": payload.player_in_id,
                "description": description, "substitutions_used": int(used) + 1}


@app.post("/api/matches/{match_id}/tactics")
@retry_on_interface_error
async def update_live_match_tactics(
    match_id: int,
    payload: TacticsPayload,
    user=Depends(authenticated_telegram_user),
):
    """Update this side's tactical settings without overwriting club defaults."""
    from .game.tactics import ALLOWED_FORMATIONS, Style

    formation = payload.formation.strip()
    style = payload.style.strip().upper()
    if formation not in ALLOWED_FORMATIONS:
        raise HTTPException(status_code=400, detail="Неизвестная схема расстановки.")
    if style not in {item.value for item in Style}:
        raise HTTPException(status_code=400, detail="Неизвестный стиль игры.")

    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        club_id = int(club["id"])
        match = (await session.execute(text("""SELECT id,home_club_id,away_club_id,status,live_phase,
                COALESCE(current_minute,0) AS current_minute,halftime_data,second_half_data
            FROM matches WHERE id=:m FOR UPDATE"""), {"m": match_id})).mappings().first()
        if not match:
            raise HTTPException(status_code=404, detail="Матч не найден.")
        if club_id not in (int(match["home_club_id"]), int(match["away_club_id"])):
            raise HTTPException(status_code=403, detail="Вы не управляете ни одной из команд этого матча.")
        if str(match["status"]).upper() != "LIVE":
            raise HTTPException(status_code=409, detail="Тактику можно менять только во время LIVE-матча.")

        tactic_values = {"formation": formation, "style": style, "tempo": payload.tempo,
                         "pressing": payload.pressing, "width": payload.width,
                         "defensive_line": payload.defensive_line, "aggression": payload.aggression}
        current_tactic = (await session.execute(text("""SELECT id FROM match_tactics
            WHERE match_id=:m AND club_id=:c ORDER BY id LIMIT 1 FOR UPDATE"""),
            {"m": match_id, "c": club_id})).first()
        if current_tactic:
            await session.execute(text("""UPDATE match_tactics SET formation=:formation,style=:style,
                tempo=:tempo,pressing=:pressing,width=:width,defensive_line=:defensive_line,
                aggression=:aggression WHERE id=:id"""), {**tactic_values, "id": current_tactic.id})
        else:
            await session.execute(text("""INSERT INTO match_tactics
                (match_id,club_id,formation,style,tempo,pressing,width,defensive_line,aggression)
                VALUES(:m,:c,:formation,:style,:tempo,:pressing,:width,:defensive_line,:aggression)"""),
                {"m": match_id, "c": club_id, **tactic_values})

        tactical_state = {**tactic_values}
        live_phase = str(match.get("live_phase") or "").upper()
        side_key = "home_tactics" if club_id == int(match["home_club_id"]) else "away_tactics"
        phase_column = "halftime_data" if live_phase == "FIRST_HALF" else (
            "second_half_data" if live_phase == "SECOND_HALF" else None
        )
        # Persist tactics for second-half initialization as well, even if the
        # manager changes the plan before the break.
        second_column = "home_second_tactics" if side_key == "home_tactics" else "away_second_tactics"
        await session.execute(text(f"UPDATE matches SET {second_column}=CAST(:data AS jsonb) WHERE id=:m"),
            {"data": json.dumps(tactical_state, ensure_ascii=False, default=str), "m": match_id})

        minute = int(match.get("current_minute") or 0)
        description = f"Тактика изменена: {formation}, {style}"
        event_metadata = {
            **tactical_state,
            "new_formation": formation,
            "new_style": style,
            "new_sliders": {key: tactic_values[key] for key in
                ("tempo", "pressing", "width", "defensive_line", "aggression")},
        }
        if phase_column:
            phase_data = match.get(phase_column) or {}
            if isinstance(phase_data, str):
                try:
                    phase_data = json.loads(phase_data)
                except (TypeError, json.JSONDecodeError):
                    phase_data = {}
            phase_data = dict(phase_data)
            phase_events = list(phase_data.get("events") or [])
            phase_events.append({
                "minute": minute, "type": "TACTICAL_CHANGE",
                "team": "HOME" if side_key == "home_tactics" else "AWAY",
                "player_id": None, "secondary_player_id": None,
                "text": description, "metadata": event_metadata,
            })
            phase_data["events"] = phase_events
            await session.execute(text(f"UPDATE matches SET {phase_column}=CAST(:data AS jsonb) WHERE id=:m"),
                {"data": json.dumps(phase_data, ensure_ascii=False, default=str), "m": match_id})

        await session.execute(text("""INSERT INTO match_events
            (match_id,minute,event_type,club_id,description,metadata)
            VALUES(:m,:minute,'TACTICAL_CHANGE',:c,:description,CAST(:metadata AS jsonb))"""),
            {"m": match_id, "minute": minute, "c": club_id,
             "description": description,
             "metadata": json.dumps(event_metadata, ensure_ascii=False)})
        await session.commit()
    return {"status": "updated", "match_id": match_id, "club_id": club_id,
            "formation": formation, "style": style, "tempo": payload.tempo,
            "pressing": payload.pressing, "width": payload.width,
            "defensive_line": payload.defensive_line, "aggression": payload.aggression}


@app.get("/api/tactics/{club_id}")
@retry_on_interface_error
async def get_tactics(club_id: int, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club=await user_and_club(user, session)
        if int(club["id"]) != club_id: raise HTTPException(status_code=403, detail="This club does not belong to you")
        row=(await session.execute(text("SELECT formation,style,tempo,pressing,width,defensive_line,aggression FROM club_tactics WHERE club_id=:c"), {"c":club_id})).mappings().first()
        return dict(row) if row else {"formation":"4-3-3","style":"BALANCE","tempo":50,"pressing":50,"width":50,"defensive_line":50,"aggression":50}


@app.post("/api/tactics/{club_id}")
async def save_tactics(club_id: int, payload: TacticsPayload, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club=await user_and_club(user, session)
        if int(club["id"]) != club_id: raise HTTPException(status_code=403, detail="This club does not belong to you")
        await session.execute(text("""INSERT INTO club_tactics(club_id,formation,style,tempo,pressing,width,defensive_line,aggression)
            VALUES(:c,:formation,:style,:tempo,:pressing,:width,:defensive_line,:aggression)
            ON CONFLICT(club_id) DO UPDATE SET formation=EXCLUDED.formation,style=EXCLUDED.style,tempo=EXCLUDED.tempo,
            pressing=EXCLUDED.pressing,width=EXCLUDED.width,defensive_line=EXCLUDED.defensive_line,aggression=EXCLUDED.aggression"""),
            {"c":club_id, **payload.model_dump()})
        await session.commit()
        return {"status":"ok", **payload.model_dump()}


@app.post("/api/transfers/bid")
async def transfer_bid(payload: BidPayload, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club=await user_and_club(user, session)
        try:
            listing = (await session.execute(text("""SELECT id,seller_club_id,asking_price FROM transfer_listings
                WHERE player_id=:p AND status='LISTED' FOR UPDATE"""), {"p": payload.player_id})).mappings().first()
            if listing:
                if listing["seller_club_id"] is not None and int(listing["seller_club_id"]) == int(club["id"]):
                    raise ValueError("Нельзя подать предложение за собственного игрока.")
                offer_id = await make_transfer_offer(session, int(club["id"]), int(listing["id"]), int(listing["asking_price"]))
                await session.commit()
                return {"status":"offer_submitted", "player_id":int(payload.player_id),
                        "price":int(listing["asking_price"]), "offer_id":int(offer_id)}
            player, price = await buy_player(session, int(club["id"]), payload.player_id)
            await session.commit()
            return {"status":"purchased", "player_id":int(player["id"]), "price":int(price)}
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except InterfaceError:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            log.exception("Transfer purchase failed")
            raise HTTPException(status_code=500, detail="Transfer failed")


@app.post("/api/transfers/sell")
@retry_on_interface_error
async def transfer_sell(payload: SellPlayerPayload, user=Depends(authenticated_telegram_user)):
    """Sell an owned player into the market and credit the club budget atomically."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        club_id = int(club["id"])
        try:
            player = (await session.execute(text("""SELECT p.id,p.first_name,p.last_name,
                    ROUND((p.pace+p.shooting+p.passing+p.dribbling+p.defending+p.physical+p.stamina+p.mental)/8.0)::int AS overall
                FROM club_players cp JOIN players p ON p.id=cp.player_id
                WHERE cp.club_id=:club_id AND p.id=:player_id FOR UPDATE OF cp"""),
                {"club_id": club_id, "player_id": payload.player_id})).mappings().first()
            if not player:
                raise HTTPException(status_code=404, detail="Игрок не найден в вашем клубе")
            count = int((await session.execute(text("SELECT COUNT(*) FROM club_players WHERE club_id=:c"),
                {"c": club_id})).scalar_one())
            if count <= 11:
                raise HTTPException(status_code=400, detail="Нельзя продать игрока: в клубе должно остаться минимум 11 игроков")
            await session.execute(text("DELETE FROM club_players WHERE club_id=:c AND player_id=:p"),
                {"c": club_id, "p": payload.player_id})
            await session.execute(text("UPDATE clubs SET budget=COALESCE(budget,0)+:price WHERE id=:c"),
                {"price": payload.price, "c": club_id})
            await session.commit()
            new_budget = int((await session.execute(text("SELECT budget FROM clubs WHERE id=:c"),
                {"c": club_id})).scalar_one())
            return {"status": "sold", "player_id": payload.player_id, "sale_price": payload.price, "budget": new_budget}
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            log.exception("Player sale failed for club_id=%s player_id=%s", club_id, payload.player_id)
            raise HTTPException(status_code=500, detail="Не удалось продать игрока")

@app.get("/api/transfers/offers")
@retry_on_interface_error
async def transfer_offers(user=Depends(authenticated_telegram_user)):
    """Offers related to the authenticated club, separated into incoming/outgoing."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        cid = int(club["id"])
        rows = (await session.execute(text("""SELECT o.id,o.amount,o.status,o.created_at,l.player_id,
                p.first_name,p.last_name,p.position,p.age,
                o.buyer_club_id,bc.name AS buyer_club_name,l.seller_club_id,sc.name AS seller_club_name
            FROM transfer_offers o
            JOIN transfer_listings l ON l.id=o.listing_id
            JOIN players p ON p.id=l.player_id
            JOIN clubs bc ON bc.id=o.buyer_club_id
            LEFT JOIN clubs sc ON sc.id=l.seller_club_id
            WHERE o.buyer_club_id=:c OR l.seller_club_id=:c
            ORDER BY o.created_at DESC,o.id DESC LIMIT 100"""), {"c": cid})).mappings().all()
        return {"offers": [{**dict(r), "id": int(r["id"]), "amount": int(r["amount"]),
            "player_id": int(r["player_id"]), "is_incoming": r["seller_club_id"] == cid,
            "player_name": (f"{r['first_name'] or ''} {r['last_name'] or ''}").strip() or f"Игрок #{r['player_id']}"}
            for r in rows]}


class OfferActionPayload(BaseModel):
    offer_id: int = Field(gt=0)


@app.post("/api/transfers/offers/accept")
async def transfer_offer_accept(payload: OfferActionPayload, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        try:
            result = await accept_transfer_offer(session, int(club["id"]), int(payload.offer_id))
            await session.commit()
            return {"status": "accepted", "offer_id": int(payload.offer_id),
                    "player_id": int(result["player_id"]), "amount": int(result["amount"])}
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            await session.rollback()
            log.exception("Transfer offer acceptance failed")
            raise HTTPException(status_code=500, detail="Не удалось принять предложение")


@app.post("/api/transfers/offers/reject")
async def transfer_offer_reject(payload: OfferActionPayload, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        result = (await session.execute(text("""UPDATE transfer_offers o SET status='REJECTED'
            FROM transfer_listings l WHERE o.listing_id=l.id AND o.id=:o AND o.status='PENDING'
              AND l.seller_club_id=:c RETURNING o.id"""),
            {"o": int(payload.offer_id), "c": int(club["id"])})).scalar_one_or_none()
        if result is None:
            await session.rollback()
            raise HTTPException(status_code=404, detail="Предложение не найдено или уже обработано")
        await session.commit()
        return {"status": "rejected", "offer_id": int(result)}


@app.get("/api/transfers/history")
@retry_on_interface_error
async def transfer_history(user=Depends(authenticated_telegram_user)):
    """Completed transfers involving the authenticated club."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        rows = (await session.execute(text("""SELECT t.id,t.player_id,t.from_club_id,t.to_club_id,t.price,t.created_at,
                p.first_name,p.last_name,fc.name AS from_club_name,tc.name AS to_club_name
            FROM transfers t JOIN players p ON p.id=t.player_id
            LEFT JOIN clubs fc ON fc.id=t.from_club_id LEFT JOIN clubs tc ON tc.id=t.to_club_id
            WHERE t.from_club_id=:c OR t.to_club_id=:c
            ORDER BY t.created_at DESC,t.id DESC LIMIT 100"""), {"c": int(club["id"])})).mappings().all()
        return {"transfers": [{**dict(r), "id": int(r["id"]), "player_id": int(r["player_id"]),
            "price": int(r["price"]), "player_name": (f"{r['first_name'] or ''} {r['last_name'] or ''}").strip() or f"Игрок #{r['player_id']}"}
            for r in rows]}


@app.get("/api/scout/report/{player_id}")
@retry_on_interface_error
async def scout_player_report(player_id: int, user=Depends(authenticated_telegram_user)):
    """Return a real scouting report from persisted player attributes, not a UI-only toast."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        row = (await session.execute(text("""SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.nationality,
                p.market_value,p.potential,p.rarity,p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,
                ROUND((p.pace+p.shooting+p.passing+p.dribbling+p.defending+p.physical+p.stamina+p.mental)/8.0)::int AS overall,
                EXISTS(SELECT 1 FROM club_players cp WHERE cp.club_id=:c AND COALESCE(cp.template_id,cp.player_id)=COALESCE(p.template_id,p.id)) AS already_owned
            FROM players p WHERE p.id=:p AND p.is_template=TRUE"""), {"c": int(club["id"]), "p": player_id})).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Игрок для скаутинга не найден")
        data = dict(row)
        if data["already_owned"]:
            raise HTTPException(status_code=400, detail="Этот игрок уже есть в вашем клубе")
        attrs = {key: int(data.get(key) or 0) for key in ("pace","shooting","passing","dribbling","defending","physical","stamina","mental")}
        strengths = sorted(attrs.items(), key=lambda item: item[1], reverse=True)[:3]
        weaknesses = sorted(attrs.items(), key=lambda item: item[1])[:2]
        labels = {"pace":"Скорость","shooting":"Удар","passing":"Пас","dribbling":"Дриблинг","defending":"Защита","physical":"Физика","stamina":"Выносливость","mental":"Ментальность"}
        return {"player": {k: data[k] for k in ("id","first_name","last_name","position","age","nationality","market_value","potential","rarity","overall")},
            "strengths": [{"attribute": labels[k], "value": v} for k,v in strengths],
            "weaknesses": [{"attribute": labels[k], "value": v} for k,v in weaknesses],
            "potential_gap": max(0, int(data["potential"] or 0)-int(data["overall"] or 0)),
            "recommendation": "Высокий приоритет" if int(data["potential"] or 0)-int(data["overall"] or 0) >= 15 else "Рассмотреть по потребностям состава"}


@app.get("/api/league")
@retry_on_interface_error
async def league_table(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club=await user_and_club(user, session)
        row=(await session.execute(text("""SELECT l.id, l.name, l.tier FROM leagues l JOIN league_teams lt ON lt.league_id=l.id
            WHERE lt.club_id=:c ORDER BY CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'WAITING' THEN 1 ELSE 2 END,
                     l.season_id DESC NULLS LAST,l.id DESC LIMIT 1"""), {"c":club["id"]})).first()
        if not row: return {"league_id":None,"league_name":None,"division_code":None,"division_name":None,"table":[]}
        rows=(await session.execute(text("""SELECT c.id,c.name,lt.played,lt.wins,lt.draws,lt.losses,lt.goals_for,lt.goals_against,lt.points
            FROM league_teams lt JOIN clubs c ON c.id=lt.club_id WHERE lt.league_id=:l
            ORDER BY lt.points DESC,(lt.goals_for-lt.goals_against) DESC,lt.goals_for DESC,c.name"""), {"l":row.id})).mappings().all()
        return {"league_id":int(row.id),"league_name":row.name,
                "division_code":str(row.tier).upper() if row.tier else None,
                "division_name":division_name(row.tier),"table":[dict(r) for r in rows]}


class IdPayload(BaseModel):
    id: int = Field(gt=0)


@app.get("/api/transfers/listings")
@retry_on_interface_error
async def transfer_listings(user=Depends(authenticated_telegram_user)):
    """Real market data for the authenticated club; never return owned players as buyable."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        rows = await market_players(session, limit=100, listed_only=False)
        result = []
        for row in rows:
            if row.get("seller_club_id") is not None and int(row["seller_club_id"]) == int(club["id"]):
                continue
            item = dict(row)
            item["id"] = int(item["id"])
            item["market_value"] = int(item.get("market_value") or 0)
            item["potential"] = int(item.get("potential") or 0)
            item["age"] = int(item.get("age") or 0)
            attributes = [int(item.get(key) or 0) for key in ("pace", "shooting", "passing", "dribbling", "defending", "physical", "stamina", "mental")]
            item["overall"] = int(round(sum(attributes) / len(attributes)))
            item["asking_price"] = int(item["asking_price"] or item["market_value"])
            item["is_listed"] = item.get("listing_id") is not None
            result.append(item)
        return {"club_id": int(club["id"]), "listings": result}


@app.get("/api/scout/recommendations")
@retry_on_interface_error
async def scout_recommendations(user=Depends(authenticated_telegram_user)):
    """Recommend unowned player templates based on potential and value."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        rows = (await session.execute(text("""SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.nationality,
                p.market_value,p.potential,p.rarity,
                ROUND((p.pace+p.shooting+p.passing+p.dribbling+p.defending+p.physical+p.stamina+p.mental)/8.0)::int AS overall
            FROM players p
            WHERE p.is_template=TRUE
              AND NOT EXISTS (SELECT 1 FROM club_players cp WHERE cp.club_id=:c AND cp.template_id=p.id)
            ORDER BY (p.potential - ROUND((p.pace+p.shooting+p.passing+p.dribbling+p.defending+p.physical+p.stamina+p.mental)/8.0)) DESC,
                     p.potential DESC,p.market_value ASC,p.id ASC LIMIT 40"""), {"c": int(club["id"])})).mappings().all()
        return {"recommendations": [dict(r) for r in rows]}


@app.get("/api/league/fixtures")
@retry_on_interface_error
async def league_fixtures(user=Depends(authenticated_telegram_user)):
    """Return fixtures from the club's active/current league, in actual kickoff order."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        league = (await session.execute(text("""
            SELECT l.id,l.season_id
            FROM league_teams lt
            JOIN leagues l ON l.id=lt.league_id
            WHERE lt.club_id=:c
            ORDER BY CASE WHEN l.status='ACTIVE' THEN 0 ELSE 1 END,
                     l.season_id DESC NULLS LAST,l.id DESC
            LIMIT 1
        """), {"c": int(club["id"])})).mappings().first()
        active_season_id = (league['season_id'] if league else None)
        if active_season_id is None:
            active_season_id = (await session.execute(text("""SELECT id FROM seasons
                WHERE state IN ('ACTIVE','PRESEASON') ORDER BY id DESC LIMIT 1"""))).scalar_one_or_none()
        rows = (await session.execute(text("""SELECT m.id,m.round,m.home_club_id,m.away_club_id,
                hc.name AS home_name,ac.name AS away_name,
                CASE WHEN UPPER(COALESCE(m.status,'')) IN ('FINISHED','LIVE') THEN m.home_score ELSE NULL END AS home_score,
                CASE WHEN UPPER(COALESCE(m.status,'')) IN ('FINISHED','LIVE') THEN m.away_score ELSE NULL END AS away_score,
                m.status,m.scheduled_at,'LEAGUE' AS competition_type,'LEAGUE' AS comp_type,
                COALESCE(l.name,'Чемпионат') AS competition_name
            FROM matches m
            JOIN clubs hc ON hc.id=m.home_club_id
            JOIN clubs ac ON ac.id=m.away_club_id
            LEFT JOIN leagues l ON l.id=m.league_id
            WHERE COALESCE(m.is_test,FALSE)=FALSE
              AND CAST(:l AS BIGINT) IS NOT NULL
              AND m.league_id=:l
              AND (m.season_id=:season OR m.season_id IS NULL)
              AND UPPER(COALESCE(m.competition_type,'LEAGUE'))='LEAGUE'
              AND NOT EXISTS (SELECT 1 FROM cup_matches cm WHERE cm.match_id=m.id)
              AND NOT EXISTS (SELECT 1 FROM champions_matches clm WHERE clm.match_id=m.id)
            ORDER BY CASE WHEN CAST(:season AS BIGINT) IS NOT NULL AND m.season_id=:season THEN 0
                          WHEN m.season_id IS NULL THEN 1 ELSE 2 END,
                     CASE UPPER(COALESCE(m.status,'')) WHEN 'LIVE' THEN 0 WHEN 'SCHEDULED' THEN 1 ELSE 2 END,
                     m.scheduled_at ASC NULLS LAST,m.round ASC,m.id ASC LIMIT 300"""),
            {"l": int(league["id"]) if league else None, "season": active_season_id})).mappings().all()
        fixtures = [dict(row) for row in rows]
        return {"fixtures": fixtures}


@app.get("/api/training/today")
@retry_on_interface_error
async def training_today(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        plan = (await session.execute(text("SELECT focus,intensity,updated_at FROM club_training WHERE club_id=:c"),
            {"c": int(club["id"])})).mappings().first()
        row = (await session.execute(text("""SELECT max(last_training_at) AS last_training_at,
                count(*) AS squad_size,coalesce(avg(fitness),0)::int AS avg_fitness
            FROM club_players WHERE club_id=:c"""), {"c": int(club["id"])})).mappings().first()
        return {"focus": (plan["focus"] if plan else "BALANCED"),
                "intensity": (plan["intensity"] if plan else "NORMAL"),
                "last_training_at": row["last_training_at"], "squad_size": int(row["squad_size"] or 0),
                "avg_fitness": int(row["avg_fitness"] or 0)}


@app.post("/api/training/complete")
async def training_complete(payload: TrainingPayload = TrainingPayload(), user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        try:
            focus = payload.focus.upper()
            intensity = payload.intensity.upper()
            if focus not in {"ATTACK", "PLAYMAKING", "DEFENSE", "PHYSICAL", "TECHNICAL", "MENTAL", "BALANCED", "RECOVERY"}:
                raise HTTPException(status_code=400, detail="Неизвестный тип тренировки")
            if intensity not in {"LIGHT", "NORMAL", "HIGH"}:
                raise HTTPException(status_code=400, detail="Неизвестная интенсивность тренировки")
            from .services import set_training_plan
            await set_training_plan(session, int(club["id"]), focus, intensity)
            changes, batch_id = await train_squad(session, int(club["id"]))
            await session.commit()
            if batch_id is None:
                return {"status": "cooldown", "remaining_seconds": int(changes[0][1]) if changes else 0,
                        "players_trained": 0, "batch_id": None}
            return {"status": "completed", "players_trained": len(changes), "batch_id": batch_id}
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            # Preserve explicit validation responses (400) instead of converting them to 500.
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            log.exception("Training completion failed")
            raise HTTPException(status_code=500, detail="Не удалось завершить тренировку")


@app.get("/api/academy/youth")
@retry_on_interface_error
async def academy_youth(user=Depends(authenticated_telegram_user)):
    """Show a small, academy-level-appropriate pool rather than the strongest 30 templates."""
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        academy_level = int(club.get("academy_level") or 1)
        # Better academies can support a slightly larger intake and stronger prospects.
        # Low-level academies do not surface a screen full of near-elite 80+ OVR players.
        max_overall = min(82, 58 + academy_level * 2)
        limit = min(12, 3 + academy_level)
        rows = (await session.execute(text("""WITH youth_pool AS (
              SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.potential,p.rarity,
                     ROUND((p.pace+p.shooting+p.passing+p.dribbling+p.defending+p.physical+p.stamina+p.mental)/8.0)::int AS overall
              FROM players p
              WHERE p.is_template=TRUE AND p.age BETWEEN 16 AND 21
                AND NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.club_id=:c AND cp.template_id=p.id)
            )
            SELECT * FROM youth_pool
            WHERE overall<=:max_overall
            ORDER BY CASE WHEN potential>=90 THEN 1 ELSE 0 END ASC,
                     (potential-overall) DESC, potential DESC, age ASC, id ASC
            LIMIT :limit"""), {"c": int(club["id"]), "max_overall": max_overall, "limit": limit})).mappings().all()
        return {"youth": [dict(r) for r in rows], "academy_level": academy_level,
                "max_overall": max_overall, "capacity": limit}


@app.post("/api/academy/promote")
async def academy_promote(payload: IdPayload, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        try:
            row = (await session.execute(text("SELECT id,first_name,last_name FROM players WHERE id=:p AND is_template=TRUE AND age<=21"),
                {"p": payload.id})).mappings().first()
            if not row:
                raise ValueError("Воспитанник больше недоступен.")
            player_id, _ = await add_copy_to_club(session, int(club["id"]), int(row["id"]))
            if player_id is None:
                raise ValueError("Этот воспитанник уже есть в составе.")
            await session.commit()
            return {"status": "promoted", "player_id": int(player_id),
                    "name": f"{row['first_name']} {row['last_name']}".strip()}
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/packs/list")
@retry_on_interface_error
async def packs_list(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        rows = await list_packs(session)
        return {"club_id": int(club["id"]), "packs": [dict(r) for r in rows]}


@app.post("/api/packs/open")
async def packs_open(payload: IdPayload, user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        try:
            result = await open_pack(session, int(club["id"]), payload.id)
            await session.commit()
            return {"status": "opened", **result}
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            await session.rollback()
            log.exception("Pack opening failed")
            raise HTTPException(status_code=500, detail="Не удалось открыть пак")


@app.get("/api/stadium/info")
@retry_on_interface_error
async def stadium_info(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        level = int(club["stadium_level"] or 1)
        return {"name": club["stadium_name"], "level": level,
                "capacity": stadium_capacity(level), "fans": int(club["fans"] or 0),
                "ticket_price": int(club.get("ticket_price") or 20),
                "upgrade_cost": 1_000_000 * level if level < 20 else None,
                "budget": int(club["budget"] or 0)}


@app.post("/api/stadium/upgrade")
async def stadium_upgrade(user=Depends(authenticated_telegram_user)):
    async with SessionLocal() as session:
        club = await user_and_club(user, session)
        try:
            level, cost, capacity = await upgrade_stadium(session, int(club["id"]))
            await session.commit()
            return {"status": "upgraded", "level": level, "cost": cost, "capacity": capacity}
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            await session.rollback()
            log.exception("Stadium upgrade failed")
            raise HTTPException(status_code=500, detail="Не удалось улучшить стадион")


@app.websocket("/ws")
async def match_events_socket(websocket: WebSocket):
    # WebSocket authentication uses the same signature and freshness checks as HTTP.
    init_data = websocket.query_params.get("initData", "")
    try:
        verified_user = validate_telegram_init_data(init_data, settings.bot_token)
    except TelegramInitDataError as exc:
        log.warning("WebSocket Telegram authentication rejected (reason=%s)", str(exc))
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        while True:
            try:
                async with SessionLocal() as session:
                    club=await user_and_club({"telegram_id":verified_user["telegram_id"]}, session)
                    rows=(await session.execute(text("""SELECT m.id,m.home_club_id,m.away_club_id,m.home_score,m.away_score,
                        m.status FROM matches m WHERE m.status='LIVE' AND (m.home_club_id=:c OR m.away_club_id=:c)
                          AND UPPER(COALESCE(m.competition_type,'LEAGUE')) IN ('LEAGUE','FRIENDLY')
                          AND NOT EXISTS (SELECT 1 FROM cup_matches cm WHERE cm.match_id=m.id)
                          AND NOT EXISTS (SELECT 1 FROM champions_matches clm WHERE clm.match_id=m.id)"""), {"c":club["id"]})).mappings().all()
                    output=[]
                    for match in rows:
                        events=(await session.execute(text("SELECT id,minute,event_type,club_id,player_id,description,metadata FROM match_events WHERE match_id=:m ORDER BY id DESC LIMIT 20"), {"m":match["id"]})).mappings().all()
                        output.append({"match":dict(match),"events":[dict(e) for e in reversed(events)]})
                    await websocket.send_text(json.dumps({"matches":output}, default=str))
            except InterfaceError:
                log.warning("DB connection closed, retrying...")
                await asyncio.sleep(0.1)
                continue
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
    except Exception:
        log.exception("WebSocket match feed failed")
        try: await websocket.close(code=1011)
        except Exception: pass


# Static mount comes after API routes, so it cannot shadow /api/* or /ws.
if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/", include_in_schema=False)
    async def mini_app_not_built():
        return JSONResponse(status_code=200, content={"detail":"Mini App not built"})
