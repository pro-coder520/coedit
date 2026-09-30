from fastapi.testclient import TestClient

from app.main import create_app


def _test_app(tmp_path):
    return create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite'}",
        initialize_schema=True,
    )


def _assert_initial_messages(websocket, join_events: int = 0) -> None:
    assert websocket.receive_json()["type"] == "sync"
    assert websocket.receive_json()["type"] == "canvas_sync"
    assert websocket.receive_json()["type"] == "presence_snapshot"
    for _ in range(join_events):
        event = websocket.receive_json()
        assert event["type"] == "presence"
        assert event["event"] == "join"


def test_broadcast_reaches_room_peers_without_echo_or_cross_room_leaks(tmp_path) -> None:
    with TestClient(_test_app(tmp_path)) as client:
        with (
            client.websocket_connect("/ws/alpha") as alpha_sender,
            client.websocket_connect("/ws/alpha") as alpha_peer,
            client.websocket_connect("/ws/beta") as beta_sender,
            client.websocket_connect("/ws/beta") as beta_peer,
        ):
            _assert_initial_messages(alpha_sender, join_events=1)
            _assert_initial_messages(alpha_peer, join_events=1)
            _assert_initial_messages(beta_sender, join_events=1)
            _assert_initial_messages(beta_peer, join_events=1)

            alpha_sender.send_text("alpha edit")
            assert alpha_peer.receive_text() == "alpha edit"

            beta_sender.send_text("beta edit")
            assert beta_peer.receive_text() == "beta edit"

            beta_peer.send_text("reply")
            assert beta_sender.receive_text() == "reply"


def test_valid_operation_updates_document_and_reaches_room_peers(tmp_path) -> None:
    app = _test_app(tmp_path)
    operation = {
        "type": "insert",
        "element_id": {"client_id": "client-a", "counter": 1},
        "after": None,
        "value": "A",
    }

    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws/document") as sender,
            client.websocket_connect("/ws/document") as peer,
        ):
            _assert_initial_messages(sender, join_events=1)
            _assert_initial_messages(peer, join_events=1)
            sender.send_json(operation)

            assert sender.receive_json() == {
                "type": "ack",
                "seq": 1,
                "operation_id": {"client_id": "client-a", "counter": 1},
            }
            assert peer.receive_json() == {
                "type": "operation",
                "seq": 1,
                "operation": operation,
            }

            sender.send_json(operation)
            assert sender.receive_json() == {
                "type": "ack",
                "seq": 1,
                "operation_id": {"client_id": "client-a", "counter": 1},
            }

            second_operation = {
                "type": "insert",
                "element_id": {"client_id": "client-a", "counter": 2},
                "after": {"client_id": "client-a", "counter": 1},
                "value": "B",
            }
            sender.send_json(second_operation)
            assert sender.receive_json()["seq"] == 2
            assert peer.receive_json() == {
                "type": "operation",
                "seq": 2,
                "operation": second_operation,
            }

        with client.websocket_connect("/ws/document?last_seq=0") as reconnect:
            sync = reconnect.receive_json()
            assert sync["last_seq"] == 2
            assert sync["operations"] == [
                {"seq": 1, "operation": operation},
                {"seq": 2, "operation": second_operation},
            ]


def test_malformed_operation_returns_error_without_broadcasting(tmp_path) -> None:
    with TestClient(_test_app(tmp_path)) as client:
        with client.websocket_connect("/ws/document") as sender:
            _assert_initial_messages(sender)
            sender.send_json(
                {
                    "type": "insert",
                    "element_id": {"client_id": "client-a", "counter": 1},
                    "after": None,
                    "value": "AB",
                }
            )

            assert sender.receive_json()["type"] == "error"


def test_presence_events_and_heartbeat_are_ephemeral(tmp_path) -> None:
    with TestClient(_test_app(tmp_path)) as client:
        with (
            client.websocket_connect("/ws/presence?user_id=alice") as alice,
            client.websocket_connect("/ws/presence?user_id=bob") as bob,
        ):
            _assert_initial_messages(alice, join_events=1)
            _assert_initial_messages(bob, join_events=1)

            alice.send_json({"type": "presence", "event": "cursor", "value": 4})
            assert bob.receive_json() == {
                "type": "presence",
                "event": "cursor",
                "user_id": "alice",
                "value": 4,
            }

            bob.send_json({"type": "presence", "event": "typing", "value": True})
            assert alice.receive_json() == {
                "type": "presence",
                "event": "typing",
                "user_id": "bob",
                "value": True,
            }

            alice.send_json({"type": "ping"})
            assert alice.receive_json() == {"type": "pong"}


def test_drawing_operations_are_shared_acknowledged_and_replayed(tmp_path) -> None:
    app = _test_app(tmp_path)
    stroke = {
        "type": "stroke",
        "operation_id": {"client_id": "artist-a", "counter": 1},
        "color": "#db4f36",
        "width": 5,
        "opacity": 1,
        "points": [{"x": 0.2, "y": 0.3}, {"x": 0.6, "y": 0.8}],
    }

    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws/board") as artist,
            client.websocket_connect("/ws/board") as viewer,
        ):
            _assert_initial_messages(artist, join_events=1)
            _assert_initial_messages(viewer, join_events=1)
            artist.send_json(stroke)

            assert artist.receive_json() == {
                "type": "canvas_ack",
                "seq": 1,
                "operation_id": {"client_id": "artist-a", "counter": 1},
            }
            assert viewer.receive_json() == {
                "type": "canvas_operation",
                "seq": 1,
                "operation": stroke,
            }

        with client.websocket_connect("/ws/board") as reconnect:
            assert reconnect.receive_json()["type"] == "sync"
            canvas_sync = reconnect.receive_json()
            assert canvas_sync == {
                "type": "canvas_sync",
                "last_seq": 1,
                "operations": [{"seq": 1, "operation": stroke}],
            }
            assert reconnect.receive_json()["type"] == "presence_snapshot"


def test_restart_sync_sends_snapshot_and_operations_after_snapshot(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'documents.sqlite'}"
    operations = [
        {
            "type": "insert",
            "element_id": {"client_id": "client-a", "counter": counter},
            "after": (
                {"client_id": "client-a", "counter": counter - 1}
                if counter > 1
                else None
            ),
            "value": value,
        }
        for counter, value in enumerate(("A", "B", "C"), start=1)
    ]

    with TestClient(
        create_app(database_url, snapshot_interval=2, initialize_schema=True)
    ) as client:
        with client.websocket_connect("/ws/document") as sender:
            _assert_initial_messages(sender)
            for operation in operations:
                sender.send_json(operation)
                assert sender.receive_json()["type"] == "ack"

    with TestClient(
        create_app(database_url, snapshot_interval=2, initialize_schema=True)
    ) as client:
        with client.websocket_connect("/ws/document?last_seq=0") as reconnect:
            sync = reconnect.receive_json()

            assert sync["last_seq"] == 3
            assert sync["snapshot_seq"] == 2
            assert sync["snapshot"] is not None
            assert sync["operations"] == [{"seq": 3, "operation": operations[2]}]

            from app.crdt.rga import RGA

            assert RGA.from_snapshot(sync["snapshot"]).text == "AB"
