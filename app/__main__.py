import asyncio,logging,sys
from .config import settings
from .db import init_db
from .bot import create_bot
from .seed import seed

logger=logging.getLogger(__name__)

async def _portrait_warmup():
    # Run outside the polling loop so cold-start image preparation never blocks the bot.
    for module in ('scripts.build_portraits','scripts.load_real_portraits'):
        logger.info('Portrait warmup starting: %s',module)
        try:
            proc=await asyncio.create_subprocess_exec(sys.executable,'-m',module)
            returncode=await proc.wait()
            if returncode:
                logger.error('Portrait warmup failed (%s): exit=%s',module,returncode)
            else:
                logger.info('Portrait warmup finished: %s',module)
        except Exception:
            logger.exception('Portrait warmup could not start: %s',module)

async def main():
    logging.basicConfig(level=getattr(logging,settings.log_level.upper(),logging.INFO))
    await init_db()
    await seed()
    asyncio.create_task(_portrait_warmup(),name='portrait-warmup')
    bot,dp=create_bot()
    await dp.start_polling(bot, tasks_concurrency_limit=8)

if __name__=='__main__': asyncio.run(main())
