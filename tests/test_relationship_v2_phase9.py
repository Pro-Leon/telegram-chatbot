"""Phase 9 unit tests: locks, dedupe contracts, DLQ, retry bounds.

No live Redis/Postgres (fake ports + pure helpers). Covers
IDEMPOTENCY_AND_CONCURRENCY.md duplicate/concurrency rules.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from relationship_v2.domain.response import ValidationOutcome, ValidationVerdict
from relationship_v2.services.queue import (
    BACKOFF_MAX_SECONDS,
    LOCK_TTL_SECONDS,
    MAX_REPLAY_ATTEMPTS,
    acquire_scope_lock,
    backoff_seconds,
    enqueue_validated_turn,
    is_stale_memory_write,
    lock_key,
    record_turn_failure,
    release_scope_lock,
    should_replay,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_lock_namespace_is_v2_never_legacy() -> None:
    key = lock_key(1, 2)
    assert key.startswith("v2:lock:")
    assert "lock:user:" not in key


def test_lock_ttl_bounded_and_scope_fail_closed() -> None:
    with pytest.raises(ValueError):
        _run(acquire_scope_lock(0, 2))
    with pytest.raises(ValueError):
        _run(acquire_scope_lock(1, 2, ttl_seconds=0))
    with pytest.raises(ValueError):
        _run(acquire_scope_lock(1, 2, ttl_seconds=9999))


def test_fake_lock_port_contention_defers() -> None:
    held: set[str] = set()

    async def fake_acquire(key: str, ttl: int) -> bool:
        if key in held:
            return False
        held.add(key)
        return True

    async def fake_release(key: str) -> None:
        held.discard(key)

    assert _run(acquire_scope_lock(1, 2, acquire=fake_acquire)) is True
    # Contention defers (no double-process), never raises.
    assert _run(acquire_scope_lock(1, 2, acquire=fake_acquire)) is False
    _run(release_scope_lock(1, 2, release=fake_release))
    assert _run(acquire_scope_lock(1, 2, acquire=fake_acquire)) is True


def test_backoff_bounded() -> None:
    assert backoff_seconds(1) == 2
    assert backoff_seconds(2) == 4
    assert backoff_seconds(100) == BACKOFF_MAX_SECONDS
    with pytest.raises(ValueError):
        backoff_seconds(0)


def test_replay_limits_suppress_beyond_max() -> None:
    assert should_replay(1) is True
    assert should_replay(MAX_REPLAY_ATTEMPTS) is True
    assert should_replay(MAX_REPLAY_ATTEMPTS + 1) is False
    assert should_replay(0) is False


def test_stale_memory_write_gated() -> None:
    now = datetime.now(UTC)
    assert is_stale_memory_write(now, now - timedelta(seconds=1)) is True
    assert is_stale_memory_write(now - timedelta(seconds=1), now) is False
    with pytest.raises(ValueError):
        is_stale_memory_write({"unorderable": True}, now)


def test_enqueue_rejects_non_valid_verdict_before_db() -> None:
    from uuid import uuid4

    verdict = ValidationVerdict(outcome=ValidationOutcome.NEEDS_REVIEW, reason="soft")
    with pytest.raises(ValueError, match="only VALID"):
        _run(
            enqueue_validated_turn(
                1, 2, uuid4(), uuid4(), "gen-1", "evt-1", "hash", verdict, "auto"
            )
        )


def test_failure_record_requires_reason_before_db() -> None:
    from uuid import uuid4

    with pytest.raises(ValueError, match="reason"):
        _run(record_turn_failure(1, 2, uuid4(), uuid4(), "gen-1", "", 1))


def test_lock_ttl_default_bounded() -> None:
    assert 1 <= LOCK_TTL_SECONDS <= 300


def test_phase9_no_legacy_lock_keys_or_v1_imports() -> None:
    import ast

    target = REPO_ROOT / "relationship_v2" / "services" / "queue.py"
    text = target.read_text(encoding="utf-8")
    assert "lock:user:{" not in text
    assert '"lock:user:"' not in text and "'lock:user:'" not in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if mod.startswith("relationship_v2") or mod in ("db.postgres", "db", "db.redis"):
                continue
            assert not (mod == "commerce" or mod.startswith("commerce.")), mod
            assert "core.one_call" not in mod and mod != "core.routing", mod
            assert "integrations.dropfans" not in mod, mod
