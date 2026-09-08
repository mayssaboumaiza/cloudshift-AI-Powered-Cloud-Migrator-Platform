"""
api/rate_limiter.py — Shared SlowAPI limiter singleton.

Imported by both app.py (to register state + exception handler)
and routers (to decorate endpoints with @limiter.limit(...)).

Using a dedicated module avoids circular imports between app.py and the routers.
"""
import logging

logger = logging.getLogger("RateLimiter")

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address

    limiter = Limiter(key_func=get_remote_address)
    SLOWAPI_AVAILABLE = True
    logger.debug("SlowAPI rate limiter initialised")
except ImportError:
    limiter = None  # type: ignore[assignment]
    RateLimitExceeded = None  # type: ignore[assignment, misc]
    _rate_limit_exceeded_handler = None  # type: ignore[assignment]
    SLOWAPI_AVAILABLE = False
    logger.warning("slowapi not installed — rate limiting disabled. Run: pip install slowapi")
