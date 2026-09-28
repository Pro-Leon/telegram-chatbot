"""P3.3.2 — ownership overlap policy tests (mocked DB, no live data).

Covers the centralized classifier in ``commerce.ownership`` and its gate in
``commerce.product_selection.resolve_commerce_product_with_history``:

- ZERO eligible / PARTIAL rejected / FULL rejected / INVALID rejected
- canonicalization, duplicates, empty-set safety
- creator/user isolation through the fetch boundary
- single-candidate fast paths cannot bypass the gate
- fallback continues, never resurrects, never slices/reprices
- DB failure stays fail-closed
"""

from unittest.mock import AsyncMock, patch

import pytest

from commerce.ownership import (
    OverlapResult,
    classify_vault_overlap,
    is_overlap_eligible,
    overlap_denial_reason,
)

pytestmark = [pytest.mark.unit]


def _product(pid, vids, price=2500):
    return {
        "id": pid,
        "creator_id": 1,
        "title": "Quiet Object",
        "price_minor": price,
        "sales_url": f"https://www.dropfans.io/buy/p{pid}",
        "is_accessible": True,
        "raw": {"dropfans_product_id": f"df_{pid}", "vaultItemIds": list(vids)},
    }


class TestClassifier:
    def test_zero_overlap_eligible(self):
        result = classify_vault_overlap(["V1", "V2"], {"V3"})
        assert result is OverlapResult.ZERO
        assert is_overlap_eligible(result) is True
        assert overlap_denial_reason(result) is None

    def test_full_overlap_rejected(self):
        result = classify_vault_overlap(["V1", "V2"], {"V1", "V2"})
        assert result is OverlapResult.FULL
        assert is_overlap_eligible(result) is False
        assert overlap_denial_reason(result) == "reject_full_overlap"

    def test_partial_overlap_rejected(self):
        result = classify_vault_overlap(["V1", "V2"], {"V1"})
        assert result is OverlapResult.PARTIAL
        assert is_overlap_eligible(result) is False
        assert overlap_denial_reason(result) == "reject_partial_overlap"

    def test_duplicate_candidate_ids(self):
        result = classify_vault_overlap(["V1", "V1", "V2"], {"V1"})
        assert result is OverlapResult.PARTIAL

    def test_duplicate_owned_ids_deterministic(self):
        first = classify_vault_overlap(["V1", "V2"], ["V1", "V1"])
        second = classify_vault_overlap(["V1", "V2"], ["V1", "V1"])
        assert first is second is OverlapResult.PARTIAL

    @pytest.mark.parametrize("bad", [[], (), None, "", ["  "], [None], [123]])
    def test_empty_candidate_rejected_never_eligible(self, bad):
        result = classify_vault_overlap(bad, {"V1"})
        assert result is OverlapResult.INVALID
        assert is_overlap_eligible(result) is False
        assert overlap_denial_reason(result) == "invalid_candidate_vault_set"

    def test_empty_owned_means_zero(self):
        assert classify_vault_overlap(["V1"], set()) is OverlapResult.ZERO

    def test_whitespace_canonicalized(self):
        assert classify_vault_overlap(["  V1 ", "V2"], {"V1", "V2"}) is OverlapResult.FULL


class TestFetchBoundaryIsolation:
    @pytest.mark.asyncio
    async def test_creator_isolation(self):
        from commerce import ownership as own

        seen = {}

        async def _fetch(creator_id, user_id):
            seen["creator"] = creator_id
            return frozenset({"V1"}) if creator_id == 1 else frozenset({"V2"})

        with patch.object(own, "fetch_owned_vault_ids", side_effect=_fetch):
            kept_a = await own.filter_products_by_ownership(1, 10, [_product(1, ["V1"]), _product(2, ["V9"])])
            assert [p["id"] for p in kept_a] == [2]
            assert seen["creator"] == 1
            kept_b = await own.filter_products_by_ownership(2, 10, [_product(1, ["V1"]), _product(2, ["V9"])])
            # Creator B owns V2, not V1: the V1 candidate stays eligible here.
            assert [p["id"] for p in kept_b] == [1, 2]

    @pytest.mark.asyncio
    async def test_user_isolation(self):
        from commerce import ownership as own

        async def _fetch(creator_id, user_id):
            return frozenset({"V1"}) if user_id == 10 else frozenset({"V2"})

        with patch.object(own, "fetch_owned_vault_ids", side_effect=_fetch):
            kept_a = await own.filter_products_by_ownership(1, 10, [_product(1, ["V1"])])
            assert kept_a == []
            kept_b = await own.filter_products_by_ownership(1, 11, [_product(1, ["V1"])])
            assert [p["id"] for p in kept_b] == [1]


class TestSelectorGate:
    @pytest.mark.asyncio
    async def test_single_candidate_fully_owned_not_returned(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
        ) as mock_purch, patch(
            "commerce.ownership.fetch_owned_vault_ids", new_callable=AsyncMock
        ) as mock_owned:
            mock_db.list_fangate_products = AsyncMock(return_value=[_product(10, ["V1", "V2"])])
            mock_purch.return_value = set()
            mock_owned.return_value = frozenset({"V1", "V2"})
            assert await resolve_commerce_product_with_history(1, 10) is None

    @pytest.mark.asyncio
    async def test_single_candidate_partial_never_sliced(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        product = _product(10, ["V1", "V2"])
        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
        ) as mock_purch, patch(
            "commerce.ownership.fetch_owned_vault_ids", new_callable=AsyncMock
        ) as mock_owned:
            mock_db.list_fangate_products = AsyncMock(return_value=[product])
            mock_purch.return_value = set()
            mock_owned.return_value = frozenset({"V1"})
            assert await resolve_commerce_product_with_history(1, 10) is None
            # No slicing: the candidate object is unmodified.
            assert product["raw"]["vaultItemIds"] == ["V1", "V2"]

    @pytest.mark.asyncio
    async def test_fallback_to_next_unowned(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
        ) as mock_purch, patch(
            "commerce.ownership.fetch_owned_vault_ids", new_callable=AsyncMock
        ) as mock_owned:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_product(10, ["V1"], price=100), _product(20, ["V9"], price=200)]
            )
            mock_purch.return_value = set()
            mock_owned.return_value = frozenset({"V1"})
            assert await resolve_commerce_product_with_history(1, 10) == 20

    @pytest.mark.asyncio
    async def test_all_owned_no_candidate(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
        ) as mock_purch, patch(
            "commerce.ownership.fetch_owned_vault_ids", new_callable=AsyncMock
        ) as mock_owned:
            mock_db.list_fangate_products = AsyncMock(
                return_value=[_product(10, ["V1"]), _product(20, ["V2"])]
            )
            mock_purch.return_value = set()
            mock_owned.return_value = frozenset({"V1", "V2"})
            assert await resolve_commerce_product_with_history(1, 10) is None

    @pytest.mark.asyncio
    async def test_partial_bundle_never_sliced_no_reprice(self):
        from commerce import ownership as own

        owned = frozenset({"V1"})
        assert classify_vault_overlap(["V1", "V2"], owned) is OverlapResult.PARTIAL
        # Filter-level proof: partially overlapping product is excluded whole.
        with patch.object(own, "fetch_owned_vault_ids", AsyncMock(return_value=owned)):
            result = await own.filter_products_by_ownership(1, 10, [_product(10, ["V1", "V2"])])
        assert result == []

    @pytest.mark.asyncio
    async def test_db_failure_fail_closed(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
        ) as mock_purch, patch(
            "commerce.ownership.fetch_owned_vault_ids", new_callable=AsyncMock
        ) as mock_owned:
            mock_db.list_fangate_products = AsyncMock(return_value=[_product(10, ["V1"])])
            mock_purch.return_value = set()
            mock_owned.side_effect = RuntimeError("connection down")
            assert await resolve_commerce_product_with_history(1, 10) is None

    @pytest.mark.asyncio
    async def test_filter_propagates_db_failure(self):
        from commerce import ownership as own

        with patch.object(
            own, "fetch_owned_vault_ids", side_effect=RuntimeError("connection down")
        ):
            with pytest.raises(RuntimeError, match="connection down"):
                await own.filter_products_by_ownership(1, 10, [_product(10, ["V1"])])


class TestCentralization:
    def test_selector_uses_central_gate(self):
        import inspect

        from commerce.product_selection import resolve_commerce_product_with_history

        src = inspect.getsource(resolve_commerce_product_with_history)
        assert "filter_products_by_ownership" in src
        # No independent set-intersection ownership logic in the selector.
        assert "canonical_identity_ids" not in src
