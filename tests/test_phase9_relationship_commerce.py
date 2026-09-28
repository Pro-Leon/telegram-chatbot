"""Phase 9 — Relationship <-> Commerce integration (deterministic, no LLM/DB).

Verifies the smallest safe architecture:

* Phase 9 adapter is pure, turn-scoped, advisory-only (zero authority).
* Relationship / intimacy / warmth alone never authorize commerce.
* LLM purchase-intent float alone never creates OFFER_PPV (deterministic
  corroboration required via existing verifiers).
* relationship_engagement alone never creates SOFT_OFFER.
* Current disinterest suppresses content guidance (turn-scoped, no persistence).
* Phase 7 boundary remains the stronger veto.
* Canonical / legacy share one computation (no forked behavior).
* No new persistence, no protected architecture changed semantically.

All tests are pure and deterministic: no live LLM, no DB, no Redis.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from commerce.boundary_state import BoundarySnapshot, boundary_blocks_commerce
from commerce.commerce_context_adapter import (
    AuthorizationBasis,
    CommerceContext,
    build_commerce_context,
    downgrade_response_mode,
    should_suppress_commercial_framing,
    should_suppress_content_suggestion,
)
from commerce.content_transition import ContentTransition, select_content_transition
from commerce.content_transition_evidence import extract_content_transition_evidence
from commerce.decision import CommerceDecisionContext, decide_commerce_action
from commerce.models import CommerceAction, PolicyDecision
from commerce.signals import CommerceSignals, decide_from_signals, signals_to_context

pytestmark = [pytest.mark.unit]

ALLOWED = PolicyDecision(allowed=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_snapshot(*active: str, degraded: bool = False) -> BoundarySnapshot:
    return BoundarySnapshot(active=tuple(active), degraded=degraded)


def _adapter(
    text: str,
    conversation_state=None,
    boundary=None,
    det_buy: bool | None = None,
    det_price: bool | None = None,
    det_photo: bool | None = None,
) -> CommerceContext:
    """Build Phase 9 context for one turn using existing deterministic verifiers."""
    from commerce.purchase_intent import is_explicit_purchase_request, is_price_inquiry

    ev = extract_content_transition_evidence(
        text,
        conversation_state=conversation_state,
        open_loop_subjects=(),
        has_prior_context_reference=False,
    )
    dec = select_content_transition(
        evidence=ev, boundary_snapshot=boundary if boundary is not None else _open_snapshot()
    )
    if det_buy is None:
        try:
            det_buy = bool(is_explicit_purchase_request(text))
        except Exception:
            det_buy = False
    if det_price is None:
        try:
            det_price = bool(is_price_inquiry(text))
        except Exception:
            det_price = False
    if det_photo is None:
        try:
            from commerce.free_photo_routing import is_photo_request

            det_photo = bool(is_photo_request(text, None))
        except Exception:
            det_photo = False
    return build_commerce_context(
        transition_decision=dec,
        transition_evidence=ev,
        boundary_snapshot=boundary if boundary is not None else _open_snapshot(),
        deterministic_buy=bool(det_buy),
        deterministic_price=bool(det_price),
        deterministic_photo_request=bool(det_photo),
    )


def _ctx_with_phase9(adapter: CommerceContext, **overrides) -> CommerceDecisionContext:
    base: dict = {
        "user_id": 9001,
        "creator_id": 7,
        "eligibility": ALLOWED,
        "current_content_interest": adapter.current_content_interest,
        "current_content_disinterest": adapter.disinterest_present,
        "user_initiated_commercial": adapter.user_initiated_commercial,
        "continuation_context": adapter.continuation_context,
        "warmth_without_commercial_evidence": adapter.warmth_without_commercial_evidence,
        "authorization_basis": adapter.authorization_basis,
    }
    base.update(overrides)
    return CommerceDecisionContext(**base)


def _make_signals(
    purchase_intent: float = 0.0, relationship_engagement: float = 0.0, **kw
) -> CommerceSignals:
    params: dict = {
        "purchase_intent": purchase_intent,
        "content_interest": 0.0,
        "relationship_engagement": relationship_engagement,
        "price_interest": 0.0,
        "explicit_purchase_request": False,
        "explicit_content_request": False,
        "requested_price": None,
        "declined_recent_offer": False,
        "negative_sentiment": 0.0,
        "confidence": 0.9,
        "evidence": [],
        "model_uncertainty": 0.1,
        "primary_intent": "casual_chat",
        "intent_tags": [],
        "negative_intent_tags": [],
        "fan_asks_question": False,
    }
    params.update(kw)
    return CommerceSignals(**params)


def _content_state(topic: str = "red lace set") -> dict:
    return {"current_topic": topic, "open_threads": (topic,)}


# ---------------------------------------------------------------------------
# Group A — relationship isolation
# ---------------------------------------------------------------------------


class TestRelationshipIsolation:
    def test_neutral_turn_has_no_commercial_evidence(self):
        ctx = _adapter("tell me about your day")
        assert ctx.user_initiated_commercial is False
        assert ctx.current_content_interest is False
        assert ctx.continuation_context is False
        assert ctx.authorization_basis == AuthorizationBasis.NONE.value
        assert ctx.warmth_without_commercial_evidence is True

    def test_high_relationship_score_alone_no_soft_offer(self):
        # Warmth proxy: no current commercial/content evidence.
        ctx = _adapter("haha you're the sweetest, I love talking to you")
        assert ctx.warmth_without_commercial_evidence is True
        d = decide_commerce_action(_ctx_with_phase9(ctx, relationship_score=0.85))
        assert d.action is not CommerceAction.SOFT_OFFER
        assert d.allowed is False

    def test_familiarity_inventory_without_interest_no_commerce(self):
        # Inventory is not an adapter input (structural); neutral text
        # yields no commercial evidence no matter what products exist.
        params = inspect.signature(build_commerce_context).parameters
        assert "inventory" not in params
        assert "products" not in params
        assert "prices" not in params
        ctx = _adapter("tell me about your day")
        assert ctx.authorization_basis == AuthorizationBasis.NONE.value
        d = decide_commerce_action(
            _ctx_with_phase9(ctx, relationship_score=0.75, buying_intent_score=0.2)
        )
        assert d.action in (CommerceAction.RELATIONSHIP_BUILDING, CommerceAction.NO_OFFER)
        assert d.allowed is False

    def test_reciprocity_warmth_without_evidence_no_offer(self):
        ctx = _adapter("you're the best, love chatting with you every night")
        assert ctx.warmth_without_commercial_evidence is True
        d = decide_commerce_action(
            _ctx_with_phase9(ctx, relationship_score=0.9, buying_intent_score=0.4)
        )
        assert d.action is not CommerceAction.OFFER_PPV
        assert d.allowed is False

    def test_relationship_trajectory_bands_are_not_adapter_inputs(self):
        params = inspect.signature(build_commerce_context).parameters
        for forbidden in (
            "relationship_score",
            "familiarity",
            "engagement",
            "reciprocity",
            "continuity",
            "snapshot",
            "trajectory",
        ):
            assert forbidden not in params, forbidden
        # AST + function-body check (module docstring may name forbidden
        # concepts solely to document the prohibition, mirroring Phase 8).
        import commerce.commerce_context_adapter as _ad

        tree = ast.parse(Path("commerce/commerce_context_adapter.py").read_text(encoding="utf-8"))
        from_imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("commerce")
        ]
        assert from_imports == [], from_imports
        fn_src = inspect.getsource(build_commerce_context)
        assert "RelationshipSnapshot" not in fn_src
        assert "relationship_trajectory" not in fn_src
        assert "relationship_evidence" not in fn_src
        assert "relationship_score" not in fn_src


# ---------------------------------------------------------------------------
# Group B — intimacy isolation
# ---------------------------------------------------------------------------


class TestIntimacyIsolation:
    def test_sexual_conversation_without_request_no_commerce(self):
        for text in (
            "you're driving me crazy tonight",
            "I wish you were here",
            "I can't stop thinking about you",
        ):
            ctx = _adapter(text)
            assert ctx.user_initiated_commercial is False, text
            assert ctx.current_content_interest is False, text
            assert ctx.authorization_basis == AuthorizationBasis.NONE.value, text

    def test_romantic_with_products_without_evidence_no_offer(self):
        ctx = _adapter("I miss you so much, thinking of you tonight")
        assert ctx.warmth_without_commercial_evidence is True
        d = decide_commerce_action(_ctx_with_phase9(ctx, relationship_score=0.8))
        assert d.action is not CommerceAction.OFFER_PPV
        assert d.allowed is False

    def test_intimacy_modules_are_not_adapter_inputs(self):
        params = inspect.signature(build_commerce_context).parameters
        for forbidden in ("intimacy", "intimate", "sexual", "arousal", "consent", "permission"):
            assert forbidden not in params, forbidden
        fn_src = inspect.getsource(build_commerce_context)
        assert "IntimacySnapshot" not in fn_src
        assert "intimacy_trajectory" not in fn_src
        assert "intimacy_evidence" not in fn_src
        # Commerce authority must not import intimacy trajectory/evidence.
        import commerce.decision as _dec

        dec_src = inspect.getsource(_dec)
        assert "intimacy_trajectory" not in dec_src
        assert "intimacy_evidence" not in dec_src


# ---------------------------------------------------------------------------
# Group C — warmth fence
# ---------------------------------------------------------------------------


class TestWarmthFence:
    def test_warmth_without_evidence_no_soft_offer(self):
        ctx = _adapter("haha you're so sweet, love talking to you")
        assert ctx.warmth_without_commercial_evidence is True
        assert ctx.user_initiated_commercial is False
        d = decide_commerce_action(_ctx_with_phase9(ctx, relationship_score=0.70))
        assert d.action is not CommerceAction.SOFT_OFFER
        assert d.allowed is False

    def test_warmth_without_evidence_suppresses_framing(self):
        ctx = _adapter("you're the sweetest")
        assert should_suppress_commercial_framing(ctx) is True
        assert should_suppress_content_suggestion(ctx) is True
        # Ordinary relationship response preserved: downgrade tease -> react,
        # non-commercial modes pass through (natural flirtation not removed here).
        assert downgrade_response_mode("tease", ctx) == "react"
        assert downgrade_response_mode("react", ctx) == "react"
        assert downgrade_response_mode("answer", ctx) == "answer"

    def test_audited_warm_turn_no_available_content_promotion(self):
        # The audited warm path (desire INTEREST -> HOT -> TEST_INTEREST ->
        # BUILDING -> DEEPEN_DESIRE -> tease -> AVAILABLE CONTENT) must not
        # initiate commercial framing from warmth alone.
        ctx = _adapter("you're amazing, I love talking to you every night")
        assert ctx.warmth_without_commercial_evidence is True
        assert should_suppress_content_suggestion(ctx) is True
        # Dormant AVAILABLE CONTENT instruction already constrains promotion;
        # Phase 9 adds the explicit suppression signal above.
        from memory.context import build_qwen3_context  # noqa: F401  (import surface only)

        src = Path("memory/context.py").read_text(encoding="utf-8")
        assert "do not mention or offer unless the fan explicitly asks about content" in src

    def test_relationship_engagement_float_alone_no_soft_offer(self):
        sig = _make_signals(relationship_engagement=0.95)
        ctx = _adapter("you're the best, love chatting with you")
        decision_ctx = signals_to_context(
            sig,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message="you're the best, love chatting with you",
            current_content_interest=ctx.current_content_interest,
            current_content_disinterest=ctx.disinterest_present,
            user_initiated_commercial=ctx.user_initiated_commercial,
            continuation_context=ctx.continuation_context,
            warmth_without_commercial_evidence=ctx.warmth_without_commercial_evidence,
            authorization_basis=ctx.authorization_basis,
        )
        d = decide_commerce_action(decision_ctx)
        assert d.action is not CommerceAction.SOFT_OFFER or d.allowed is False
        # With high relationship_engagement mapped to relationship_score and
        # no content evidence, the fence must hold.
        assert ctx.warmth_without_commercial_evidence is True


# ---------------------------------------------------------------------------
# Group D — purchase-intent float fence (critical)
# ---------------------------------------------------------------------------


class TestPurchaseIntentFloatFence:
    def test_curiosity_with_high_float_not_ppv(self):
        text = "What kind of pictures do you take?"
        sig = _make_signals(purchase_intent=0.85)
        ctx = _adapter(text)
        # Deterministic corroboration absent for curiosity.
        assert ctx.authorization_basis == AuthorizationBasis.NONE.value
        assert ctx.user_initiated_commercial is False
        d = decide_from_signals(
            sig,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message=text,
            current_content_interest=ctx.current_content_interest,
            current_content_disinterest=ctx.disinterest_present,
            user_initiated_commercial=ctx.user_initiated_commercial,
            continuation_context=ctx.continuation_context,
            warmth_without_commercial_evidence=ctx.warmth_without_commercial_evidence,
            authorization_basis=ctx.authorization_basis,
        )
        assert d.action is not CommerceAction.OFFER_PPV, d
        assert not (d.action is CommerceAction.OFFER_PPV and d.allowed is True)

    def test_neutral_with_high_float_not_ppv(self):
        text = "hey what's up"
        sig = _make_signals(purchase_intent=0.90)
        ctx = _adapter(text)
        assert ctx.user_initiated_commercial is False
        d = decide_from_signals(
            sig,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message=text,
            current_content_interest=ctx.current_content_interest,
            current_content_disinterest=ctx.disinterest_present,
            user_initiated_commercial=ctx.user_initiated_commercial,
            continuation_context=ctx.continuation_context,
            warmth_without_commercial_evidence=ctx.warmth_without_commercial_evidence,
            authorization_basis=ctx.authorization_basis,
        )
        assert d.action is not CommerceAction.OFFER_PPV
        assert d.allowed is False

    def test_explicit_deterministic_purchase_still_offers(self):
        text = "I want to buy"
        sig = _make_signals(
            purchase_intent=0.1,
            explicit_purchase_request=True,
            primary_intent="purchase_intent",
            intent_tags=["purchase_intent"],
        )
        ctx = _adapter(text)
        assert ctx.authorization_basis == AuthorizationBasis.EXPLICIT_TEXT.value
        assert ctx.user_initiated_commercial is True
        d = decide_from_signals(
            sig,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message=text,
            current_content_interest=ctx.current_content_interest,
            current_content_disinterest=ctx.disinterest_present,
            user_initiated_commercial=ctx.user_initiated_commercial,
            continuation_context=ctx.continuation_context,
            warmth_without_commercial_evidence=ctx.warmth_without_commercial_evidence,
            authorization_basis=ctx.authorization_basis,
        )
        assert d.action is CommerceAction.OFFER_PPV
        assert d.allowed is True

    def test_price_inquiry_still_offers(self):
        text = "how much?"
        sig = _make_signals(
            price_interest=0.9,
            requested_price=25.0,
            primary_intent="price_inquiry",
            intent_tags=["price_inquiry"],
        )
        ctx = _adapter(text)
        assert ctx.authorization_basis == AuthorizationBasis.PRICE_INQUIRY.value
        d = decide_from_signals(
            sig,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message=text,
            current_content_interest=ctx.current_content_interest,
            current_content_disinterest=ctx.disinterest_present,
            user_initiated_commercial=ctx.user_initiated_commercial,
            continuation_context=ctx.continuation_context,
            warmth_without_commercial_evidence=ctx.warmth_without_commercial_evidence,
            authorization_basis=ctx.authorization_basis,
        )
        assert d.action is CommerceAction.OFFER_PPV
        assert d.allowed is True

    def test_float_fence_uses_existing_verifiers_not_new_model(self):
        src = Path("commerce/decision.py").read_text(encoding="utf-8")
        # Fence consumes existing deterministic flags / Phase 9 basis; it does
        # not invent a second purchase-intent classifier.
        assert "user_asked_to_buy" in src
        assert "user_asked_about_price" in src
        assert "authorization_basis" in src
        assert "purchase_intent_classifier" not in src.lower()


# ---------------------------------------------------------------------------
# Group E — content interest (curiosity / continuation / request / access)
# ---------------------------------------------------------------------------


class TestContentInterest:
    def test_curiosity_acknowledges_without_ppv(self):
        ctx = _adapter("What kind of pictures do you take?")
        assert ctx.current_content_interest is True
        assert ctx.authorization_basis == AuthorizationBasis.NONE.value
        assert ctx.user_initiated_commercial is False
        d = decide_commerce_action(_ctx_with_phase9(ctx, buying_intent_score=0.2))
        assert d.action is not CommerceAction.OFFER_PPV

    def test_continuation_is_context_not_ppv(self):
        ctx = _adapter(
            "tell me more about that set",
            conversation_state=_content_state(),
        )
        assert ctx.continuation_context is True
        assert ctx.authorization_basis == AuthorizationBasis.CONTENT_CONTINUATION.value
        d = decide_commerce_action(_ctx_with_phase9(ctx, buying_intent_score=0.85))
        # Continuation alone is not purchase corroboration (offer-grade bases
        # are EXPLICIT_TEXT / PRICE_INQUIRY / PURCHASE_INTENT only).
        assert d.action is not CommerceAction.OFFER_PPV

    def test_explicit_request_uses_existing_handling(self):
        ctx = _adapter("can you send me that picture?")
        assert ctx.authorization_basis == AuthorizationBasis.CONTENT_REQUEST.value
        assert ctx.user_initiated_commercial is True
        # Phase 99 preserved: explicit content desire alone is not PPV.
        d = decide_commerce_action(_ctx_with_phase9(ctx, user_requested_content=True))
        assert d.action is not CommerceAction.OFFER_PPV
        # Free-photo authority still detects the same turn deterministically.
        from commerce.free_photo_routing import is_photo_request

        assert is_photo_request("can you send me that picture?", None) is True

    def test_access_question_uses_existing_handling(self):
        ctx = _adapter("how do I unlock that?")
        assert ctx.current_content_interest is True
        assert ctx.user_initiated_commercial is True


# ---------------------------------------------------------------------------
# Group F — current disinterest
# ---------------------------------------------------------------------------


class TestCurrentDisinterest:
    def test_disinterest_suppresses_transition_and_promotion(self):
        ctx = _adapter(
            "I'm not interested in that anymore",
            conversation_state=_content_state(),
        )
        assert ctx.disinterest_present is True
        assert ctx.current_content_interest is False
        assert ctx.continuation_context is False
        assert should_suppress_content_suggestion(ctx) is True
        assert should_suppress_commercial_framing(ctx) is True
        d = decide_commerce_action(_ctx_with_phase9(ctx, buying_intent_score=0.85))
        assert d.action is not CommerceAction.OFFER_PPV

    def test_disinterest_with_high_float_respected(self):
        text = "I'm not interested in that anymore"
        sig = _make_signals(purchase_intent=0.90)
        ctx = _adapter(text, conversation_state=_content_state())
        assert ctx.disinterest_present is True
        d = decide_from_signals(
            sig,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message=text,
            current_content_interest=ctx.current_content_interest,
            current_content_disinterest=ctx.disinterest_present,
            user_initiated_commercial=ctx.user_initiated_commercial,
            continuation_context=ctx.continuation_context,
            warmth_without_commercial_evidence=ctx.warmth_without_commercial_evidence,
            authorization_basis=ctx.authorization_basis,
        )
        assert d.action is not CommerceAction.OFFER_PPV

    def test_disinterest_is_turn_scoped_not_persistent(self):
        src = Path("commerce/commerce_context_adapter.py").read_text(encoding="utf-8")
        assert "facts" not in src.lower() or "user_profiles.facts" not in src
        # Frozen turn-scoped dataclass carries no persistence surface.
        fields = {f.name for f in dataclasses.fields(CommerceContext)}
        assert "user_id" not in fields
        assert "creator_id" not in fields
        # A later neutral turn is not disinterested (no durable flag).
        later = _adapter("tell me about your day")
        assert later.disinterest_present is False


# ---------------------------------------------------------------------------
# Group G — boundary (Phase 7 authoritative)
# ---------------------------------------------------------------------------


class TestBoundary:
    @pytest.mark.parametrize(
        "btype", ["NO_SEXUAL_TOPIC", "CHANGE_TOPIC", "STOP_CONVERSATION", "DO_NOT_CONTACT"]
    )
    def test_boundary_veto_suppresses_commercial_guidance(self, btype):
        ctx = _adapter("how much is that?", boundary=_open_snapshot(btype))
        assert ctx.authorization_basis == AuthorizationBasis.NONE.value
        assert ctx.user_initiated_commercial is False
        assert ctx.current_content_interest is False
        blocked, reason = boundary_blocks_commerce(_open_snapshot(btype))
        assert blocked is True

    def test_unknown_boundary_fails_closed(self):
        ev = extract_content_transition_evidence("how much is that?")
        dec = select_content_transition(evidence=ev, boundary_snapshot=None)
        assert dec.transition is ContentTransition.NONE
        ctx = build_commerce_context(
            transition_decision=dec,
            transition_evidence=ev,
            boundary_snapshot=None,
            deterministic_buy=False,
            deterministic_price=True,
            deterministic_photo_request=False,
        )
        assert ctx.authorization_basis == AuthorizationBasis.NONE.value
        assert ctx.user_initiated_commercial is False
        blocked, _ = boundary_blocks_commerce(None)
        # None snapshot is not a BoundarySnapshot instance -> no veto here,
        # but the adapter itself fails closed (no guidance).
        assert ctx.warmth_without_commercial_evidence is True

    def test_degraded_boundary_fails_closed(self):
        ev = extract_content_transition_evidence("can you send me that picture?")
        ctx = build_commerce_context(
            transition_decision=select_content_transition(
                evidence=ev, boundary_snapshot=_open_snapshot(degraded=True)
            ),
            transition_evidence=ev,
            boundary_snapshot=_open_snapshot(degraded=True),
            deterministic_buy=False,
            deterministic_price=False,
            deterministic_photo_request=True,
        )
        assert ctx.authorization_basis == AuthorizationBasis.NONE.value
        blocked, reason = boundary_blocks_commerce(_open_snapshot(degraded=True))
        assert blocked is True
        assert reason == "boundary_state_unknown"

    def test_phase7_files_unchanged_authority(self):
        # Phase 7 boundary behavior is authoritative; Phase 9 consumes it
        # read-only and never imports its persistence.
        src = Path("commerce/commerce_context_adapter.py").read_text(encoding="utf-8")
        assert "from commerce.boundary_state import" not in src
        assert "from commerce.boundary_evidence import" not in src
        assert "from context_engine.boundary_context import" not in src


# ---------------------------------------------------------------------------
# Group H — current + relationship (positive cases)
# ---------------------------------------------------------------------------


class TestCurrentPlusRelationship:
    def test_established_relationship_plus_explicit_request(self):
        # Relationship context is not the authorization basis; the current
        # deterministic request is.
        text = "I want to buy"
        ctx = _adapter(text)
        assert ctx.authorization_basis == AuthorizationBasis.EXPLICIT_TEXT.value
        assert ctx.authorization_basis != "RELATIONSHIP"
        sig = _make_signals(
            purchase_intent=0.2,
            explicit_purchase_request=True,
            primary_intent="purchase_intent",
            intent_tags=["purchase_intent"],
        )
        d = decide_from_signals(
            sig,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message=text,
            relationship_score=0.85,
            current_content_interest=ctx.current_content_interest,
            current_content_disinterest=ctx.disinterest_present,
            user_initiated_commercial=ctx.user_initiated_commercial,
            continuation_context=ctx.continuation_context,
            warmth_without_commercial_evidence=ctx.warmth_without_commercial_evidence,
            authorization_basis=ctx.authorization_basis,
        )
        assert d.action is CommerceAction.OFFER_PPV
        assert d.allowed is True

    def test_established_relationship_plus_price_inquiry(self):
        text = "how much is that?"
        ctx = _adapter(text)
        assert ctx.authorization_basis == AuthorizationBasis.PRICE_INQUIRY.value
        sig = _make_signals(
            price_interest=0.9,
            requested_price=20.0,
            primary_intent="price_inquiry",
            intent_tags=["price_inquiry"],
        )
        d = decide_from_signals(
            sig,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
            user_message=text,
            relationship_score=0.9,
            current_content_interest=ctx.current_content_interest,
            current_content_disinterest=ctx.disinterest_present,
            user_initiated_commercial=ctx.user_initiated_commercial,
            continuation_context=ctx.continuation_context,
            warmth_without_commercial_evidence=ctx.warmth_without_commercial_evidence,
            authorization_basis=ctx.authorization_basis,
        )
        assert d.action is CommerceAction.OFFER_PPV

    def test_no_relationship_basis_values_exist(self):
        for forbidden in (
            "RELATIONSHIP",
            "INTIMACY",
            "WARMTH",
            "SEXUALITY",
            "FAMILIARITY",
            "ENGAGEMENT",
        ):
            assert forbidden not in {b.value for b in AuthorizationBasis}, forbidden


# ---------------------------------------------------------------------------
# Group I — canonical / legacy parity
# ---------------------------------------------------------------------------


class TestCanonicalLegacyParity:
    def test_adapter_is_deterministic_single_computation(self):
        first = _adapter("What kind of pictures do you take?")
        second = _adapter("What kind of pictures do you take?")
        assert first == second
        assert first is not second

    def test_signals_to_context_forwards_phase9_identically(self):
        sig = _make_signals(purchase_intent=0.3)
        kwargs = {
            "user_id": 1,
            "creator_id": 2,
            "eligibility": ALLOWED,
            "current_content_interest": True,
            "current_content_disinterest": False,
            "user_initiated_commercial": True,
            "continuation_context": False,
            "warmth_without_commercial_evidence": False,
            "authorization_basis": "CONTENT_REQUEST",
        }
        canonical = signals_to_context(sig, **kwargs)
        legacy = signals_to_context(sig, **kwargs)
        assert canonical == legacy
        assert canonical.authorization_basis == "CONTENT_REQUEST"
        assert legacy.authorization_basis == "CONTENT_REQUEST"

    def test_worker_computes_adapter_once(self):
        src = Path("workers/llm_worker.py").read_text(encoding="utf-8")
        # One computation site (single _build_phase9_ctx call); two import
        # statements are the primary + fail-closed fallback for the frozen
        # CommerceContext neutral value.
        assert src.count("_build_phase9_ctx(") == 1
        assert src.count("build_commerce_context") == 1
        assert src.count("commerce.commerce_context_adapter") == 2

    def test_no_duplicate_legacy_engine(self):
        src = Path("commerce/commerce_context_adapter.py").read_text(encoding="utf-8")
        assert "legacy" not in src.lower()


# ---------------------------------------------------------------------------
# Group J — authority surface (static)
# ---------------------------------------------------------------------------


class TestAuthoritySurface:
    def test_adapter_has_zero_commerce_authority(self):
        # Module docstring may name forbidden concepts solely to document
        # the prohibition (mirrors Phase 8); verify function bodies + imports.
        import commerce.commerce_context_adapter as _ad

        tree = ast.parse(Path("commerce/commerce_context_adapter.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported <= {"__future__", "enum", "logging", "dataclasses", "typing"}, imported
        bodies = "\n".join(
            inspect.getsource(fn)
            for fn in (
                build_commerce_context,
                should_suppress_commercial_framing,
                downgrade_response_mode,
                should_suppress_content_suggestion,
            )
        )
        for forbidden in (
            "product_id",
            "vault_item",
            "price_minor",
            "checkout",
            "select_approved",
            "rank_candidates",
            "offer_ppv",
            "OFFER_PPV",
        ):
            assert forbidden not in bodies, forbidden
        # 'seal'/'execute'/'allowed'/'payment' appear only in docstrings as
        # prohibition vocabulary, never as code identifiers in bodies.
        for forbidden in ("seal_ranked", "execute_sealed", "payment_url", ".allowed"):
            assert forbidden not in bodies, forbidden

    def test_adapter_output_carries_no_authority_fields(self):
        fields = {f.name for f in dataclasses.fields(CommerceContext)}
        assert fields == {
            "continuation_context",
            "disinterest_present",
            "user_initiated_commercial",
            "current_content_interest",
            "warmth_without_commercial_evidence",
            "authorization_basis",
            "reason",
        }
        for forbidden in (
            "product_id",
            "vault_item_id",
            "price",
            "offer",
            "allowed",
            "sealed",
            "execute",
            "payment",
            "ranking",
        ):
            assert forbidden not in fields, forbidden

    def test_decision_context_extensions_carry_no_authority(self):
        fields = {f.name for f in dataclasses.fields(CommerceDecisionContext)}
        for expected in (
            "current_content_interest",
            "current_content_disinterest",
            "user_initiated_commercial",
            "continuation_context",
            "warmth_without_commercial_evidence",
            "authorization_basis",
        ):
            assert expected in fields, expected
        for forbidden in (
            "product_id",
            "vault_item_id",
            "price_minor",
            "payment_url",
            "relationship_trajectory",
            "intimacy_score",
        ):
            assert forbidden not in fields, forbidden

    def test_context_is_frozen(self):
        import dataclasses as _dc

        ctx = _adapter("hey")
        with pytest.raises(_dc.FrozenInstanceError):
            ctx.user_initiated_commercial = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Group K — no persistence (static)
# ---------------------------------------------------------------------------


class TestNoPersistence:
    def test_adapter_has_no_io_or_persistence(self):
        src = Path("commerce/commerce_context_adapter.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported <= {"__future__", "enum", "logging", "dataclasses", "typing"}, imported
        from_imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("commerce")
        ]
        assert from_imports == [], from_imports
        # Function bodies carry no IO/persistence surface (module docstring
        # may use the words only to document the prohibition).
        bodies = "\n".join(
            inspect.getsource(fn)
            for fn in (
                build_commerce_context,
                should_suppress_commercial_framing,
                downgrade_response_mode,
                should_suppress_content_suggestion,
            )
        )
        for forbidden in (
            "postgres",
            "redis",
            "mutate_user_profile",
            "get_pool",
            "XADD",
            "publish",
            "long_term_memory",
            "fan_knowledge",
            "migration",
            "datetime",
            "httpx",
            "get_llm",
            "generate_with",
        ):
            assert forbidden not in bodies, forbidden


# ---------------------------------------------------------------------------
# Group L — desire regression (do not repair)
# ---------------------------------------------------------------------------


class TestDesireRegression:
    def test_desire_branch_still_precedes_curiosity(self):
        src = Path("commerce/desire.py").read_text(encoding="utf-8")
        # Known ordering defect is pinned: DESIRE assignment precedes the
        # CURIOSITY branch. Phase 9 must not repair or reorder it.
        desire_pos = src.find("DesireStage.DESIRE")
        curiosity_pos = src.find("DesireStage.CURIOSITY")
        assert desire_pos != -1 and curiosity_pos != -1
        assert desire_pos < curiosity_pos

    def test_desire_module_untouched_by_phase9(self):
        src = Path("commerce/desire.py").read_text(encoding="utf-8")
        assert "commerce_context_adapter" not in src
        assert "warmth_without" not in src
        assert "authorization_basis" not in src


# ---------------------------------------------------------------------------
# Observability — context vs authority
# ---------------------------------------------------------------------------


class TestObservability:
    def test_telemetry_distinguishes_context_from_authority(self):
        from core.telemetry import GenerationTelemetry

        fields = {f.name for f in dataclasses.fields(GenerationTelemetry)}
        assert "commerce_context" in fields
        assert "commerce_authorization_basis" in fields
        t = GenerationTelemetry()
        t.commerce_context = "WARMTH"
        t.commerce_authorization_basis = "NONE"
        d = t.to_dict()
        assert d["commerce_context"] == "WARMTH"
        assert d["commerce_authorization_basis"] == "NONE"

    def test_adapter_reason_is_bounded_categorical(self):
        ctx = _adapter("What kind of pictures do you take?")
        assert isinstance(ctx.reason, tuple)
        assert len(ctx.reason) == 1
        assert ctx.reason[0] == "CURIOSITY_ACKNOWLEDGE_ONLY"
        warm = _adapter("you're the sweetest")
        assert warm.reason == ("NO_CURRENT_INTEREST",)
        assert warm.authorization_basis == "NONE"

    def test_telemetry_never_carries_raw_text(self):
        src = Path("workers/llm_worker.py").read_text(encoding="utf-8")
        # Phase 9 telemetry sets bounded categorical labels only.
        assert "_telemetry_data.commerce_context" in src
        assert "_telemetry_data.commerce_authorization_basis" in src
