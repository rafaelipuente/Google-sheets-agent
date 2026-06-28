"""Small retry wrapper for flaky outbound calls (OpenAI, Google Sheets).

Retries ONLY transient connection-level failures (broken pipe, reset, timeout,
DNS/socket errors). It never retries a 4xx client response, and it does not
retry plain logic errors (e.g. ValueError for an unknown column).
"""

from __future__ import annotations

import errno
import http.client
import logging
import socket
import time
from typing import Callable, TypeVar

log = logging.getLogger("sheets_agent")

T = TypeVar("T")

# OS-level errnos that indicate a dropped/refused connection.
_RETRYABLE_ERRNOS = {
    errno.EPIPE,        # 32 broken pipe
    errno.ECONNRESET,   # 54 connection reset by peer
    errno.ECONNREFUSED,
    errno.ETIMEDOUT,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
}

# Class names from optional libs (requests/urllib3/openai/google) we treat as
# transient without importing them directly.
_RETRYABLE_NAMES = {
    "APIConnectionError",      # openai
    "APITimeoutError",         # openai
    "ConnectionError",         # requests
    "Timeout",                 # requests
    "ConnectTimeout",
    "ReadTimeout",
    "ProtocolError",           # urllib3
    "TransportError",          # google.auth
    "ServerNotFoundError",     # httplib2
}


def _status_code(exc: Exception) -> int | None:
    """Best-effort HTTP status extraction across client libraries."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if code is None:
            code = getattr(resp, "status", None)
        if isinstance(code, int):
            return code
    raw = getattr(exc, "resp", None)  # googleapiclient HttpError
    if raw is not None:
        try:
            return int(getattr(raw, "status"))
        except (TypeError, ValueError, AttributeError):
            pass
    code = getattr(exc, "status_code", None)  # openai APIStatusError
    if isinstance(code, int):
        return code
    return None


def is_connection_error(exc: Exception) -> bool:
    """True for transient connection/timeout failures (ignores status codes)."""
    if isinstance(
        exc,
        (BrokenPipeError, ConnectionError, TimeoutError, socket.timeout,
         http.client.RemoteDisconnected),
    ):
        return True
    if isinstance(exc, OSError) and exc.errno in _RETRYABLE_ERRNOS:
        return True
    return type(exc).__name__ in _RETRYABLE_NAMES


def is_retryable(exc: Exception) -> bool:
    """Retry only transient connection errors, and never a 4xx response."""
    code = _status_code(exc)
    if code is not None and 400 <= code < 500:
        return False
    return is_connection_error(exc)


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    label: str = "outbound call",
) -> T:
    """Call ``fn`` up to ``attempts`` times, backing off exponentially on
    transient connection errors. Re-raises immediately on anything else."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below if not retryable
            last_exc = exc
            if attempt == attempts - 1 or not is_retryable(exc):
                raise
            delay = base_delay * (2 ** attempt)
            log.warning(
                "Transient %s on %s (attempt %d/%d); retrying in %.1fs",
                type(exc).__name__, label, attempt + 1, attempts, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
