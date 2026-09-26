from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, TypeAlias


ElementId: TypeAlias = tuple[str, int]


@dataclass(frozen=True, slots=True)
class Insert:
    element_id: ElementId
    after: ElementId | None
    value: str

    @property
    def operation_id(self) -> ElementId:
        return self.element_id


@dataclass(frozen=True, slots=True)
class Delete:
    operation_id: ElementId
    target: ElementId


Operation: TypeAlias = Insert | Delete


@dataclass(slots=True)
class _Node:
    value: str
    deleted: bool = False


class RGA:
    def __init__(self) -> None:
        self._nodes: dict[ElementId, _Node] = {}
        self._children: dict[ElementId | None, set[ElementId]] = {None: set()}
        self._operations: dict[ElementId, Operation] = {}
        self._pending: dict[ElementId, list[Operation]] = {}

    @property
    def text(self) -> str:
        result: list[str] = []
        stack = sorted(self._children[None], key=_id_order)

        while stack:
            element_id = stack.pop()
            node = self._nodes[element_id]
            if not node.deleted:
                result.append(node.value)
            stack.extend(sorted(self._children.get(element_id, ()), key=_id_order))

        return "".join(result)

    def apply(self, operation: Operation) -> bool:
        existing = self._operations.get(operation.operation_id)
        if existing is not None:
            if existing != operation:
                raise ValueError("operation ID was reused with different content")
            return False

        self._validate(operation)
        self._operations[operation.operation_id] = operation
        self._apply_or_defer(operation)
        return True

    def to_snapshot(self) -> dict[str, object]:
        return {
            "version": 1,
            "operations": [
                operation_to_data(operation)
                for operation in sorted(
                    self._operations.values(), key=lambda item: _id_order(item.operation_id)
                )
            ],
        }

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, object]) -> RGA:
        if snapshot.get("version") != 1:
            raise ValueError("unsupported RGA snapshot version")

        operations = snapshot.get("operations")
        if not isinstance(operations, list):
            raise ValueError("RGA snapshot operations must be a list")

        replica = cls()
        for data in operations:
            if not isinstance(data, dict):
                raise ValueError("RGA snapshot operation must be an object")
            replica.apply(operation_from_data(data))
        return replica

    def _validate(self, operation: Operation) -> None:
        if not operation.operation_id[0] or operation.operation_id[1] < 1:
            raise ValueError("operation IDs require a client ID and positive counter")

        if isinstance(operation, Insert):
            if len(operation.value) != 1:
                raise ValueError("insert operations must contain exactly one character")
            if operation.after == operation.element_id:
                raise ValueError("an element cannot be inserted after itself")

    def _apply_or_defer(self, operation: Operation) -> None:
        if isinstance(operation, Insert):
            if operation.after is not None and operation.after not in self._nodes:
                self._defer(operation.after, operation)
                return

            self._nodes[operation.element_id] = _Node(operation.value)
            self._children.setdefault(operation.after, set()).add(operation.element_id)
            self._children.setdefault(operation.element_id, set())
            self._apply_pending(operation.element_id)
            return

        node = self._nodes.get(operation.target)
        if node is None:
            self._defer(operation.target, operation)
            return

        node.deleted = True

    def _defer(self, dependency: ElementId, operation: Operation) -> None:
        self._pending.setdefault(dependency, []).append(operation)

    def _apply_pending(self, dependency: ElementId) -> None:
        pending = self._pending.pop(dependency, ())
        for operation in sorted(pending, key=lambda item: _id_order(item.operation_id)):
            self._apply_or_defer(operation)


def operation_to_data(operation: Operation) -> dict[str, object]:
    if isinstance(operation, Insert):
        return {
            "type": "insert",
            "element_id": _element_id_to_data(operation.element_id),
            "after": _element_id_to_data(operation.after),
            "value": operation.value,
        }

    return {
        "type": "delete",
        "operation_id": _element_id_to_data(operation.operation_id),
        "target": _element_id_to_data(operation.target),
    }


def operation_from_data(data: Mapping[str, object]) -> Operation:
    operation_type = data.get("type")
    if operation_type == "insert":
        element_id = _element_id_from_data(data.get("element_id"))
        after_data = data.get("after")
        value = data.get("value")
        if not isinstance(value, str):
            raise ValueError("insert operation value must be a string")
        return Insert(
            element_id=element_id,
            after=_element_id_from_data(after_data) if after_data is not None else None,
            value=value,
        )

    if operation_type == "delete":
        return Delete(
            operation_id=_element_id_from_data(data.get("operation_id")),
            target=_element_id_from_data(data.get("target")),
        )

    raise ValueError("unknown operation type")


def _element_id_to_data(element_id: ElementId | None) -> dict[str, object] | None:
    if element_id is None:
        return None
    return {"client_id": element_id[0], "counter": element_id[1]}


def _element_id_from_data(data: object) -> ElementId:
    if not isinstance(data, dict):
        raise ValueError("element ID must be an object")

    client_id = data.get("client_id")
    counter = data.get("counter")
    if not isinstance(client_id, str) or not isinstance(counter, int):
        raise ValueError("element ID has invalid fields")
    return client_id, counter


def _id_order(element_id: ElementId) -> tuple[int, str]:
    return element_id[1], element_id[0]