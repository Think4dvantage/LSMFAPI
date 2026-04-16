import logging
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    async def download(self, url: str, dest_path: str) -> None:
        logger.info("Downloading %s", url)
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
        logger.info("Downloaded %s -> %s", url, dest_path)

    @abstractmethod
    async def collect(self) -> None:
        """Download, parse, and populate the forecast cache for all known stations."""
