"""Small process-local fixed-window limiter; production can replace this dependency."""

from collections import OrderedDict, deque
from functools import lru_cache
from threading import Lock
from time import monotonic
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status


class RateLimiter:
    def __init__(self, *, max_buckets: int = 10_000) -> None:
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._max_buckets = max_buckets
        self._lock = Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = monotonic()
        threshold = now - window_seconds
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_buckets:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)
            while events and events[0] <= threshold:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return RateLimiter()


Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


def client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def enforce(limiter: RateLimiter, key: str, *, limit: int, window_seconds: int) -> None:
    if not limiter.allow(key, limit=limit, window_seconds=window_seconds):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limit_exceeded", "message": "Too many requests"},
            headers={"Retry-After": str(window_seconds)},
        )
