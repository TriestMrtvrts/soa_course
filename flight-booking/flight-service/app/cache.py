"""Cache-Aside on Redis Sentinel. Redis failures never break a request: the service falls back to the DB."""
import logging

from redis.exceptions import RedisError
from redis.sentinel import Sentinel

logger = logging.getLogger("flight.cache")


def flight_key(flight_id: str) -> str:
    return f"flight:{flight_id}"


def search_key(origin: str, destination: str, date: str | None) -> str:
    return f"search:{origin}:{destination}:{date or 'any'}"


class Cache:
    def __init__(self, sentinels: list[tuple[str, int]], master_name: str, ttl_seconds: int):
        self.ttl = ttl_seconds
        timeouts = {"socket_timeout": 0.5, "socket_connect_timeout": 0.5}
        self._sentinel = Sentinel(sentinels, sentinel_kwargs=timeouts, **timeouts)
        # master_for re-discovers the current master through the sentinels after a failover
        self._master = self._sentinel.master_for(master_name, **timeouts)

    def get(self, key: str) -> bytes | None:
        try:
            value = self._master.get(key)
        except RedisError as exc:
            logger.warning("cache unavailable", extra={"fields": {"key": key, "error": str(exc)}})
            return None
        logger.info("cache hit" if value is not None else "cache miss",
                    extra={"fields": {"cache": "hit" if value is not None else "miss", "key": key}})
        return value

    def set(self, key: str, value: bytes) -> None:
        try:
            self._master.set(key, value, ex=self.ttl)  # every key has a TTL
        except RedisError as exc:
            logger.warning("cache write failed", extra={"fields": {"key": key, "error": str(exc)}})

    def invalidate(self, *keys: str) -> None:
        try:
            self._master.delete(*keys)
            logger.info("cache invalidated", extra={"fields": {"keys": list(keys)}})
        except RedisError as exc:
            logger.error("cache invalidation failed", extra={"fields": {"keys": list(keys), "error": str(exc)}})
