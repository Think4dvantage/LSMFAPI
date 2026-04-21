import asyncio
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from lsmfapi.collectors.icon_ch1_eps import IconCh1EpsCollector
from lsmfapi.collectors.icon_ch2_eps import IconCh2EpsCollector
from lsmfapi.config import get_config
from lsmfapi.database import collection_state as cs
from lsmfapi.database.cache import save_cache

logger = logging.getLogger(__name__)

_ch1_collector = IconCh1EpsCollector()
_ch2_collector = IconCh2EpsCollector()


async def _warm_cache() -> None:
    """Run CH1 then CH2 once at startup to fill the cache. Sequential to avoid overloading downloads."""
    await _run_ch1eps()
    await _run_ch2eps()


async def _run_ch1eps() -> None:
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
        cfg = get_config()
        self._scheduler.add_job(
            _run_ch1eps,
            IntervalTrigger(
                hours=cfg.scheduler.ch1eps_interval_hours,
                jitter=cfg.scheduler.ch1eps_jitter_seconds,
            ),
            id="collect_ch1eps",
        )
        self._scheduler.add_job(
            _run_ch2eps,
            IntervalTrigger(
                hours=cfg.scheduler.ch2eps_interval_hours,
                jitter=cfg.scheduler.ch2eps_jitter_seconds,
            ),
            id="collect_ch2eps",
        )
        self._scheduler.start()
        logger.info("Scheduler started — warming cache in background")
        asyncio.create_task(_warm_cache())

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
