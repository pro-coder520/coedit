from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.websocket import router as websocket_router
from app.db.session import Database
from app.realtime.connection_manager import ConnectionManager
from app.realtime.presence import PresenceTracker
from app.realtime.redis_broker import RedisBroker
from app.services.canvas_store import CanvasStore
from app.services.document_store import DocumentStore
from app.settings import Settings


def create_app(
    database_url: str | None = None,
    snapshot_interval: int | None = None,
    initialize_schema: bool = False,
) -> FastAPI:
    settings = Settings()
    database = Database(database_url or settings.database_url)
    store = DocumentStore(
        database,
        snapshot_interval if snapshot_interval is not None else settings.snapshot_interval,
    )
    canvas_store = CanvasStore(database)
    manager = ConnectionManager()
    broker = (
        RedisBroker(settings.redis_url, store, canvas_store, manager)
        if settings.redis_url is not None
        else None
    )
    presence = PresenceTracker(broker.redis if broker is not None else None)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            if initialize_schema:
                await database.create_schema()
            if broker is not None:
                await broker.start()
            yield
        finally:
            try:
                if broker is not None:
                    await broker.close()
            finally:
                await database.close()

    app = FastAPI(title="Coedit Backend", lifespan=lifespan)
    app.state.connection_manager = manager
    app.state.document_store = store
    app.state.canvas_store = canvas_store
    app.state.presence_tracker = presence
    app.state.database = database
    app.state.redis_broker = broker
    app.include_router(websocket_router)
    static_directory = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", StaticFiles(directory=static_directory, html=True), name="demo")
    return app


app = create_app()
