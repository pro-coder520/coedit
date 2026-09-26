import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from app.crdt.rga import Operation, RGA, operation_from_data, operation_to_data
from app.db.models import DocumentRecord, OperationRecord
from app.db.session import Database


class DocumentStore:
    def __init__(self, database: Database, snapshot_interval: int = 100) -> None:
        if snapshot_interval < 1:
            raise ValueError("snapshot interval must be positive")

        self._database = database
        self._snapshot_interval = snapshot_interval
        self._documents: dict[str, RGA] = {}
        self._sequences: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def apply(self, room_id: str, operation: Operation) -> tuple[bool, int | None]:
        lock = self._locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            current = await self._load_locked(room_id)
            candidate = RGA.from_snapshot(current.to_snapshot())
            if not candidate.apply(operation):
                client_id, counter = operation.operation_id
                async with self._database.sessions() as session:
                    existing = await session.scalar(
                        select(OperationRecord).where(
                            OperationRecord.room_id == room_id,
                            OperationRecord.client_id == client_id,
                            OperationRecord.counter == counter,
                        )
                    )
                if existing is None:
                    return False, None
                if existing.payload != operation_to_data(operation):
                    raise ValueError("operation ID was reused with different content")
                return False, existing.seq

            payload = operation_to_data(operation)
            client_id, counter = operation.operation_id
            async with self._database.sessions() as session:
                if session.bind is not None and session.bind.dialect.name == "postgresql":
                    await session.execute(
                        postgres_insert(DocumentRecord)
                        .values(room_id=room_id, snapshot_seq=0, last_seq=0)
                        .on_conflict_do_nothing(index_elements=[DocumentRecord.room_id])
                    )
                elif await session.get(DocumentRecord, room_id) is None:
                    session.add(
                        DocumentRecord(
                            room_id=room_id,
                            snapshot=None,
                            snapshot_seq=0,
                            last_seq=0,
                        )
                    )
                    await session.flush()

                document = (
                    await session.execute(
                        select(DocumentRecord)
                        .where(DocumentRecord.room_id == room_id)
                        .with_for_update()
                    )
                ).scalar_one()

                cached_sequence = self._sequences.get(room_id, 0)
                if document.last_seq > cached_sequence:
                    rows = (
                        await session.scalars(
                            select(OperationRecord)
                            .where(
                                OperationRecord.room_id == room_id,
                                OperationRecord.seq > cached_sequence,
                            )
                            .order_by(OperationRecord.seq)
                        )
                    ).all()
                    for row in rows:
                        candidate.apply(operation_from_data(row.payload))

                existing = await session.scalar(
                    select(OperationRecord).where(
                        OperationRecord.room_id == room_id,
                        OperationRecord.client_id == client_id,
                        OperationRecord.counter == counter,
                    )
                )
                if existing is not None:
                    if existing.payload != payload:
                        raise ValueError("operation ID was reused with different content")
                    self._documents[room_id] = candidate
                    self._sequences[room_id] = document.last_seq
                    return False, existing.seq

                sequence = document.last_seq + 1
                document.last_seq = sequence
                session.add(
                    OperationRecord(
                        room_id=room_id,
                        seq=sequence,
                        client_id=client_id,
                        counter=counter,
                        payload=payload,
                    )
                )
                if sequence - document.snapshot_seq >= self._snapshot_interval:
                    document.snapshot = candidate.to_snapshot()
                    document.snapshot_seq = sequence
                await session.commit()

            self._documents[room_id] = candidate
            self._sequences[room_id] = sequence
            return True, sequence

    async def apply_remote(
        self,
        room_id: str,
        operation: Operation,
        sequence: int,
    ) -> list[tuple[int, Operation]]:
        lock = self._locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            current = await self._load_locked(room_id)
            current_sequence = self._sequences.get(room_id, 0)
            if sequence <= current_sequence:
                return []

            if sequence != current_sequence + 1:
                async with self._database.sessions() as session:
                    rows = (
                        await session.scalars(
                            select(OperationRecord)
                            .where(
                                OperationRecord.room_id == room_id,
                                OperationRecord.seq > current_sequence,
                                OperationRecord.seq <= sequence,
                            )
                            .order_by(OperationRecord.seq)
                        )
                    ).all()
                self._documents.pop(room_id, None)
                self._sequences.pop(room_id, None)
                await self._load_locked(room_id)
                return [
                    (row.seq, operation_from_data(row.payload)) for row in rows
                ]

            candidate = RGA.from_snapshot(current.to_snapshot())
            if not candidate.apply(operation):
                raise ValueError("remote operation duplicates local state at a new sequence")
            self._documents[room_id] = candidate
            self._sequences[room_id] = sequence
            return [(sequence, operation)]

    async def sync(self, room_id: str, last_seq: int) -> dict[str, Any]:
        lock = self._locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            await self._load_locked(room_id)
            async with self._database.sessions() as session:
                document = await session.get(DocumentRecord, room_id)
                if document is None:
                    return {
                        "type": "sync",
                        "snapshot": None,
                        "snapshot_seq": 0,
                        "last_seq": 0,
                        "operations": [],
                    }

                include_snapshot = (
                    document.snapshot is not None and last_seq < document.snapshot_seq
                )
                start_seq = document.snapshot_seq if include_snapshot else last_seq
                rows = (
                    await session.scalars(
                        select(OperationRecord)
                        .where(
                            OperationRecord.room_id == room_id,
                            OperationRecord.seq > start_seq,
                        )
                        .order_by(OperationRecord.seq)
                    )
                ).all()

                return {
                    "type": "sync",
                    "snapshot": document.snapshot if include_snapshot else None,
                    "snapshot_seq": document.snapshot_seq if include_snapshot else 0,
                    "last_seq": document.last_seq,
                    "operations": [
                        {"seq": row.seq, "operation": row.payload} for row in rows
                    ],
                }

    async def _load_locked(self, room_id: str) -> RGA:
        cached = self._documents.get(room_id)
        if cached is not None:
            return cached

        async with self._database.sessions() as session:
            document = await session.get(DocumentRecord, room_id)
            if document is None:
                state = RGA()
                sequence = 0
            else:
                sequence = document.last_seq
                state = (
                    RGA.from_snapshot(document.snapshot)
                    if document.snapshot is not None
                    else RGA()
                )
                rows = (
                    await session.scalars(
                        select(OperationRecord)
                        .where(
                            OperationRecord.room_id == room_id,
                            OperationRecord.seq > document.snapshot_seq,
                        )
                        .order_by(OperationRecord.seq)
                    )
                ).all()
                for row in rows:
                    state.apply(operation_from_data(row.payload))

        self._documents[room_id] = state
        self._sequences[room_id] = sequence
        return state