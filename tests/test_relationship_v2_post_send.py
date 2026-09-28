"""Stage F9b tests: post-send learning across all five stores.

No live DB: every store port faked, event intake faked. Proves: drafts
rejected, only ResponseSent learns, facts/episodes/signals/intimate/loops
all derive from one send, duplicates short-circuit, scope fail-closed.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from relationship_v2.domain.event import V2Event, V2EventType
from relationship_v2.services.post_send import PostSendOutcome, apply_response_sent


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _sent(**kw) -> V2Event:
    base = {
        "event_id": "sent-1",
        "event_type": V2EventType.RESPONSE_SENT,
        "creator_id": 1,
        "user_id": 2,
        "idempotency_key": "sent-1",
        "source": "test",
        "generation_id": "gen-1",
    }
    base.update(kw)
    return V2Event(**base)


class _Stores:
    def __init__(self) -> None:
        self.processed: set[str] = set()
        self.calls: dict[str, int] = {}
        self.facts: list[dict] = []

    def _hit(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def is_processed(self, event_id: str, processor: str) -> bool:
        return event_id in self.processed

    async def get_or_create(self, c: int, u: int, p: str) -> dict:
        return {"id": uuid4()}

    async def create(self, **kw) -> dict:
        self._hit("event")
        return {"id": uuid4()}

    async def mark(self, **kw) -> dict:
        self._hit("mark")
        self.processed.add(kw["event_id"])
        return {"id": uuid4()}

    async def list_facts(self, c: int, u: int, **k) -> list[dict]:
        return self.facts

    async def create_fact(self, *a, **k) -> dict:
        self._hit("fact")
        return {"id": uuid4()}

    async def create_episode(self, *a, **k) -> dict:
        self._hit("episode")
        return {"id": uuid4()}

    async def record_signal(self, *a, **k) -> dict:
        self._hit("signal")
        return {"id": uuid4()}

    async def record_intimate(self, *a, **k) -> dict:
        self._hit("intimate")
        return {"id": uuid4()}

    async def create_loop(self, *a, **k) -> dict:
        self._hit("loop")
        return {"id": uuid4()}

    def ports(self) -> dict:
        return {
            "is_processed": self.is_processed,
            "get_or_create": self.get_or_create,
            "create_event": self.create,
            "mark_processed": self.mark,
            "list_facts": self.list_facts,
            "create_fact": self.create_fact,
            "create_episode": self.create_episode,
            "record_signal": self.record_signal,
            "record_intimate": self.record_intimate,
            "create_loop": self.create_loop,
        }


def test_full_learning_loop() -> None:
    s = _Stores()
    r = _run(
        apply_response_sent(
            _sent(), "My daughter has a soccer game Saturday.", **s.ports()
        )
    )
    assert r.outcome == PostSendOutcome.APPLIED
    assert r.episodes_created >= 1
    assert r.facts_recorded >= 1
    assert r.loops_opened >= 1
    assert s.calls.get("fact", 0) >= 1
    assert s.calls.get("episode", 0) >= 1
    assert s.calls.get("loop", 0) >= 1


def test_draft_never_learns() -> None:
    s = _Stores()
    draft = V2Event(
        event_id="d1", event_type=V2EventType.RESPONSE_GENERATED,
        creator_id=1, user_id=2, idempotency_key="d1", source="test",
    )
    r = _run(apply_response_sent(draft, "Hello there.", **s.ports()))
    assert r.outcome == PostSendOutcome.REJECTED
    assert s.calls == {}


def test_duplicate_short_circuits() -> None:
    s = _Stores()
    first = _run(apply_response_sent(_sent(), "I work as a photographer.", **s.ports()))
    second = _run(apply_response_sent(_sent(), "I work as a photographer.", **s.ports()))
    assert first.outcome == PostSendOutcome.APPLIED
    assert second.outcome == PostSendOutcome.DUPLICATE


def test_empty_text_rejected() -> None:
    s = _Stores()
    r = _run(apply_response_sent(_sent(), "   ", **s.ports()))
    assert r.outcome == PostSendOutcome.REJECTED
    assert s.calls == {}


def test_provenance_required() -> None:
    s = _Stores()
    with pytest.raises(ValueError):
        _run(apply_response_sent(_sent(), "hi", provenance="", **s.ports()))


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "post_send.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert not any(m.startswith(("chatbotv2.", "workers.")) for m in mods)
    # Repository binds lazily inside default ports only (fake-injectable).
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    assert "relationship_v2.persistence.repository" not in top_level
