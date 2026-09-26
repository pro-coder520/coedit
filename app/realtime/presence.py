import time
from urllib.parse import quote

from redis.asyncio import Redis


class PresenceTracker:
    def __init__(self, redis: Redis | None = None, lease_seconds: int = 60) -> None:
        self._redis = redis
        self._lease_seconds = lease_seconds
        self._local: dict[str, dict[str, str]] = {}

    async def register(self, room_id: str, connection_id: str, user_id: str) -> None:
        if self._redis is None:
            self._local.setdefault(room_id, {})[connection_id] = user_id
            return

        connections_key, users_key = self._keys(room_id)
        pipeline = self._redis.pipeline()
        pipeline.zadd(connections_key, {connection_id: time.time() + self._lease_seconds})
        pipeline.hset(users_key, connection_id, user_id)
        pipeline.expire(connections_key, self._lease_seconds * 2)
        pipeline.expire(users_key, self._lease_seconds * 2)
        await pipeline.execute()

    async def heartbeat(self, room_id: str, connection_id: str, user_id: str) -> None:
        await self.register(room_id, connection_id, user_id)

    async def disconnect(
        self,
        room_id: str,
        connection_id: str,
        user_id: str,
    ) -> bool:
        if self._redis is None:
            connections = self._local.get(room_id, {})
            connections.pop(connection_id, None)
            if not connections:
                self._local.pop(room_id, None)
            return user_id not in connections.values()

        connections_key, users_key = self._keys(room_id)
        pipeline = self._redis.pipeline()
        pipeline.zrem(connections_key, connection_id)
        pipeline.hdel(users_key, connection_id)
        await pipeline.execute()
        return user_id not in await self.users(room_id)

    async def users(self, room_id: str) -> list[str]:
        if self._redis is None:
            return sorted(set(self._local.get(room_id, {}).values()))

        connections_key, users_key = self._keys(room_id)
        await self._redis.zremrangebyscore(connections_key, "-inf", time.time())
        connection_ids = await self._redis.zrange(connections_key, 0, -1)
        if not connection_ids:
            await self._redis.delete(connections_key, users_key)
            return []

        tracked_ids = await self._redis.hkeys(users_key)
        stale_ids = set(tracked_ids).difference(connection_ids)
        if stale_ids:
            await self._redis.hdel(users_key, *stale_ids)
        user_ids = await self._redis.hmget(users_key, connection_ids)
        return sorted({user_id for user_id in user_ids if user_id is not None})

    def _keys(self, room_id: str) -> tuple[str, str]:
        encoded_room = quote(room_id, safe="")
        prefix = f"coedit:presence:{encoded_room}"
        return f"{prefix}:connections", f"{prefix}:users"