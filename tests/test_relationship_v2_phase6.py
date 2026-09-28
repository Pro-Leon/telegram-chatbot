"""Phase 6 unit tests: escalation stages, guards, cooldowns.

No external infra required. Covers ESCALATION_ENGINE.md rules +
STATE_MACHINES.md escalation guards + 04 strategy reversibility.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from relationship_v2.domain.escalation import EscalationStage
from relationship_v2.domain.relationship import RelationshipLifecycle
from relationship_v2.domain.strategy import StrategyInputs
from relationship_v2.services.escalation import (
    is_cooling_down,
    is_valid_escalation_transition,
    select_stage,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _inputs(**kw) -> StrategyInputs:
    base: dict = {
        "relationship_lifecycle": RelationshipLifecycle.WARMING,
        "familiarity": "familiar",
        "responsiveness": 0.8,
        "engagement_evidence": 10,
    }
    base.update(kw)
    return StrategyInputs(**base)


def _select(current: EscalationStage, inputs: StrategyInputs):
    return select_stage(uuid4(), 1, 2, current, inputs, datetime.now(UTC))


def test_new_fan_holds_relationship_no_forced_funnel() -> None:
    d = _select(
        EscalationStage.RELATIONSHIP,
        StrategyInputs(relationship_lifecycle=RelationshipLifecycle.NEW),
    )
    assert d.stage == EscalationStage.RELATIONSHIP
    assert d.commerce_intent == "none"


def test_ineligible_blocks_present_offer() -> None:
    assert not is_valid_escalation_transition(
        EscalationStage.RECOMMEND, EscalationStage.PRESENT_OFFER, False
    )
    assert is_valid_escalation_transition(
        EscalationStage.RECOMMEND, EscalationStage.PRESENT_OFFER, True
    )
    d = _select(
        EscalationStage.RECOMMEND,
        _inputs(commerce_eligible=False, responsiveness=0.9),
    )
    assert d.stage != EscalationStage.PRESENT_OFFER


def test_eligible_qualified_issues_request_intent_only() -> None:
    d = _select(
        EscalationStage.RECOMMEND,
        _inputs(commerce_eligible=True, responsiveness=0.9),
    )
    assert d.stage == EscalationStage.PRESENT_OFFER
    # Intent only: the offer exists when commerce confirms (Phase 8).
    assert d.commerce_intent == "request"


def test_cooldown_blocks_reentry() -> None:
    future = datetime.now(UTC) + timedelta(hours=12)
    assert is_cooling_down(future) is True
    assert is_cooling_down(None) is False
    d = _select(EscalationStage.EXPLORE, _inputs(cooling_down_until=future))
    assert d.stage == EscalationStage.COOLING_DOWN


def test_stall_steps_back_reversible() -> None:
    d = _select(EscalationStage.BUILD_DESIRE, _inputs(negative_evidence=4))
    assert d.stage == EscalationStage.EXPLORE
    d2 = _select(EscalationStage.RECOMMEND, _inputs(negative_evidence=3))
    assert d2.stage == EscalationStage.RELATIONSHIP


def test_aftercare_requires_purchase_confirmation() -> None:
    d = _select(EscalationStage.PRESENT_OFFER, _inputs(purchase_ref_count=1))
    assert d.stage == EscalationStage.AFTERCARE
    d_hold = _select(EscalationStage.RELATIONSHIP, _inputs(purchase_ref_count=0))
    assert d_hold.stage != EscalationStage.AFTERCARE


def test_boundaries_exit_commercial_pursuit() -> None:
    d = _select(EscalationStage.PRESENT_OFFER, _inputs(has_boundaries=True, commerce_eligible=True))
    assert d.stage == EscalationStage.EXITED


def test_confidence_has_named_inputs() -> None:
    d = _select(EscalationStage.RELATIONSHIP, _inputs())
    assert 0.0 <= d.confidence.total <= 1.0
    assert d.confidence.responsiveness == 0.8
    assert d.previous_stage == EscalationStage.RELATIONSHIP


def test_phase6_no_v1_or_commerce_leakage() -> None:
    import ast

    target = REPO_ROOT / "relationship_v2" / "services" / "escalation.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if mod.startswith("relationship_v2"):
                continue
            assert not (mod == "commerce" or mod.startswith("commerce.")), mod
            assert "context_engine" not in mod, mod
            assert "integrations.dropfans" not in mod, mod
