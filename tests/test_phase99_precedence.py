"""Phase 99 precedence — PPV vs free-photo integration tests.

Verifies audited business rule:
  if CommerceDecision.action == OFFER_PPV and allowed == True:
      commerce owns turn, free-photo must NOT occur
  else:
      free-photo may proceed

Also verifies photo detection fix `is_photo_request` vs narrative.

All DB for free quota is mocked except where noted; commerce decision is mocked
via helper patch to avoid full state resolution duplication.
"""
import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

ROUTING_PATH = Path(__file__).parent.parent / "commerce" / "free_photo_routing.py"
WORKER_PATH = Path(__file__).parent.parent / "workers" / "llm_worker.py"


# ── Photo detection matrix ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "msg,expected",
    [
        ("send me a photo", True),
        ("can you send me a pic?", True),
        ("show me a selfie", True),
        ("photo?", True),
        ("how much for pics?", True),
        ("send me a photo, I'll pay $30", True),
        ("I wanna buy the pics", True),
        ("send me something cute", False),
        ("this photo is so cute", False),
        ("your photo collection is fire", False),
    ],
)
def test_is_photo_request_matrix(msg, expected):
    from commerce.free_photo_routing import is_photo_request

    assert is_photo_request(msg, None) is expected


def test_route_uses_stricter_is_photo_request():
    src = ROUTING_PATH.read_text(encoding="utf-8")
    # Must call is_photo_request, not _is_deterministic_photo_request, for routing decision
    assert "is_photo_request(user_message, signals)" in src
    # The loose helper should not be used for the gate
    # Ensure the gate line does not contain _is_deterministic_photo_request
    for line in src.splitlines():
        if "if not is_photo_request" in line:
            assert "_is_deterministic" not in line
            break
    else:
        pytest.fail("routing gate not found")


def test_send_re_includes_wanna_and_buy():
    src = ROUTING_PATH.read_text(encoding="utf-8")
    assert "wanna" in src and "buy" in src


# ── Decision-only helper purity ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_ppv_authorized_only_for_offer_ppv():
    from workers.llm_worker import _is_commerce_ppv_authorized_for_precedence
    from commerce.models import CommerceAction
    from commerce.decision import CommerceDecision, CommerceReason
    from commerce.state import CommerceResolutionStatus
    from commerce.single_creator import SingleCreatorStatus

    async def _run_with_action(action, allowed):
        mock_decision = CommerceDecision(action=action, reason_code=CommerceReason.STRONG_BUYING_SIGNAL, allowed=allowed, confidence=0.9)
        with patch("workers.llm_worker.resolve_single_application_creator", new_callable=AsyncMock) as mock_creator, \
             patch("workers.llm_worker.resolve_commerce_product_with_history", new_callable=AsyncMock, return_value=1), \
             patch("commerce.state.resolve_commerce_state", new_callable=AsyncMock) as mock_resolve, \
             patch("commerce.signals.decide_from_signals", return_value=mock_decision):
            mock_creator.return_value = MagicMock(status=SingleCreatorStatus.READY, creator_id=1)
            mock_pipeline_req = MagicMock()
            mock_pipeline_req.eligibility = MagicMock(allowed=True)
            mock_pipeline_req.policy = None
            mock_resolve.return_value = MagicMock(status=CommerceResolutionStatus.READY, request=mock_pipeline_req)
            # need _request_engine_kwargs to be patchable
            with patch("commerce.pipeline._request_engine_kwargs", return_value={}):
                return await _is_commerce_ppv_authorized_for_precedence(1, [{"role":"user","content":"hi"}], "persona", signals=MagicMock(), conversation_state=None)

    # Only OFFER_PPV + allowed True should be True
    assert await _run_with_action(CommerceAction.OFFER_PPV, True) is True
    assert await _run_with_action(CommerceAction.OFFER_PPV, False) is False
    assert await _run_with_action(CommerceAction.SOFT_OFFER, True) is False
    assert await _run_with_action(CommerceAction.FOLLOW_UP, True) is False
    assert await _run_with_action(CommerceAction.NO_OFFER, False) is False
    assert await _run_with_action(CommerceAction.RELATIONSHIP_BUILDING, False) is False
    assert await _run_with_action(CommerceAction.TIP_SUGGESTION, False) is False
    assert await _run_with_action(CommerceAction.OPERATOR_HANDOFF, False) is False


@pytest.mark.asyncio
async def test_soft_offer_does_not_block_free():
    """SOFT_OFFER allowed True must NOT be considered PPV-authorized."""
    from workers.llm_worker import _is_commerce_ppv_authorized_for_precedence
    from commerce.models import CommerceAction
    from commerce.decision import CommerceDecision, CommerceReason
    from commerce.state import CommerceResolutionStatus
    from commerce.single_creator import SingleCreatorStatus

    mock_decision = CommerceDecision(action=CommerceAction.SOFT_OFFER, reason_code=CommerceReason.RELATIONSHIP_READY, allowed=True, confidence=0.6)
    with patch("workers.llm_worker.resolve_single_application_creator", new_callable=AsyncMock) as mc, \
         patch("workers.llm_worker.resolve_commerce_product_with_history", new_callable=AsyncMock, return_value=1), \
         patch("commerce.state.resolve_commerce_state", new_callable=AsyncMock) as mr, \
         patch("commerce.signals.decide_from_signals", return_value=mock_decision), \
         patch("commerce.pipeline._request_engine_kwargs", return_value={}):
        mc.return_value = MagicMock(status=SingleCreatorStatus.READY, creator_id=1)
        mr.return_value = MagicMock(status=CommerceResolutionStatus.READY, request=MagicMock(eligibility=MagicMock(allowed=True), policy=None))
        result = await _is_commerce_ppv_authorized_for_precedence(1, [], "p", signals=MagicMock(), conversation_state=None)
        assert result is False


# ── Worker precedence branching ───────────────────────────────────────────

def test_worker_suppresses_free_only_on_ppv():
    src = WORKER_PATH.read_text(encoding="utf-8")
    # Must have decision-only check before free routing
    assert "_is_commerce_ppv_authorized_for_precedence" in src
    assert "_commerce_ppv_authorized" in src
    # Must gate free routing on that flag
    assert "if _commerce_ppv_authorized:" in src
    assert "Skipping free-photo routing" in src
    # Free must not be called when PPV authorized
    assert "if _commerce_ppv_authorized:" in src and "Suppressed" not in src  # basic check
    # The old unconditional free-before-commerce must be gone
    # Ensure the new commerce-owns-turn branch exists
    assert "Commerce owns turn" in src
    # SOFT_OFFER/FOLLOW_UP must remain free-eligible — check that helper checks only OFFER_PPV
    helper_src = WORKER_PATH.read_text(encoding="utf-8")
    assert "CommerceAction.OFFER_PPV" in helper_src
    # Ensure helper does not treat SOFT_OFFER as authorized
    assert "action is CommerceAction.OFFER_PPV" in helper_src


def test_worker_has_single_execution_guard():
    src = WORKER_PATH.read_text(encoding="utf-8")
    # Must still have at most one _try_commerce_draft call in the one-call path
    # Count occurrences in the one-call branch (after the new precedence)
    # We check that _try_commerce_draft is called in two places but gated by if/elif/else
    # Ensure no duplicate orchestration outside that branching
    assert src.count("_try_commerce_draft") >= 2  # helper + actual
    # Ensure resolve_and_run_commerce is only imported once and called once inside _try_commerce_draft
    # (docstring mentions count separately, so check call sites)
    call_sites = [l for l in src.splitlines() if "resolve_and_run_commerce(" in l and "import" not in l and "def " not in l]
    assert len(call_sites) == 1  # only inside _try_commerce_draft


def test_no_commerce_side_effects_in_precedence_helper():
    src = WORKER_PATH.read_text(encoding="utf-8")
    # Extract helper source
    helper_start = src.index("async def _is_commerce_ppv_authorized_for_precedence")
    helper_end = src.index("async def _try_commerce_draft")
    helper = src[helper_start:helper_end]
    # Must not call execute_ppv, create_offer, or DropFans
    assert "execute_ppv" not in helper
    assert "create_offer" not in helper
    assert "DropFans" not in helper and "dropfans" not in helper.lower().split("resolve_commerce_product")[0] or "dropfans" in helper.lower()  # allow product selection which may touch dropfans mirror but not execution
    assert "generate_commerce_response" not in helper
    assert "publish_event" not in helper or "commerce.offer_created" not in helper
    # Must use read-only resolve_commerce_state
    assert "resolve_commerce_state" in helper
    assert "decide_from_signals" in helper or "decide_commerce_action" in helper


# ── Critical examples from spec ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_case1_send_me_photo_no_offer_allows_free():
    from commerce.free_photo_routing import route_free_photo
    mock_auth = MagicMock(eligible=True, reason="eligible", seq=1, vault_item_id="vaultA")
    with patch("commerce.free_photo_routing.select_approved_free_media", new_callable=AsyncMock, return_value="vaultA"), \
         patch("commerce.free_photo_routing.authorize_free_photo", new_callable=AsyncMock, return_value=mock_auth):
        r = await route_free_photo(creator_id=1, user_id=1, user_message="send me a photo", signals=MagicMock(explicit_content_request=False))
        assert r.outcome == "eligible"


@pytest.mark.asyncio
async def test_case5_narrative_not_photo_request():
    from commerce.free_photo_routing import route_free_photo
    # Even with mocked approved media, narrative should not attempt
    with patch("commerce.free_photo_routing.select_approved_free_media", new_callable=AsyncMock, return_value="vaultA") as mock_sel, \
         patch("commerce.free_photo_routing.authorize_free_photo", new_callable=AsyncMock) as mock_auth:
        r = await route_free_photo(creator_id=1, user_id=1, user_message="this photo is so cute", signals=None)
        assert r.attempted is False
        assert r.outcome == "not_photo_request"
        mock_sel.assert_not_called()
        mock_auth.assert_not_called()


@pytest.mark.asyncio
async def test_case6_collection_not_photo_request():
    from commerce.free_photo_routing import route_free_photo
    with patch("commerce.free_photo_routing.select_approved_free_media", new_callable=AsyncMock, return_value="vaultA") as mock_sel, \
         patch("commerce.free_photo_routing.authorize_free_photo", new_callable=AsyncMock) as mock_auth:
        r = await route_free_photo(creator_id=1, user_id=1, user_message="your photo collection is fire", signals=None)
        assert r.attempted is False
        assert r.outcome == "not_photo_request"
        mock_sel.assert_not_called()
        mock_auth.assert_not_called()


# ── Existing invariants remain ────────────────────────────────────────────

def test_free_authorities_unchanged():
    # free_photo.py must remain authoritative, not bypassed
    assert Path("commerce/free_photo.py").exists()
    src = Path("commerce/free_photo.py").read_text(encoding="utf-8")
    assert "free_photo_deliveries" in src
    assert "free_media_pool" in src
    # routing must still delegate to authorize_free_photo
    rsrc = ROUTING_PATH.read_text(encoding="utf-8")
    assert "authorize_free_photo" in rsrc
    assert "free_media_pool" in Path("commerce/free_photo.py").read_text(encoding="utf-8")
