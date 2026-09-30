# Coedit Backend

FastAPI WebSockets carry character-level RGA operations and vector drawing events. PostgreSQL stores sequenced text operations, snapshots, and canvas history; Redis provides cross-instance fanout and expiring presence leases. SQLite is the default for a single local instance.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --ws-ping-interval 20 --ws-ping-timeout 20
```

Open `http://127.0.0.1:8000` for the collaborative board. Draw with the pen, marker, or eraser; choose a color and brush size, or switch to **Write** for shared text. The color-assist toggle switches to a color-blind-friendly palette. The browser creates a stable client ID and restores text and canvas operations after reconnect.

Set `DATABASE_URL` before running Alembic or the server to use PostgreSQL, for example `postgresql+asyncpg://postgres:postgres@localhost:5432/coedit`. Set `REDIS_URL=redis://localhost:6379/0` to enable shared presence and cross-instance broadcasts. Snapshots are written every 100 operations by default; override with `SNAPSHOT_INTERVAL`. The append-only operation log is retained for recovery.

## WebSocket protocol

Connect to `/ws/{room_id}?user_id=alice&last_seq=0`. The server sends `sync`, `canvas_sync`, and `presence_snapshot` messages. Text sync contains a snapshot when the requested sequence predates it, followed by operations after that snapshot or requested sequence. Canvas sync replays sequenced strokes and clear events. Accepted writes receive a durable-sequence acknowledgement; retries of the same operation ID receive the original sequence without rebroadcast.

An insert contains one character and references the preceding character ID, or `null` at the start:

```json
{"type":"insert","element_id":{"client_id":"alice","counter":1},"after":null,"value":"H"}
```

A delete tombstones its target and has its own unique operation ID:

```json
{"type":"delete","operation_id":{"client_id":"alice","counter":2},"target":{"client_id":"alice","counter":1}}
```

Clients should choose counters greater than any operation counter they have observed, and never reuse an operation ID. Cursor and typing messages are ephemeral. The `user_id` query value is a demo identity, not authentication.

A stroke uses normalized canvas points, a hex color, and a brush width:

```json
{"type":"stroke","operation_id":{"client_id":"alice","counter":3},"color":"#0072b2","width":6,"opacity":1,"points":[{"x":0.2,"y":0.3},{"x":0.6,"y":0.8}]}
```

Clear the board with `{"type":"canvas_clear","operation_id":{"client_id":"alice","counter":4}}`. Stroke and clear operations are persisted and replayed on reconnect.

## Multi-instance demo

Run `docker compose up --build`. Connect one browser to `http://127.0.0.1:8001` and another to `http://127.0.0.1:8002`; both should use the same room to exercise Redis fanout across API instances. PostgreSQL data is stored in a named Compose volume.

## Test

```powershell
python -m pytest
```

## CI and delivery

GitHub Actions runs the tests against Postgres and Redis on pull requests and pushes. A push to `main` publishes the verified container image to GitHub Container Registry (`ghcr.io`). Configure the repository's default branch as `main`; the publish job uses `GITHUB_TOKEN` with package-write permission and needs no extra secret. This publishes an image but does not deploy it to a running host.
