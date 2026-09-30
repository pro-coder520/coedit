import json
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from app.crdt.rga import Delete, ElementId, Insert, Operation


class ElementIdMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1)
    counter: int = Field(gt=0)

    def as_element_id(self) -> ElementId:
        return (self.client_id, self.counter)


class InsertMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["insert"]
    element_id: ElementIdMessage
    after: ElementIdMessage | None
    value: str = Field(min_length=1, max_length=1)

    def as_operation(self) -> Insert:
        return Insert(
            element_id=self.element_id.as_element_id(),
            after=self.after.as_element_id() if self.after else None,
            value=self.value,
        )


class DeleteMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["delete"]
    operation_id: ElementIdMessage
    target: ElementIdMessage

    def as_operation(self) -> Delete:
        return Delete(
            operation_id=self.operation_id.as_element_id(),
            target=self.target.as_element_id(),
        )


class PresenceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["presence"]
    event: Literal["cursor", "typing"]
    value: object

    @model_validator(mode="after")
    def validate_value(self) -> PresenceMessage:
        if self.event == "cursor" and (type(self.value) is not int or self.value < 0):
            raise ValueError("cursor value must be a non-negative integer")
        if self.event == "typing" and type(self.value) is not bool:
            raise ValueError("typing value must be a boolean")
        return self


class PointMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class StrokeDataMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: ElementIdMessage
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    width: float = Field(ge=1, le=64)
    opacity: float = Field(default=1, ge=0.1, le=1)
    points: list[PointMessage] = Field(min_length=1, max_length=5000)


class StrokeMessage(StrokeDataMessage):
    type: Literal["stroke"]


class ClearCanvasMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["canvas_clear"]
    operation_id: ElementIdMessage


class EraseCanvasMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["canvas_erase"]
    operation_id: ElementIdMessage
    target: ElementIdMessage


class RestoreCanvasMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["canvas_restore"]
    operation_id: ElementIdMessage
    strokes: list[StrokeDataMessage] = Field(min_length=1, max_length=1000)


DrawingMessage: TypeAlias = Annotated[
    StrokeMessage | ClearCanvasMessage | EraseCanvasMessage | RestoreCanvasMessage,
    Field(discriminator="type"),
]
_drawing_adapter = TypeAdapter(DrawingMessage)


OperationMessage: TypeAlias = Annotated[
    InsertMessage | DeleteMessage,
    Field(discriminator="type"),
]
_operation_adapter = TypeAdapter(OperationMessage)


def parse_operation(message: str) -> tuple[OperationMessage, Operation] | None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict) or payload.get("type") not in {"insert", "delete"}:
        return None

    operation_message = _operation_adapter.validate_python(payload)
    return operation_message, operation_message.as_operation()


def parse_presence(message: str) -> PresenceMessage | None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict) or payload.get("type") != "presence":
        return None

    return PresenceMessage.model_validate(payload)


def parse_drawing(message: str) -> DrawingMessage | None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict) or payload.get("type") not in {
        "stroke",
        "canvas_clear",
        "canvas_erase",
        "canvas_restore",
    }:
        return None

    return _drawing_adapter.validate_python(payload)


def is_heartbeat(message: str) -> bool:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload == {"type": "ping"}