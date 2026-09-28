"""Stage F10 tests: ordered replay with dry-run default and dedupe.

No live DB: processor ports faked. Proves: chronological application,
dry-run touches nothing, reruns duplicate, non-inbound skipped, scope
fail-closed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from relationship_v2.services.replay import ReplayMessage
from relationship_v2.services.replay_job import run_replay


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _row(mid: int, direction: str = "inbound", day: int = 20) -> ReplayMessage:
    return ReplayMessage(
        message_id=mid, user_id=2, direction=direction, content=f"msg {mid}",
        sent_at=datetime(2026, 9, day, 12, 0, tzinfo=UTC),
    )


class _Stores:
    def __init__(self) -> None:
        self.processed: set[str] = set()
        self.order: list[str] = []

    async def is_processed(self, event_id: str, processor: str) -> bool:
        return event_id in self.processed

    async def get_or_create(self, c: int, u: int, p: str) -> dict:
        return {"id": uuid4()}

    async def create(self, **kw) -> dict:
        self.order.append(kw["event_id"])
        return {"id": uuid4()}

    async def mark(self, **kw) -> dict:
        self.processed.add(kw["event_id"])
        return {"id": uuid4()}

    def ports(self) -> dict:
        return {
            "is_processed": self.is_processed,
            "get_or_create": self.get_or_create,
            "create_event": self.create,
            "mark_processed": self.mark,
        }


def test_apply_in_order() -> None:
    s = _Stores()
    rows = [_row(3, day=22), _row(1, day=20), _row(2, day=21)]
    rep = _run(run_replay(rows, 1, dry_run=False, **s.ports()))
    assert rep.scanned == 3 and rep.applied == 3 and rep.duplicates == 0
    assert rep.applied_ids == ["replay:msg:1", "replay:msg:2", "replay:msg:3"]


def test_dry_run_processes_nothing() -> None:
    async def _never(*a, **k):
        raise AssertionError("no writes in dry-run")

    rep = _run(
        run_replay(
            [_row(1), _row(2)], 1, is_processed=_never, get_or_create=_never,
            create_event=_never, mark_processed=_never,
        )
    )
    assert rep.dry_run is True
    assert rep.applied == 2 and rep.scanned == 2


def test_rerun_duplicates() -> None:
    s = _Stores()
    rows = [_row(5)]
    first = _run(run_replay(rows, 1, dry_run=False, **s.ports()))
    second = _run(run_replay(rows, 1, dry_run=False, **s.ports()))
    assert (first.applied, first.duplicates) == (1, 0)
    assert (second.applied, second.duplicates) == (0, 1)


def test_outbound_skipped() -> None:
    s = _Stores()
    rep = _run(
        run_replay([_row(6, direction="outbound"), _row(7)], 1,
                   dry_run=False, **s.ports())
    )
    assert rep.skipped_non_inbound == 1 and rep.applied == 1


def test_scope_fail_closed() -> None:
    with pytest.raises(ValueError):
        _run(run_replay([_row(1)], 0, dry_run=True))


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "replay_job.py"
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
