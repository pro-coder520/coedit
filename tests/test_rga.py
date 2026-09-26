from itertools import permutations

import pytest

from app.crdt.rga import Delete, Insert, RGA


def test_all_delivery_orders_converge_with_duplicates_and_tombstones() -> None:
    first = Insert(element_id=("a", 1), after=None, value="A")
    second = Insert(element_id=("b", 1), after=("a", 1), value="B")
    concurrent = Insert(element_id=("c", 1), after=("a", 1), value="C")
    descendant = Insert(element_id=("d", 1), after=("b", 1), value="D")
    delete = Delete(operation_id=("x", 1), target=("b", 1))
    operations = [first, second, concurrent, descendant, delete]

    for delivery_order in permutations(operations):
        replica = RGA()
        for operation in delivery_order:
            assert replica.apply(operation)
            assert not replica.apply(operation)

        assert replica.text == "ACD"


def test_insert_waits_for_missing_parent() -> None:
    replica = RGA()
    child = Insert(element_id=("b", 1), after=("a", 1), value="B")

    assert replica.apply(child)
    assert replica.text == ""

    replica.apply(Insert(element_id=("a", 1), after=None, value="A"))
    assert replica.text == "AB"


def test_newer_sibling_insert_appears_before_existing_successor() -> None:
    replica = RGA()
    replica.apply(Insert(element_id=("client", 1), after=None, value="A"))
    replica.apply(Insert(element_id=("client", 2), after=("client", 1), value="B"))
    replica.apply(Insert(element_id=("client", 3), after=("client", 1), value="X"))

    assert replica.text == "AXB"


def test_lamport_counter_orders_cross_client_cursor_insertions() -> None:
    replica = RGA()
    replica.apply(Insert(element_id=("z-client", 4), after=None, value="A"))
    replica.apply(
        Insert(element_id=("z-client", 5), after=("z-client", 4), value="B")
    )
    replica.apply(Insert(element_id=("a-client", 6), after=("z-client", 4), value="X"))

    assert replica.text == "AXB"


def test_delete_waits_for_missing_target() -> None:
    replica = RGA()
    deletion = Delete(operation_id=("b", 1), target=("a", 1))

    assert replica.apply(deletion)
    replica.apply(Insert(element_id=("a", 1), after=None, value="A"))

    assert replica.text == ""


def test_reusing_operation_id_with_different_content_is_rejected() -> None:
    replica = RGA()
    replica.apply(Insert(element_id=("a", 1), after=None, value="A"))

    with pytest.raises(ValueError, match="reused"):
        replica.apply(Insert(element_id=("a", 1), after=None, value="B"))


def test_snapshot_round_trip_preserves_tombstones_and_pending_operations() -> None:
    replica = RGA()
    replica.apply(Insert(element_id=("b", 1), after=("a", 1), value="B"))
    replica.apply(Delete(operation_id=("x", 1), target=("a", 1)))
    replica.apply(Insert(element_id=("a", 1), after=None, value="A"))

    restored = RGA.from_snapshot(replica.to_snapshot())

    assert restored.text == replica.text
    assert restored.text == "B"
    assert not restored.apply(Insert(element_id=("b", 1), after=("a", 1), value="B"))