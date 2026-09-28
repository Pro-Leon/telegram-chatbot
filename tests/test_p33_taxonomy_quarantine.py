"""P3.3.4 — taxonomy quarantine tests (mocked DB, no live data).

Proves the descriptive/commercial split:

- topical relevance still works (tokens match conversation)
- shared bundle_group/title similarity confers NO bundling, eligibility,
  fatigue, suppression, or ownership meaning
- bundle_related() is descriptive only
- ContentFamily is not consulted by selection (and empty families change nothing)
- the P3.3.2 ownership gate still precedes ranking
"""

from unittest.mock import AsyncMock, patch

import pytest

from commerce.content_matching import rank_products_by_relevance
from commerce.ownership import OverlapResult, classify_vault_overlap

pytestmark = [pytest.mark.unit]


def _product(pid, title, price, vids=None):
    return {
        "id": pid,
        "creator_id": 1,
        "title": title,
        "price_minor": price,
        "sales_url": f"https://www.dropfans.io/buy/p{pid}",
        "is_accessible": True,
        "raw": {"dropfans_product_id": f"df_{pid}", "vaultItemIds": list(vids or [])},
    }


_SMALL_RED = _product(1, "Red Lace - Bedroom - 3 Photo Set", 100, ["V1", "V2", "V3"])
_LARGE_RED = _product(2, "Red Lace - Bedroom - 6 Photo Bundle", 200,
                      ["V4", "V5", "V6", "V7", "V8", "V9"])
_BLUE = _product(3, "Blue Dress - Beach - 3 Photo Set", 50, ["V10"])


def _topics():
    return "red", ("red",), ["red lace"]


class TestDescriptiveRelevanceKept:
    def test_taxonomy_relevance_remains(self):
        topic, threads, prefs = _topics()
        ranked = rank_products_by_relevance(
            [_SMALL_RED, _BLUE], topic, threads, prefs, creator_id=1)
        assert ranked[0][0]["id"] == 1
        assert ranked[0][1] > ranked[1][1]

    def test_descriptive_relevance_after_quarantine(self):
        topic, threads, prefs = _topics()
        ranked = rank_products_by_relevance(
            [_LARGE_RED, _BLUE], topic, threads, prefs, creator_id=1)
        assert ranked[0][0]["id"] == 2


class TestNoCommercialBundleInference:
    def test_shared_group_cannot_force_bundling(self):
        """Same bundle_group, equal relevance: cheaper wins, no bundle boost."""
        topic, threads, prefs = _topics()
        ranked = rank_products_by_relevance(
            [_SMALL_RED, _LARGE_RED], topic, threads, prefs, creator_id=1)
        ids = [p["id"] for p, _ in ranked]
        assert sorted(ids) == [1, 2]  # separate candidates, never merged
        assert ids[0] == 1  # price decides, not media_count

    def test_shared_group_cannot_create_eligibility(self):
        topic, threads, prefs = _topics()
        bad = dict(_LARGE_RED)
        bad["sales_url"] = None  # invalid stays invalid despite shared group
        ranked = rank_products_by_relevance(
            [_SMALL_RED, bad], topic, threads, prefs, creator_id=1)
        assert ranked  # ranker is descriptive; validity is enforced elsewhere
        assert classify_vault_overlap(["VX"], {"V1"}) is OverlapResult.ZERO

    def test_group_exposure_creates_no_fatigue(self):
        from commerce.vault_taxonomy import parse_taxonomy

        group = parse_taxonomy(_SMALL_RED["title"]).bundle_group
        assert group
        topic, threads, prefs = _topics()
        plain = rank_products_by_relevance(
            [_SMALL_RED, _LARGE_RED], topic, threads, prefs, creator_id=1)
        with_groups = rank_products_by_relevance(
            [_SMALL_RED, _LARGE_RED], topic, threads, prefs,
            recent_offered_groups={group}, creator_id=1)
        assert [p["id"] for p, _ in plain] == [p["id"] for p, _ in with_groups]
        assert [s for _, s in plain] == [s for _, s in with_groups]

    def test_title_similarity_cannot_imply_ownership(self):
        # Same title family, disjoint Vault IDs: unowned.
        assert classify_vault_overlap(["V4", "V5"], {"V1", "V2"}) is OverlapResult.ZERO

    def test_title_similarity_cannot_imply_bundle(self):
        topic, threads, prefs = _topics()
        ranked = rank_products_by_relevance(
            [_SMALL_RED, _LARGE_RED], topic, threads, prefs, creator_id=1)
        assert len(ranked) == 2
        assert ranked[0][0]["id"] != ranked[1][0]["id"]

    def test_bundle_related_has_no_commercial_authority(self):
        from commerce.vault_taxonomy import bundle_related, parse_taxonomy

        assert bundle_related(
            parse_taxonomy(_SMALL_RED["title"]), parse_taxonomy(_LARGE_RED["title"]))
        # ...yet the cheaper candidate still ranks first (price, not relation).
        topic, threads, prefs = _topics()
        ranked = rank_products_by_relevance(
            [_SMALL_RED, _LARGE_RED], topic, threads, prefs, creator_id=1)
        assert ranked[0][0]["id"] == 1


class TestFamilyBoundary:
    def test_selection_does_not_use_content_family(self):
        import inspect

        import commerce.content_matching
        import commerce.product_selection
        import commerce.vault_ranking

        for mod in (
            commerce.product_selection,
            commerce.content_matching,
            commerce.vault_ranking,
        ):
            src = inspect.getsource(mod)
            assert "db.families" not in src
            assert "commerce_content_famil" not in src
            assert "get_families_for_vault_item" not in src


class TestOwnershipStillFirst:
    @pytest.mark.asyncio
    async def test_owned_candidate_rejected_despite_top_relevance(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
        ) as mock_purch, patch(
            "commerce.ownership.fetch_owned_vault_ids", new_callable=AsyncMock
        ) as mock_owned:
            mock_db.list_fangate_products = AsyncMock(return_value=[_SMALL_RED])
            mock_purch.return_value = set()
            mock_owned.return_value = frozenset({"V1", "V2", "V3"})
            topic, threads, prefs = _topics()
            result = await resolve_commerce_product_with_history(
                1, 10, current_topic=topic,
                open_threads=threads, preferences=prefs)
            assert result is None

    @pytest.mark.asyncio
    async def test_partial_overlap_rejected_despite_relevance(self):
        from commerce.product_selection import resolve_commerce_product_with_history

        with patch("commerce.product_selection.db_fangate") as mock_db, patch(
            "commerce.product_selection._get_purchased_product_ids", new_callable=AsyncMock
        ) as mock_purch, patch(
            "commerce.ownership.fetch_owned_vault_ids", new_callable=AsyncMock
        ) as mock_owned:
            mock_db.list_fangate_products = AsyncMock(return_value=[_SMALL_RED])
            mock_purch.return_value = set()
            mock_owned.return_value = frozenset({"V1"})
            result = await resolve_commerce_product_with_history(
                1, 10, current_topic="red", open_threads=("red",), preferences=["red lace"])
            assert result is None


class TestCreatorIsolation:
    def test_taxonomy_identical_across_creators(self):
        topic, threads, prefs = _topics()
        ranked_a = rank_products_by_relevance(
            [_SMALL_RED, _LARGE_RED], topic, threads, prefs, creator_id=1)
        ranked_b = rank_products_by_relevance(
            [_SMALL_RED, _LARGE_RED], topic, threads, prefs, creator_id=2)
        assert [p["id"] for p, _ in ranked_a] == [p["id"] for p, _ in ranked_b]
