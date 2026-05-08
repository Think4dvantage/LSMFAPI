import asyncio
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lsmfapi.collectors.icon_ch1_eps import IconCh1EpsCollector
from lsmfapi.collectors.icon_ch2_eps import IconCh2EpsCollector
from lsmfapi.database import collection_state as cs
from lsmfapi.database.cache import save_cache

logger = logging.getLogger(__name__)

_ch1_collector = IconCh1EpsCollector()
_ch2_collector = IconCh2EpsCollector()

_ch1_lock = asyncio.Lock()
_ch2_lock = asyncio.Lock()


async def _warm_cache() -> None:
    """Run CH1 then CH2 once at startup to fill the cache. Sequential to avoid overloading downloads."""
    await _run_ch1eps()
    await _run_ch2eps()


async def _run_ch1eps() -> None:
    if _ch1_lock.locked():
        logger.info("CH1 collection already in progress — skipping trigger")
        return
    async with _ch1_lock:
        cs.mark_started("ch1")
        t0 = time.monotonic()
        try:
            await _ch1_collector.collect()
            cs.mark_done("ch1", time.monotonic() - t0)
            save_cache()
        except NotImplementedError as e:
            cs.mark_failed("ch1", str(e))
            logger.warning("CH1-EPS collector not yet implemented: %s", e)
        except Exception as exc:
            cs.mark_failed("ch1", str(exc))
            logger.exception("CH1-EPS collection failed")


async def _run_ch2eps() -> None:
    if _ch2_lock.locked():
        logger.info("CH2 collection already in progress — skipping trigger")
        return
    async with _ch2_lock:
        cs.mark_started("ch2")
        t0 = time.monotonic()
        try:
            await _ch2_collector.collect()
            cs.mark_done("ch2", time.monotonic() - t0)
            save_cache()
        except NotImplementedError as e:
            cs.mark_failed("ch2", str(e))
            logger.warning("CH2-EPS collector not yet implemented: %s", e)
        except Exception as exc:
            cs.mark_failed("ch2", str(exc))
            logger.exception("CH2-EPS collection failed")


class CollectorScheduler:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    async def startup(self) -> None:
        # CH1 (0–33 h, 1 km): trigger at 02/08/14/20 UTC — 2 h after each 00/06/12/18Z run
        self._scheduler.add_job(
            _run_ch1eps,
            CronTrigger(hour="2,8,14,20", minute=0, timezone="UTC"),
            id="collect_ch1eps",
        )
        # CH2 (34–120 h, 2.1 km): trigger at 03/09/15/21 UTC — 3 h after each 00/06/12/18Z run
        self._scheduler.add_job(
            _run_ch2eps,
            CronTrigger(hour="3,9,15,21", minute=0, timezone="UTC"),
            id="collect_ch2eps",
        )
        self._scheduler.start()
        logger.info("Scheduler started — warming cache in background")
        asyncio.create_task(_warm_cache())

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
