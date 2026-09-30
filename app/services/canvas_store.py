import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from app.api.protocol import DrawingMessage
from app.db.models import CanvasDocumentRecord, CanvasOperationRecord
from app.db.session import Database


class CanvasStore:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._sequences: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def apply(
        self,
        room_id: str,
        operation: DrawingMessage,
    ) -> tuple[bool, int]:
        lock = self._locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            payload = operation.model_dump(mode="json")
            client_id = operation.operation_id.client_id
            counter = operation.operation_id.counter
            async with self._database.sessions() as session:
                if session.bind is not None and session.bind.dialect.name == "postgresql":
                    await session.execute(
                        postgres_insert(CanvasDocumentRecord)
                        .values(room_id=room_id, last_seq=0)
                        .on_conflict_do_nothing(index_elements=[CanvasDocumentRecord.room_id])
                    )
                elif await session.get(CanvasDocumentRecord, room_id) is None:
                    session.add(CanvasDocumentRecord(room_id=room_id, last_seq=0))
                    await session.flush()

                document = (
                    await session.execute(
                        select(CanvasDocumentRecord)
                        .where(CanvasDocumentRecord.room_id == room_id)
                        .with_for_update()
                    )
                ).scalar_one()
                existing = await session.scalar(
                    select(CanvasOperationRecord).where(
                        CanvasOperationRecord.room_id == room_id,
                        CanvasOperationRecord.client_id == client_id,
                        CanvasOperationRecord.counter == counter,
                    )
                )
                if existing is not None:
                    if existing.payload != payload:
                        raise ValueError("canvas operation ID was reused with different content")
                    return False, existing.seq

                sequence = document.last_seq + 1
                document.last_seq = sequence
                session.add(
                    CanvasOperationRecord(
                        room_id=room_id,
                        seq=sequence,
                        client_id=client_id,
                        counter=counter,
                        payload=payload,
                    )
                )
                await session.commit()
            delivered_sequence = self._sequences.get(room_id, 0)
            if sequence == delivered_sequence + 1:
                self._sequences[room_id] = sequence
            return True, sequence

    async def sync(self, room_id: str) -> dict[str, Any]:
        async with self._database.sessions() as session:
            document = await session.get(CanvasDocumentRecord, room_id)
            if document is None:
                self._sequences[room_id] = 0
                return {"type": "canvas_sync", "last_seq": 0, "operations": []}

            rows = (
                await session.scalars(
                    select(CanvasOperationRecord)
                    .where(CanvasOperationRecord.room_id == room_id)
                    .order_by(CanvasOperationRecord.seq)
                )
            ).all()
            self._sequences[room_id] = document.last_seq
            return {
                "type": "canvas_sync",
                "last_seq": document.last_seq,
                "operations": [
                    {"seq": row.seq, "operation": row.payload} for row in rows
                ],
            }

    async def apply_remote(
        self,
        room_id: str,
        sequence: int,
        operation: dict[str, Any],
    ) -> list[tuple[int, dict[str, Any]]]:
        lock = self._locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            current_sequence = self._sequences.get(room_id, 0)
            if sequence <= current_sequence:
                return []
            if sequence == current_sequence + 1:
                self._sequences[room_id] = sequence
                return [(sequence, operation)]

            async with self._database.sessions() as session:
                rows = (
                    await session.scalars(
                        select(CanvasOperationRecord)
                        .where(
                            CanvasOperationRecord.room_id == room_id,
                            CanvasOperationRecord.seq > current_sequence,
                            CanvasOperationRecord.seq <= sequence,
                        )
                        .order_by(CanvasOperationRecord.seq)
                    )
                ).all()
            self._sequences[room_id] = sequence
            return [(row.seq, row.payload) for row in rows]