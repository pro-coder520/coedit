import pytest

from app.api.protocol import (
    ClearCanvasMessage,
    EraseCanvasMessage,
    RestoreCanvasMessage,
    StrokeMessage,
)
from app.db.session import Database
from app.services.canvas_store import CanvasStore


@pytest.mark.asyncio
async def test_canvas_store_sequences_strokes_and_clear_and_deduplicates() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    store = CanvasStore(database)
    stroke = StrokeMessage.model_validate(
        {
            "type": "stroke",
            "operation_id": {"client_id": "artist", "counter": 1},
            "color": "#db4f36",
            "width": 5,
            "points": [{"x": 0.2, "y": 0.3}, {"x": 0.6, "y": 0.8}],
        }
    )
    clear = ClearCanvasMessage.model_validate(
        {
            "type": "canvas_clear",
            "operation_id": {"client_id": "artist", "counter": 2},
        }
    )
    erase = EraseCanvasMessage.model_validate(
        {
            "type": "canvas_erase",
            "operation_id": {"client_id": "artist", "counter": 3},
            "target": {"client_id": "artist", "counter": 1},
        }
    )
    restore = RestoreCanvasMessage.model_validate(
        {
            "type": "canvas_restore",
            "operation_id": {"client_id": "artist", "counter": 4},
            "strokes": [
                {
                    "operation_id": {"client_id": "artist", "counter": 1},
                    "color": "#db4f36",
                    "width": 5,
                    "opacity": 1,
                    "points": [{"x": 0.2, "y": 0.3}, {"x": 0.6, "y": 0.8}],
                }
            ],
        }
    )

    assert await store.apply("board", stroke) == (True, 1)
    assert await store.apply("board", stroke) == (False, 1)
    assert await store.apply("board", clear) == (True, 2)
    assert await store.apply("board", erase) == (True, 3)
    assert await store.apply("board", restore) == (True, 4)
    sync = await store.sync("board")

    assert sync["last_seq"] == 4
    assert [entry["operation"]["type"] for entry in sync["operations"]] == [
        "stroke",
        "canvas_clear",
        "canvas_erase",
        "canvas_restore",
    ]
    await database.close()