from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel


class MeteoSwissConfig(BaseModel):
    stac_base_url: str = "https://data.geo.admin.ch/api/stac/v1"
    ch1eps_collection: str = "ch.meteoschweiz.ogd-forecasting-icon-ch1"
    ch2eps_collection: str = "ch.meteoschweiz.ogd-forecasting-icon-ch2"


class LenticularisConfig(BaseModel):
    base_url: str


class SchedulerConfig(BaseModel):
    ch1eps_interval_hours: int = 3
    ch1eps_jitter_seconds: int = 600
    ch2eps_interval_hours: int = 6
    ch2eps_jitter_seconds: int = 600


class Config(BaseModel):
    meteoswiss: MeteoSwissConfig
    lenticularis: LenticularisConfig
    scheduler: SchedulerConfig = SchedulerConfig()


@lru_cache(maxsize=1)
def get_config() -> Config:
    data = yaml.safe_load(Path("config.yml").read_text())
    return Config(**data)
