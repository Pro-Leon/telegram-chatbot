"""Tests for commerce.product_selection — deterministic product resolution.

Groups:

A: valid product criteria (_is_valid_product).
B: resolve_commerce_product outcomes (no products, single, multiple, DB failure).
C: creator isolation.
D: ordering independence (DB order does not affect selection).
E: edge cases (inaccessible products, missing sales_url, empty strings).
F: failure isolation (DB errors never raise).
"""

from unittest.mock import AsyncMock, patch

import pytest

from commerce.product_selection import _is_valid_product, resolve_commerce_product

pytestmark = [pytest.mark.unit]


# ── helpers ──────────────────────────────────────────────────────────────────


def _product(
    product_id: int = 10,
    *,
    is_accessible: bool = True,
    sales_url: str = "https://fangate.test/p/10",
) -> dict:
    return {
        "id": product_id,
        "creator_id": 1,
        "product_type": "video",
        "title": f"Product {product_id}",
        "preview_url": "https://f.test/pv",
        "price_minor": 500,
        "in_collection": True,
        "sales_url": sales_url,
        "link_clicks": 0,
        "unlocks": 0,
        "total_earnings": 0,
        "folder_id": None,
        "folder_name": None,
        "is_adult_content": False,
        "is_verif_age": False,
        "is_epoch_enabled": False,
        "is_should_consent": False,
        "is_downloadable": True,
        "is_accessible": is_accessible,
        "private_description": None,
        "public_description": None,
        "synced_at": None,
    }


# ── GROUP A — valid product criteria ─────────────────────────────────────────


class TestIsValidProduct:
    def test_accessible_with_sales_url_is_valid(self):
        assert _is_valid_product(_product()) is True

    def test_inaccessible_is_invalid(self):
        assert _is_valid_product(_product(is_accessible=False)) is False

    def test_no_sales_url_is_invalid(self):
        assert _is_valid_product(_product(sales_url=None)) is False

    def test_empty_sales_url_is_invalid(self):
        assert _is_valid_product(_product(sales_url="")) is False

    def test_whitespace_only_sales_url_is_invalid(self):
        assert _is_valid_product(_product(sales_url="   ")) is False

    def test_missing_is_accessible_defaults_false(self):
        p = _product()
        del p["is_accessible"]
        assert _is_valid_product(p) is False

    def test_missing_sales_url_defaults_invalid(self):
        p = _product()
        del p["sales_url"]
        assert _is_valid_product(p) is False

    def test_false_string_is_accessible_is_truthy_in_python(self):
        """Python strings are truthy even when content is 'false'. This edge
        case doesn't occur in practice (DB returns actual booleans), but the
        code correctly handles it since the eligibility layer also uses truthy
        checks."""
        p = _product()
        p["is_accessible"] = "false"
        # "false" is truthy in Python — product is considered accessible
        assert _is_valid_product(p) is True


# ── GROUP B — resolve_commerce_product outcomes ──────────────────────────────


class TestResolveCommerceProduct:
    @pytest.mark.asyncio
    async def test_no_products_returns_none(self):
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=[]),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_single_valid_product_returns_its_id(self):
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=[_product(100)]),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result == 100

    @pytest.mark.asyncio
    async def test_single_valid_product_returns_int(self):
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=[_product(42)]),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_multiple_valid_products_returns_none(self):
        products = [_product(10), _product(20), _product(30)]
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=products),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_two_valid_products_returns_none(self):
        products = [_product(10), _product(20)]
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=products),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_one_valid_among_inaccessible_returns_valid(self):
        products = [_product(10, is_accessible=False), _product(20)]
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=products),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result == 20

    @pytest.mark.asyncio
    async def test_one_valid_among_no_sales_url_returns_valid(self):
        products = [_product(10, sales_url=None), _product(20)]
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=products),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result == 20

    @pytest.mark.asyncio
    async def test_all_inaccessible_returns_none(self):
        products = [_product(10, is_accessible=False), _product(20, is_accessible=False)]
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=products),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_db_failure_returns_none(self):
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(side_effect=RuntimeError("DB connection lost")),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None


# ── GROUP C — creator isolation ──────────────────────────────────────────────


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_creator_scoping_passed_to_db(self):
        mock_list = AsyncMock(return_value=[])
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=mock_list,
        ):
            await resolve_commerce_product(creator_id=42)
        mock_list.assert_awaited_once_with(42, limit=200, offset=0)

    @pytest.mark.asyncio
    async def test_creator_a_does_not_see_creator_b_products(self):
        """The DB query filters by creator_id; the resolver only sees products
        belonging to the requested creator. Cross-creator products are never
        returned by the DB layer (enforced in db/fangate.py)."""
        mock_list = AsyncMock(return_value=[_product(100)])
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=mock_list,
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result == 100
        mock_list.assert_awaited_once_with(1, limit=200, offset=0)


# ── GROUP D — ordering independence ─────────────────────────────────────────


class TestOrderingIndependence:
    @pytest.mark.asyncio
    async def test_single_product_regardless_of_position(self):
        """A single valid product is returned regardless of its position in
        the DB result set."""
        for product in [_product(10), _product(99), _product(999)]:
            with patch(
                "commerce.product_selection.db_fangate.list_fangate_products",
                new=AsyncMock(return_value=[product]),
            ):
                result = await resolve_commerce_product(creator_id=1)
            assert result == product["id"]

    @pytest.mark.asyncio
    async def test_multiple_products_never_picks_first(self):
        """With multiple valid products, the resolver returns None regardless
        of DB ordering — it never picks the first row."""
        for order in [
            [_product(10), _product(20)],
            [_product(20), _product(10)],
            [_product(30), _product(10), _product(20)],
        ]:
            with patch(
                "commerce.product_selection.db_fangate.list_fangate_products",
                new=AsyncMock(return_value=order),
            ):
                result = await resolve_commerce_product(creator_id=1)
            assert result is None


# ── GROUP E — edge cases ────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_product_with_zero_price_and_valid_url(self):
        """Price is not a validity criterion — execute_ppv verifies price."""
        p = _product(10)
        p["price_minor"] = 0
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=[p]),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result == 10

    @pytest.mark.asyncio
    async def test_product_with_none_price_and_valid_url(self):
        p = _product(10)
        p["price_minor"] = None
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=[p]),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result == 10

    @pytest.mark.asyncio
    async def test_large_product_id(self):
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=[_product(999999999)]),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result == 999999999

    @pytest.mark.asyncio
    async def test_many_products_all_inaccessible(self):
        products = [_product(i, is_accessible=False) for i in range(50)]
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=products),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_products_with_duplicate_ids(self):
        """Even if the DB returns duplicates (shouldn't happen), the single
        valid product rule still applies."""
        p = _product(10)
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(return_value=[p, p]),
        ):
            result = await resolve_commerce_product(creator_id=1)
        # Two entries in the list → len(valid) == 2 → ambiguous → None
        assert result is None


# ── GROUP F — failure isolation ──────────────────────────────────────────────


class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_db_timeout_returns_none(self):
        import asyncio

        async def _timeout(*_args, **_kwargs):
            raise asyncio.TimeoutError("connection timed out")

        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=_timeout,
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_postgres_error_returns_none(self):
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(side_effect=RuntimeError("asyncpg: connection pool exhausted")),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_none(self):
        with patch(
            "commerce.product_selection.db_fangate.list_fangate_products",
            new=AsyncMock(side_effect=KeyError("unexpected")),
        ):
            result = await resolve_commerce_product(creator_id=1)
        assert result is None
