import json
from uuid import uuid4

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.protocol import is_heartbeat, parse_drawing, parse_operation, parse_presence
from app.crdt.rga import operation_to_data
from app.realtime.connection_manager import ConnectionManager
from app.realtime.presence import PresenceTracker
from app.services.document_store import DocumentStore
from app.services.canvas_store import CanvasStore

router = APIRouter()


@router.websocket("/ws/{room_id}")
async def websocket_room(
    websocket: WebSocket,
    room_id: str,
    last_seq: int = Query(default=0, ge=0),
    user_id: str = Query(default="anonymous", min_length=1, max_length=128),
) -> None:
    manager: ConnectionManager = websocket.app.state.connection_manager
    store: DocumentStore = websocket.app.state.document_store
    canvas_store: CanvasStore = websocket.app.state.canvas_store
    presence: PresenceTracker = websocket.app.state.presence_tracker
    broker = websocket.app.state.redis_broker
    connection_id = uuid4().hex
    await manager.connect(room_id, websocket)
    registered = False

    try:
        await presence.register(room_id, connection_id, user_id)
        registered = True
        await websocket.send_json(await store.sync(room_id, last_seq))
        await websocket.send_json(await canvas_store.sync(room_id))
        await websocket.send_json(
            {"type": "presence_snapshot", "users": await presence.users(room_id)}
        )
        await manager.activate(websocket)
        await manager.broadcast(
            room_id,
            json.dumps({"type": "presence", "event": "join", "user_id": user_id}),
            sender=websocket,
        )
        if broker is not None:
            await broker.publish_presence(room_id, "join", user_id)

        while True:
            message = await websocket.receive_text()

            if is_heartbeat(message):
                await presence.heartbeat(room_id, connection_id, user_id)
                await websocket.send_json({"type": "pong"})
                continue

            try:
                presence_message = parse_presence(message)
            except ValidationError as error:
                await websocket.send_json(
                    {"type": "error", "detail": error.errors(include_input=False)}
                )
                continue

            if presence_message is not None:
                event = {
                    "type": "presence",
                    "event": presence_message.event,
                    "user_id": user_id,
                    "value": presence_message.value,
                }
                await manager.broadcast(room_id, json.dumps(event), sender=websocket)
                if broker is not None:
                    await broker.publish_presence(
                        room_id,
                        presence_message.event,
                        user_id,
                        presence_message.value,
                    )
                continue

            try:
                drawing_message = parse_drawing(message)
            except ValidationError as error:
                await websocket.send_json(
                    {"type": "error", "detail": error.errors(include_input=False)}
                )
                continue

            if drawing_message is not None:
                drawing_operation = drawing_message.model_dump(mode="json")
                try:
                    accepted, sequence = await canvas_store.apply(room_id, drawing_message)
                except ValueError as error:
                    await websocket.send_json({"type": "error", "detail": str(error)})
                    continue

                if accepted:
                    if broker is not None:
                        await broker.publish_drawing(room_id, sequence, drawing_operation)
                    await manager.broadcast(
                        room_id,
                        json.dumps(
                            {
                                "type": "canvas_operation",
                                "seq": sequence,
                                "operation": drawing_operation,
                            }
                        ),
                        sender=websocket,
                    )
                await websocket.send_json(
                    {
                        "type": "canvas_ack",
                        "seq": sequence,
                        "operation_id": drawing_message.operation_id.model_dump(mode="json"),
                    }
                )
                continue

            try:
                parsed_operation = parse_operation(message)
            except ValidationError as error:
                await websocket.send_json(
                    {"type": "error", "detail": error.errors(include_input=False)}
                )
                continue

            if parsed_operation is None:
                await manager.broadcast(room_id, message, sender=websocket)
                continue

            _, operation = parsed_operation
            try:
                accepted, sequence = await store.apply(room_id, operation)
            except ValueError as error:
                await websocket.send_json({"type": "error", "detail": str(error)})
                continue

            if accepted:
                broker = websocket.app.state.redis_broker
                if broker is not None and sequence is not None:
                    await broker.publish(room_id, sequence, operation)
                await manager.broadcast(
                    room_id,
                    json.dumps(
                        {
                            "type": "operation",
                            "seq": sequence,
                            "operation": operation_to_data(operation),
                        }
                    ),
                    sender=websocket,
                )
            if sequence is not None:
                await websocket.send_json(
                    {
                        "type": "ack",
                        "seq": sequence,
                        "operation_id": {
                            "client_id": operation.operation_id[0],
                            "counter": operation.operation_id[1],
                        },
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(room_id, websocket)
        if registered:
            last_connection = await presence.disconnect(room_id, connection_id, user_id)
            if last_connection:
                leave_message = json.dumps(
                    {"type": "presence", "event": "leave", "user_id": user_id}
                )
                await manager.broadcast(room_id, leave_message)
                if broker is not None:
                    await broker.publish_presence(room_id, "leave", user_id)
