import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_request_count: int = 0
_error_count: int = 0
_started_at: str = datetime.now(timezone.utc).isoformat()
_recent_errors: deque[dict[str, Any]] = deque(maxlen=20)


def record_request(method: str, path: str) -> None:
    global _request_count
    _request_count += 1


def record_error(method: str, path: str, status_code: int, detail: str) -> None:
    global _error_count
    _error_count += 1
    _recent_errors.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "status": status_code,
        "detail": detail[:400],
    })
    logger.debug("Telemetry error recorded: %s %s → %d", method, path, status_code)


def record_download_error(model: str, variable: str, horizon_h: int, error_msg: str) -> None:
    global _error_count
    _error_count += 1
    _recent_errors.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": model.upper(),
        "path": f"{variable} h+{horizon_h}",
        "status": "DL-ERR",
        "detail": error_msg[:400],
    })
    logger.debug("Telemetry download error recorded: %s %s h+%d", model, variable, horizon_h)


def get_telemetry() -> dict[str, Any]:
    return {
        "started_at": _started_at,
        "request_count": _request_count,
        "error_count": _error_count,
        "recent_errors": list(_recent_errors),
    }
