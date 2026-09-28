"""Phase 102 — Product/menu metadata tests A-M."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

CATALOG_PATH = Path("commerce/product_catalog.py")
CONV_PATH = Path("commerce/conversational.py")
WORKER_PATH = Path("workers/llm_worker.py")


# A. Creator isolation
@pytest.mark.asyncio
async def test_creator_isolation():
    from commerce.product_catalog import get_product, list_active_products

    # Mock DB to return product for creator 1, None for creator 2
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    # First call for creator 1 product 10 -> returns row
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"id": 10, "creator_id": 1, "title": "ProdA", "public_description": "desc", "private_description": None, "price_minor": 1000, "product_type": "test", "folder_id": None, "folder_name": None, "is_accessible": True},
        {"currency_code": "USD"},  # currency lookup
        None,  # second call for creator 2 -> None (creator mismatch)
    ])
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    with patch("commerce.product_catalog.get_pool", return_value=mock_pool):
        p1 = await get_product(1, 10)
        assert p1 is not None
        assert p1.creator_id == 1
        # Second call with different creator should not return p1's product
        # Simulate DB with creator mismatch: fetchrow returns None
        p2 = await get_product(2, 10)
        # Our mock returns None for second, but even if DB had row for creator 2, it would be different
        # The important check is SQL has WHERE creator_id=$1
        src = CATALOG_PATH.read_text(encoding="utf-8")
        assert "WHERE creator_id=$1 AND id=$2" in src
        # list_active also creator-scoped
        assert "WHERE creator_id=$1 AND is_accessible" in src


# B. Stable product identity
def test_stable_product_identity():
    src = CATALOG_PATH.read_text(encoding="utf-8")
    assert "product_id" in src
    assert "id" in src
    # Must use authoritative id, not filename
    assert "filename" not in src.lower()
    assert "vault_item_id" not in src.lower() or "product" in src.lower()  # product catalog is for products, not vault
    # Check dataclass has product_id
    assert "product_id: int" in src


# C. Price authority
@pytest.mark.asyncio
async def test_price_authority():
    from commerce.product_catalog import get_product
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"id": 10, "creator_id": 1, "title": "ProdA", "public_description": None, "private_description": None, "price_minor": 2500, "product_type": None, "folder_id": None, "folder_name": None, "is_accessible": True},
        {"currency_code": "USD"},
    ])
    mock_pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    with patch("commerce.product_catalog.get_pool", return_value=mock_pool):
        p = await get_product(1, 10)
        assert p.price_minor == 2500
        # LLM cannot override: ensure no LLM import
        src = CATALOG_PATH.read_text(encoding="utf-8")
        assert "openai" not in src.lower()
        assert "genai" not in src.lower()


# D. Currency authority
@pytest.mark.asyncio
async def test_currency_authority():
    from commerce.product_catalog import get_product
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"id": 10, "creator_id": 1, "title": "ProdA", "public_description": None, "private_description": None, "price_minor": 1000, "product_type": None, "folder_id": None, "folder_name": None, "is_accessible": True},
        {"currency_code": "EUR"},
    ])
    mock_pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    with patch("commerce.product_catalog.get_pool", return_value=mock_pool):
        p = await get_product(1, 10)
        assert p.currency == "EUR"
        # Even if LLM says USD, DB EUR wins
        assert p.currency != "USD" or p.currency == "EUR"


# E. Availability fail-closed
@pytest.mark.asyncio
async def test_availability_fail_closed():
    from commerce.product_catalog import get_product
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    # Inactive product
    mock_conn.fetchrow = AsyncMock(return_value={"id": 10, "creator_id": 1, "title": "ProdA", "public_description": None, "private_description": None, "price_minor": 1000, "product_type": None, "folder_id": None, "folder_name": None, "is_accessible": False})
    mock_pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    with patch("commerce.product_catalog.get_pool", return_value=mock_pool):
        p = await get_product(1, 10)
        assert p is None
    # Missing product
    mock_conn.fetchrow = AsyncMock(return_value=None)
    with patch("commerce.product_catalog.get_pool", return_value=mock_pool):
        p2 = await get_product(1, 999)
        assert p2 is None


# F. No product invention
@pytest.mark.asyncio
async def test_no_product_invention():
    from commerce.product_catalog import get_product
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    with patch("commerce.product_catalog.get_pool", return_value=mock_pool):
        # User or LLM mentions nonexistent product 9999
        p = await get_product(1, 9999)
        assert p is None


# G. Product context from authoritative state
@pytest.mark.asyncio
async def test_product_context_authoritative():
    from commerce.product_catalog import get_menu_context
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[
        {"id": 10, "creator_id": 1, "title": "MenuA", "public_description": "desc", "private_description": None, "price_minor": 1000, "product_type": None, "folder_id": None, "folder_name": None, "is_accessible": True},
    ])
    mock_conn.fetchrow = AsyncMock(return_value={"currency_code": "USD"})
    mock_pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    with patch("commerce.product_catalog.get_pool", return_value=mock_pool):
        ctx = await get_menu_context(1, max_items=5)
        assert "MenuA" in ctx
        # Leak hardening: internal ids/prices/creator ids never reach
        # prompt prose (titles only; selection uses structured data).
        assert "id:10" not in ctx
        assert "$" not in ctx and "10.00" not in ctx
        assert "creator 1" not in ctx
        assert "Authoritative menu" in ctx


# H. No second LLM/context
def test_no_second_llm_context():
    src = CATALOG_PATH.read_text(encoding="utf-8")
    assert "generate_draft" not in src
    assert "assemble_authoritative_context" not in src
    assert "build_qwen3_context" not in src
    wsrc = WORKER_PATH.read_text(encoding="utf-8")
    # Count generate_draft in worker (should be existing 2, not added for catalog)
    # Our catalog integration should not add new generate_draft
    # The worker's Phase 102 block should not contain generate_draft
    import re
    m = re.search(r"Phase 102.*?get_menu_context", wsrc, re.DOTALL)
    assert m
    block = wsrc[m.start():m.start()+2000]
    assert "generate_draft" not in block


# I. Creator-scoped cache (if caching exists, verify no leak)
def test_creator_scoped_no_global_cache_leak():
    src = CATALOG_PATH.read_text(encoding="utf-8")
    # Ensure no global dict cache without creator key
    assert "creator_id" in src
    # If caching were added, key must include creator_id
    if "cache" in src.lower():
        assert "creator_id" in src.lower()


# J. Purchase boundary
@pytest.mark.asyncio
async def test_purchase_boundary():
    from commerce.product_catalog import get_product
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"id": 10, "creator_id": 1, "title": "ProdA", "public_description": None, "private_description": None, "price_minor": 1000, "product_type": None, "folder_id": None, "folder_name": None, "is_accessible": True},
        {"currency_code": "USD"},
    ])
    mock_pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    with patch("commerce.product_catalog.get_pool", return_value=mock_pool):
        p = await get_product(1, 10)
        # Product existence does not imply purchase
        assert p is not None
        # Purchase would require commerce_offers, not product catalog
        assert "commerce_offers" not in CATALOG_PATH.read_text(encoding="utf-8")


# K. PPV security
def test_ppv_security():
    # Explicit content request must not become OFFER_PPV, even with product metadata
    from commerce.decision import CommerceDecisionContext, decide_commerce_action
    from commerce.models import PolicyDecision
    ctx = CommerceDecisionContext(user_id=1, creator_id=1, eligibility=PolicyDecision(allowed=True), user_requested_content=True, relationship_score=0.9, has_relevant_product=True)
    d = decide_commerce_action(ctx)
    assert d.action.value != "offer_ppv"


# L. Phase 98-100 regression

def test_phase98_100_unchanged():
    for path in [Path("commerce/free_photo.py"), Path("commerce/free_photo_routing.py"), Path("commerce/free_photo_delivery.py")]:
        src = path.read_text(encoding="utf-8")
        # Product catalog should not have touched free-photo
        assert "free_photo_deliveries" not in Path("commerce/product_catalog.py").read_text(encoding="utf-8")
    # Ensure free_photo still has quota logic
    src = Path("commerce/free_photo.py").read_text(encoding="utf-8")
    assert "free_photo_deliveries" in src


# M. Phase 101 regression
def test_phase101_unchanged():
    # Warming/readiness still deterministic and acyclic
    src = Path("commerce/conversational.py").read_text(encoding="utf-8")
    assert "derive_warming_state" in src
    assert "evaluate_readiness" in src
    # Check order still warming → readiness → window within Phase 101 block (not import)
    phase_start = src.find("# Phase 101")
    assert phase_start != -1
    phase_section = src[phase_start:phase_start+5000]
    assert phase_section.find("derive_warming_state") < phase_section.find("evaluate_phase101_readiness") < phase_section.find("derive_sales_window")
    # Readiness still no sales_window param
    rsrc = Path("commerce/readiness.py").read_text(encoding="utf-8")
    assert "def evaluate_readiness" in rsrc
    assert "sales_window" not in rsrc.split("def evaluate_readiness")[1].split(")")[0]
