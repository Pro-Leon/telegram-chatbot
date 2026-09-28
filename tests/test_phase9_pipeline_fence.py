"""Phase 9 universal gate — pipeline threading pin tests (deterministic, no LLM/DB).

Proves the Phase 2 fence engages through the single legacy choke
(``CommercePipelineRequest`` -> ``_request_engine_kwargs`` ->
``decide_from_signals`` -> ``decide_commerce_action``) while every
legacy caller (no Phase-9 keys) keeps existing behavior:

* warm-only kill-chain input yields SOFT_OFFER unfenced (all-None) and
  RELATIONSHIP_BUILDING fenced (six keys set, no current-turn
  commercial evidence) — the ONLY sanctioned behavior change.
* step-8 explicit-buy still yields OFFER_PPV when fenced (fence bypass
  by design, deterministic verifier-backed).
* ``CommercePipelineRequest`` shape preserved (six optional fields
  default None; extras still forbidden).

All tests are pure and deterministic: no live LLM, no DB, no Redis.
"""

from __future__ import annotations

import pytest

from commerce.commerce_context_adapter import AuthorizationBasis, build_commerce_context
from commerce.content_transition import select_content_transition
from commerce.content_transition_evidence import extract_content_transition_evidence
from commerce.decision import CommerceReason, decide_commerce_action
from commerce.models import CommerceAction, PolicyDecision
from commerce.pipeline import CommercePipelineRequest, run_commerce_pipeline
from commerce.signals import CommerceSignals, decide_from_signals

pytestmark = [pytest.mark.unit]

ALLOWED = PolicyDecision(allowed=True)

WARM_TEXT = "You're gorgeous, I love talking to you"
BUY_TEXT = "I want to buy"


def _open_snapshot():
    from commerce.boundary_state import BoundarySnapshot

    return BoundarySnapshot(active=(), degraded=False)


def _adapter(text: str):
    """Mirror test_phase9_relationship_commerce.py threading style."""
    from commerce.free_photo_routing import is_photo_request
    from commerce.purchase_intent import is_explicit_purchase_request, is_price_inquiry

    ev = extract_content_transition_evidence(
        text,
        conversation_state=None,
        open_loop_subjects=(),
        has_prior_context_reference=False,
    )
    dec = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot())
    return build_commerce_context(
        transition_decision=dec,
        transition_evidence=ev,
        boundary_snapshot=_open_snapshot(),
        deterministic_buy=bool(is_explicit_purchase_request(text)),
        deterministic_price=bool(is_price_inquiry(text)),
        deterministic_photo_request=bool(is_photo_request(text, None)),
    )


def _phase9_kwargs(ctx) -> dict:
    return {
        "current_content_interest": ctx.current_content_interest,
        "current_content_disinterest": ctx.disinterest_present,
        "user_initiated_commercial": ctx.user_initiated_commercial,
        "continuation_context": ctx.continuation_context,
        "warmth_without_commercial_evidence": ctx.warmth_without_commercial_evidence,
        "authorization_basis": ctx.authorization_basis,
    }


def _low_signals(**kw) -> CommerceSignals:
    params: dict = {
        "purchase_intent": 0.0,
        "content_interest": 0.0,
        "relationship_engagement": 0.0,
        "price_interest": 0.0,
        "explicit_purchase_request": False,
        "explicit_content_request": False,
        "requested_price": None,
        "declined_recent_offer": False,
        "negative_sentiment": 0.0,
        "confidence": 0.5,
        "evidence": [],
        "model_uncertainty": 0.1,
        "primary_intent": "other",
        "intent_tags": [],
        "negative_intent_tags": [],
        "fan_asks_question": False,
    }
    params.update(kw)
    return CommerceSignals(**params)


def _messages() -> list:
    return [
        {"role": "system", "content": "persona boilerplate"},
        {"role": "user", "content": "hi there"},
    ]


class TestWarmOnlyKillChainFence:
    def test_unfenced_warmth_yields_soft_offer(self):
        # Legacy caller: all six Phase-9 keys absent (None) -> fences dormant.
        d = decide_from_signals(
            _low_signals(),
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            relationship_score=0.65,
            relationship_state="warm",
        )
        assert d.action is CommerceAction.SOFT_OFFER
        assert d.reason_code is CommerceReason.RELATIONSHIP_READY
        assert d.allowed is True

    def test_fenced_warmth_becomes_relationship_building(self):
        # THE sanctioned behavior change: same input + Phase-9 warmth evidence.
        ctx = _adapter(WARM_TEXT)
        assert ctx.warmth_without_commercial_evidence is True
        assert ctx.authorization_basis == AuthorizationBasis.NONE.value
        assert ctx.user_initiated_commercial is False
        d = decide_from_signals(
            _low_signals(),
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            relationship_score=0.65,
            relationship_state="warm",
            **_phase9_kwargs(ctx),
        )
        assert d.action is not CommerceAction.SOFT_OFFER
        assert d.action is CommerceAction.RELATIONSHIP_BUILDING
        assert d.allowed is False

    def test_fenced_explicit_buy_still_offer_ppv(self):
        # Step 8 bypasses fences by design (deterministic verifier-backed).
        ctx = _adapter(BUY_TEXT)
        assert ctx.authorization_basis == AuthorizationBasis.EXPLICIT_TEXT.value
        assert ctx.user_initiated_commercial is True
        d = decide_from_signals(
            _low_signals(explicit_purchase_request=True),
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message=BUY_TEXT,
            **_phase9_kwargs(ctx),
        )
        assert d.action is CommerceAction.OFFER_PPV
        assert d.reason_code is CommerceReason.STRONG_BUYING_SIGNAL
        assert d.allowed is True


class TestPipelineRequestPhase9Shape:
    def test_phase9_fields_default_none(self):
        req = CommercePipelineRequest(
            user_id=1,
            creator_id=2,
            messages=_messages(),
            eligibility=ALLOWED,
        )
        assert req.current_content_interest is None
        assert req.current_content_disinterest is None
        assert req.user_initiated_commercial is None
        assert req.continuation_context is None
        assert req.warmth_without_commercial_evidence is None
        assert req.authorization_basis is None

    def test_extras_still_forbidden(self):
        with pytest.raises(Exception):
            CommercePipelineRequest(
                user_id=1,
                creator_id=2,
                messages=_messages(),
                eligibility=ALLOWED,
                bogus_field=True,  # type: ignore[call-arg]
            )


class TestPipelineThreadingEndToEnd:
    @pytest.mark.asyncio
    async def test_pipeline_unfenced_warmth_soft_offer(self):
        req = CommercePipelineRequest(
            user_id=1,
            creator_id=2,
            messages=_messages(),
            eligibility=ALLOWED,
            relationship_score=0.65,
            relationship_state="warm",
        )
        result = await run_commerce_pipeline(req, signals=_low_signals())
        assert result.decision.action is CommerceAction.SOFT_OFFER
        assert result.decision.reason_code is CommerceReason.RELATIONSHIP_READY

    @pytest.mark.asyncio
    async def test_pipeline_fenced_warmth_no_soft_offer(self):
        ctx = _adapter(WARM_TEXT)
        req = CommercePipelineRequest(
            user_id=1,
            creator_id=2,
            messages=_messages(),
            eligibility=ALLOWED,
            relationship_score=0.65,
            relationship_state="warm",
            **_phase9_kwargs(ctx),
        )
        result = await run_commerce_pipeline(req, signals=_low_signals())
        assert result.decision.action is not CommerceAction.SOFT_OFFER
        assert result.decision.action is CommerceAction.RELATIONSHIP_BUILDING
        assert result.decision.allowed is False

    def test_direct_engine_parity_with_pipeline_kwargs(self):
        # The pipeline threads request fields verbatim: direct-engine call
        # with the same kwargs must agree with the pipelined verdict.
        ctx = _adapter(WARM_TEXT)
        direct = decide_from_signals(
            _low_signals(),
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            relationship_score=0.65,
            relationship_state="warm",
            **_phase9_kwargs(ctx),
        )
        assert direct.action is CommerceAction.RELATIONSHIP_BUILDING
        assert direct.allowed is False
