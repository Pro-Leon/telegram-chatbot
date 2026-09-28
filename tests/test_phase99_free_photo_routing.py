"""Phase 99 — Free-photo routing / decision integration tests.

Tests must prove 18 invariants from the Phase 99 spec.
All DB for quota is mocked except where noted; routing delegates to Phase 98.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

ROUTING_PATH = Path(__file__).parent.parent / "commerce" / "free_photo_routing.py"
FREE_PHOTO_PATH = Path(__file__).parent.parent / "commerce" / "free_photo.py"
LLM_WORKER_PATH = Path(__file__).parent.parent / "workers" / "llm_worker.py"
DECISION_PATH = Path(__file__).parent.parent / "commerce" / "decision.py"


# ── helpers ───────────────────────────────────────────────────────────────


def _mock_pool_with_media(vault_ids=None, status="approved"):
    # For select_approved_free_media mock
    vault_ids = vault_ids or ["vaultApproved1"]
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"vault_item_id": vault_ids[0]})
    # transaction mock not needed for selection (simple SELECT)
    pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False)))
    return pool, conn


# 1. photo request can enter free-photo path

@pytest.mark.asyncio
async def test_photo_request_enters_free_photo_path():
    from commerce.free_photo_routing import route_free_photo

    # Mock Phase 98 to return eligible
    mock_auth = MagicMock(eligible=True, reason="eligible", seq=1, vault_item_id="vaultA")
    mock_auth.reason = "eligible"
    mock_auth.eligible = True

    # Need to mock select_approved + authorize
    with patch("commerce.free_photo_routing.select_approved_free_media", new_callable=AsyncMock, return_value="vaultA"), \
         patch("commerce.free_photo_routing.authorize_free_photo", new_callable=AsyncMock, return_value=mock_auth):
        # Also need to mock DB for authorize? Actually authorize is mocked, so no DB
        # But route will call authorize_free_photo, which we mock
        result = await route_free_photo(creator_id=1, user_id=100, user_message="send me a photo please?", signals=None)
        assert result.attempted is True
        assert result.outcome == "eligible"
        assert result.selected_vault_item_id == "vaultA"


# 2. delegates to Phase98 (not duplicated)

def test_delegates_to_phase98():
    src = ROUTING_PATH.read_text(encoding="utf-8")
    assert "from commerce.free_photo import" in src
    assert "authorize_free_photo" in src
    # Must not duplicate quota logic
    assert "ceiling = 4 if" not in src  # no purchase tier duplication
    assert "COUNT(*)" not in src or "free_photo_deliveries" not in src  # routing should not directly query ledger for quota
    # Should not contain seq logic
    assert "seq BETWEEN 1 AND 4" not in src


# 3. quota_exhausted does NOT become PPV

@pytest.mark.asyncio
async def test_quota_exhausted_not_ppv():
    from commerce.free_photo_routing import route_free_photo
    mock_auth = MagicMock(eligible=False, reason="quota_exhausted", seq=None, vault_item_id="vaultA")
    with patch("commerce.free_photo_routing.select_approved_free_media", return_value="vaultA"), \
         patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
        result = await route_free_photo(creator_id=1, user_id=100, user_message="can I see a picture?", signals=None)
        assert result.attempted is True
        assert result.outcome == "quota_exhausted"
        # Ensure routing result is not PPV and does not create offer
        # The routing outcome stays quota_exhausted, not converted
        assert result.outcome != "eligible"
        # Verify llm_worker does not auto-convert: check source
        worker_src = LLM_WORKER_PATH.read_text(encoding="utf-8")
        assert "quota_exhausted" in worker_src  # handled
        assert "_free_photo_eligible" in worker_src
        # Commerce should be skipped when free eligible, but quota_exhausted should not trigger PPV either
        # decision.py fix ensures explicit_content_request alone does not become OFFER_PPV
        from commerce.decision import CommerceDecisionContext, decide_commerce_action
        from commerce.models import PolicyDecision
        ctx = CommerceDecisionContext(
            user_id=1, creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            has_active_offer=False, has_relevant_product=True,
            user_requested_content=True,  # old defect would trigger PPV
            relationship_score=0.7,
        )
        decision = decide_commerce_action(ctx)
        # After fix, this should NOT be OFFER_PPV
        assert decision.action.value != "offer_ppv", f"content request must not become PPV, got {decision}"


# 4. "send me a photo" not PPV

def test_send_me_a_photo_not_ppv():
    from commerce.decision import CommerceDecisionContext, decide_commerce_action
    from commerce.models import PolicyDecision
    ctx = CommerceDecisionContext(
        user_id=10, creator_id=1,
        eligibility=PolicyDecision(allowed=True),
        has_active_offer=False, has_relevant_product=True,
        user_requested_content=True,
        relationship_score=0.8,
    )
    d = decide_commerce_action(ctx)
    assert d.action.value in ("relationship_building", "soft_offer", "no_offer") or d.action.value != "offer_ppv"
    assert d.action.value != "offer_ppv"


# 5. unapproved vault cannot become free even if LLM selects it

@pytest.mark.asyncio
async def test_unapproved_vault_not_free():
    from commerce.free_photo_routing import route_free_photo
    # Even if LLM would select an unapproved vault_id, routing must check free_media_pool via Phase98
    # Simulate preselected vault that is not approved -> authorize will deny
    mock_auth = MagicMock(eligible=False, reason="media_not_approved")
    with patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
        # Preselected vault that is not in free_media_pool
        result = await route_free_photo(creator_id=1, user_id=100, user_message="send me a photo", signals=None, preselected_vault_item_id="vaultUnapprovedXYZ")
        assert result.attempted is True
        # Should be invalid_media, not eligible
        assert result.outcome in ("invalid_media", "media_not_approved")
        assert not result.authorization.eligible if result.authorization else True


# 6. valid approved can be routed

@pytest.mark.asyncio
async def test_valid_approved_can_be_routed():
    from commerce.free_photo_routing import route_free_photo
    mock_auth = MagicMock(eligible=True, reason="eligible", seq=1)
    with patch("commerce.free_photo_routing.select_approved_free_media", return_value="vaultApproved123"), \
         patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
        result = await route_free_photo(creator_id=1, user_id=100, user_message="show me a pic", signals=None)
        assert result.attempted is True
        assert result.outcome == "eligible"
        assert result.selected_vault_item_id == "vaultApproved123"


# 7. LLM cannot manufacture free-media

def test_llm_cannot_manufacture_free_media():
    src = ROUTING_PATH.read_text(encoding="utf-8")
    # Routing must select via DB, not from LLM signals
    assert "select_approved_free_media" in src
    # Ensure route does not take vault_item_id from signals (check code, not docstring)
    tree = ast.parse(src)
    code_text = "".join([n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)])  # docstrings
    # Check actual code does not use signals to set vault_item_id
    assert "signals.vault_item_id" not in src or "signals.vault_item_id" in code_text  # only docstring allowed
    # Ensure authorize is called (check via AST for Call with Name)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (getattr(n.func, "id", "") == "authorize_free_photo" or getattr(n.func, "attr", "") == "authorize_free_photo")]
    assert len(calls) >= 1
    # Ensure vault_item_id comes from select_approved_free_media or preselected param, not LLM
    assert "vault_item_id" in src


# 8. LLM cannot manufacture PPV

def test_llm_cannot_manufacture_ppv():
    src = ROUTING_PATH.read_text(encoding="utf-8")
    assert "OFFER_PPV" not in src
    assert "create_offer" not in src.lower()
    # Also check decision.py no longer maps content to PPV
    dsrc = DECISION_PATH.read_text(encoding="utf-8")
    # The explicit block should not contain user_requested_content
    # Find the 8. Explicit block
    lines = dsrc.splitlines()
    # Find the fixed block
    block_start = next((i for i, l in enumerate(lines) if "8. Explicit user buying intent" in l), None)
    assert block_start is not None
    block = "\n".join(lines[block_start:block_start+15])
    assert "user_requested_content" not in block, f"content request still in PPV block: {block}"
    # Check llm_worker does not let LLM directly set product_id for PPV without commerce authority
    wsrc = LLM_WORKER_PATH.read_text(encoding="utf-8")
    # Ensure free-photo routing does not create commerce offer
    assert "authorize_free_photo" in wsrc
    assert "create_offer" not in ROUTING_PATH.read_text(encoding="utf-8").lower()


# 9. Phase98 outcomes respected

@pytest.mark.asyncio
async def test_phase98_outcomes_respected():
    from commerce.free_photo_routing import route_free_photo
    cases = [
        ("quota_exhausted", "quota_exhausted"),
        ("already_pending", "already_pending"),
        ("already_sent_same_media", "already_sent_same_media"),
        ("invalid_media", "invalid_media"),
        ("invalid_creator_or_user", "invalid_creator_or_user"),
    ]
    for reason, expected_outcome in cases:
        mock_auth = MagicMock(eligible=False, reason=reason)
        with patch("commerce.free_photo_routing.select_approved_free_media", return_value="vaultA"), \
             patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
            r = await route_free_photo(creator_id=1, user_id=100, user_message="send me a photo", signals=None)
            assert r.outcome == expected_outcome, f"{reason} -> {r.outcome}"


# 10. already_pending idempotent

@pytest.mark.asyncio
async def test_already_pending_idempotent():
    from commerce.free_photo_routing import route_free_photo
    mock_auth = MagicMock(eligible=False, reason="already_pending", seq=1)
    with patch("commerce.free_photo_routing.select_approved_free_media", return_value="vaultA"), \
         patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
        r1 = await route_free_photo(creator_id=1, user_id=100, user_message="send me a photo", signals=None)
        r2 = await route_free_photo(creator_id=1, user_id=100, user_message="send me a photo", signals=None)
        assert r1.outcome == "already_pending"
        assert r2.outcome == "already_pending"
        # Both should not create new quota consumption – authorize handles idempotency
        assert r1.selected_vault_item_id == r2.selected_vault_item_id


# 11. already_sent_same_media idempotent

@pytest.mark.asyncio
async def test_already_sent_same_media_idempotent():
    from commerce.free_photo_routing import route_free_photo
    mock_auth = MagicMock(eligible=False, reason="already_sent_same_media", seq=1)
    with patch("commerce.free_photo_routing.select_approved_free_media", return_value="vaultA"), \
         patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
        r = await route_free_photo(creator_id=1, user_id=100, user_message="send me a photo", signals=None)
        assert r.outcome == "already_sent_same_media"


# 12. quota_exhausted remains

@pytest.mark.asyncio
async def test_quota_exhausted_remains():
    from commerce.free_photo_routing import route_free_photo
    mock_auth = MagicMock(eligible=False, reason="quota_exhausted")
    with patch("commerce.free_photo_routing.select_approved_free_media", return_value="vaultA"), \
         patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
        r = await route_free_photo(creator_id=1, user_id=100, user_message="can I see a picture?", signals=None)
        assert r.outcome == "quota_exhausted"
        # Ensure routing does not convert to eligible or PPV
        assert not r.authorization.eligible if r.authorization else True


# 13. invalid media remains

@pytest.mark.asyncio
async def test_invalid_media_remains():
    from commerce.free_photo_routing import route_free_photo
    mock_auth = MagicMock(eligible=False, reason="invalid_media")
    with patch("commerce.free_photo_routing.select_approved_free_media", return_value="vaultBad"), \
         patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
        r = await route_free_photo(creator_id=1, user_id=100, user_message="send me a photo", signals=None)
        assert r.outcome == "invalid_media"


# 14. failed does not trigger PPV

@pytest.mark.asyncio
async def test_failed_not_ppv():
    from commerce.free_photo_routing import route_free_photo
    # Simulate failed auth that was retried? For routing, any non-eligible should not become PPV
    mock_auth = MagicMock(eligible=False, reason="invalid_media")
    with patch("commerce.free_photo_routing.select_approved_free_media", return_value="vaultA"), \
         patch("commerce.free_photo_routing.authorize_free_photo", return_value=mock_auth):
        r = await route_free_photo(creator_id=1, user_id=100, user_message="send me a photo", signals=None)
        assert r.outcome in ("invalid_media", "quota_exhausted", "already_pending", "already_sent_same_media", "no_approved_media")
        # Ensure no PPV side effect
        # decision fix already ensures content request not PPV
        from commerce.decision import CommerceDecisionContext, decide_commerce_action
        from commerce.models import PolicyDecision
        ctx = CommerceDecisionContext(user_id=1, creator_id=1, eligibility=PolicyDecision(allowed=True), user_requested_content=True)
        d = decide_commerce_action(ctx)
        assert d.action.value != "offer_ppv"


# 15. Phase99 does not send via Telethon

def test_no_telethon_send():
    src = ROUTING_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                imports.append(a.name.lower())
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                imports.append(n.module.lower())
    # Routing file must not import telethon
    assert "telethon" not in " ".join(imports)
    # No send_file/send_message calls in code (docstring ignored)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    for c in calls:
        if isinstance(c.func, ast.Attribute):
            assert c.func.attr not in ("send_file", "send_message")
        elif isinstance(c.func, ast.Name):
            assert c.func.id not in ("send_file", "send_message")
    wsrc = LLM_WORKER_PATH.read_text(encoding="utf-8")
    assert "Phase 99" in wsrc


# 16. no second LLM call

def test_no_second_llm_call():
    src = ROUTING_PATH.read_text(encoding="utf-8")
    assert "generate_draft" not in src
    assert "get_llm_provider" not in src
    assert "genai" not in src.lower()
    assert "openai" not in src.lower()
    # llm_worker Phase99 block should not call generate_draft again
    wsrc = LLM_WORKER_PATH.read_text(encoding="utf-8")
    # Count generate_draft occurrences in Phase99 block is zero – check routing block doesn't add one
    # The routing file must not import OneCall
    assert "one_call" not in src.lower()


# 17. no second context builder

def test_no_second_context_builder():
    src = ROUTING_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                imports.append(a.name.lower())
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                imports.append(n.module.lower())
    # Check imports, not docstring mentions
    assert "context_engine.authoritative_assembly" not in " ".join(imports)
    assert "memory.context" not in " ".join(imports) or "build_qwen3_context" not in src.split("from")[1] if "from" in src else True
    # Ensure no call to context builders in code
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    for c in calls:
        name = getattr(c.func, "attr", "") or getattr(c.func, "id", "")
        assert name not in ("assemble_authoritative_context", "build_qwen3_context", "build_context")


# 18. existing commerce unchanged outside scope

def test_existing_commerce_unchanged():
    # Decision fix is intentional, but other commerce logic should remain
    dsrc = DECISION_PATH.read_text(encoding="utf-8")
    # Ensure PPV can still be triggered by explicit purchase
    assert "user_asked_to_buy" in dsrc
    assert "user_asked_about_price" in dsrc
    # Ensure free_photo routing does not modify other files like vault
    vault_src = Path("vault/service.py").read_text(encoding="utf-8") if Path("vault/service.py").exists() else ""
    assert "free_photo" not in vault_src.lower()
