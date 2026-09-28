"""Phase 2.4 lifecycle authority tests (no DB/LLM; mocked pool where needed)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_owner_unifies_luna_shape():
    from core.conversation_state import derive_lifecycle_state

    # Luna incident shape: 27 messages, 0 purchases, recent activity, funnel new.
    st = derive_lifecycle_state(
        funnel_stage="new",
        message_count=27,
        last_message_days_ago=0.1,
        purchase_count=0,
        last_purchase_days_ago=None,
        has_active_offer=False,
    )
    assert st.lifecycle == "established"
    assert st.funnel_stage == "new"
    assert st.relationship_state == "engaged"
    assert st.degraded is False


def test_owner_unknown_purchase_is_degraded_not_zero():
    from core.conversation_state import derive_lifecycle_state

    st = derive_lifecycle_state(
        funnel_stage="new",
        message_count=27,
        last_message_days_ago=0.1,
        purchase_count=None,
        has_active_offer=None,
    )
    assert st.degraded is True


def test_owner_never_raises():
    from core.conversation_state import derive_lifecycle_state

    st = derive_lifecycle_state(funnel_stage=None, message_count="xx", last_message_days_ago="yy")
    assert st.lifecycle == "new"
    assert st.degraded is True


def test_funnel_relationship_rank_locked():
    # Static map rank matches dynamic rules 8/9: warming+recent ⇒ ENGAGED,
    # engaged+recent ⇒ WARM (monotonic with funnel depth, not inverted).
    from commerce.relationship import derive_relationship_state

    assert (
        derive_relationship_state(
            funnel_stage="warming", message_count=10, last_message_days_ago=1.0
        ).value
        == "engaged"
    )
    assert (
        derive_relationship_state(
            funnel_stage="engaged", message_count=10, last_message_days_ago=1.0
        ).value
        == "warm"
    )


def test_funnel_transitions_and_idempotency():
    from core.conversation_state import derive_funnel_stage

    assert derive_funnel_stage("new", 4) is None
    assert derive_funnel_stage("new", 5) == "warming"
    assert derive_funnel_stage("new", 27) == "engaged"
    assert derive_funnel_stage("warming", 27) == "engaged"
    assert derive_funnel_stage("warming", 10) is None
    assert derive_funnel_stage("engaged", 99) is None
    assert derive_funnel_stage("converted", 99) is None
    assert derive_funnel_stage("vip", 99) is None
    assert derive_funnel_stage(None, 0) is None
    assert derive_funnel_stage("new", "xx") is None
    # Idempotent: applying the result and re-deriving yields None.
    nxt = derive_funnel_stage("new", 27)
    assert derive_funnel_stage(nxt, 27) is None


@pytest.mark.asyncio
async def test_funnel_writer_advances_and_skips():
    import db.postgres as _pg

    updated = []

    async def fake_update(uid, stage):
        updated.append((uid, stage))

    with (
        patch.object(_pg, "get_user", new=AsyncMock(return_value={"id": 1, "funnel_stage": "new"})),
        patch.object(_pg, "update_funnel_stage", side_effect=fake_update),
    ):
        assert await _pg.maybe_advance_funnel_stage(1, 27) == "engaged"
        assert await _pg.maybe_advance_funnel_stage(2, 3) is None
    assert updated == [(1, "engaged")]


@pytest.mark.asyncio
async def test_funnel_writer_never_demotes_terminal():
    import db.postgres as _pg

    updated = []

    async def fake_update(uid, stage):
        updated.append((uid, stage))

    with (
        patch.object(
            _pg, "get_user", new=AsyncMock(return_value={"id": 9, "funnel_stage": "converted"})
        ),
        patch.object(_pg, "update_funnel_stage", side_effect=fake_update),
    ):
        assert await _pg.maybe_advance_funnel_stage(9, 99) is None
    assert updated == []


@pytest.mark.asyncio
async def test_summarizer_gate_and_catchup():
    import memory.summarizer as _s

    calls = []

    async def fake_summarize(uid, count, creator_id=None, source_count=None):
        calls.append((uid, count, source_count))
        return "s"

    async def run(count, watermark):
        calls.clear()
        with (
            patch("db.postgres.get_summary_watermark", new=AsyncMock(return_value=watermark)),
            patch.object(_s, "summarize_conversation", side_effect=fake_summarize),
        ):
            await _s.maybe_summarize(7, count, creator_id=42)
        return list(calls)

    assert await run(19, 0) == []
    assert await run(20, 0) == [(7, 20, 20)]
    assert await run(21, 0) == [(7, 21, 21)]
    assert await run(40, 20) == [(7, 40, 40)]
    assert await run(41, 20) == [(7, 41, 41)]
    # Missed-multiple catch-up: jump 19 → 41 summarizes exactly once.
    assert await run(41, 0) == [(7, 41, 41)]
    # Fresh watermark suppresses.
    assert await run(41, 41) == []


@pytest.mark.asyncio
async def test_summarizer_failure_tagged_and_retried():
    import memory.summarizer as _s

    metrics = []

    def fake_metric(**kwargs):
        metrics.append(kwargs)

    with (
        patch.object(_s, "get_latest_summary", new=AsyncMock(return_value="old")),
        patch.object(_s, "get_recent_messages", new=AsyncMock(return_value=[])),
        patch.object(
            _s,
            "get_llm_provider",
            return_value=MagicMock(generate=AsyncMock(side_effect=RuntimeError("llm down"))),
        ),
        patch("commerce.production_control.record_metric", side_effect=fake_metric),
    ):
        out = await _s.summarize_conversation(7, 25, creator_id=42)
    assert out == "old"
    assert any(m.get("name") == "summarize_failed" for m in metrics)
