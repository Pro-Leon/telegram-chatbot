"""P3.3.14.4 — thin sealed-offer execution tests (unit, no live I/O)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from commerce.opportunity_sealing import SealResult

pytestmark = [pytest.mark.unit]

EXEC_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_execution.py"
WORKER_PATH = Path(__file__).parent.parent / "workers" / "llm_worker.py"

FAIL_STATUSES = [
    "PROVIDER_DRIFT",
    "PROVIDER_REJECTED",
    "PROVIDER_NOT_FOUND",
    "CREATOR_MISMATCH",
    "MALFORMED_PROVIDER_DATA",
    "PROVIDER_TIMEOUT_AMBIGUOUS",
    "LOCAL_PERSISTENCE_FAILURE",
]


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


def _sealed_offer_dict(**over):
    return _offer(**over)


def _patch_send(
    monkeypatch,
    reserve_ret="reserved:tok1",
    enqueue_ret="msg-1",
    enqueue_exc=None,
    dedup_value="1",
):
    calls = []
    confirms = []
    releases = []

    async def fake_reserve(dedup_id, creator_id=None, **kw):
        calls.append(("reserve", dedup_id, creator_id))
        return reserve_ret

    async def fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None, **kw):
        calls.append(("enqueue", dict(payload), dedup_id, generation_id, creator_id))
        if enqueue_exc is not None:
            raise enqueue_exc
        return enqueue_ret

    async def fake_confirm(dedup_id, creator_id=None, token=None, **kw):
        confirms.append((dedup_id, creator_id, token))
        return True

    async def fake_release(dedup_id, creator_id=None, token=None, **kw):
        releases.append((dedup_id, creator_id, token))
        return True

    async def fake_get_value(dedup_id, creator_id=None, **kw):
        calls.append(("get_value", dedup_id, creator_id))
        return dedup_value

    monkeypatch.setattr("db.redis.try_reserve_send_dedup", fake_reserve)
    monkeypatch.setattr("db.redis.enqueue_send", fake_enqueue)
    monkeypatch.setattr("db.redis.confirm_send_dedup", fake_confirm)
    monkeypatch.setattr("db.redis.release_send_dedup", fake_release)
    monkeypatch.setattr("db.redis.get_send_dedup_value", fake_get_value)
    return calls, confirms, releases


def _patch_no_send(monkeypatch):
    async def _explode(*a, **kw):
        raise AssertionError("send path must not be called")

    monkeypatch.setattr("db.redis.try_reserve_send_dedup", _explode)
    monkeypatch.setattr("db.redis.enqueue_send", _explode)
    monkeypatch.setattr("db.redis.confirm_send_dedup", _explode)
    monkeypatch.setattr("db.redis.release_send_dedup", _explode)
    monkeypatch.setattr("db.redis.get_send_dedup_value", _explode)


# ---------------------------------------------------------------------------
# A. Sealed validation
# ---------------------------------------------------------------------------


class TestSealedValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", FAIL_STATUSES)
    async def test_non_sealed_no_send(self, monkeypatch, status):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_no_send(monkeypatch)
        sr = SealResult(status=status, offer=None)
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "NOT_SEALED"

    @pytest.mark.asyncio
    async def test_non_sealed_with_offer_still_no_send(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_no_send(monkeypatch)
        sr = SealResult(status="PROVIDER_DRIFT", subreason="PRICE_DRIFT", offer=_offer())
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "NOT_SEALED"

    @pytest.mark.asyncio
    async def test_sealed_without_offer_no_send(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_no_send(monkeypatch)
        sr = SealResult(status="SEALED", offer=None)
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "NOT_SEALED"

    @pytest.mark.asyncio
    async def test_creator_mismatch_no_send(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_no_send(monkeypatch)
        sr = SealResult(status="SEALED", offer=_offer(creator_id=1, user_id=10))
        res = await execute_sealed_offer(sr, creator_id=2, user_id=10, generation_id="g1")
        assert res.status == "SCOPE_MISMATCH"

    @pytest.mark.asyncio
    async def test_user_mismatch_no_send(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_no_send(monkeypatch)
        sr = SealResult(status="SEALED", offer=_offer(creator_id=1, user_id=10))
        res = await execute_sealed_offer(sr, creator_id=1, user_id=99, generation_id="g1")
        assert res.status == "SCOPE_MISMATCH"


# ---------------------------------------------------------------------------
# B. Sealed authority
# ---------------------------------------------------------------------------


class TestSealedAuthority:
    @pytest.mark.asyncio
    async def test_final_message_uses_sealed_link_price_currency(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        calls, confirms, releases = _patch_send(monkeypatch)

        # Mocked mirror with different values — adapter must never consult it.
        async def _mirror_explode(*a, **kw):
            raise AssertionError("mirror must not be queried")

        # Patch mirror-adjacent entry points if importable; ignore missing.
        try:
            monkeypatch.setattr(
                "db.dropfans.find_dropfans_product",
                AsyncMock(return_value={"id": 999, "price_minor": 1}),
                raising=False,
            )
        except Exception:
            pass
        try:
            monkeypatch.setattr(
                "db.fangate.get_fangate_product",
                AsyncMock(
                    return_value={
                        "price_minor": 1,
                        "sales_url": "https://evil.example/steal",
                        "raw": {"price": 0.01},
                    }
                ),
                raising=False,
            )
        except Exception:
            pass
        try:
            monkeypatch.setattr(
                "integrations.dropfans.service.get_drop",
                AsyncMock(
                    return_value={
                        "id": "dpfn_A",
                        "price": 0.01,
                        "currency": "USD",
                        "buyUrl": "https://evil.example/steal",
                    }
                ),
                raising=False,
            )
        except Exception:
            pass

        sealed = _offer(
            link="https://www.dropfans.io/buy/dpfn_A",
            price_minor=1999,
            currency="USD",
        )
        sr = SealResult(status="SEALED", offer=sealed)
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "EXECUTED"
        assert res.content is not None
        assert "https://www.dropfans.io/buy/dpfn_A" in res.content
        assert "$19.99" in res.content
        assert "https://evil.example/steal" not in res.content
        assert "$0.01" not in res.content
        # Payload preserves sealed values too.
        enqueues = [c for c in calls if c[0] == "enqueue"]
        assert len(enqueues) == 1
        payload = enqueues[0][1]
        assert "https://www.dropfans.io/buy/dpfn_A" in payload["content"]
        assert "$19.99" in payload["content"]

    @pytest.mark.asyncio
    async def test_vault_membership_from_offer_not_mirror(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_send(monkeypatch)
        sealed = _offer(vault_item_ids=["V1", "V2"], media_count=2)
        sr = SealResult(status="SEALED", offer=sealed)
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "EXECUTED"
        assert res.facts is not None
        assert tuple(res.facts.vault_item_ids) == ("V1", "V2")
        assert res.facts.media_count == 2

    @pytest.mark.asyncio
    async def test_identity_provenance_preserved(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_send(monkeypatch)
        sealed = _offer(
            id=77,
            dropfans_product_id="dpfn_X",
            reason=_reason(definition_id=11, definition_version=2, stable_key="alpha"),
        )
        sr = SealResult(status="SEALED", offer=sealed)
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "EXECUTED"
        assert res.offer_id == 77
        assert res.dedup_id == "sealed:77"
        assert res.facts is not None
        assert res.facts.dropfans_product_id == "dpfn_X"
        assert res.facts.definition_id == 11
        assert res.facts.definition_version == 2
        assert res.facts.stable_key == "alpha"


# ---------------------------------------------------------------------------
# C. Wording boundary (deterministic)
# ---------------------------------------------------------------------------


class TestWordingBoundary:
    @pytest.mark.asyncio
    async def test_exact_url_and_price_inserted_no_llm(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        calls, _, _ = _patch_send(monkeypatch)
        # LLM must not be consulted: explode if provider touched.
        try:
            monkeypatch.setattr(
                "core.llm_provider.get_llm_provider",
                MagicMock(side_effect=AssertionError("LLM must not be called")),
                raising=False,
            )
        except Exception:
            pass
        sealed = _offer(
            price_minor=2500,
            link="https://www.dropfans.io/buy/dpfn_B",
            dropfans_product_id="dpfn_B",
        )
        sr = SealResult(status="SEALED", offer=sealed)
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "EXECUTED"
        assert "https://www.dropfans.io/buy/dpfn_B" in (res.content or "")
        assert "$25.00" in (res.content or "")

    @pytest.mark.asyncio
    async def test_llm_cannot_replace_price_or_url(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_send(monkeypatch)
        # Even if an LLM were available with different values, adapter ignores it
        # because it never calls the provider (see previous test). Here verify
        # the rendered message derives price from sealed facts only.
        sealed = _offer(price_minor=1999, currency="USD", link="https://www.dropfans.io/buy/dpfn_A")
        sr = SealResult(status="SEALED", offer=sealed)
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.facts is not None
        assert res.facts.price_minor == 1999
        assert res.facts.currency == "USD"
        assert res.facts.link == "https://www.dropfans.io/buy/dpfn_A"
        assert "$19.99" in (res.content or "")


# ---------------------------------------------------------------------------
# D. Send dedup
# ---------------------------------------------------------------------------


class TestSendDedup:
    @pytest.mark.asyncio
    async def test_reserve_before_enqueue_and_dedup_id(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        calls, _, _ = _patch_send(monkeypatch)
        sr = SealResult(status="SEALED", offer=_offer(id=42))
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="gen-9")
        assert res.status == "EXECUTED"
        kinds = [c[0] for c in calls]
        assert kinds.index("reserve") < kinds.index("enqueue")
        reserve = [c for c in calls if c[0] == "reserve"][0]
        assert reserve[1] == "sealed:42"
        assert reserve[2] == 1
        enqueue = [c for c in calls if c[0] == "enqueue"][0]
        assert enqueue[2] == "sealed:42"
        assert enqueue[4] == 1

    @pytest.mark.asyncio
    async def test_duplicate_reservation_no_second_enqueue(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        calls, confirms, releases = _patch_send(monkeypatch, reserve_ret=None, dedup_value="1")
        sr = SealResult(status="SEALED", offer=_offer(id=42))
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "ALREADY_ENQUEUED"
        assert res.subreason == "ALREADY_DELIVERED"
        assert res.already_delivered is True
        assert [c[0] for c in calls] == ["reserve", "get_value"]
        assert confirms == []
        assert releases == []

    @pytest.mark.asyncio
    async def test_duplicate_reserved_inflight_reports_reserved(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        calls, confirms, releases = _patch_send(
            monkeypatch, reserve_ret=None, dedup_value="reserved:other-token"
        )
        sr = SealResult(status="SEALED", offer=_offer(id=42))
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "ALREADY_ENQUEUED"
        assert res.subreason == "ALREADY_RESERVED"
        assert res.already_delivered is False
        assert [c[0] for c in calls] == ["reserve", "get_value"]
        assert confirms == []
        assert releases == []


# ---------------------------------------------------------------------------
# E. Confirmation
# ---------------------------------------------------------------------------


class TestConfirmation:
    @pytest.mark.asyncio
    async def test_success_releases_no_confirm(self, monkeypatch):
        # H4 Batch 3 (D1): confirmation means "Telegram accepted", which only
        # the send worker can prove. execute must release its enqueue-time
        # reservation so the worker can reserve/send/confirm normally.
        from commerce.opportunity_execution import execute_sealed_offer

        calls, confirms, releases = _patch_send(monkeypatch, reserve_ret="reserved:tok1")
        sr = SealResult(status="SEALED", offer=_offer(id=42))
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "EXECUTED"
        assert confirms == []
        assert releases == [("sealed:42", 1, "reserved:tok1")]

    @pytest.mark.asyncio
    async def test_enqueue_failure_releases_no_confirm_no_retry(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        calls, confirms, releases = _patch_send(
            monkeypatch, reserve_ret="reserved:tok9", enqueue_exc=RuntimeError("redis down")
        )
        sr = SealResult(status="SEALED", offer=_offer(id=42))
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "ENQUEUE_FAILED"
        assert confirms == []
        assert releases == [("sealed:42", 1, "reserved:tok9")]
        # No alternate ID attempted: exactly one reserve + one enqueue.
        assert [c[0] for c in calls] == ["reserve", "enqueue"]


# ---------------------------------------------------------------------------
# F. Failure behavior (all seal failures)
# ---------------------------------------------------------------------------


class TestFailureBehavior:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", FAIL_STATUSES)
    async def test_all_failures_no_send_no_fallback(self, monkeypatch, status):
        from commerce.opportunity_execution import execute_sealed_offer

        _patch_no_send(monkeypatch)
        # Legacy must never be invoked even on failure.
        for target in [
            "commerce.product_selection.resolve_commerce_product_with_history",
            "commerce.integration.resolve_and_run_commerce",
            "commerce.pipeline.run_commerce_pipeline",
            "commerce.orchestrator.orchestrate_commerce",
            "commerce.execution.execute_ppv",
        ]:
            try:
                monkeypatch.setattr(
                    target, AsyncMock(side_effect=AssertionError("legacy fallback")), raising=False
                )
            except Exception:
                pass
        sr = SealResult(status=status, subreason="X", offer=None)
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "NOT_SEALED"


# ---------------------------------------------------------------------------
# G. Idempotent sealed offer
# ---------------------------------------------------------------------------


class TestIdempotent:
    @pytest.mark.asyncio
    async def test_same_offer_twice_second_not_enqueued(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        state = {"reserved": False}
        calls = []
        confirms = []

        async def fake_reserve(dedup_id, creator_id=None, **kw):
            assert dedup_id == "sealed:42"
            if not state["reserved"]:
                state["reserved"] = True
                calls.append("reserve:token")
                return "reserved:tok-state"
            calls.append("reserve:dup")
            return None

        async def fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None, **kw):
            calls.append("enqueue")
            return "msg-1"

        async def fake_confirm(dedup_id, creator_id=None, token=None, **kw):
            confirms.append((dedup_id, token))
            return True

        async def fake_release(*a, **kw):
            raise AssertionError("release must not be called here")

        async def fake_get_value(dedup_id, creator_id=None, **kw):
            return "1"

        monkeypatch.setattr("db.redis.try_reserve_send_dedup", fake_reserve)
        monkeypatch.setattr("db.redis.enqueue_send", fake_enqueue)
        monkeypatch.setattr("db.redis.confirm_send_dedup", fake_confirm)
        monkeypatch.setattr("db.redis.release_send_dedup", fake_release)
        monkeypatch.setattr("db.redis.get_send_dedup_value", fake_get_value)

        sr = SealResult(status="SEALED", offer=_offer(id=42))
        r1 = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        r2 = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert r1.status == "EXECUTED"
        assert r2.status == "ALREADY_ENQUEUED"
        assert r2.subreason == "ALREADY_DELIVERED"
        assert calls.count("enqueue") == 1


# ---------------------------------------------------------------------------
# H. Process-message integration
# ---------------------------------------------------------------------------


class TestProcessMessageIntegration:
    def test_worker_single_evaluation_single_seal_single_execution(self):
        src = WORKER_PATH.read_text(encoding="utf-8")
        start = src.find("async def process_message")
        end = src.find("async def run_worker")
        assert start != -1 and end != -1 and end > start
        body = src[start:end]
        assert body.count("await evaluate_opportunity(") == 1
        assert body.count("await seal_ranked_candidate(") == 1
        assert body.count("await execute_sealed_offer(") == 1

    def test_worker_execution_guarded_and_scoped(self):
        src = WORKER_PATH.read_text(encoding="utf-8")
        start = src.find("async def process_message")
        body = src[start : src.find("async def run_worker")]
        # Guard: only when SEALED
        assert (
            'if _seal_result.status == "SEALED":' in body
            or "if _seal_result.status == 'SEALED':" in body
        )
        # Exact scope variables
        assert "creator_id=_creator_id" in body
        assert "user_id=user_id" in body
        assert "generation_id=generation_id" in body
        # Execution occurs after sealing in source order
        assert body.find("await seal_ranked_candidate(") < body.find("await execute_sealed_offer(")

    def test_worker_no_second_evaluation_or_seal_after_execution(self):
        src = WORKER_PATH.read_text(encoding="utf-8")
        start = src.find("async def process_message")
        body = src[start : src.find("async def run_worker")]
        exec_pos = body.find("await execute_sealed_offer(")
        assert exec_pos != -1
        tail = body[exec_pos:]
        assert "await evaluate_opportunity(" not in tail
        assert "await seal_ranked_candidate(" not in tail

    def test_worker_no_legacy_fallback_in_seal_execute_region(self):
        src = WORKER_PATH.read_text(encoding="utf-8")
        start = src.find("async def process_message")
        body = src[start : src.find("async def run_worker")]
        seal_pos = body.find("await seal_ranked_candidate(")
        assert seal_pos != -1
        region = body[seal_pos : seal_pos + 4000]
        for tok in [
            "resolve_commerce_product_with_history(",
            "resolve_and_run_commerce(",
            "execute_ppv(",
        ]:
            assert tok not in region

    @pytest.mark.asyncio
    async def test_single_attempt_runtime_sequence(self, monkeypatch):
        # Simulate the worker's exact three-step sequence with counting mocks.
        from commerce.opportunity import candidate_from_definition
        from commerce.opportunity_engine import OpportunityEngineResult
        from commerce.opportunity_ranking import OpportunityRankingResult, RankedCandidate
        from commerce.fan_commercial_state import FanCommercialState
        from commerce.offer_history import OfferHistory
        from core.conversation_state import ConversationState
        from datetime import datetime, timezone

        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        fan = FanCommercialState(
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
        hist = OfferHistory(
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
        cand = candidate_from_definition(1, 10, row, mapped_drop_ids=["dpfn_A"])
        ranked = RankedCandidate(11, "alpha", 1, ("STABLE_ID_TIEBREAK",), True, 0)
        ranking = OpportunityRankingResult(
            evaluated_at=now, creator_id=1, user_id=10, ranked=(ranked,), selected=ranked
        )
        opp = OpportunityEngineResult(
            creator_id=1,
            user_id=10,
            evaluated_at=now,
            fan_commercial_state=fan,
            offer_history=hist,
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
        eval_calls = []
        seal_calls = []
        enqueue_calls = []

        async def fake_evaluate(*a, **kw):
            eval_calls.append(1)
            return opp

        async def fake_seal(candidate, ranking_result, creator_id, user_id, cuid, **kw):
            seal_calls.append((candidate, cuid))
            assert len(candidate.mapped_drop_ids) == 1
            return SealResult(status="SEALED", offer=_offer(id=42))

        async def fake_reserve(dedup_id, creator_id=None, **kw):
            assert dedup_id == "sealed:42"
            return "reserved:t"

        async def fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None, **kw):
            enqueue_calls.append((dedup_id, generation_id))
            return "msg-1"

        monkeypatch.setattr("commerce.opportunity_engine.evaluate_opportunity", fake_evaluate)
        monkeypatch.setattr("commerce.opportunity_sealing.seal_ranked_candidate", fake_seal)
        monkeypatch.setattr("db.redis.try_reserve_send_dedup", fake_reserve)
        monkeypatch.setattr("db.redis.enqueue_send", fake_enqueue)
        monkeypatch.setattr("db.redis.confirm_send_dedup", AsyncMock(return_value=True))
        monkeypatch.setattr("db.redis.release_send_dedup", AsyncMock(return_value=True))

        from commerce.opportunity_engine import evaluate_opportunity
        from commerce.opportunity_sealing import seal_ranked_candidate
        from commerce.opportunity_execution import execute_sealed_offer
        from core.conversation_state import ConversationState as _CS

        cs = ConversationState(
            lifecycle="established",
            identity_already_established=True,
            current_topic="movie",
            recent_topics=("movie",),
            open_threads=("movie",),
            last_question=None,
            last_question_answered=False,
            consecutive_questions=0,
            tone="warm",
            last_user_fact=None,
            questions_in_last_3=0,
        )
        # One evaluation
        result = await evaluate_opportunity(1, 10, cs, now)
        assert len(eval_calls) == 1
        # One sealing for the single Drop
        assert len(result.selected_candidate.mapped_drop_ids) == 1
        seal = await seal_ranked_candidate(
            result.selected_candidate,
            result.ranking_result,
            1,
            10,
            result.selected_candidate.mapped_drop_ids[0],
        )
        assert len(seal_calls) == 1
        # One execution
        from unittest.mock import AsyncMock as _AM  # noqa

        for target in [
            "commerce.product_selection.resolve_commerce_product_with_history",
            "commerce.integration.resolve_and_run_commerce",
        ]:
            try:
                monkeypatch.setattr(
                    target, AsyncMock(side_effect=AssertionError("legacy")), raising=False
                )
            except Exception:
                pass
        ex = await execute_sealed_offer(seal, creator_id=1, user_id=10, generation_id="gen-h")
        assert ex.status == "EXECUTED"
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0] == ("sealed:42", "gen-h")
        # No second evaluation / sealing / enqueue
        assert len(eval_calls) == 1
        assert len(seal_calls) == 1
        assert len(enqueue_calls) == 1


# ---------------------------------------------------------------------------
# I. Legacy quarantine
# ---------------------------------------------------------------------------


class TestLegacyQuarantine:
    def test_execution_module_has_zero_legacy_references(self):
        src = EXEC_PATH.read_text(encoding="utf-8")
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
            assert tok not in src, f"forbidden token in execution adapter: {tok}"

    @pytest.mark.asyncio
    async def test_execution_never_invokes_legacy(self, monkeypatch):
        from commerce.opportunity_execution import execute_sealed_offer

        calls, _, _ = _patch_send(monkeypatch)
        for target in [
            "commerce.product_selection.resolve_commerce_product_with_history",
            "commerce.integration.resolve_and_run_commerce",
            "commerce.pipeline.run_commerce_pipeline",
            "commerce.orchestrator.orchestrate_commerce",
            "commerce.execution.execute_ppv",
            "commerce.decision.decide_from_signals",
            "commerce.strategy.build_strategy",
        ]:
            try:
                monkeypatch.setattr(
                    target,
                    AsyncMock(side_effect=AssertionError(f"legacy {target} called")),
                    raising=False,
                )
            except Exception:
                pass
        # Mirror-adjacent reads must also not be consulted.
        try:
            monkeypatch.setattr(
                "db.fangate.get_fangate_product",
                AsyncMock(side_effect=AssertionError("mirror called")),
                raising=False,
            )
        except Exception:
            pass
        try:
            monkeypatch.setattr(
                "integrations.dropfans.service.get_drop",
                AsyncMock(side_effect=AssertionError("provider called")),
                raising=False,
            )
        except Exception:
            pass
        sr = SealResult(status="SEALED", offer=_offer())
        res = await execute_sealed_offer(sr, creator_id=1, user_id=10, generation_id="g1")
        assert res.status == "EXECUTED"
        assert len([c for c in calls if c[0] == "enqueue"]) == 1
