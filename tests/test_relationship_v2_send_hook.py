"""Stage F4 tests: send hook flag-gating, identity, and fail-open behavior.

No live transport/DB: processor ports faked. Proves: flag off -> None with
zero I/O, flag on -> ResponseSent APPLIED through the real processor path,
no stable identity -> None, port explosion -> None, both main.py sites wired.
"""

from __future__ import annotations

import pathlib
from uuid import uuid4

import pytest

from relationship_v2.integration.send_hook import note_send_confirmed
from relationship_v2.services.event_processor import ProcessorOutcome

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _ports(store: dict, fail: bool = False) -> dict:
    async def _is_processed(event_id: str, processor: str) -> bool:
        return event_id in store

    async def _get_or_create(c: int, u: int, p: str) -> dict:
        return {"id": uuid4()}

    async def _create(**kw) -> dict:
        if fail:
            raise ConnectionError("db down")
        return {"id": uuid4()}

    async def _mark(**kw) -> dict:
        store[kw["event_id"]] = True
        return {"id": uuid4()}

    return {
        "is_processed": _is_processed,
        "get_or_create": _get_or_create,
        "create_event": _create,
        "mark_processed": _mark,
    }


def test_flag_off_no_io() -> None:
    async def _never(*a, **k):
        raise AssertionError("no I/O while flag is off")

    r = _run(
        note_send_confirmed(
            1, 2, 99, "gen-1",
            create_event=_never, mark_processed=_never,
            is_processed=_never, get_or_create=_never,
        )
    )
    assert r is None


def test_flag_on_applies_sent(monkeypatch) -> None:
    monkeypatch.setenv("RELATIONSHIP_V2_WRITE_ENABLED", "true")
    store: dict = {}
    r = _run(note_send_confirmed(1, 2, 99, "gen-1", **_ports(store)))
    assert r is not None and r.outcome == ProcessorOutcome.APPLIED
    assert "sent-tg-99" in store


def test_no_stable_identity_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("RELATIONSHIP_V2_WRITE_ENABLED", "true")
    r = _run(note_send_confirmed(1, 2, None, "gen-1", **_ports({})))
    assert r is None


def test_port_failure_fail_open(monkeypatch) -> None:
    monkeypatch.setenv("RELATIONSHIP_V2_WRITE_ENABLED", "true")
    r = _run(note_send_confirmed(1, 2, 100, "gen-2", **_ports({}, fail=True)))
    assert r is None


def test_bad_scope_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("RELATIONSHIP_V2_WRITE_ENABLED", "true")
    r = _run(note_send_confirmed(0, 2, 101, "gen-3", **_ports({})))
    assert r is None


def test_both_transport_sites_wired() -> None:
    src = (REPO_ROOT / "chatbotv2" / "main.py").read_text(encoding="utf-8")
    assert src.count("note_send_confirmed as _note_v2_sent") == 2
    assert src.count("Relationship V2 send hook (Stage F4)") == 2


def test_no_protected_imports() -> None:
    import ast

    path = REPO_ROOT / "relationship_v2" / "integration" / "send_hook.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert not any(m.startswith(("chatbotv2.", "workers.")) for m in mods)
    with pytest.raises(StopIteration):
        next(m for m in mods if "ws_manager" in m or "telethon" in m.lower())
