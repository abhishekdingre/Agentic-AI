"""Async FHIR R4 client: timeout/retry, a Redis-backed circuit breaker, and a
short-TTL Redis response cache (SPEC.md §7, §9).

Fetch shape (SPEC.md §7/§9): `Encounter` is an authorization gate, not just
another resource to fetch in parallel — callers must resolve `get_encounter()`
first (and run the RBAC ownership check) before calling
`fetch_linked_resources()` for the remaining <=19 resources. This module only
provides the two primitives; the two-phase *ordering* (gate, then parallel
remainder) is enforced by the orchestration layer that calls this client, not
by the client itself.

Circuit breaker state (failure counters, open/closed) lives in Redis, keyed
per resource type, NOT in-process — the API runs as multiple stateless
replicas (SPEC.md §9), and an in-process counter would let each replica
independently hammer a down FHIR server before ever tripping, and might never
trip at all under low per-replica traffic. A shared Redis counter makes the
breaker protect the FHIR server across the whole fleet.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from redis.asyncio import Redis
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt

from banner_copilot.config import get_settings
from banner_copilot.fhir.cache import FHIRCache
from banner_copilot.fhir.resources import bundle_entries
from banner_copilot.redis_client import get_redis_client

LINKED_RESOURCE_TYPES = (
    "Condition",
    "Observation",
    "MedicationRequest",
    "Procedure",
    "DocumentReference",
)


class FHIRError(Exception):
    """Base class for FHIR-client errors."""


class FHIRNotFoundError(FHIRError):
    """The requested resource does not exist (HTTP 404). Not a breaker/retry trigger."""


class FHIRUnavailableError(FHIRError):
    """Retries exhausted, or the breaker is open. Maps to `{status:"failed", reason:"fhir_unavailable"}`."""


class _RetryableFHIRError(FHIRError):
    """Internal: a transient failure (timeout/connection error/5xx) worth retrying."""


class _RedisCircuitBreaker:
    """Per-resource-type breaker: opens after N consecutive failures within a
    window, half-open retry after a cooldown.

    Simplification, noted deliberately: re-opening after the cooldown also
    requires the same N-consecutive-failures threshold (not a single
    half-open-trial failure) — simpler and still correct against SPEC.md's
    literal wording ("opens after 3 consecutive failures within 30s, half-open
    retry after 15s"), which does not separately specify half-open-trial
    sensitivity.
    """

    def __init__(self, redis: Redis, failure_threshold: int, reset_seconds: float) -> None:
        self._redis = redis
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds

    def _failures_key(self, resource_type: str) -> str:
        return f"fhir:breaker:{resource_type}:failures"

    def _open_key(self, resource_type: str) -> str:
        return f"fhir:breaker:{resource_type}:open"

    async def is_open(self, resource_type: str) -> bool:
        return await self._redis.get(self._open_key(resource_type)) is not None

    async def record_success(self, resource_type: str) -> None:
        await self._redis.delete(self._failures_key(resource_type))

    async def record_failure(self, resource_type: str) -> None:
        key = self._failures_key(resource_type)
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, 30)
        if count >= self._failure_threshold:
            await self._redis.set(self._open_key(resource_type), str(time.time()), ex=int(self._reset_seconds))
            await self._redis.delete(key)


def _is_retryable_status(status_code: int) -> bool:
    return status_code >= 500


class FHIRClient:
    def __init__(self, redis: Redis | None = None) -> None:
        settings = get_settings()
        self._settings = settings
        self._redis = redis or get_redis_client()
        self._cache = FHIRCache(self._redis, ttl_seconds=settings.fhir_cache_ttl_seconds)
        self._breaker = _RedisCircuitBreaker(
            self._redis,
            failure_threshold=settings.fhir_breaker_failure_threshold,
            reset_seconds=settings.fhir_breaker_reset_seconds,
        )
        self._http = httpx.AsyncClient(
            base_url=settings.fhir_base_url, timeout=settings.fhir_timeout_seconds
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, resource_type: str, params: dict | None = None) -> dict:
        """GET with breaker + retry. Raises `FHIRNotFoundError`/`FHIRUnavailableError`."""
        if await self._breaker.is_open(resource_type):
            raise FHIRUnavailableError(f"circuit open for {resource_type}")

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._settings.fhir_max_retries + 1),
                retry=retry_if_exception_type(_RetryableFHIRError),
                reraise=True,
            ):
                with attempt:
                    try:
                        response = await self._http.get(path, params=params)
                    except (httpx.TimeoutException, httpx.ConnectError) as exc:
                        raise _RetryableFHIRError(str(exc)) from exc

                    if response.status_code == 404:
                        raise FHIRNotFoundError(path)
                    if _is_retryable_status(response.status_code):
                        raise _RetryableFHIRError(f"{response.status_code} from {path}")
                    response.raise_for_status()
                    await self._breaker.record_success(resource_type)
                    return response.json()
        except _RetryableFHIRError as exc:
            await self._breaker.record_failure(resource_type)
            raise FHIRUnavailableError(str(exc)) from exc

        raise FHIRUnavailableError(f"exhausted retries for {resource_type}")  # pragma: no cover

    async def get_encounter(self, encounter_id: str) -> dict:
        """Fetch a single `Encounter` by id. The authorization gate — call this
        (and check ownership) before `fetch_linked_resources`."""
        cached = await self._cache.get("Encounter", encounter_id)
        if cached is not None:
            return cached
        resource = await self._get(f"/Encounter/{encounter_id}", "Encounter")
        await self._cache.set("Encounter", encounter_id, resource)
        return resource

    async def _search(self, resource_type: str, patient_id: str) -> list[dict]:
        cache_key = f"patient:{patient_id}"
        cached = await self._cache.get(resource_type, cache_key)
        if cached is not None:
            return cached
        bundle = await self._get(f"/{resource_type}", resource_type, params={"patient": patient_id})
        entries = bundle_entries(bundle)
        await self._cache.set(resource_type, cache_key, entries)
        return entries

    async def fetch_linked_resources(self, patient_id: str) -> dict[str, list[dict]]:
        """Fetch the remaining <=19 linked resources concurrently (SPEC.md §7/§9).

        Only call this after `get_encounter` + the RBAC ownership check have
        already passed — this is the parallel remainder of the two-phase fetch,
        not a substitute for the gate.
        """
        results = await asyncio.gather(
            *(self._search(resource_type, patient_id) for resource_type in LINKED_RESOURCE_TYPES)
        )
        return dict(zip(LINKED_RESOURCE_TYPES, results, strict=True))
