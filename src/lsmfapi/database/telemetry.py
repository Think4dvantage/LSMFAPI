import logging
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_request_count: int = 0
_client_error_count: int = 0  # 4xx — routine client traffic, not necessarily our fault
_error_count: int = 0  # 5xx + collector/download failures — might be our fault
_started_at: str = datetime.now(timezone.utc).isoformat()

_MAX_ERROR_GROUPS = 20
# Keyed by (method, path, status, detail) so recurring noise (e.g. a deleted station
# polled every 30 min) collapses into one growing count instead of saturating the ring.
_error_groups: "OrderedDict[tuple[str, str, object, str], dict[str, Any]]" = OrderedDict()


def _sanitize(s: str) -> str:
    """Strip markup so the error ring can never carry HTML, regardless of caller."""
    return s.replace("<", "&lt;").replace(">", "&gt;")


def record_request() -> None:
    global _request_count
    with _lock:
        _request_count += 1


def _record_group(method: str, path: str, status: object, detail: str, is_client_error: bool) -> None:
    global _error_count, _client_error_count
    now = datetime.now(timezone.utc).isoformat()
    key = (method, path, status, detail)
    with _lock:
        if is_client_error:
            _client_error_count += 1
        else:
            _error_count += 1
        group = _error_groups.get(key)
        if group is not None:
            group["count"] += 1
            group["last_seen"] = now
        else:
            if len(_error_groups) >= _MAX_ERROR_GROUPS:
                _error_groups.popitem(last=False)
            group = {
                "method": method, "path": path, "status": status, "detail": detail,
                "first_seen": now, "last_seen": now, "count": 1,
            }
            _error_groups[key] = group
        _error_groups.move_to_end(key)


def record_error(method: str, path: str, status_code: int, detail: str) -> None:
    path = _sanitize(path[:400])
    detail = _sanitize(detail[:400])
    is_client_error = 400 <= status_code < 500
    _record_group(method, path, status_code, detail, is_client_error)
    logger.log(
        logging.WARNING if is_client_error else logging.ERROR,
        "Telemetry error recorded: %s %s → %d", method, path, status_code,
    )


def record_download_error(model: str, variable: str, horizon_h: int, error_msg: str) -> None:
    path = f"{variable} h+{horizon_h}"
    detail = _sanitize(error_msg[:400])
    _record_group(model.upper(), path, "DL-ERR", detail, is_client_error=False)
    logger.error("Telemetry download error recorded: %s %s h+%d", model, variable, horizon_h)


def get_telemetry() -> dict[str, Any]:
    with _lock:
        return {
            "started_at": _started_at,
            "request_count": _request_count,
            "client_error_count": _client_error_count,
            "error_count": _error_count,
            "recent_errors": [dict(g) for g in _error_groups.values()],
        }
