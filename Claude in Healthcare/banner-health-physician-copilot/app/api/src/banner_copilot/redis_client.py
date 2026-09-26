from collections.abc import AsyncGenerator

from redis.asyncio import ConnectionPool, Redis

from banner_copilot.config import get_settings

settings = get_settings()

redis_pool = ConnectionPool.from_url(settings.redis_url, decode_responses=True)


def get_redis_client() -> Redis:
    return Redis(connection_pool=redis_pool)


async def get_redis() -> AsyncGenerator[Redis, None]:
    client = get_redis_client()
    try:
        yield client
    finally:
        await client.aclose()
