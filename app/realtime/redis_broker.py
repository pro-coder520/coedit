import asyncio
import json
import logging
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.crdt.rga import Operation, operation_from_data, operation_to_data
from app.realtime.connection_manager import ConnectionManager
from app.services.document_store import DocumentStore


logger = logging.getLogger(__name__)


class RedisBroker:
    operation_channel = "coedit:operations"
    presence_channel = "coedit:presence"

    def __init__(
        self,
        url: str,
        store: DocumentStore,
        manager: ConnectionManager,
    ) -> None:
        self.instance_id = uuid4().hex
        self._redis = Redis.from_url(url, decode_responses=True)
        self._store = store
        self._manager = manager
        self._listener: asyncio.Task[None] | None = None
        self._subscribed = asyncio.Event()

    @property
    def redis(self) -> Redis:
        return self._redis

    async def start(self) -> None:
        await self._redis.ping()
        self._listener = asyncio.create_task(self._listen())
        await asyncio.wait_for(self._subscribed.wait(), timeout=10)

    async def publish(self, room_id: str, sequence: int, operation: Operation) -> None:
        event = {
            "instance_id": self.instance_id,
            "room_id": room_id,
            "seq": sequence,
            "operation": operation_to_data(operation),
        }
        try:
            await self._redis.publish(self.operation_channel, json.dumps(event))
        except RedisError:
            logger.exception("Failed to publish operation for room %s", room_id)

    async def publish_presence(
        self,
        room_id: str,
        event_name: str,
        user_id: str,
        value: object = None,
    ) -> None:
        event = {
            "instance_id": self.instance_id,
            "kind": "presence",
            "room_id": room_id,
            "event": event_name,
            "user_id": user_id,
            "value": value,
        }
        try:
            await self._redis.publish(self.presence_channel, json.dumps(event))
        except RedisError:
            logger.exception("Failed to publish presence for room %s", room_id)

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
        await self._redis.aclose()

    async def _listen(self) -> None:
        while True:
            try:
                async with self._redis.pubsub() as pubsub:
                    await pubsub.subscribe(self.operation_channel, self.presence_channel)
                    self._subscribed.set()
                    async for message in pubsub.listen():
                        if message.get("type") == "message":
                            try:
                                await self._handle_message(message["data"])
                            except Exception:
                                logger.exception("Failed to process Redis event")
            except asyncio.CancelledError:
                raise
            except RedisError:
                logger.exception("Redis subscriber disconnected; retrying")
                await asyncio.sleep(1)

    async def _handle_message(self, payload: str) -> None:
        try:
            event = json.loads(payload)
            if event.get("instance_id") == self.instance_id:
                return

            room_id = event["room_id"]
            if event.get("kind") == "presence":
                event_name = event["event"]
                if event_name not in {"join", "leave", "cursor", "typing"}:
                    return
                message = {
                    "type": "presence",
                    "event": event_name,
                    "user_id": event["user_id"],
                }
                if "value" in event:
                    message["value"] = event["value"]
                await self._manager.broadcast(room_id, json.dumps(message))
                return

            sequence = event["seq"]
            operation = operation_from_data(event["operation"])
            recovered = await self._store.apply_remote(room_id, operation, sequence)
            for recovered_sequence, recovered_operation in recovered:
                await self._manager.broadcast(
                    room_id,
                    json.dumps(
                        {
                            "type": "operation",
                            "seq": recovered_sequence,
                            "operation": operation_to_data(recovered_operation),
                        }
                    ),
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.exception("Ignoring malformed Redis operation event")