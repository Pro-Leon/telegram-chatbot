"""Phase 7 unit tests: plan -> generate (port) -> validate.

No external infra, no live LLM (fake port). Covers RESPONSE_ENGINE.md
stages + hard rules + outbound lifecycle guards.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from relationship_v2.domain.response import (
    GeneratedResponse,
    OutboundState,
    ResponseIntent,
    ValidationOutcome,
)
from relationship_v2.services.response_engine import (
    build_plan,
    generate,
    interpret,
    next_outbound_state,
    plan_hash,
    validate_response,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _plan(**kw):
    base: dict = {
        "generation_id": "g1",
        "creator_id": 1,
        "user_id": 2,
        "relationship_id": uuid4(),
        "intent": ResponseIntent.ACKNOWLEDGE,
        "key_points": ["greet warmly"],
        "commerce_refs": [],
        "forbidden_claims": [],
        "provenance": "test",
        "now": datetime.now(UTC),
    }
    base.update(kw)
    return build_plan(**base)


def _gen(text: str) -> GeneratedResponse:
    return GeneratedResponse(
        text=text,
        generation_id="g1",
        strategy="relationship",
        confidence=0.8,
    )


def test_interpret_routes_commerce_and_boundaries() -> None:
    assert interpret("how much for the pics?") == ResponseIntent.COMMERCE_TRANSITION
    assert interpret("stop, don't talk about that") == ResponseIntent.BOUNDARY_RESPECT
    assert interpret("how was your day?") == ResponseIntent.QUESTION


def test_plan_persisted_before_generation_has_hash() -> None:
    plan = _plan()
    assert len(plan_hash(plan)) == 16
    assert plan_hash(plan) == plan_hash(plan)  # deterministic


def test_generate_requires_port_no_silent_fallback() -> None:
    async def _run() -> None:
        await generate(_plan(), None)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(_run())


def test_generate_uses_caller_port() -> None:
    import asyncio

    async def fake_port(plan):
        return _gen("hey there")

    out = asyncio.run(generate(_plan(), fake_port))
    assert out.text == "hey there"


def test_invented_price_suppressed_without_confirmation() -> None:
    v = validate_response(_gen("I can send it for $20"), _plan())
    assert v.outcome == ValidationOutcome.SUPPRESSED
    assert "invented_price" in v.flags


def test_quoted_confirmation_passes_price_rail() -> None:
    v = validate_response(
        _gen("it's $35 as listed"), _plan(), confirmed_commerce_text="$35 confirmed"
    )
    assert v.outcome == ValidationOutcome.VALID


def test_invented_purchase_suppressed() -> None:
    v = validate_response(_gen("you already purchased this"), _plan())
    assert v.outcome == ValidationOutcome.SUPPRESSED
    assert "invented_purchase" in v.flags


def test_boundary_violation_suppressed() -> None:
    plan = _plan(forbidden_claims=[])
    v = validate_response(_gen("let's talk about work more"), plan, boundary_topics=["work"])
    assert v.outcome == ValidationOutcome.SUPPRESSED


def test_forbidden_plan_claim_needs_review() -> None:
    plan = _plan(forbidden_claims=["guaranteed results"])
    v = validate_response(_gen("guaranteed results for you"), plan)
    assert v.outcome == ValidationOutcome.NEEDS_REVIEW


def test_outbound_lifecycle_blocks_planned_to_sent() -> None:
    assert next_outbound_state(OutboundState.PLANNED, "sent") is None
    assert next_outbound_state(OutboundState.PLANNED, "validate") == OutboundState.VALIDATED
    assert next_outbound_state(OutboundState.VALIDATED, "enqueue") == OutboundState.ENQUEUED
    assert next_outbound_state(OutboundState.FAILED, "enqueue") == OutboundState.ENQUEUED
    assert next_outbound_state(OutboundState.SENT, "enqueue") is None


def test_phase7_no_provider_or_v1_imports() -> None:
    import ast

    target = REPO_ROOT / "relationship_v2" / "services" / "response_engine.py"
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
            assert "llamacpp" not in mod and "llm_provider" not in mod, mod
            assert "one_call" not in mod and mod != "core.routing", mod
            assert not (mod == "commerce" or mod.startswith("commerce.")), mod
            assert "integrations.dropfans" not in mod, mod
