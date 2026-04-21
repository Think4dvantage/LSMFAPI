import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_state: dict[str, dict[str, Any]] = {
    "ch1": {
        "status": "not_started",
        "started_at": None,
        "finished_at": None,
        "duration_s": None,
        "ref_dt": None,
        "files_done": 0,
        "files_total": 0,
        "last_error": None,
    },
    "ch2": {
        "status": "not_started",
        "started_at": None,
        "finished_at": None,
        "duration_s": None,
        "ref_dt": None,
        "files_done": 0,
        "files_total": 0,
        "last_error": None,
    },
}


def mark_started(model: str) -> None:
    s = _state[model]
    s["status"] = "running"
    s["started_at"] = datetime.now(timezone.utc).isoformat()
    s["finished_at"] = None
    s["duration_s"] = None
    s["files_done"] = 0
    s["files_total"] = 0
    s["last_error"] = None


def mark_running(model: str, ref_dt: datetime, files_total: int) -> None:
    s = _state[model]
    s["ref_dt"] = ref_dt.isoformat()
    s["files_total"] = files_total
    s["files_done"] = 0


def mark_progress(model: str, done: int) -> None:
    _state[model]["files_done"] = done


def mark_done(model: str, duration_s: float) -> None:
    s = _state[model]
    s["status"] = "done"
    s["finished_at"] = datetime.now(timezone.utc).isoformat()
    s["duration_s"] = round(duration_s, 1)
    if s["files_total"]:
        s["files_done"] = s["files_total"]


def mark_failed(model: str, error_msg: str) -> None:
    s = _state[model]
    s["status"] = "failed"
    s["finished_at"] = datetime.now(timezone.utc).isoformat()
    s["last_error"] = error_msg[:500]
    logger.error("Collection failed for %s: %s", model, error_msg[:200])


def get_all_states() -> dict[str, dict[str, Any]]:
    return {m: dict(s) for m, s in _state.items()}
