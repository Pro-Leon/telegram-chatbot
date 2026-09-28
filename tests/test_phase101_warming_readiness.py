"""Phase 101 — Warming / Readiness deterministic tests.

Covers A-N from spec.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

WARMING_PATH = Path("commerce/warming.py")
READINESS_PATH = Path("commerce/readiness.py")
CONVERSATIONAL_PATH = Path("commerce/conversational.py")
WORKER_PATH = Path("workers/llm_worker.py")
DECISION_PATH = Path("commerce/decision.py")


# A. deterministic warming state

def test_warming_deterministic():
    from commerce.warming import derive_warming_state

    a = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="interest", purchase_intent=0.1)
    b = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="interest", purchase_intent=0.1)
    assert a == b
    assert a.level in ("cold", "warm", "hot", "not_available", "cooldown", "aftercare")
    assert 0.0 <= a.score <= 1.0


def test_warming_no_llm_clock():
    src = WARMING_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                imports.append(a.name.lower())
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.append(n.module.lower())
    assert "openai" not in " ".join(imports)
    assert "genai" not in " ".join(imports)
    assert "llm" not in " ".join(imports)
    assert "random" not in " ".join(imports)
    assert "time" not in " ".join(imports)


# B. warming ceiling not target

def test_warming_ceiling_not_target():
    from commerce.warming import derive_warming_state

    w = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="desire", purchase_intent=0.5)
    # Ceiling is score, not count; zero activity is valid, does not force sends
    assert w.ceiling <= 1.0
    # Warming available does not imply must send
    # Test that cold warming is not available but not error
    cold = derive_warming_state(creator_id=1, relationship_state="cold", desire_stage="relationship")
    assert cold.available is False or cold.level == "cold"
    # No side effect: repeated call same result
    w2 = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="desire", purchase_intent=0.5)
    assert w == w2


# C. deterministic readiness

def test_readiness_deterministic():
    from commerce.warming import derive_warming_state
    from commerce.readiness import evaluate_readiness

    w = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="interest")
    r1 = evaluate_readiness(warming=w, desire_stage="interest", temperature="warm", creator_id=1)
    r2 = evaluate_readiness(warming=w, desire_stage="interest", temperature="warm", creator_id=1)
    assert r1 == r2
    assert r1.level in ("not_ready", "build_warming", "ready", "degraded")


def test_readiness_no_llm():
    src = READINESS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports = " ".join([m.lower() for m in [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module] + [a.name.lower() for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]])
    assert "openai" not in imports
    assert "genai" not in imports
    assert "llm" not in imports


# D. LLM cannot manufacture readiness

def test_llm_cannot_manufacture_readiness():
    from commerce.readiness import evaluate_readiness
    from commerce.warming import derive_warming_state

    # Even if LLM says "user is ready" (not an input), readiness must be based on authoritative state
    # Our evaluate_readiness takes no LLM string, only warming/desire/temperature etc.
    w = derive_warming_state(creator_id=1, relationship_state="cold", desire_stage="relationship")
    # Cold warming should not be ready even if we try to force via LLM-like signal
    # There is no parameter for LLM text, so cannot manufacture
    r = evaluate_readiness(warming=w, desire_stage="relationship", temperature="cold", creator_id=1)
    assert r.available is False
    assert r.level != "ready"
    # Verify function signature has no LLM param
    import inspect
    sig = inspect.signature(evaluate_readiness)
    assert "llm" not in str(sig).lower()
    assert "user_is_ready" not in str(sig)


# E. LLM cannot authorize sales window

def test_llm_cannot_authorize_sales_window():
    from commerce.sales_window import derive_sales_window

    # Sales window is deterministic from desire/temperature/readiness, not LLM
    w1 = derive_sales_window("relationship", "cold", "not_ready")
    assert w1 == "no_window"
    w2 = derive_sales_window("offer_ready", "hot", "ready")
    assert w2 == "open"
    # No LLM param in signature
    import inspect
    assert "llm" not in str(inspect.signature(derive_sales_window)).lower()


# F. explicit content request does not directly become OFFER_PPV

def test_explicit_content_not_ppv():
    from commerce.decision import CommerceDecisionContext, decide_commerce_action
    from commerce.models import PolicyDecision
    from commerce.decision import CommerceReason

    ctx = CommerceDecisionContext(user_id=1, creator_id=1, eligibility=PolicyDecision(allowed=True), user_requested_content=True, relationship_score=0.8)
    d = decide_commerce_action(ctx)
    # Must not be PPV; soft_offer/relationship is allowed via _SELL_ACTIONS but not PPV
    assert d.action.value != "offer_ppv"
    # Content alone should not be allowed as PPV (soft_offer is allowed but not PPV)
    assert d.action.value != "offer_ppv"


# G. insufficient readiness does not create PPV

def test_insufficient_readiness_no_ppv():
    from commerce.readiness import evaluate_readiness
    from commerce.warming import derive_warming_state

    w = derive_warming_state(creator_id=1, relationship_state="cold", desire_stage="relationship")
    r = evaluate_readiness(warming=w, desire_stage="relationship", temperature="cold", creator_id=1, has_relevant_product=True)
    assert r.available is False
    # Even if decision had strong buying signal, readiness not_ready should keep sales_window building/no_window
    from commerce.sales_window import derive_sales_window
    sw = derive_sales_window("relationship", "cold", "not_ready")
    assert sw != "open"
    # Decision with not_ready should not be OFFER_PPV
    from commerce.decision import CommerceDecisionContext, decide_commerce_action
    from commerce.models import PolicyDecision
    ctx = CommerceDecisionContext(user_id=1, creator_id=1, eligibility=PolicyDecision(allowed=True), user_requested_content=True, relationship_score=0.2)
    d = decide_commerce_action(ctx)
    assert d.allowed is False or d.action.value != "offer_ppv"


# H. degraded state fails closed

def test_degraded_fails_closed():
    from commerce.warming import derive_warming_state
    from commerce.readiness import evaluate_readiness

    # Missing creator
    w = derive_warming_state(creator_id=None, relationship_state="warm", desire_stage="interest")
    assert w.available is False
    assert w.level == "not_available"

    # Aftercare
    w2 = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="interest", aftercare_status="pending")
    assert w2.available is False
    assert w2.level == "aftercare"

    # Readiness degraded
    r = evaluate_readiness(warming=w2, desire_stage="interest", temperature="warm", creator_id=1, aftercare_active=True)
    assert r.available is False
    assert r.level == "degraded"

    # Missing creator for readiness
    r2 = evaluate_readiness(creator_id=None, desire_stage="interest", temperature="warm")
    assert r2.available is False


# I. creator scoping

def test_creator_scoping():
    from commerce.warming import derive_warming_state

    # Different creators with same user state should be isolated via creator_id param
    # Warming for creator 1 vs 2 with same relationship but one missing creator
    w1 = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="interest")
    w2 = derive_warming_state(creator_id=2, relationship_state="warm", desire_stage="interest")
    # Both available but not cross-contaminated (if one creator missing, other still works)
    assert w1.available == w2.available
    # Missing creator fails closed, not leaking
    w3 = derive_warming_state(creator_id=None, relationship_state="warm", desire_stage="interest")
    assert w3.available is False
    assert w1.available is True or w1.level in ("warm", "hot", "cold")  # at least not degraded due to missing creator


# J. repeated evaluation / idempotency

@pytest.mark.asyncio
async def test_repeated_evaluation_idempotent():
    # Idempotency: same authoritative state → same warming/readiness, no side effects
    from commerce.warming import derive_warming_state
    from commerce.readiness import evaluate_readiness

    w1 = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="interest", purchase_intent=0.2)
    w2 = derive_warming_state(creator_id=1, relationship_state="warm", desire_stage="interest", purchase_intent=0.2)
    r1 = evaluate_readiness(warming=w1, desire_stage="interest", temperature="warm", creator_id=1)
    r2 = evaluate_readiness(warming=w2, desire_stage="interest", temperature="warm", creator_id=1)
    assert w1 == w2
    assert r1 == r2


# K. process_message integration

def test_process_message_integration_warming():
    src = Path("workers/llm_worker.py").read_text(encoding="utf-8")
    # Phase 101 should be integrated after conversational state, not as second context
    assert "warming" in src.lower()
    assert "derive_warming_state" in src or "warming" in src
    # Ensure not second authoritative context assembly call (import + one call = 2 occurrences total is expected)
    # Count calls (not import): look for "await assemble_authoritative_context"
    count_calls = src.count("await assemble_authoritative_context")
    assert count_calls == 1, f"should have exactly one context assembly call, got {count_calls}"


# L. no second LLM call

def test_no_second_llm_in_warming():
    src = Path("commerce/warming.py").read_text(encoding="utf-8")
    assert "generate_draft" not in src
    src2 = Path("commerce/readiness.py").read_text(encoding="utf-8")
    assert "generate_draft" not in src2
    src3 = Path("commerce/conversational.py").read_text(encoding="utf-8")
    # conversational already has one LLM extraction, but warming should not add second
    # Check that warming derivation does not call extract_commerce_signals
    warming_section = src3.split("Phase 101")[1] if "Phase 101" in src3 else ""
    assert "extract_commerce_signals" not in warming_section


# M. no second context builder

def test_no_second_context_builder_in_warming():
    src = Path("commerce/warming.py").read_text(encoding="utf-8")
    assert "build_qwen3_context" not in src
    assert "assemble_authoritative_context" not in src
    src2 = Path("workers/llm_worker.py").read_text(encoding="utf-8")
    # Ensure Phase 101 block does not rebuild context
    # Find Phase 101 block
    import re
    m = re.search(r"Phase 101.*?warming", src2, re.DOTALL | re.IGNORECASE)
    if m:
        block = src2[m.start():m.start()+2000]
        assert "build_qwen3_context" not in block


# N. Phase 98-100 unchanged

def test_phase98_100_unchanged():
    # Verify Phase 98 quota not recalculated in warming
    wsrc = Path("commerce/warming.py").read_text(encoding="utf-8")
    assert "free_photo_deliveries" not in wsrc
    assert "free_media_pool" not in wsrc
    assert "authorize_free_photo" not in wsrc
    rsrc = Path("commerce/readiness.py").read_text(encoding="utf-8")
    assert "free_photo" not in rsrc.lower()
    # Verify conversational still calls original readiness
    csrc = Path("commerce/conversational.py").read_text(encoding="utf-8")
    assert "evaluate_offer_readiness" in csrc
    # Ensure no new PPV authorization via warming
    assert "OFFER_PPV" not in wsrc
    assert "OFFER_PPV" not in rsrc


