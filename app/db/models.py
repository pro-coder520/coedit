from typing import Any

from sqlalchemy import ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentRecord(Base):
    __tablename__ = "documents"

    room_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    snapshot_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OperationRecord(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint(
            "room_id", "client_id", "counter", name="uq_operation_client_counter"
        ),
    )

    room_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("documents.room_id", ondelete="CASCADE"),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    counter: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)