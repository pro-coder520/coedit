import json

import pytest

from app.crdt.rga import Insert
from app.db.session import Database
from app.realtime.redis_broker import RedisBroker
from app.services.document_store import DocumentStore


class BroadcastCollector:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def broadcast(self, room_id: str, message: str, sender=None) -> None:
        self.messages.append((room_id, message))


@pytest.mark.asyncio
async def test_broker_fans_out_remote_events_and_recovers_gaps() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    originating_store = DocumentStore(database)
    receiving_store = DocumentStore(database)
    manager = BroadcastCollector()
    broker = RedisBroker("redis://localhost:6379/0", receiving_store, manager)

    await receiving_store.sync("shared-room", last_seq=0)
    first = Insert(element_id=("client-a", 1), after=None, value="A")
    accepted, first_sequence = await originating_store.apply("shared-room", first)
    assert accepted and first_sequence == 1
    first_event = {
        "instance_id": "origin",
        "room_id": "shared-room",
        "seq": first_sequence,
        "operation": {"type": "insert", "element_id": {"client_id": "client-a", "counter": 1}, "after": None, "value": "A"},
    }

    await broker._handle_message(json.dumps(first_event))
    await broker._handle_message(json.dumps(first_event))

    assert [json.loads(message)["seq"] for _, message in manager.messages] == [1]

    second = Insert(element_id=("client-a", 2), after=("client-a", 1), value="B")
    third = Insert(element_id=("client-a", 3), after=("client-a", 2), value="C")
    await originating_store.apply("shared-room", second)
    accepted, third_sequence = await originating_store.apply("shared-room", third)
    assert accepted and third_sequence == 3
    third_event = {
        **first_event,
        "seq": third_sequence,
        "operation": {"type": "insert", "element_id": {"client_id": "client-a", "counter": 3}, "after": {"client_id": "client-a", "counter": 2}, "value": "C"},
    }

    await broker._handle_message(json.dumps(third_event))

    assert [json.loads(message)["seq"] for _, message in manager.messages] == [1, 2, 3]

    presence_event = {
        "instance_id": "origin",
        "kind": "presence",
        "room_id": "shared-room",
        "event": "cursor",
        "user_id": "alice",
        "value": 7,
    }
    await broker._handle_message(json.dumps(presence_event))
    assert json.loads(manager.messages[-1][1]) == {
        "type": "presence",
        "event": "cursor",
        "user_id": "alice",
        "value": 7,
    }

    await broker.close()
    await database.close()