"""Stage F3 tests: replay normalization, ordering, and event mapping.

Pure normalizer, no infra. Proves: inbound rows normalize with stable
keys, outbound rows excluded (sent history not invented), ordering is
chronological, reruns dedupe, fail-closed scopes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from relationship_v2.domain.event import V2EventType
from relationship_v2.services.replay import (
    ReplayMessage,
    normalize_message,
    order_for_replay,
    replay_key,
    text_hash,
    to_fan_received,
)


def _row(mid: int, direction: str = "inbound", content: str = "hey there") -> ReplayMessage:
    return ReplayMessage(
        message_id=mid,
        user_id=2,
        direction=direction,
        content=content,
        telegram_message_id=1000 + mid,
        sent_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
    )


def test_inbound_normalizes_with_stable_key() -> None:
    ev = normalize_message(_row(7))
    assert ev is not None
    assert ev.idempotency_key == "replay:msg:7"
    assert ev.inbound_event_id == "replay:msg:7"
    assert ev.user_id == 2
    assert ev.text_length == len("hey there")
    assert normalize_message(_row(7)) == ev


def test_outbound_excluded() -> None:
    assert normalize_message(_row(8, direction="outbound")) is None


def test_blank_content_excluded() -> None:
    assert normalize_message(_row(9, content="   ")) is None


def test_ordering_chronological() -> None:
    a = _row(1)
    b = ReplayMessage(
        message_id=2, user_id=2, direction="inbound", content="later",
        sent_at=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )
    assert [m.message_id for m in order_for_replay([b, a])] == [1, 2]


def test_replay_key_fail_closed() -> None:
    with pytest.raises(ValueError):
        replay_key(0)
    assert replay_key(42) == "replay:msg:42"


def test_text_hash_stable_and_bounded() -> None:
    assert text_hash("hello") == text_hash("hello")
    assert len(text_hash("hello")) == 16


def test_to_fan_received_maps_processor_event() -> None:
    inbound = normalize_message(_row(11))
    assert inbound is not None
    ev = to_fan_received(inbound, creator_id=5)
    assert ev.event_type == V2EventType.FAN_MESSAGE_RECEIVED
    assert ev.creator_id == 5 and ev.user_id == 2
    assert ev.idempotency_key == "replay:msg:11"
    assert ev.payload["replay"] is True
    assert "content" not in ev.payload


def test_to_fan_received_fail_closed() -> None:
    inbound = normalize_message(_row(12))
    assert inbound is not None
    with pytest.raises(ValueError):
        to_fan_received(inbound, creator_id=0)


def test_end_to_end_rerun_dedupes() -> None:
    from relationship_v2.services.event_processor import ProcessorOutcome, process_event

    store: dict[str, dict] = {}

    async def _is_processed(event_id: str, processor: str) -> bool:
        return event_id in store

    async def _get_or_create(c: int, u: int, p: str) -> dict:
        from uuid import uuid4

        return {"id": uuid4()}

    async def _create(**kw) -> dict:
        from uuid import uuid4

        return {"id": uuid4()}

    async def _mark(**kw) -> dict:
        from uuid import uuid4

        store[kw["event_id"]] = {"id": uuid4()}
        return store[kw["event_id"]]

    import asyncio

    async def _run_twice():
        ev = to_fan_received(normalize_message(_row(13)), creator_id=1)
        assert ev is not None
        first = await process_event(
            ev, is_processed=_is_processed, get_or_create=_get_or_create,
            create_event=_create, mark_processed=_mark,
        )
        ev2 = to_fan_received(normalize_message(_row(13)), creator_id=1)
        second = await process_event(
            ev2, is_processed=_is_processed, get_or_create=_get_or_create,
            create_event=_create, mark_processed=_mark,
        )
        return first, second

    first, second = asyncio.run(_run_twice())
    assert first.outcome == ProcessorOutcome.APPLIED
    assert second.outcome == ProcessorOutcome.DUPLICATE


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "replay.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert "relationship_v2.persistence.repository" not in mods
