import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from lsmfapi.collectors.icon_ch1_eps import IconCh1EpsCollector
from lsmfapi.collectors.icon_ch2_eps import IconCh2EpsCollector
from lsmfapi.config import get_config

logger = logging.getLogger(__name__)

_ch1_collector = IconCh1EpsCollector()
_ch2_collector = IconCh2EpsCollector()


async def _run_ch1eps() -> None:
    try:
        await _ch1_collector.collect()
    except NotImplementedError as e:
        logger.warning("CH1-EPS collector not yet implemented: %s", e)
    except Exception:
        logger.exception("CH1-EPS collection failed")


async def _run_ch2eps() -> None:
    try:
        await _ch2_collector.collect()
    except NotImplementedError as e:
        logger.warning("CH2-EPS collector not yet implemented: %s", e)
    except Exception:
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
        logger.info("Scheduler started — warming cache")
        await _run_ch1eps()
        await _run_ch2eps()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
