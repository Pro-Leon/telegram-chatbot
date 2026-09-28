"""Stage E2 tests: guarded sections + idempotency proofs.

No live Redis/PostgreSQL: locks and stores are faked (atomics modeled with
asyncio primitives). Proves: contention defers without running, release on
all paths, duplicate redelivery short-circuits, crash-retry converges to a
single logical mutation, concurrent same-event processing yields exactly
one APPLIED.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from relationship_v2.domain.event import V2Event, V2EventType
from relationship_v2.services.event_processor import ProcessorOutcome, process_event
from relationship_v2.services.turn_guard import run_guarded


def _run(coro):
    return asyncio.run(coro)


def _ev(event_id: str = "e1") -> V2Event:
    return V2Event(
        event_id=event_id,
        event_type=V2EventType.FAN_MESSAGE_RECEIVED,
        creator_id=1,
        user_id=2,
        idempotency_key="k1",
        source="test",
    )


class FakeStore:
    """In-DB UNIQUE semantics modeled with an asyncio lock."""

    def __init__(self) -> None:
        self._mu = asyncio.Lock()
        self.processed: set[str] = set()
        self.creates = 0
        self.marks = 0

    async def is_processed(self, event_id: str, processor: str) -> bool:
        async with self._mu:
            return event_id in self.processed

    async def get_or_create(self, c: int, u: int, p: str) -> dict:
        return {"id": uuid4()}

    async def create(self, **kw) -> dict:
        async with self._mu:
            self.creates += 1
            return {"id": uuid4()}

    async def mark(self, **kw) -> dict:
        async with self._mu:
            if kw["event_id"] in self.processed:
                return {"id": uuid4(), "inserted": False}
            self.marks += 1
            self.processed.add(kw["event_id"])
            return {"id": uuid4(), "inserted": True}


def _ports(store: FakeStore) -> dict:
    return {
        "is_processed": store.is_processed,
        "get_or_create": store.get_or_create,
        "create_event": store.create,
        "mark_processed": store.mark,
    }


def test_guarded_runs_and_releases() -> None:
    released: list[str] = []
    ran: list[str] = []

    async def _acquire(key: str, ttl: int) -> bool:
        return True

    async def _release(key: str) -> None:
        released.append(key)

    async def _fn() -> str:
        ran.append("yes")
        return "done"

    r = _run(run_guarded(1, 2, _fn, acquire=_acquire, release=_release))
    assert r.acquired is True and r.result == "done"
    assert ran == ["yes"] and released == ["v2:lock:1:2"]


def test_contended_defers_without_running() -> None:
    ran: list[str] = []

    async def _busy(key: str, ttl: int) -> bool:
        return False

    async def _fn() -> str:
        ran.append("yes")
        return "done"

    r = _run(run_guarded(1, 2, _fn, acquire=_busy))
    assert r.acquired is False and r.result is None
    assert ran == []


def test_release_on_fn_error_and_error_propagates() -> None:
    released: list[str] = []

    async def _acquire(key: str, ttl: int) -> bool:
        return True

    async def _release(key: str) -> None:
        released.append(key)

    async def _boom() -> str:
        raise RuntimeError("fn failed")

    with pytest.raises(RuntimeError):
        _run(run_guarded(1, 2, _boom, acquire=_acquire, release=_release))
    assert released == ["v2:lock:1:2"]


def test_guard_scope_fail_closed() -> None:
    async def _fn() -> str:
        return "x"

    with pytest.raises(ValueError):
        _run(run_guarded(0, 2, _fn))


def test_duplicate_redelivery_short_circuits() -> None:
    store = FakeStore()
    first = _run(process_event(_ev(), **_ports(store)))
    second = _run(process_event(_ev(), **_ports(store)))
    assert first.outcome == ProcessorOutcome.APPLIED
    assert second.outcome == ProcessorOutcome.DUPLICATE
    assert store.creates == 1 and store.marks == 1


def test_crash_between_persist_and_mark_converges() -> None:
    store = FakeStore()
    attempts = {"n": 0}
    real_mark = store.mark

    async def _flaky_mark(**kw) -> dict:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("crash after persist")
        return await real_mark(**kw)

    with pytest.raises(ConnectionError):
        _run(
            process_event(
                _ev(),
                is_processed=store.is_processed,
                get_or_create=store.get_or_create,
                create_event=store.create,
                mark_processed=_flaky_mark,
            )
        )
    retry = _run(process_event(_ev(), **_ports(store)))
    third = _run(process_event(_ev(), **_ports(store)))
    assert retry.outcome == ProcessorOutcome.APPLIED
    assert third.outcome == ProcessorOutcome.DUPLICATE
    # Re-persisted once (deduped by repo ON CONFLICT in prod), marked once.
    assert store.marks == 1


def test_concurrent_same_event_single_mutation() -> None:
    store = FakeStore()

    async def _both() -> tuple:
        return await asyncio.gather(
            process_event(_ev("conc-1"), **_ports(store)),
            process_event(_ev("conc-1"), **_ports(store)),
        )

    first, second = _run(_both())
    assert {first.outcome, second.outcome} == {
        ProcessorOutcome.APPLIED,
        ProcessorOutcome.DUPLICATE,
    }
    assert store.marks == 1


def test_lost_mark_race_reports_duplicate() -> None:
    store = FakeStore()

    async def _blind(event_id: str, processor: str) -> bool:
        # Both workers pass the pre-check (stale read); the UNIQUE insert
        # is the linearization point via the inserted flag.
        return False

    async def _both() -> tuple:
        ports = _ports(store)
        ports["is_processed"] = _blind
        return await asyncio.gather(
            process_event(_ev("race-1"), **ports),
            process_event(_ev("race-1"), **ports),
        )

    first, second = _run(_both())
    assert {first.outcome, second.outcome} == {
        ProcessorOutcome.APPLIED,
        ProcessorOutcome.DUPLICATE,
    }


def test_no_legacy_lock_keys() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "turn_guard.py"
    )
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # module docstring may name the legacy key it avoids
    literals = [
        n.value for n in ast.walk(ast.Module(body=body, type_ignores=[]))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    assert not any("lock:user:" in lit for lit in literals)
    assert "v2:lock:" in src  # own namespace only, via queue.lock_key
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
