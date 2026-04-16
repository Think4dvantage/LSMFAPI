import logging

from lsmfapi.collectors.base import BaseCollector
from lsmfapi.config import get_config

logger = logging.getLogger(__name__)


class IconCh1EpsCollector(BaseCollector):
    """ICON-CH1-EPS collector — 0-30h, 4 runs/day (00Z/06Z/12Z/18Z), ~21 members."""

    async def collect(self) -> None:
        cfg = get_config()
        raise NotImplementedError(
            "IconCh1EpsCollector.collect() not implemented — "
            "resolve open questions in .ai/context/architecture.md (member enumeration, RELHUM_2M, PMSL) then implement"
        )
