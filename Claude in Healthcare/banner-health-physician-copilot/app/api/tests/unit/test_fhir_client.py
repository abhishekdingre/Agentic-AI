"""Unit tests for `fhir.client.FHIRClient`: cache hits, 404 vs transient-failure
handling, retry-then-succeed, and the Redis-backed circuit breaker tripping
after N consecutive failures (SPEC.md §7).

Uses a hand-rolled in-memory fake Redis (get/set/delete/incr/expire only —
the small subset FHIRClient/FHIRCache actually call) instead of a live Redis
server, and `respx` to mock the FHIR HTTP endpoints — no live services
required, deterministic, no TTL/timing dependency.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from banner_copilot.config import get_settings
from banner_copilot.fhir.client import FHIRClient, FHIRNotFoundError, FHIRUnavailableError

settings = get_settings()


class FakeRedis:
    """Minimal async fake covering exactly what FHIRCache/_RedisCircuitBreaker call."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def incr(self, key: str) -> int:
        value = int(self._store.get(key, "0")) + 1
        self._store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        pass  # TTL not simulated — tests never rely on real expiry timing.


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def client(fake_redis: FakeRedis) -> FHIRClient:
    return FHIRClient(redis=fake_redis)


ENCOUNTER_JSON = {"resourceType": "Encounter", "id": "encounter-1", "status": "finished"}


@pytest.mark.asyncio
@respx.mock
async def test_get_encounter_cache_hit_skips_http(client: FHIRClient, fake_redis: FakeRedis) -> None:
    import json

    await fake_redis.set("fhir:cache:Encounter:encounter-1", json.dumps(ENCOUNTER_JSON))
    # No respx route registered at all — a real HTTP attempt would raise.
    result = await client.get_encounter("encounter-1")
    assert result == ENCOUNTER_JSON


@pytest.mark.asyncio
@respx.mock
async def test_get_encounter_success_populates_cache(client: FHIRClient, fake_redis: FakeRedis) -> None:
    route = respx.get(f"{settings.fhir_base_url}/Encounter/encounter-1").mock(
        return_value=httpx.Response(200, json=ENCOUNTER_JSON)
    )
    result = await client.get_encounter("encounter-1")
    assert result == ENCOUNTER_JSON
    assert route.call_count == 1
    assert await fake_redis.get("fhir:cache:Encounter:encounter-1") is not None


@pytest.mark.asyncio
@respx.mock
async def test_get_encounter_not_found_does_not_trip_breaker(
    client: FHIRClient, fake_redis: FakeRedis
) -> None:
    respx.get(f"{settings.fhir_base_url}/Encounter/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(FHIRNotFoundError):
        await client.get_encounter("missing")
    assert await fake_redis.get("fhir:breaker:Encounter:failures") is None
    assert await fake_redis.get("fhir:breaker:Encounter:open") is None


@pytest.mark.asyncio
@respx.mock
async def test_transient_failure_then_success_within_retry_budget(
    client: FHIRClient, fake_redis: FakeRedis
) -> None:
    route = respx.get(f"{settings.fhir_base_url}/Encounter/encounter-1")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json=ENCOUNTER_JSON)]
    result = await client.get_encounter("encounter-1")
    assert result == ENCOUNTER_JSON
    assert route.call_count == 2
    # Resolved within the retry budget -> never counted as a breaker failure.
    assert await fake_redis.get("fhir:breaker:Encounter:failures") is None


@pytest.mark.asyncio
@respx.mock
async def test_breaker_opens_after_threshold_and_short_circuits(
    client: FHIRClient, fake_redis: FakeRedis
) -> None:
    route = respx.get(f"{settings.fhir_base_url}/Encounter/encounter-1").mock(
        return_value=httpx.Response(503)
    )

    for _ in range(settings.fhir_breaker_failure_threshold):
        with pytest.raises(FHIRUnavailableError):
            await client.get_encounter("encounter-1")

    calls_before_open = route.call_count
    assert calls_before_open == settings.fhir_breaker_failure_threshold * (
        settings.fhir_max_retries + 1
    )
    assert await fake_redis.get("fhir:breaker:Encounter:open") is not None

    # Breaker is now open: no further HTTP calls, fails fast.
    with pytest.raises(FHIRUnavailableError):
        await client.get_encounter("encounter-1")
    assert route.call_count == calls_before_open


@pytest.mark.asyncio
@respx.mock
async def test_fetch_linked_resources_runs_concurrently(client: FHIRClient) -> None:
    empty_bundle = {"resourceType": "Bundle", "type": "searchset", "entry": []}
    for resource_type in ("Condition", "Observation", "MedicationRequest", "Procedure", "DocumentReference"):
        respx.get(f"{settings.fhir_base_url}/{resource_type}", params={"patient": "patient-1"}).mock(
            return_value=httpx.Response(200, json=empty_bundle)
        )

    results = await client.fetch_linked_resources("patient-1")
    assert set(results.keys()) == {
        "Condition",
        "Observation",
        "MedicationRequest",
        "Procedure",
        "DocumentReference",
    }
    assert all(value == [] for value in results.values())
