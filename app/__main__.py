import asyncio,logging
from .config import settings
from .db import init_db
from .bot import create_bot
from .seed import seed

async def main():
    logging.basicConfig(level=getattr(logging,settings.log_level.upper(),logging.INFO))
    await init_db()
    await seed()
    bot,dp=create_bot()
    await dp.start_polling(bot)

if __name__=='__main__': asyncio.run(main())
