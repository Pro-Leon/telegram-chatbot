"""Phase 5 golden — intimacy trajectories with commerce held flat (Driver 2 + Driver 1).

In-memory accumulation only (phase-12 WorldState pattern,
tests/test_phase12_behavioral_simulation.py:43-99,179-245): intimacy
anchors dict through real accumulate_intimacy_turn +
derive_intimacy_snapshot; boundary constraints dict through real
apply_evidence + derive_boundary_snapshot. Per turn, the Driver-1
commerce chain (same helper shape as
tests/test_phase5_golden_warm_commerce.py, duplicated locally to keep
this file self-contained) asserts commerce stays flat. No DB, no LLM,
no network. Moments are stepped hours apart so the 30d decay horizon
(commerce/intimacy_trajectory.py:151) never fires.

Probe strings are exact per the Phase 5 audit (the extractors match on
exact phrasing — notably "turn me on" hits _SEXUAL_STRONG_RE while the
gerund "turning me on" does not, and single soft tokens never set a
signal alone per intimacy_evidence.py:121-127).

Composition pins (overlap, not duplicates — fragments live in):
- trajectory/boundary/content patterns: phase12 :583-637 (E),
  :712-770 (G), :771-869 (H)
- intimacy accumulation: test_intimacy_trajectory_phase6.py:115-231,
  test_intimacy_context_phase6.py:286-302
- boundary evidence/veto: test_phase7_boundary.py:75-278, :1066-1135
- commerce fences/tokens/CTA: phase9 :154-488, phase-3 file, phase-4 file
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = [pytest.mark.unit]

T0 = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)

# Exact ramp probes per the Phase 5 audit (measured evidence noted).
RAMP = (
    "You're gorgeous",  # {} — excluded token, flirty tone only
    "I'm horny thinking about you",  # {} — single soft token, never alone
    "I'm horny and naughty tonight",  # sexual (soft-pair rule)
    "turn me on",  # sexual (strong single-hit; NOT the gerund)
    "kiss me",  # sexual (strong)
    "I miss you",  # romantic
    "that is so funny lol",  # playful
    "I miss you so much",  # romantic
)


def _moment(step_hours: int) -> datetime:
    return T0 + timedelta(hours=step_hours)


def _commerce_turn(
    *,
    relationship_score: float = 0.65,
    consecutive_rejections: int = 0,
    aftercare_status: str = "none",
    user_asked_to_buy: bool = False,
    user_asked_about_price: bool = False,
    phase9: dict | None = None,
):
    """Driver-1 commerce chain for one turn (literals only)."""
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
        relationship_state="warm",
        primary_intent="casual_chat",
        purchase_intent=0.1,
        consecutive_rejections=consecutive_rejections,
        aftercare_status=aftercare_status,
    )
    temp = derive_commercial_temperature(
        relationship_score=relationship_score,
        desire_stage=desire.stage.value,
        purchase_intent=0.1,
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
        "readiness": readiness,
        "readiness101": readiness101,
        "selected": selected,
        "candidates": {c.objective: c for c in candidates},
        "decision": decision,
        "cta": detect_unauthorized_commercial_cta,
    }


def _intimacy_turn(anchors, history: list, msg: str, moment):
    """One Driver-2 turn through the real extractor + accumulator."""
    from commerce.intimacy_evidence import extract_intimacy_evidence
    from commerce.intimacy_trajectory import (
        accumulate_intimacy_turn,
        derive_intimacy_snapshot,
    )

    history.append({"direction": "inbound", "content": msg})
    ev = extract_intimacy_evidence(user_message=msg, history=list(history[:-1]))
    anchors = accumulate_intimacy_turn(anchors, ev, moment)
    snap = derive_intimacy_snapshot(anchors, ev, moment)
    return anchors, ev, snap


class TestGoldenBIntimacyRamp:
    """Scenario B — 8-turn ramp, zero commerce. THE key composition test:
    bands rise while desire/readiness/decision stay identical to the
    warm-only pins every turn."""

    def test_ramp_bands_rise_commerce_flat(self):
        from commerce.intimacy_trajectory import (
            IntimacyBand,
            neutral_intimacy_anchors,
        )
        from commerce.models import CommerceAction
        from context_engine.intimacy_context import (
            has_intimate_context_reference,
            select_intimacy_context,
        )

        anchors = neutral_intimacy_anchors(T0)
        history: list = []
        for i, msg in enumerate(RAMP):
            moment = _moment(i)
            anchors, ev, snap = _intimacy_turn(anchors, history, msg, moment)
            if i < 2:
                for band in (
                    snap.romantic,
                    snap.playful,
                    snap.emotional,
                    snap.sexual_conversation,
                    snap.intimate_continuity,
                ):
                    assert band == IntimacyBand.UNKNOWN, f"turn {i + 1}"
            # Bands alone never manufacture a reference (turns 1-2 have no
            # linkage possible; later turns may carry genuine current-turn
            # linkage via the extractor, which is the designed path).
            if i < 2:
                ref = select_intimacy_context(snapshot=snap, evidence=ev)
                assert has_intimate_context_reference(ref) is False, f"turn {i + 1}"
            # Commerce identical to scenario A every turn.
            out = _commerce_turn()
            assert out["desire"].stage.value == "interest"
            assert out["desire"].evidence == ("interest:warmth",)
            assert out["readiness"].value == "build_desire"
            assert out["readiness101"].offer_readiness == "build_desire"
            assert out["decision"].action in (
                CommerceAction.RELATIONSHIP_BUILDING,
                CommerceAction.NO_OFFER,
            )
            assert out["decision"].allowed is False
            assert out["cta"](msg) == (False, None)
        # Sexual corroborated x3 -> LOW (2-hit rule); romantic x2 -> LOW;
        # playful x1 stays UNKNOWN.
        assert snap.sexual_conversation == IntimacyBand.LOW
        assert snap.romantic == IntimacyBand.LOW
        assert snap.playful == IntimacyBand.UNKNOWN


class TestGoldenC20Turns:
    """Scenario C — 20 turns cycled to 12 corroborated sexual obs.

    Bands reach DEEP while readiness stays BUILD_DESIRE (never
    TEST_INTEREST), the decision never goes offer-grade, and the CTA
    validator stays clean on all 20 turns.
    """

    def test_twenty_turns_deep_but_flat_commerce(self):
        from commerce.intimacy_trajectory import (
            IntimacyBand,
            neutral_intimacy_anchors,
        )
        from commerce.models import CommerceAction

        cycle = (
            "turn me on",
            "kiss me",
            "I'm horny and naughty tonight",
            "I miss you",
            "that is so funny lol",
        )
        anchors = neutral_intimacy_anchors(T0)
        history: list = []
        max_sexual = IntimacyBand.UNKNOWN
        order = ("UNKNOWN", "LOW", "STEADY", "DEEP")
        for i in range(20):
            msg = cycle[i % len(cycle)]
            moment = _moment(i)
            anchors, _, snap = _intimacy_turn(anchors, history, msg, moment)
            if order.index(snap.sexual_conversation.value.upper()) > order.index(
                max_sexual.value.upper()
            ):
                max_sexual = snap.sexual_conversation
            out = _commerce_turn()
            assert out["readiness"].value == "build_desire", f"turn {i + 1}"
            assert out["readiness101"].offer_readiness == "build_desire", f"turn {i + 1}"
            assert out["decision"].action not in (
                CommerceAction.SOFT_OFFER,
                CommerceAction.OFFER_PPV,
            ), f"turn {i + 1}"
            assert out["decision"].allowed is False, f"turn {i + 1}"
            assert out["cta"](msg) == (False, None), f"turn {i + 1}"
        # 12 sexual observations -> DEEP (threshold :146).
        assert snap.sexual_conversation == IntimacyBand.DEEP
        assert max_sexual == IntimacyBand.DEEP


class TestGoldenGRejectionThenIntimacy:
    """Scenario G — suppression holds DESPITE rising bands (same-turn
    dual assertions). Counters are plain params (no DAO); aftercare is
    a status string."""

    def test_suppression_despite_renewed_intimacy(self):
        from commerce.intimacy_trajectory import (
            IntimacyBand,
            neutral_intimacy_anchors,
        )
        from commerce.models import CommerceAction

        # Counters 1,2: conversational, fenced. At 3: RELATIONSHIP.
        for rej in (1, 2):
            out = _commerce_turn(consecutive_rejections=rej)
            assert out["decision"].action is not CommerceAction.OFFER_PPV
            assert out["decision"].allowed is False
        out = _commerce_turn(consecutive_rejections=3)
        assert out["desire"].stage.value == "relationship"
        assert out["desire"].confidence == 0.40
        assert out["decision"].action is CommerceAction.RELATIONSHIP_BUILDING
        assert out["decision"].allowed is False
        # Aftercare pending + warmth: suppressed.
        out = _commerce_turn(aftercare_status="pending")
        assert out["decision"].allowed is False
        # Renewed intimacy ramp under continued suppression.
        anchors = neutral_intimacy_anchors(T0)
        history: list = []
        ramp = ("turn me on", "kiss me", "I'm horny and naughty tonight", "I miss you")
        for i, msg in enumerate(ramp):
            anchors, _, snap = _intimacy_turn(anchors, history, msg, _moment(i))
            out = _commerce_turn(consecutive_rejections=3, aftercare_status="pending")
            assert out["decision"].allowed is False, f"ramp turn {i + 1}"
            assert out["decision"].action is not CommerceAction.OFFER_PPV
        assert snap.sexual_conversation == IntimacyBand.LOW


class TestGoldenISexualPlusBoundary:
    """Scenario I — sexual ramp meets boundary asserts.

    Validator pins: flirty/sexual drafts violate under active types and
    yield a safe completion (boundary_validation.py:168-176); commerce
    veto fires for contact/stop types. The "do not sell me anything"
    turn documents the measured gap honestly: it maps to NO boundary
    type, so suppression comes from the disinterest/warmth fence, not
    a boundary — asserted as measured, source untouched.
    """

    def _constraints(self, msg, constraints, moment):
        from commerce.boundary_evidence import extract_boundary_evidence
        from commerce.boundary_state import apply_evidence, derive_boundary_snapshot

        ev = extract_boundary_evidence(msg)
        constraints = apply_evidence(constraints, ev, moment)
        return constraints, derive_boundary_snapshot(constraints, moment)

    def test_boundary_enforced_no_tease_violation(self):
        from commerce.boundary_state import boundary_blocks_commerce
        from commerce.boundary_validation import (
            safe_completion_for,
            validate_reply_against_boundaries,
        )
        from commerce.models import CommerceAction

        constraints = {}
        # Intimacy aired first (bands rise, commerce flat — mini-B).
        from commerce.intimacy_trajectory import neutral_intimacy_anchors

        anchors = neutral_intimacy_anchors(T0)
        history: list = []
        anchors, _, _ = _intimacy_turn(anchors, history, "kiss me", _moment(0))
        out = _commerce_turn()
        assert out["decision"].action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )
        # "do not flirt with me" -> NO_FLIRTING; flirty draft violates,
        # safe completion queued, CTA detector fires independently.
        constraints, snap = self._constraints("do not flirt with me", constraints, _moment(1))
        assert "NO_FLIRTING" in tuple(snap.active)
        draft = "Hey sexy, want to see my exclusive pics tonight?"
        v = validate_reply_against_boundaries(draft, snap)
        assert v.violated is True
        assert "NO_FLIRTING" in tuple(v.violations)
        assert isinstance(safe_completion_for(v.violations), str)
        # "I'm not comfortable with this" -> NO_FLIRTING + NO_SEXUAL_TOPIC.
        constraints, snap = self._constraints(
            "I'm not comfortable with this", constraints, _moment(2)
        )
        assert "NO_SEXUAL_TOPIC" in tuple(snap.active)
        v = validate_reply_against_boundaries("kiss me tonight babe", snap)
        assert v.violated is True
        # "do not contact me" -> contact veto blocks commerce.
        constraints, snap = self._constraints("do not contact me", constraints, _moment(3))
        assert "DO_NOT_CONTACT" in tuple(snap.active)
        blocked, _ = boundary_blocks_commerce(snap)
        assert blocked is True
        v = validate_reply_against_boundaries("Hello there friend", snap)
        assert v.violated is True
        # "do not sell me anything" maps to NO boundary type (measured
        # gap — documented, not fixed): suppression is via the
        # warmth/disinterest fence, asserted honestly below.
        from commerce.boundary_evidence import extract_boundary_evidence

        assert extract_boundary_evidence("do not sell me anything").asserted_types() == ()
        constraints, snap = self._constraints("do not sell me anything", constraints, _moment(4))
        out = _commerce_turn(
            phase9={
                "warmth_without_commercial_evidence": True,
                "authorization_basis": "NONE",
            }
        )
        assert out["decision"].action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )
        assert out["decision"].allowed is False
