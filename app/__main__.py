import asyncio
import logging

from .config import settings
from .db import init_db
from .bot import create_bot
from .seed import seed


async def main():
    if not settings.bot_token or settings.bot_token == "replace_me":
        raise SystemExit("BOT_TOKEN is missing or still set to the placeholder value. Copy .env.example to .env and set a valid Telegram token.")

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logging.getLogger(__name__).info("Starting Football Manager bot...")

    await init_db()
    await seed()
    bot, dp = create_bot()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
