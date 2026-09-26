import pytest

from app.realtime.presence import PresenceTracker


@pytest.mark.asyncio
async def test_user_remains_present_until_the_last_connection_leaves() -> None:
    tracker = PresenceTracker()
    await tracker.register("room", "connection-1", "alice")
    await tracker.register("room", "connection-2", "alice")

    assert await tracker.users("room") == ["alice"]
    assert not await tracker.disconnect("room", "connection-1", "alice")
    assert await tracker.users("room") == ["alice"]
    assert await tracker.disconnect("room", "connection-2", "alice")
    assert await tracker.users("room") == []