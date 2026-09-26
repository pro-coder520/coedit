import pytest

from app.crdt.rga import Insert
from app.db.session import Database
from app.services.document_store import DocumentStore


@pytest.mark.asyncio
async def test_remote_operation_updates_another_instances_cached_document() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    originating_store = DocumentStore(database)
    receiving_store = DocumentStore(database)

    await receiving_store.sync("shared-room", last_seq=0)
    first = Insert(element_id=("client-a", 1), after=None, value="A")
    accepted, sequence = await originating_store.apply("shared-room", first)
    assert accepted and sequence == 1

    await receiving_store.apply_remote("shared-room", first, sequence)
    second = Insert(
        element_id=("client-a", 2),
        after=("client-a", 1),
        value="B",
    )
    accepted, sequence = await receiving_store.apply("shared-room", second)

    assert accepted and sequence == 2
    sync = await receiving_store.sync("shared-room", last_seq=0)
    assert [item["seq"] for item in sync["operations"]] == [1, 2]

    await database.close()


@pytest.mark.asyncio
async def test_remote_sequence_gap_reloads_from_the_durable_log() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    originating_store = DocumentStore(database)
    receiving_store = DocumentStore(database)

    await receiving_store.sync("shared-room", last_seq=0)
    first = Insert(element_id=("client-a", 1), after=None, value="A")
    second = Insert(element_id=("client-a", 2), after=("client-a", 1), value="B")
    await originating_store.apply("shared-room", first)
    accepted, sequence = await originating_store.apply("shared-room", second)
    assert accepted and sequence == 2

    recovered = await receiving_store.apply_remote("shared-room", second, sequence)
    assert [item[0] for item in recovered] == [1, 2]
    third = Insert(element_id=("client-a", 3), after=("client-a", 2), value="C")
    accepted, sequence = await receiving_store.apply("shared-room", third)

    assert accepted and sequence == 3
    sync = await receiving_store.sync("shared-room", last_seq=0)
    assert [item["seq"] for item in sync["operations"]] == [1, 2, 3]

    await database.close()