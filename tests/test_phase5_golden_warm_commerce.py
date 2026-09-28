"""Phase 5 golden — warm/commerce multi-turn chain (Driver 1 only).

Pure commerce derivation per turn, no fixtures beyond literals, no DB,
no LLM, no network. Style precedent: tests/test_phase3_provenance.py
(direct calls). Scenarios A/D/E/F/H per the Phase 5 audit.

Composition pins (overlap, not duplicates — fragments live in):
- phase9 fence pins: tests/test_phase9_relationship_commerce.py:154-488
- token/fence pins: tests/test_phase3_provenance.py
- CTA pins: tests/test_phase4_commercial_cta.py
- verifier pins: phase6/phase14/75b/75e suites (untouched)
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

# Exact probe strings per the Phase 5 audit (do not paraphrase: the
# extractors match on exact phrasing, e.g. the curiosity patterns at
# commerce/content_transition_evidence.py:162-190).
WARM_FIVE = (
    "You seem really sweet",
    "I love talking to you",
    "You're gorgeous",
    "I really like your personality",
    "You make me smile",
)
CURIOSITY_MSG = "Do you have anything exclusive?"
PRICE_MSG = "How much for a video?"
BUY_MSG = "I want to buy your bundle"


def _phase9(text: str):
    """Phase-9 context for one turn (mirrors the phase-9 suite helper)."""
    from commerce.boundary_state import BoundarySnapshot
    from commerce.commerce_context_adapter import build_commerce_context
    from commerce.content_transition import select_content_transition
    from commerce.content_transition_evidence import extract_content_transition_evidence
    from commerce.free_photo_routing import is_photo_request
    from commerce.purchase_intent import is_explicit_purchase_request, is_price_inquiry

    snapshot = BoundarySnapshot(active=(), degraded=False)
    ev = extract_content_transition_evidence(text)
    dec = select_content_transition(evidence=ev, boundary_snapshot=snapshot)
    return (
        ev,
        dec,
        build_commerce_context(
            transition_decision=dec,
            transition_evidence=ev,
            boundary_snapshot=snapshot,
            deterministic_buy=bool(is_explicit_purchase_request(text)),
            deterministic_price=bool(is_price_inquiry(text)),
            deterministic_photo_request=bool(is_photo_request(text, None)),
        ),
    )


def _commerce_turn(
    *,
    relationship_state: str = "warm",
    relationship_score: float = 0.65,
    purchase_intent: float = 0.1,
    explicit_purchase_request: bool = False,
    consecutive_rejections: int = 0,
    aftercare_status: str = "none",
    user_asked_to_buy: bool = False,
    user_asked_about_price: bool = False,
    phase9: dict | None = None,
):
    """One Driver-1 turn: desire -> temp -> readiness(+Phase-101) ->
    window -> objective -> decision -> CTA. Returns a plain dict."""
    from commerce.commercial_cta import detect_unauthorized_commercial_cta
    from commerce.conversation_intelligence import derive_conversation_objective
    from commerce.decision import CommerceDecisionContext, decide_commerce_action
    from commerce.desire import derive_desire_stage
    from commerce.models import PolicyDecision
    from commerce.offer_readiness import evaluate_offer_readiness
    from commerce.readiness import evaluate_readiness
    from commerce.sales_window import derive_sales_window
    from commerce.temperature import derive_commercial_temperature

    phase9 = phase9 or {}
    desire = derive_desire_stage(
        relationship_state=relationship_state,
        primary_intent="casual_chat",
        purchase_intent=purchase_intent,
        explicit_purchase_request=explicit_purchase_request,
        consecutive_rejections=consecutive_rejections,
        aftercare_status=aftercare_status,
    )
    temp = derive_commercial_temperature(
        relationship_score=relationship_score,
        desire_stage=desire.stage.value,
        purchase_intent=purchase_intent,
    )
    readiness = evaluate_offer_readiness(
        desire.stage.value, temp.level, desire_evidence=desire.evidence
    )
    readiness101 = evaluate_readiness(
        desire_stage=desire.stage.value,
        temperature=temp.level,
        creator_id=1,
        desire_evidence=desire.evidence,
    )
    window = derive_sales_window(desire.stage.value, temp.level, readiness101.offer_readiness)
    selected, candidates = derive_conversation_objective(
        desire=desire.stage.value,
        temperature=temp.level,
        sales_window=window,
        offer_readiness=readiness.value,
        has_active_offer=False,
        aftercare_status=aftercare_status,
        is_on_cooldown=False,
        has_relevant_product=True,
        desire_evidence=desire.evidence,
    )
    decision = decide_commerce_action(
        CommerceDecisionContext(
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_score=relationship_score,
            user_asked_to_buy=user_asked_to_buy,
            user_asked_about_price=user_asked_about_price,
            consecutive_rejections=consecutive_rejections,
            aftercare_status=aftercare_status,
            current_content_interest=phase9.get("current_content_interest", False),
            current_content_disinterest=phase9.get("current_content_disinterest", False),
            user_initiated_commercial=phase9.get("user_initiated_commercial", False),
            continuation_context=phase9.get("continuation_context", False),
            warmth_without_commercial_evidence=phase9.get(
                "warmth_without_commercial_evidence", True
            ),
            authorization_basis=phase9.get("authorization_basis", "NONE"),
        )
    )
    return {
        "desire": desire,
        "temp": temp,
        "readiness": readiness,
        "readiness101": readiness101,
        "window": window,
        "selected": selected,
        "candidates": {c.objective: c for c in candidates},
        "decision": decision,
        "cta": detect_unauthorized_commercial_cta,
    }


class TestGoldenAWarmOnly:
    """Scenario A — 5 warm turns, zero commercial tokens.

    Composition pin over phase9 :270-330 (single-turn fence) and the
    phase-3 token pins: warmth must never become commerce across turns.
    """

    def test_five_warm_turns_never_commercial(self):
        from commerce.conversation_intelligence import ConversationObjective
        from commerce.models import CommerceAction

        for turn, msg in enumerate(WARM_FIVE, start=1):
            from commerce.free_photo_routing import is_photo_request
            from commerce.purchase_intent import (
                is_explicit_purchase_request,
                is_price_inquiry,
            )

            assert is_explicit_purchase_request(msg) is False
            assert is_price_inquiry(msg) is False
            assert is_photo_request(msg, None) is False
            _, _, ctx = _phase9(msg)
            assert ctx.warmth_without_commercial_evidence is True
            assert ctx.authorization_basis == "NONE"
            out = _commerce_turn(
                phase9={
                    "current_content_interest": ctx.current_content_interest,
                    "current_content_disinterest": ctx.disinterest_present,
                    "user_initiated_commercial": ctx.user_initiated_commercial,
                    "continuation_context": ctx.continuation_context,
                    "warmth_without_commercial_evidence": ctx.warmth_without_commercial_evidence,
                    "authorization_basis": ctx.authorization_basis,
                }
            )
            assert out["desire"].stage.value == "interest", f"turn {turn}"
            assert out["desire"].evidence == ("interest:warmth",), f"turn {turn}"
            assert out["desire"].confidence == 0.55, f"turn {turn}"
            assert out["readiness"].value == "build_desire", f"turn {turn}"
            assert out["readiness101"].offer_readiness == "build_desire", f"turn {turn}"
            assert out["readiness101"].available is False, f"turn {turn}"
            assert out["window"] == "building", f"turn {turn}"
            by_obj = out["candidates"]
            assert by_obj[ConversationObjective.DEEPEN_DESIRE].eligible is False
            assert by_obj[ConversationObjective.EXPLORE_INTEREST].eligible is False
            assert out["decision"].action in (
                CommerceAction.RELATIONSHIP_BUILDING,
                CommerceAction.NO_OFFER,
            ), f"turn {turn}"
            assert out["decision"].action not in (
                CommerceAction.SOFT_OFFER,
                CommerceAction.OFFER_PPV,
            ), f"turn {turn}"
            assert out["decision"].allowed is False, f"turn {turn}"
            assert out["cta"](msg) == (False, None), f"turn {turn}"


class TestGoldenDCuriosity:
    """Scenario D — turn 6 curiosity.

    NOTE (measured audit gap, not a fix): "Do you have anything
    exclusive?" yields all-False transition evidence under the frozen
    extractor (curiosity patterns at
    commerce/content_transition_evidence.py:162-190 require a content
    noun such as "any exclusive content"), so the transition is NONE,
    not ACKNOWLEDGE_ONLY (the :272 curiosity-alone rule covers
    matching phrasings). Asserted as measured; source untouched per
    harden-only scope. The turn still pins: no commercial basis, no
    PPV, CTA clean.
    """

    def test_curiosity_turn_no_commerce(self):
        from commerce.content_transition import ContentTransition
        from commerce.models import CommerceAction

        _ev, dec, ctx = _phase9(CURIOSITY_MSG)
        assert dec.transition is ContentTransition.NONE
        assert ctx.authorization_basis == "NONE"
        assert ctx.user_initiated_commercial is False
        out = _commerce_turn(
            phase9={
                "current_content_interest": ctx.current_content_interest,
                "current_content_disinterest": ctx.disinterest_present,
                "user_initiated_commercial": ctx.user_initiated_commercial,
                "continuation_context": ctx.continuation_context,
                "warmth_without_commercial_evidence": ctx.warmth_without_commercial_evidence,
                "authorization_basis": ctx.authorization_basis,
            }
        )
        assert out["decision"].action not in (
            CommerceAction.SOFT_OFFER,
            CommerceAction.OFFER_PPV,
        )
        assert out["decision"].allowed is False
        assert out["cta"](CURIOSITY_MSG) == (False, None)


class TestGoldenEPrice:
    """Scenario E — turn 7 price inquiry: genuine commercial evidence.

    DEFER_TO_COMMERCE + PRICE_INQUIRY at the Phase-8/9 layer; the
    step-8 explicit path (commerce/decision.py:630-652) then owns the
    turn, so OFFER_PPV here is the designed price path (genuine
    evidence — contrast with warmth-only scenario A).
    """

    def test_price_turn_defers_with_basis(self):
        from commerce.content_transition import ContentTransition
        from commerce.models import CommerceAction

        _ev, dec, ctx = _phase9(PRICE_MSG)
        assert dec.transition is ContentTransition.DEFER_TO_COMMERCE
        assert ctx.authorization_basis == "PRICE_INQUIRY"
        assert ctx.user_initiated_commercial is True
        out = _commerce_turn(
            user_asked_about_price=True,
            phase9={
                "current_content_interest": ctx.current_content_interest,
                "current_content_disinterest": ctx.disinterest_present,
                "user_initiated_commercial": ctx.user_initiated_commercial,
                "continuation_context": ctx.continuation_context,
                "warmth_without_commercial_evidence": ctx.warmth_without_commercial_evidence,
                "authorization_basis": ctx.authorization_basis,
            },
        )
        assert out["decision"].action is CommerceAction.OFFER_PPV
        assert out["decision"].allowed is True


class TestGoldenFBuy:
    """Scenario F — explicit buy: step-8 OFFER_PPV preserved."""

    def test_explicit_buy_offer_ready_and_ppv(self):
        from commerce.models import CommerceAction

        out = _commerce_turn(
            purchase_intent=0.9,
            explicit_purchase_request=True,
            user_asked_to_buy=True,
            phase9={
                "user_initiated_commercial": True,
                "authorization_basis": "EXPLICIT_TEXT",
            },
        )
        assert out["desire"].stage.value == "offer_ready"
        assert out["desire"].confidence == 0.95
        assert out["desire"].evidence == ("explicit purchase request",)
        assert out["decision"].action is CommerceAction.OFFER_PPV
        assert out["decision"].allowed is True


class TestGoldenHRejectionThenPrice:
    """Scenario H — rejection counters + explicit price.

    Explicit intent bypasses fatigue by design: the Phase-9 offer
    bases (EXPLICIT_TEXT/PRICE_INQUIRY, commerce/decision.py:357-360)
    own the turn even with rejection history, while warmth/float
    stays fenced. Counters stop at 2 here: at >=3 the rejection
    escalation suppresses even explicit turns (see scenario G).
    """

    def test_price_after_rejections_still_allowed(self):
        from commerce.models import CommerceAction

        # Rejection counters alone (no commercial evidence) stay fenced.
        for rej in (1, 2):
            out = _commerce_turn(consecutive_rejections=rej)
            assert out["decision"].action is not CommerceAction.OFFER_PPV
            assert out["decision"].allowed is False
        # Explicit price bypasses fatigue by design.
        out = _commerce_turn(
            consecutive_rejections=2,
            user_asked_about_price=True,
            phase9={
                "user_initiated_commercial": True,
                "authorization_basis": "PRICE_INQUIRY",
            },
        )
        assert out["decision"].action is CommerceAction.OFFER_PPV
        assert out["decision"].allowed is True
