"""Redis-backed short-TTL cache for FHIR reads (SPEC.md §9 — cached p95 <= 300ms).

Separate from the circuit-breaker state in `client.py`: this cache is a pure
latency optimization (safe to flush/miss at any time), while the breaker's
Redis keys are correctness-critical shared fleet state. Keeping them in two
modules makes that distinction explicit even though both live in Redis.
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from banner_copilot.config import get_settings

_settings = get_settings()


def _key(resource_type: str, identifier: str) -> str:
    return f"fhir:cache:{resource_type}:{identifier}"


class FHIRCache:
    def __init__(self, redis: Redis, ttl_seconds: int | None = None) -> None:
        self._redis = redis
        self._ttl = ttl_seconds if ttl_seconds is not None else _settings.fhir_cache_ttl_seconds

    async def get(self, resource_type: str, identifier: str) -> Any | None:
        raw = await self._redis.get(_key(resource_type, identifier))
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, resource_type: str, identifier: str, value: Any) -> None:
        await self._redis.set(_key(resource_type, identifier), json.dumps(value), ex=self._ttl)
