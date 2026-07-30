import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yml")


class MeteoSwissConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stac_base_url: str = "https://data.geo.admin.ch/api/stac/v1"
    ch1eps_collection: str = "ch.meteoschweiz.ogd-forecasting-icon-ch1"
    ch2eps_collection: str = "ch.meteoschweiz.ogd-forecasting-icon-ch2"


class LenticularisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meteoswiss: MeteoSwissConfig
    lenticularis: LenticularisConfig
    grib_cache_dir: str = "/tmp/lsmfapi_grib"


@lru_cache(maxsize=1)
def get_config() -> Config:
    resolved = CONFIG_PATH.resolve()
    if not CONFIG_PATH.exists():
        logger.critical("Config file not found at %s", resolved)
        raise FileNotFoundError(f"Config file not found: {resolved}")
    data = yaml.safe_load(CONFIG_PATH.read_text())
    cfg = Config(**data)
    logger.info(
        "Config loaded from %s: meteoswiss.stac_base_url=%s meteoswiss.ch1eps_collection=%s "
        "meteoswiss.ch2eps_collection=%s lenticularis.base_url=%s grib_cache_dir=%s",
        resolved,
        cfg.meteoswiss.stac_base_url,
        cfg.meteoswiss.ch1eps_collection,
        cfg.meteoswiss.ch2eps_collection,
        cfg.lenticularis.base_url,
        cfg.grib_cache_dir,
    )
    return cfg
