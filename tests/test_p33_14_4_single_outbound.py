"""P3.3.14.4 control-flow hardening — single-outbound gate tests.

Proves the sealed PPV success path suppresses only the redundant normal
send/operator handoff for the same turn, while every other state lets
normal conversation proceed. No live DB/provider I/O.
"""

import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commerce.opportunity_sealing import SealResult

pytestmark = [pytest.mark.unit]

WORKER_PATH = Path(__file__).parent.parent / "workers" / "llm_worker.py"
EXEC_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_execution.py"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _reason(**over):
    base = {
        "v": 1,
        "definition_id": 11,
        "definition_version": 1,
        "stable_key": "alpha",
        "sealed_at": "2026-06-01T12:00:00+00:00",
        "verified_at": "2026-06-01T12:00:00+00:00",
        "verified_hash": "abc123",
        "allow_download": True,
        "verifier_version": "p33.13.v1",
    }
    base.update(over)
    return json.dumps(base, sort_keys=True)


def _offer(**over):
    base = {
        "id": 42,
        "creator_id": 1,
        "user_id": 10,
        "link": "https://www.dropfans.io/buy/dpfn_A",
        "price_minor": 1999,
        "currency": "USD",
        "vault_item_ids": ["V1", "V2"],
        "media_count": 2,
        "dropfans_product_id": "dpfn_A",
        "reason": _reason(),
        "state": "pending",
    }
    base.update(over)
    return base


def _fan_state():
    from commerce.fan_commercial_state import FanCommercialState

    return FanCommercialState(
        creator_id=1,
        user_id=10,
        purchase_count=0,
        total_spend_minor=0,
        average_order_value_minor=None,
        highest_purchase_minor=None,
        last_purchase_at=None,
        recent_purchase_count=0,
        recent_spend_minor=0,
        purchased_vault_ids=frozenset(),
        delivered_vault_ids=(),
        recent_offer_count=0,
        recent_rejected_offer_count=0,
        last_offer_at=None,
        recent_offered_vault_ids=(),
        currency="USD",
    )


def _history():
    from commerce.offer_history import OfferHistory

    return OfferHistory(
        creator_id=1,
        user_id=10,
        total_offer_count=0,
        recent_offer_count=0,
        last_offer_at=None,
        declined_offer_count=0,
        recent_declined_offer_count=0,
        state_counts=(),
        has_active_offer=False,
        active_offer_count=0,
        offered_vault_sets=(),
        active_vault_sets=(),
        null_snapshot_count=0,
    )


def _definition_row(**over):
    row = {
        "creator_id": 1,
        "id": 11,
        "stable_key": "alpha",
        "version": 1,
        "offer_type": "SINGLE",
        "canonical_vault_item_ids": ["V1"],
        "family_id": None,
        "price_minor": 1999,
        "currency": "USD",
        "allow_download": True,
        "status": "active",
    }
    row.update(over)
    return row


def _conversation_dict():
    return {
        "lifecycle": "established",
        "identity_already_established": True,
        "current_topic": "movie",
        "recent_topics": ("movie",),
        "open_threads": ("movie",),
    }


def _opp_result(mapped):
    from commerce.opportunity import candidate_from_definition
    from commerce.opportunity_engine import OpportunityEngineResult
    from commerce.opportunity_ranking import OpportunityRankingResult, RankedCandidate

    if mapped is None:
        return OpportunityEngineResult(
            creator_id=1,
            user_id=10,
            evaluated_at=NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            owned_vault_ids=frozenset(),
            candidates=(),
            eligible_candidates=(),
            ineligible=(),
            ranking_inputs=(),
            ranking_result=None,
            selected_candidate=None,
            has_opportunity=False,
            status="NO_CANDIDATES",
        )
    cand = candidate_from_definition(1, 10, _definition_row(), mapped_drop_ids=mapped)
    ranked = RankedCandidate(11, "alpha", 1, ("STABLE_ID_TIEBREAK",), True, 0)
    ranking = OpportunityRankingResult(
        evaluated_at=NOW, creator_id=1, user_id=10, ranked=(ranked,), selected=ranked
    )
    return OpportunityEngineResult(
        creator_id=1,
        user_id=10,
        evaluated_at=NOW,
        fan_commercial_state=_fan_state(),
        offer_history=_history(),
        owned_vault_ids=frozenset(),
        candidates=(cand,),
        eligible_candidates=(cand,),
        ineligible=(),
        ranking_inputs=(),
        ranking_result=ranking,
        selected_candidate=cand,
        has_opportunity=True,
        status="RANKED",
    )


class _SendWorld:
    """Fake send-stream world tracking sealed vs normal enqueues."""

    def __init__(self, reserve_behavior="ok", enqueue_exc=None, dedup_value="1"):
        self.reserve_behavior = reserve_behavior
        self.enqueue_exc = enqueue_exc
        self.dedup_value = dedup_value
        self.sealed_enqueues = []
        self.normal_enqueues = []
        self.reserves = []
        self.confirms = []
        self.releases = []
        self.get_values = []

    async def fake_reserve(self, dedup_id, creator_id=None, **kw):
        self.reserves.append((dedup_id, creator_id))
        if self.reserve_behavior == "raise":
            raise RuntimeError("redis down")
        if self.reserve_behavior == "dup":
            return None
        return "reserved:tok-test"

    async def fake_sealed_enqueue(
        self, payload, dedup_id=None, generation_id=None, creator_id=None, **kw
    ):
        self.sealed_enqueues.append((dict(payload), dedup_id, generation_id, creator_id))
        if self.enqueue_exc is not None:
            raise self.enqueue_exc
        return "msg-sealed"

    async def fake_normal_enqueue(
        self, payload, dedup_id=None, generation_id=None, creator_id=None, **kw
    ):
        self.normal_enqueues.append((dict(payload), dedup_id, generation_id, creator_id))
        return "msg-normal"

    async def fake_confirm(self, dedup_id, creator_id=None, token=None, **kw):
        self.confirms.append((dedup_id, creator_id, token))
        return True

    async def fake_release(self, dedup_id, creator_id=None, token=None, **kw):
        self.releases.append((dedup_id, creator_id, token))
        return True

    async def fake_get_value(self, dedup_id, creator_id=None, **kw):
        self.get_values.append((dedup_id, creator_id))
        return self.dedup_value


async def _run_process_message(
    monkeypatch,
    *,
    opp_mapped=("dpfn_A",),
    seal_status="SEALED",
    world=None,
    score=0.9,
    flags=None,
    seal_offer=None,
):
    """Drive the real process_message with a sealed-capable mocked world."""
    from commerce.single_creator import SingleCreatorStatus

    world = world or _SendWorld()
    flags = flags if flags is not None else []
    counts = {"evaluate": 0, "seal": 0, "commerce_draft": 0, "operator": 0}
    published = []

    async def fake_evaluate(*a, **kw):
        counts["evaluate"] += 1
        return _opp_result(opp_mapped)

    async def fake_seal(candidate, ranking_result, creator_id, user_id, cuid, **kw):
        counts["seal"] += 1
        if seal_status == "SEALED":
            return SealResult(status="SEALED", offer=dict(seal_offer or _offer()))
        return SealResult(status=seal_status, subreason="X", offer=None)

    async def fake_commerce_draft(*a, **kw):
        counts["commerce_draft"] += 1
        return None

    async def fake_add_queue(**kw):
        counts["operator"] += 1
        return 55

    async def fake_publish(event_type, data, **kw):
        published.append({"event_type": event_type, "data": data, **kw})
        return "evt-1"

    async def fake_publish_batch(events):
        for ev in events:
            published.append(
                {
                    "event_type": ev["event"],
                    "data": ev["data"],
                    **{k: v for k, v in ev.items() if k not in ("event", "data")},
                }
            )
        return ["evt-b"]

    mock_creator = MagicMock()
    mock_creator.status = SingleCreatorStatus.READY
    mock_creator.creator_id = 1

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                "db.dropfans.get_dropfans_integration",
                new=AsyncMock(return_value={"status": "active"}),
            )
        )
        stack.enter_context(
            patch("workers.llm_worker.acquire_user_lock", new=AsyncMock(return_value=True))
        )
        stack.enter_context(patch("workers.llm_worker.upsert_user", new=AsyncMock()))
        stack.enter_context(
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded", new=AsyncMock(return_value=False)
            )
        )
        stack.enter_context(
            patch(
                "memory.creator_persona.get_structured_persona_async",
                new=AsyncMock(return_value=None),
                create=True,
            )
        )
        stack.enter_context(
            patch(
                "workers.llm_worker.build_qwen3_context",
                new=AsyncMock(return_value=[{"role": "system", "content": "p"}]),
            )
        )
        stack.enter_context(
            patch(
                "core.conversation_state.derive_conversation_state",
                new=MagicMock(return_value=_conversation_dict()),
            )
        )
        stack.enter_context(
            patch("memory.context.get_last_user", new=MagicMock(return_value=None), create=True)
        )
        stack.enter_context(
            patch("memory.context.get_last_profile", new=MagicMock(return_value=None), create=True)
        )
        stack.enter_context(
            patch(
                "db.postgres.get_user",
                new=AsyncMock(return_value={"id": 10, "message_count": 1}),
                create=True,
            )
        )
        stack.enter_context(
            patch("db.postgres.get_user_profile", new=AsyncMock(return_value={}), create=True)
        )
        # Fail fast on any direct pool use (no live DB in unit tests).
        stack.enter_context(
            patch(
                "db.postgres.get_pool",
                new=AsyncMock(side_effect=RuntimeError("no db in unit test")),
                create=True,
            )
        )
        stack.enter_context(
            patch(
                "commerce.conversational.build_conversational_commerce_state",
                new=AsyncMock(return_value=None),
                create=True,
            )
        )
        stack.enter_context(
            patch("commerce.opportunity_engine.evaluate_opportunity", new=fake_evaluate)
        )
        stack.enter_context(
            patch("commerce.opportunity_sealing.seal_ranked_candidate", new=fake_seal)
        )
        # Sealed path uses db.redis; normal path uses workers.llm_worker names.
        # Phase 3.3: neutralize the turn-send gate here (first-claim always).
        # These tests verify sealed-vs-normal routing, not turn deduplication;
        # turn dedup is covered by tests/test_turn_send_gate.py with isolated
        # FakeRedis (no live Redis I/O, no cross-test key pollution).
        stack.enter_context(
            patch("db.redis.try_claim_turn_send", new=AsyncMock(return_value=True))
        )
        stack.enter_context(patch("db.redis.try_reserve_send_dedup", new=world.fake_reserve))
        stack.enter_context(patch("db.redis.enqueue_send", new=world.fake_sealed_enqueue))
        stack.enter_context(patch("db.redis.confirm_send_dedup", new=world.fake_confirm))
        stack.enter_context(patch("db.redis.release_send_dedup", new=world.fake_release))
        stack.enter_context(patch("db.redis.get_send_dedup_value", new=world.fake_get_value))
        stack.enter_context(
            patch("workers.llm_worker.enqueue_send", new=world.fake_normal_enqueue, create=True)
        )
        stack.enter_context(
            patch("workers.llm_worker._try_commerce_draft", new=fake_commerce_draft, create=True)
        )
        # Isolate deterministic full-pipeline tests from live dynamic-copy
        # LLM calls: force the fallback path (no network/model I/O).
        stack.enter_context(
            patch(
                "commerce.dynamic_copy.generate_sealed_lead_in",
                new=AsyncMock(
                    return_value=MagicMock(
                        status="FAILED", text=None, failure_code="transport_error"
                    )
                ),
            )
        )
        stack.enter_context(
            patch(
                "commerce.deepseek.extract_commerce_signals",
                new=AsyncMock(return_value=None),
                create=True,
            )
        )
        stack.enter_context(
            patch(
                "workers.llm_worker.generate_draft",
                new=AsyncMock(
                    return_value="warm friendly chat reply, great to hear from you today"
                ),
            )
        )
        stack.enter_context(
            patch(
                "workers.llm_worker.score_draft", new=AsyncMock(return_value=(score, list(flags)))
            )
        )
        stack.enter_context(
            patch("workers.llm_worker.is_auto_reply_enabled", new=AsyncMock(return_value=True))
        )
        stack.enter_context(patch("workers.llm_worker.add_to_operator_queue", new=fake_add_queue))
        stack.enter_context(patch("workers.llm_worker.post_process", new=AsyncMock()))
        stack.enter_context(
            patch("workers.llm_worker.notify_operators", new=AsyncMock(), create=True)
        )
        stack.enter_context(patch("core.event_bus.publish_event", new=fake_publish))
        stack.enter_context(patch("core.event_bus.publish_events_batch", new=fake_publish_batch))
        stack.enter_context(
            patch("agent.canary.should_use_agent", new=MagicMock(return_value=False), create=True)
        )
        try:
            from workers.llm_worker import _settings as _w_settings

            stack.enter_context(patch.object(_w_settings, "llm_path", "legacy"))
            stack.enter_context(patch.object(_w_settings, "llm_tools_enabled", False))
        except Exception:
            pass
        for target in [
            "commerce.product_selection.resolve_commerce_product_with_history",
            "commerce.integration.resolve_and_run_commerce",
            "commerce.pipeline.run_commerce_pipeline",
            "commerce.orchestrator.orchestrate_commerce",
            "commerce.execution.execute_ppv",
        ]:
            try:
                stack.enter_context(
                    patch(target, new=AsyncMock(side_effect=AssertionError("legacy")), create=True)
                )
            except Exception:
                pass
        from workers.llm_worker import process_message

        await process_message(
            user_id=10,
            user_message="hi there",
            telegram_message_id=100,
            username="u",
            first_name="f",
            persona="p",
            generation_id="gen-test-1",
            creator_id=1,
        )
    return world, counts, published


def _completed_events(published):
    return [p for p in published if p["event_type"] == "ai.generation_completed"]


def _suggestion_events(published):
    return [p for p in published if p["event_type"] == "suggestion.created"]


# ---------------------------------------------------------------------------
# Helper truth table
# ---------------------------------------------------------------------------


class TestHandledHelper:
    @pytest.mark.asyncio
    async def test_executed_is_handled(self):
        from commerce.opportunity_execution import is_sealed_ppv_handled
        from commerce.opportunity_execution import ExecuteResult

        assert is_sealed_ppv_handled(ExecuteResult(status="EXECUTED")) is True
        assert is_sealed_ppv_handled(None) is False
        assert is_sealed_ppv_handled(ExecuteResult(status="NOT_SEALED")) is False
        assert is_sealed_ppv_handled(ExecuteResult(status="SCOPE_MISMATCH")) is False
        assert is_sealed_ppv_handled(ExecuteResult(status="ENQUEUE_FAILED")) is False

    @pytest.mark.asyncio
    async def test_already_delivered_is_handled(self):
        from commerce.opportunity_execution import is_sealed_ppv_handled
        from commerce.opportunity_execution import ExecuteResult

        assert (
            is_sealed_ppv_handled(
                ExecuteResult(status="ALREADY_ENQUEUED", subreason="ALREADY_DELIVERED")
            )
            is True
        )
        assert (
            is_sealed_ppv_handled(ExecuteResult(status="ALREADY_ENQUEUED", already_delivered=True))
            is True
        )
        assert (
            is_sealed_ppv_handled(ExecuteResult(status="ALREADY_ENQUEUED", dedup_value="1")) is True
        )

    @pytest.mark.asyncio
    async def test_already_reserved_is_not_handled(self):
        from commerce.opportunity_execution import is_sealed_ppv_handled
        from commerce.opportunity_execution import ExecuteResult

        assert (
            is_sealed_ppv_handled(
                ExecuteResult(status="ALREADY_ENQUEUED", subreason="ALREADY_RESERVED")
            )
            is False
        )
        assert (
            is_sealed_ppv_handled(
                ExecuteResult(
                    status="ALREADY_ENQUEUED",
                    subreason="ALREADY_RESERVED",
                    already_delivered=False,
                    dedup_value="reserved:other",
                )
            )
            is False
        )
        # Legacy shape without refinement fields must fail open toward normal.
        assert is_sealed_ppv_handled(ExecuteResult(status="ALREADY_ENQUEUED")) is False


# ---------------------------------------------------------------------------
# A. SEALED + EXECUTED suppresses normal duplicate
# ---------------------------------------------------------------------------


class TestSealedExecutedSingleOutbound:
    @pytest.mark.asyncio
    async def test_one_ppv_no_normal_no_operator(self, monkeypatch):
        world, counts, published = await _run_process_message(monkeypatch)
        assert len(world.sealed_enqueues) == 1
        payload, dedup_id, generation_id, creator_id = world.sealed_enqueues[0]
        assert dedup_id == "sealed:42"
        assert generation_id == "gen-test-1"
        assert creator_id == 1
        assert "https://www.dropfans.io/buy/dpfn_A" in payload["content"]
        assert "$19.99" in payload["content"]
        assert world.normal_enqueues == []
        assert counts["operator"] == 0
        assert counts["evaluate"] == 1
        assert counts["seal"] == 1
        completed = _completed_events(published)
        assert len(completed) == 1
        data = completed[0]["data"]
        assert data["was_auto_approved"] is True
        assert "https://www.dropfans.io/buy/dpfn_A" in data["draft"]
        assert "$19.99" in data["draft"]
        assert _suggestion_events(published) == []


# ---------------------------------------------------------------------------
# B. Already delivered suppresses
# ---------------------------------------------------------------------------


class TestAlreadyDeliveredSuppresses:
    @pytest.mark.asyncio
    async def test_delivered_value_suppresses_normal(self, monkeypatch):
        world = _SendWorld(reserve_behavior="dup", dedup_value="1")
        world, counts, published = await _run_process_message(monkeypatch, world=world)
        assert world.sealed_enqueues == []
        assert world.normal_enqueues == []
        assert counts["operator"] == 0
        completed = _completed_events(published)
        assert len(completed) == 1
        assert completed[0]["data"]["was_auto_approved"] is True


# ---------------------------------------------------------------------------
# C. Already reserved lets normal proceed
# ---------------------------------------------------------------------------


class TestAlreadyReservedProceeds:
    @pytest.mark.asyncio
    async def test_reserved_value_normal_proceeds_no_dup_ppv(self, monkeypatch):
        world = _SendWorld(reserve_behavior="dup", dedup_value="reserved:other-token")
        world, counts, published = await _run_process_message(monkeypatch, world=world)
        assert world.sealed_enqueues == []
        assert len(world.normal_enqueues) == 1
        assert counts["operator"] == 0
        completed = _completed_events(published)
        assert len(completed) == 1
        # Normal draft reported (not sealed content) in this branch.
        assert "https://www.dropfans.io/buy/dpfn_A" not in completed[0]["data"]["draft"]


# ---------------------------------------------------------------------------
# D/E. Enqueue + reservation failure lets normal proceed
# ---------------------------------------------------------------------------


class TestSendFailuresProceed:
    @pytest.mark.asyncio
    async def test_enqueue_failure_normal_proceeds_and_releases(self, monkeypatch):
        world = _SendWorld(enqueue_exc=RuntimeError("redis down"))
        world, counts, published = await _run_process_message(monkeypatch, world=world)
        # One sealed attempt was made but did not succeed; its lease released.
        assert len(world.sealed_enqueues) == 1
        assert len(world.normal_enqueues) == 1
        assert len(world.releases) == 1
        assert world.confirms == []
        assert counts["operator"] == 0

    @pytest.mark.asyncio
    async def test_reserve_failure_normal_proceeds(self, monkeypatch):
        world = _SendWorld(reserve_behavior="raise")
        world, counts, published = await _run_process_message(monkeypatch, world=world)
        assert world.sealed_enqueues == []
        assert len(world.normal_enqueues) == 1
        assert counts["operator"] == 0


# ---------------------------------------------------------------------------
# F/G/H/I. Seal failure / no opportunity / zero / multi Drops
# ---------------------------------------------------------------------------


class TestNormalPreserved:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["PROVIDER_DRIFT", "PROVIDER_TIMEOUT_AMBIGUOUS"])
    async def test_sealing_failure_normal_proceeds(self, monkeypatch, status):
        world, counts, published = await _run_process_message(monkeypatch, seal_status=status)
        assert world.sealed_enqueues == []
        assert len(world.normal_enqueues) == 1
        assert counts["operator"] == 0

    @pytest.mark.asyncio
    async def test_no_opportunity_normal_proceeds(self, monkeypatch):
        world, counts, published = await _run_process_message(monkeypatch, opp_mapped=None)
        assert counts["evaluate"] == 1
        assert counts["seal"] == 0
        assert world.sealed_enqueues == []
        assert len(world.normal_enqueues) == 1

    @pytest.mark.asyncio
    async def test_zero_drops_normal_proceeds(self, monkeypatch):
        world, counts, published = await _run_process_message(monkeypatch, opp_mapped=())
        assert counts["seal"] == 0
        assert world.sealed_enqueues == []
        assert len(world.normal_enqueues) == 1

    @pytest.mark.asyncio
    async def test_multiple_drops_normal_proceeds(self, monkeypatch):
        world, counts, published = await _run_process_message(
            monkeypatch, opp_mapped=("dpfn_A", "dpfn_B")
        )
        assert counts["seal"] == 0
        assert world.sealed_enqueues == []
        assert len(world.normal_enqueues) == 1


# ---------------------------------------------------------------------------
# J. Retry/crash semantics (adapter level + gate level)
# ---------------------------------------------------------------------------


class TestRetrySemantics:
    @pytest.mark.asyncio
    async def test_reserve_crash_then_retry_normal_not_suppressed(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer, is_sealed_ppv_handled

        state = {"seen": 0}

        async def fake_reserve(dedup_id, creator_id=None, **kw):
            state["seen"] += 1
            return None  # another worker holds the reservation

        async def fake_get(dedup_id, creator_id=None, **kw):
            return "reserved:other-token"

        async def _explode(*a, **kw):
            raise AssertionError("must not enqueue on duplicate")

        monkeypatch.setattr("db.redis.try_reserve_send_dedup", fake_reserve)
        monkeypatch.setattr("db.redis.get_send_dedup_value", fake_get)
        monkeypatch.setattr("db.redis.enqueue_send", _explode)
        monkeypatch.setattr("db.redis.confirm_send_dedup", _explode)
        monkeypatch.setattr("db.redis.release_send_dedup", _explode)
        res = await execute_sealed_offer(
            SealResult(status="SEALED", offer=_offer()), creator_id=1, user_id=10
        )
        assert res.status == "ALREADY_ENQUEUED"
        assert is_sealed_ppv_handled(res) is False

    @pytest.mark.asyncio
    async def test_xadd_crash_then_confirm_retry_single_ppv(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        calls = {"enqueue": 0}

        async def fake_reserve(dedup_id, creator_id=None, **kw):
            return "reserved:tok-a"

        async def fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None, **kw):
            calls["enqueue"] += 1
            return "msg-1"

        async def fake_confirm(dedup_id, creator_id=None, token=None, **kw):
            return True

        monkeypatch.setattr("db.redis.try_reserve_send_dedup", fake_reserve)
        monkeypatch.setattr("db.redis.enqueue_send", fake_enqueue)
        monkeypatch.setattr("db.redis.confirm_send_dedup", fake_confirm)
        monkeypatch.setattr("db.redis.release_send_dedup", AsyncMock(return_value=True))
        res = await execute_sealed_offer(
            SealResult(status="SEALED", offer=_offer()), creator_id=1, user_id=10
        )
        assert res.status == "EXECUTED"
        assert calls["enqueue"] == 1

        # Retry after confirm: reservation now held/confirmed -> no second PPV.
        async def fake_reserve_dup(dedup_id, creator_id=None, **kw):
            return None

        async def fake_get_confirmed(dedup_id, creator_id=None, **kw):
            return "1"

        async def _explode_enqueue(*a, **kw):
            raise AssertionError("second PPV must not enqueue")

        monkeypatch.setattr("db.redis.try_reserve_send_dedup", fake_reserve_dup)
        monkeypatch.setattr("db.redis.get_send_dedup_value", fake_get_confirmed)
        monkeypatch.setattr("db.redis.enqueue_send", _explode_enqueue)
        res2 = await execute_sealed_offer(
            SealResult(status="SEALED", offer=_offer()), creator_id=1, user_id=10
        )
        assert res2.status == "ALREADY_ENQUEUED"
        assert res2.subreason == "ALREADY_DELIVERED"


# ---------------------------------------------------------------------------
# K. Legacy quarantine + L. single-evaluation invariant
# ---------------------------------------------------------------------------


class TestQuarantineAndInvariants:
    def test_no_legacy_authority_in_touched_files(self):
        for path in (EXEC_PATH, WORKER_PATH):
            src = path.read_text(encoding="utf-8")
            if path == EXEC_PATH:
                for tok in [
                    "resolve_commerce_product_with_history",
                    "resolve_and_run_commerce",
                    "run_commerce_pipeline",
                    "decide_from_signals",
                    "build_strategy",
                    "orchestrate_commerce",
                    "execute_ppv",
                    "fangate_products",
                    "ProductCommerceState",
                    "seller_earning",
                    "segments",
                    "bundle_group",
                    "bundle_related",
                ]:
                    assert tok not in src, tok
        body = WORKER_PATH.read_text(encoding="utf-8")
        start = body.find("async def process_message")
        region = body[start : body.find("async def run_worker")]
        for tok in [
            "resolve_commerce_product_with_history(",
            "resolve_and_run_commerce(",
            "execute_ppv(",
        ]:
            assert region.count(tok) == 0

    def test_single_attempt_invariants_static(self):
        src = WORKER_PATH.read_text(encoding="utf-8")
        start = src.find("async def process_message")
        body = src[start : src.find("async def run_worker")]
        assert body.count("await evaluate_opportunity(") == 1
        assert body.count("await seal_ranked_candidate(") == 1
        assert body.count("await execute_sealed_offer(") == 1
        assert "_sealed_ppv_handled" in body
        assert "global _sealed_ppv_handled" not in body
        # Gate sits before the final outbound decision, not as early return.
        assert body.find("await execute_sealed_offer(") < body.find("if _sealed_ppv_handled:")
        assert body.find("if _sealed_ppv_handled:") < body.find("elif not auto_reply_on:")

    @pytest.mark.asyncio
    async def test_process_message_single_evaluation(self, monkeypatch):
        _, counts, _ = await _run_process_message(monkeypatch)
        assert counts["evaluate"] == 1
        assert counts["seal"] == 1
