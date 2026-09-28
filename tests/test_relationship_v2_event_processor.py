"""Stage A3 tests: event processor routing + idempotency + fail-closed.

No live PostgreSQL/Redis: async ports are faked. Verifies the reality rule
(drafts never touch state), duplicate short-circuit, and scope/provenance
guards, plus boundary hygiene (no commerce/realtime imports).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from relationship_v2.domain.event import V2Event, V2EventType
from relationship_v2.services.event_processor import (
    PROCESSOR_NAME,
    ProcessorOutcome,
    process_event,
    route_event,
)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _ev(
    event_type: V2EventType = V2EventType.FAN_MESSAGE_RECEIVED,
    event_id: str = "e1",
) -> V2Event:
    return V2Event(
        event_id=event_id,
        event_type=event_type,
        creator_id=1,
        user_id=2,
        idempotency_key="k1",
        source="test",
    )


def test_drafts_discarded_pure() -> None:
    for t in (
        V2EventType.RESPONSE_GENERATED,
        V2EventType.RESPONSE_REJECTED,
        V2EventType.RESPONSE_EDITED,
        V2EventType.RESPONSE_APPROVED,
    ):
        r = route_event(_ev(t))
        assert r.outcome == ProcessorOutcome.DISCARDED
        assert r.reason == "candidate_not_event"


def test_mutating_routed() -> None:
    r = route_event(_ev(V2EventType.RESPONSE_SENT))
    assert r.outcome == ProcessorOutcome.APPLIED


def test_discard_performs_zero_io() -> None:
    calls: list[str] = []

    async def _never(*a, **k):  # type: ignore[no-untyped-def]
        calls.append("io")
        raise AssertionError("no I/O allowed for candidates")

    r = _run(
        process_event(
            _ev(V2EventType.RESPONSE_GENERATED),
            is_processed=_never,
            get_or_create=_never,
            create_event=_never,
            mark_processed=_never,
        )
    )
    assert r.outcome == ProcessorOutcome.DISCARDED
    assert calls == []


def test_duplicate_short_circuits_before_writes() -> None:
    calls: list[str] = []

    async def _is_processed(event_id: str, processor: str) -> bool:
        assert processor == PROCESSOR_NAME
        return True

    async def _never(*a, **k):  # type: ignore[no-untyped-def]
        calls.append("write")
        raise AssertionError("no writes on duplicate")

    r = _run(
        process_event(
            _ev(V2EventType.FAN_MESSAGE_RECEIVED),
            is_processed=_is_processed,
            get_or_create=_never,
            create_event=_never,
            mark_processed=_never,
        )
    )
    assert r.outcome == ProcessorOutcome.DUPLICATE
    assert calls == []


def test_applied_happy_path_fake_ports() -> None:
    seen: dict[str, object] = {}

    async def _is_processed(event_id: str, processor: str) -> bool:
        return False

    async def _get_or_create(creator_id: int, user_id: int, provenance: str) -> dict:
        seen["provenance"] = provenance
        return {"id": uuid4()}

    async def _create_event(**kwargs) -> dict:  # type: ignore[no-untyped-def]
        seen["event"] = kwargs
        return {"id": uuid4()}

    async def _mark(**kwargs) -> dict:  # type: ignore[no-untyped-def]
        seen["mark"] = kwargs
        return {"id": uuid4()}

    r = _run(
        process_event(
            _ev(V2EventType.RESPONSE_SENT, event_id="sent-1"),
            is_processed=_is_processed,
            get_or_create=_get_or_create,
            create_event=_create_event,
            mark_processed=_mark,
        )
    )
    assert r.outcome == ProcessorOutcome.APPLIED
    assert seen["provenance"] == "relationship_v2.services.event_processor"
    assert seen["event"]["producer"] == PROCESSOR_NAME
    assert seen["event"]["idempotency_key"] == "k1"
    assert seen["mark"]["processor"] == PROCESSOR_NAME
    assert seen["mark"]["event_id"] == "sent-1"


def test_provenance_required_rejects() -> None:
    async def _never(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("no I/O on reject")

    r = _run(
        process_event(
            _ev(),
            provenance="",
            is_processed=_never,
            get_or_create=_never,
            create_event=_never,
            mark_processed=_never,
        )
    )
    assert r.outcome == ProcessorOutcome.REJECTED


def test_purchase_event_applied_as_history_only() -> None:
    persisted: dict[str, object] = {}

    async def _is_processed(event_id: str, processor: str) -> bool:
        return False

    async def _get_or_create(creator_id: int, user_id: int, provenance: str) -> dict:
        return {"id": uuid4()}

    async def _create_event(**kwargs) -> dict:  # type: ignore[no-untyped-def]
        persisted.update(kwargs)
        return {"id": uuid4()}

    async def _mark(**kwargs) -> dict:  # type: ignore[no-untyped-def]
        return {"id": uuid4()}

    r = _run(
        process_event(
            _ev(V2EventType.PURCHASE_COMPLETED, event_id="p1"),
            is_processed=_is_processed,
            get_or_create=_get_or_create,
            create_event=_create_event,
            mark_processed=_mark,
        )
    )
    assert r.outcome == ProcessorOutcome.APPLIED
    assert persisted["event_type"] == "PurchaseCompleted"


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "event_processor.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert not any("ws_manager" in m or "event_subscriber" in m for m in mods)
    with pytest.raises(StopIteration):
        next(m for m in mods if m.startswith("chatbotv2.dashboard"))
