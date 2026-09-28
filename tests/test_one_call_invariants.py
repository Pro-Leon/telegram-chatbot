"""Pass 11: OneCall invariant regression suite (synthetic, no DB, no real LLM).

Covers the 14 stabilization invariants plus 4 pass-specific additions
(Pass 6 zero-normalization, Pass 7 commerce gate, Pass 8 wire budget,
Pass 7L enrichment). All fixtures are synthetic AuthoritativeState-shaped
objects (funnel warming, 27-message pattern per audit).
"""

import asyncio
import json
from dataclasses import FrozenInstanceError
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _warming_auth(current_message="hey", n_recent=6, commerce_text="Purchases: 0"):
    recent = []
    for i in range(n_recent):
        if i % 2 == 0:
            recent.append({"direction": "inbound", "content": f"hello {i}"})
        else:
            recent.append({"direction": "outbound", "content": f"hi back {i}"})
    return SimpleNamespace(
        user={"first_name": "Mason", "funnel_stage": "warming", "message_count": 27},
        profile={},
        persona="You are Sunny Skye.",
        persona_name="Sunny Skye",
        conversation_state=None,
        recent_messages=tuple(recent),
        summary=None,
        summary_age_days=None,
        commerce_context_text=commerce_text,
        participants=None,
        conversation_contract=None,
        current_message=current_message,
        relationship_context_text="",
        intimacy_context_text="",
        boundary_context_text="",
        content_transition_context_text="",
        strategy_block_text="",
        behavior_block_text="",
    )


def _valid_raw(requested_price=None):
    return json.dumps({
        "reply": "Hey friend, great to hear from you today, how is everything going?",
        "commerce_signals": {
            "purchase_intent": 0.0, "content_interest": 0.0,
            "relationship_engagement": 0.5, "price_interest": 0.0,
            "explicit_purchase_request": False, "explicit_content_request": False,
            "requested_price": requested_price, "declined_recent_offer": False,
            "asks_for_free_content": False, "negative_sentiment": 0.0,
            "confidence": 0.5, "evidence": [], "model_uncertainty": 0.5,
            "primary_intent": "casual_chat", "intent_tags": [],
            "negative_intent_tags": [], "fan_asks_question": False,
        },
        "confidence": 0.8, "needs_handoff": False,
    })


# ── 1. ONE snapshot reused deterministically (no refetch) ────────────────

class TestOneSnapshot:
    def test_snapshot_reuse_is_byte_identical(self):
        from core.context_compact import build_one_call_from_snapshot
        auth = _warming_auth()
        m1 = build_one_call_from_snapshot(snapshot=None, authoritative_state=auth, pipeline_result=None)
        m2 = build_one_call_from_snapshot(snapshot=None, authoritative_state=auth, pipeline_result=None)
        assert m1 == m2
        assert len(m1) > 0


# ── 2. ZERO duplicate authoritative reads ───────────────────────────────

class TestZeroDuplicateReads:
    def test_pipeline_result_messages_reused_without_refetch(self):
        from core.context_compact import build_one_call_from_snapshot
        msgs = [
            {"role": "system", "content": "[CURRENT AUTHORITATIVE STATE] STATE: Mason | warming"},
            {"role": "user", "content": "Mason: hey"},
        ]
        pr = SimpleNamespace(messages=msgs, snapshot=None)
        out = build_one_call_from_snapshot(snapshot=None, authoritative_state=_warming_auth(), pipeline_result=pr)
        assert any("STATE: Mason" in m.get("content", "") for m in out)
        assert any(m.get("role") == "user" for m in out)


# ── 3. ONE LLM generation ───────────────────────────────────────────────

class TestOneGeneration:
    @pytest.mark.asyncio
    async def test_single_provider_call(self):
        from core import one_call_pipeline as pl
        provider = MagicMock()
        provider.generate = AsyncMock(return_value=_valid_raw())
        provider.provider_name = "llamacpp"
        provider._model = "test"
        provider.last_prompt_tokens = 10
        provider.last_generation_tokens = 5
        with patch("core.one_call_pipeline.get_llm_provider", return_value=provider):
            res = await pl.one_call_generation(
                user_id=1, creator_id=1, user_message="hey", persona="p",
                profile={}, user={}, authoritative_state=_warming_auth(),
            )
        assert provider.generate.await_count == 1
        assert res.is_valid is True


# ── 4. ONE current-message occurrence (exactly-once) ─────────────────────

class TestExactlyOnce:
    def test_current_message_appears_once(self):
        from core.context_compact import build_one_call_from_snapshot
        auth = _warming_auth(current_message="hey")
        msgs = build_one_call_from_snapshot(snapshot=None, authoritative_state=auth, pipeline_result=None)
        hits = sum("Mason:\nhey" in m.get("content", "") for m in msgs if m.get("role") == "user")
        assert hits == 1


# ── 5. ZERO legacy fallback on valid one-call ───────────────────────────

class TestZeroLegacyFallback:
    @pytest.mark.asyncio
    async def test_valid_result_never_calls_fallback(self):
        from core import one_call_pipeline as pl
        provider = MagicMock()
        provider.generate = AsyncMock(return_value=_valid_raw())
        provider.provider_name = "llamacpp"
        provider._model = "test"
        provider.last_prompt_tokens = 10
        provider.last_generation_tokens = 5
        with (
            patch("core.one_call_pipeline.get_llm_provider", return_value=provider),
            patch("core.one_call_pipeline._fallback_3llm_pipeline", new_callable=AsyncMock) as fb,
        ):
            res = await pl.one_call_pipeline_with_fallback(
                user_id=1, creator_id=1, user_message="hey", persona="p",
                profile={}, user={}, authoritative_state=_warming_auth(),
            )
        assert res.is_valid is True
        assert fb.await_count == 0


# ── 6. Semantic only when retrieval gate requires ───────────────────────

class TestGatedRetrieval:
    def test_hey_skips_semantic(self):
        from context_engine.retrieval_gate import should_retrieve_knowledge
        assert should_retrieve_knowledge("hey", None) is False

    def test_knowledge_question_requires(self):
        from context_engine.retrieval_gate import should_retrieve_knowledge
        assert should_retrieve_knowledge("remember what you told me last time?", None) is True


# ── 7. Hard timeout 2.0s degraded (fail-open) ────────────────────────────

class TestHardTimeout:
    @pytest.mark.asyncio
    async def test_slow_source_bounded_and_degraded(self):
        import time as _t
        from context_engine.gatherer import (
            AuthorityLevel,
            ContextCategory,
            ContextGatherer,
            DataSource,
            GathererConfig,
        )

        class _Slow(DataSource):
            @property
            def source_name(self):
                return "slow_stub"

            @property
            def category(self):
                return ContextCategory.MEMORY

            @property
            def authority(self):
                return AuthorityLevel.DETERMINISTIC_RULE

            async def gather(self, config):
                await asyncio.sleep(5)
                return []

        cfg = GathererConfig(creator_id=1, user_id=1, current_message="hey")
        g = ContextGatherer(sources=[_Slow()])
        start = _t.monotonic()
        items = await g.gather_all(cfg)
        elapsed = _t.monotonic() - start
        assert items == []
        assert elapsed < 4.0
        assert isinstance(getattr(cfg, "_retrieval_metrics", None), dict)
        assert cfg._retrieval_metrics.get("degraded") is True


# ── 8. Bounded top_k (ONE_CALL_MAX_MESSAGES=8) ───────────────────────────

class TestBoundedTopK:
    def test_conversation_window_bounded(self):
        from core.context_compact import build_one_call_from_snapshot
        auth = _warming_auth(n_recent=20)
        msgs = build_one_call_from_snapshot(snapshot=None, authoritative_state=auth, pipeline_result=None)
        conv = [m for m in msgs if m.get("role") != "system"]
        # Window is 8 history turns + at most 1 injected [PLAYER MESSAGE]
        # (exactly-once current-message rule takes precedence over the cap).
        injected = [m for m in conv if "[PLAYER MESSAGE]" in m.get("content", "")]
        assert len(injected) <= 1
        assert len(conv) - len(injected) <= 8


# ── 9. Speaker direction preserved ───────────────────────────────────────

class TestSpeakerDirection:
    def test_inbound_user_outbound_assistant(self):
        from core.context_compact import build_one_call_from_snapshot
        auth = _warming_auth()
        msgs = build_one_call_from_snapshot(snapshot=None, authoritative_state=auth, pipeline_result=None)
        users = [m for m in msgs if m.get("role") == "user"]
        assistants = [m for m in msgs if m.get("role") == "assistant"]
        assert users and all("Mason:" in m.get("content", "") for m in users)
        assert assistants and all("Sunny Skye:" in m.get("content", "") for m in assistants)


# ── 10. Authoritative state frozen ───────────────────────────────────────

class TestAuthoritativeFrozen:
    def test_frozen_dataclass(self):
        from context_engine.models import AuthoritativeState
        assert AuthoritativeState.__dataclass_params__.frozen is True
        obj = AuthoritativeState.__new__(AuthoritativeState)
        with pytest.raises(FrozenInstanceError):
            obj.user_id = 1  # type: ignore

    def test_mapping_proxy_in_models(self):
        import inspect

        import context_engine.models as _models
        assert "MappingProxyType" in inspect.getsource(_models)


# ── 11. Final prompt within budget (1800 wire / 8192 total) ─────────────

class TestPromptBudget:
    def test_wire_and_total_within_budget(self):
        import json as _json

        from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS
        from core.context_compact import (
            ONE_CALL_MAX_PROMPT_TOKENS,
            build_one_call_from_snapshot,
            estimate_one_call_tokens,
            validate_one_call_context,
        )
        from core.one_call import ONE_CALL_SYSTEM_PROMPT
        from memory.context import count_tokens
        auth = _warming_auth(n_recent=20)
        msgs = build_one_call_from_snapshot(snapshot=None, authoritative_state=auth, pipeline_result=None)
        assert validate_one_call_context(msgs) == (True, "")
        wire = count_tokens(ONE_CALL_SYSTEM_PROMPT + "\n\n" + COMMERCE_SIGNAL_INSTRUCTIONS) + count_tokens(
            _json.dumps(msgs)
        )
        assert wire <= ONE_CALL_MAX_PROMPT_TOKENS
        assert estimate_one_call_tokens(msgs) <= 8192


# ── 12. Invalid commerce never executes (0 invalid, negative still fails) ─

class TestInvalidCommerce:
    def test_zero_price_never_executes_as_price(self):
        from core.one_call import validate_one_call_response
        res = validate_one_call_response(_valid_raw(requested_price=0))
        assert res.is_valid is True
        assert res.signals.requested_price is None

    def test_negative_price_still_invalid(self):
        from core.one_call import validate_one_call_response
        res = validate_one_call_response(_valid_raw(requested_price=-5))
        assert res.is_valid is False
        assert res.needs_handoff is True


# ── 13. strict:true kept ─────────────────────────────────────────────────

class TestStrictTrue:
    def test_json_schema_envelope_strict(self):
        from core.llm_provider_llamacpp import LlamaCppProvider
        p = LlamaCppProvider()
        payload = p._build_payload(
            [{"role": "user", "content": "hi"}], "m",
            response_mime_type="application/json", onecall_json_schema=True,
        )
        assert payload["response_format"]["json_schema"]["strict"] is True


# ── 14. Events intact (best-effort publish surface) ──────────────────────

class TestEventsIntact:
    def test_phase87_events_module_present(self):
        import pathlib
        assert pathlib.Path("core/phase87_events.py").exists()


# ── N1. Pass 6: zero normalization before finite>0 ───────────────────────

class TestZeroNormalization:
    def test_zero_variants_normalize_to_none(self):
        from commerce.signals import CommerceSignals
        base = dict(
            purchase_intent=0.0, content_interest=0.0, relationship_engagement=0.0,
            price_interest=0.0, explicit_purchase_request=False,
            explicit_content_request=False, declined_recent_offer=False,
            negative_sentiment=0.0, confidence=0.0, evidence=[], model_uncertainty=0.0,
        )
        for v in (0, 0.0, "0", "0.0", "0.00"):
            s = CommerceSignals(**{**base, "requested_price": v})
            assert s.requested_price is None


# ── N2. Pass 7: commerce hints only when required ────────────────────────

class TestCommerceGate:
    def test_hey_suppressed_price_required(self):
        from core.commerce_context_gate import commerce_context_required
        assert commerce_context_required("hey", "engaged", False, True) is False
        assert commerce_context_required("what is price of Red Lace?", "engaged", True, True) is True


# ── N3. Pass 8: wire 1800 hard, components soft ──────────────────────────

class TestWireBudget:
    def test_system_overage_warns_but_passes(self):
        from core.context_compact import validate_one_call_context
        msgs = [{"role": "system", "content": "S " + "y" * 2000}]
        assert validate_one_call_context(msgs) == (True, "")


# ── N4. Pass 7L: enrichment activates without textual cue ────────────────

class TestEnrichmentGate:
    def test_desire_purchase_without_cue(self):
        from core.commerce_context_gate import commerce_context_required
        assert commerce_context_required("ok sounds good", "engaged", False, True) is False
        assert commerce_context_required("ok sounds good", "engaged", False, True, "purchase", None, None) is True
