import logging

from lsmfapi.collectors.base import BaseCollector
from lsmfapi.config import get_config

logger = logging.getLogger(__name__)


class IconCh2EpsCollector(BaseCollector):
    """ICON-CH2-EPS collector — 30-120h, 2 runs/day (00Z/12Z), ~21 members."""

    async def collect(self) -> None:
        cfg = get_config()
        raise NotImplementedError(
            "IconCh2EpsCollector.collect() not implemented — "
            "resolve open questions in .ai/context/architecture.md (member enumeration, RELHUM_2M, PMSL) then implement"
        )
