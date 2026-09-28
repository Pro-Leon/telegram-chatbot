"""P3.3.7 — OfferDefinition resolver tests (no DB, no provider, no LLM).

Covers ``commerce.offer_definition_resolver`` via injected catalog rows and
DAO fakes: lifecycle, eligibility, creator isolation, versions, mappings,
family passthrough, verbatim commercial data, determinism, DB-failure
propagation, and production boundaries (no provider/ownership/selector/
taxonomy/family-creation integration).
"""

from pathlib import Path

import pytest

from commerce.offer_definition_resolver import (
    OfferDefinitionCandidate,
    is_structurally_eligible,
    resolve_offer_definitions,
)

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "offer_definition_resolver.py"


def _row(did=7, creator=1, key="black-lingerie", version=1, otype="SINGLE",
         vids=("V1",), price=1999, currency="USD", dl=True, status="active",
         family=None):
    return {
        "id": did, "creator_id": creator, "stable_key": key, "version": version,
        "offer_type": otype, "canonical_vault_item_ids": list(vids),
        "family_id": family, "price_minor": price, "currency": currency,
        "allow_download": dl, "status": status, "config": None,
        "created_at": None, "updated_at": None,
    }


def _map(did=7, creator=1, cuid="drop_abc", version=1):
    return {"definition_id": did, "definition_version": version,
            "creator_id": creator, "dropfans_product_id": cuid, "created_at": None}


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_draft_excluded(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(status="draft")], mappings=[])
        assert res == []

    @pytest.mark.asyncio
    async def test_active_included(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row()], mappings=[_map()])
        assert len(res) == 1
        assert res[0].definition_id == 7

    @pytest.mark.asyncio
    async def test_retired_excluded(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(status="retired")], mappings=[_map()])
        assert res == []


class TestEligibility:
    @pytest.mark.asyncio
    async def test_all_offer_types_included(self):
        rows = [_row(did=i, key=f"k{i}", otype=t)
                for i, t in enumerate(
                    ("SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM"), start=1)]
        res = await resolve_offer_definitions(1, definitions=rows, mappings=[])
        assert sorted(c.offer_type for c in res) == [
            "CORE_BUNDLE", "PREMIUM", "SINGLE", "SMALL_BUNDLE"]

    @pytest.mark.asyncio
    async def test_invalid_offer_type_excluded(self):
        assert await resolve_offer_definitions(
            1, definitions=[_row(otype="FLASH")], mappings=[]) == []

    @pytest.mark.asyncio
    async def test_single_vault_id_accepted(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(vids=("V1",))], mappings=[])
        assert res[0].canonical_vault_item_ids == ("V1",)

    @pytest.mark.asyncio
    async def test_ten_vault_ids_accepted(self):
        vids = tuple(f"V{i:02d}" for i in range(10))
        res = await resolve_offer_definitions(
            1, definitions=[_row(vids=vids)], mappings=[])
        assert len(res[0].canonical_vault_item_ids) == 10

    @pytest.mark.asyncio
    async def test_over_limit_excluded_never_truncated(self):
        vids = tuple(f"V{i:02d}" for i in range(11))
        res = await resolve_offer_definitions(
            1, definitions=[_row(vids=vids)], mappings=[])
        assert res == []

    @pytest.mark.asyncio
    async def test_empty_vault_ids_excluded(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(vids=[])], mappings=[])
        assert res == []

    @pytest.mark.asyncio
    async def test_uncanonical_ids_excluded_not_repaired(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(vids=("V2", "V1"))], mappings=[])
        assert res == []

    @pytest.mark.asyncio
    async def test_duplicate_ids_excluded_not_repaired(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(vids=("V1", "V1"))], mappings=[])
        assert res == []

    @pytest.mark.asyncio
    async def test_price_zero_accepted(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(price=0)], mappings=[])
        assert res[0].price_minor == 0

    @pytest.mark.asyncio
    async def test_negative_price_excluded(self):
        assert await resolve_offer_definitions(
            1, definitions=[_row(price=-1)], mappings=[]) == []

    @pytest.mark.asyncio
    async def test_empty_currency_excluded(self):
        assert await resolve_offer_definitions(
            1, definitions=[_row(currency="  ")], mappings=[]) == []

    def test_predicate_rejects_malformed(self):
        assert is_structurally_eligible(_row()) is True
        assert is_structurally_eligible(_row(status="draft")) is False
        assert is_structurally_eligible({}) is False
        assert is_structurally_eligible(None) is False
        assert is_structurally_eligible(_row(price=True)) is False


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_other_creator_definitions_invisible(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(creator=2), _row(did=8)], mappings=[])
        assert [c.definition_id for c in res] == [8]

    @pytest.mark.asyncio
    async def test_same_stable_key_isolated(self):
        rows = [_row(did=7, creator=1, key="shared"),
                _row(did=8, creator=2, key="shared")]
        res = await resolve_offer_definitions(1, definitions=rows, mappings=[])
        assert [(c.definition_id, c.creator_id) for c in res] == [(7, 1)]

    @pytest.mark.asyncio
    async def test_same_drop_cuid_isolated(self):
        maps = [_map(did=7, creator=1, cuid="drop_x"),
                _map(did=8, creator=2, cuid="drop_x")]
        res = await resolve_offer_definitions(
            1, definitions=[_row()], mappings=maps)
        assert res[0].mapped_drop_ids == ("drop_x",)

    @pytest.mark.asyncio
    async def test_invalid_creator_rejected(self):
        with pytest.raises(ValueError):
            await resolve_offer_definitions(0, definitions=[], mappings=[])

    @pytest.mark.asyncio
    async def test_dao_reads_are_creator_scoped(self, monkeypatch):
        import db.offer_definitions as odb

        seen = {}

        async def fake_list(creator_id, **kw):
            seen["definitions"] = (creator_id, kw)
            return []

        async def fake_maps(creator_id, *a, **kw):
            seen["mappings"] = creator_id
            return []

        monkeypatch.setattr(odb, "list_offer_definitions", fake_list)
        monkeypatch.setattr(odb, "list_offer_definition_drops", fake_maps)
        assert await resolve_offer_definitions(1) == []
        assert seen["definitions"][0] == 1
        assert seen["definitions"][1] == {"status": "active"}
        assert seen["mappings"] == 1


class TestVersions:
    @pytest.mark.asyncio
    async def test_only_active_version_returned(self):
        rows = [_row(did=7, version=1, status="retired"),
                _row(did=8, version=2, status="active")]
        res = await resolve_offer_definitions(1, definitions=rows, mappings=[])
        assert [(c.definition_id, c.version) for c in res] == [(8, 2)]

    @pytest.mark.asyncio
    async def test_identity_fields_retained(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(did=9, key="k", version=3)], mappings=[])
        assert (res[0].definition_id, res[0].stable_key, res[0].version) == (9, "k", 3)

    @pytest.mark.asyncio
    async def test_retired_prior_version_excluded(self):
        rows = [_row(did=7, version=1, status="retired"),
                _row(did=8, version=1, status="retired", key="other")]
        assert await resolve_offer_definitions(1, definitions=rows, mappings=[]) == []


class TestMapping:
    @pytest.mark.asyncio
    async def test_zero_mappings_empty_tuple(self):
        res = await resolve_offer_definitions(1, definitions=[_row()], mappings=[])
        assert res[0].mapped_drop_ids == ()

    @pytest.mark.asyncio
    async def test_one_mapping(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row()], mappings=[_map()])
        assert res[0].mapped_drop_ids == ("drop_abc",)

    @pytest.mark.asyncio
    async def test_multiple_mappings_all_returned(self):
        maps = [_map(cuid="drop_b"), _map(cuid="drop_a"), _map(cuid="drop_b")]
        res = await resolve_offer_definitions(1, definitions=[_row()], mappings=maps)
        assert res[0].mapped_drop_ids == ("drop_a", "drop_b")

    @pytest.mark.asyncio
    async def test_mappings_not_ranked_or_selected(self):
        maps = [_map(cuid="drop_z"), _map(cuid="drop_a")]
        res = await resolve_offer_definitions(1, definitions=[_row()], mappings=maps)
        assert set(res[0].mapped_drop_ids) == {"drop_a", "drop_z"}
        assert len(res) == 1  # definition listed once regardless of mapping count

    @pytest.mark.asyncio
    async def test_mapping_without_provider_get(self, monkeypatch):
        import integrations.dropfans.service as svc

        called = []

        async def _boom(*a, **kw):
            called.append(True)
            raise AssertionError("provider must not be called")

        monkeypatch.setattr(svc, "get_drop", _boom, raising=False)
        res = await resolve_offer_definitions(
            1, definitions=[_row()], mappings=[_map()])
        assert len(res) == 1 and called == []


class TestFamily:
    @pytest.mark.asyncio
    async def test_null_family_preserved(self):
        res = await resolve_offer_definitions(1, definitions=[_row()], mappings=[])
        assert res[0].family_id is None

    @pytest.mark.asyncio
    async def test_explicit_family_preserved(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(family=4)], mappings=[])
        assert res[0].family_id == 4

    def test_no_family_mechanics(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("db.families", "families.", "create_content_family",
                      "get_families_for_vault_item", "list_family_members",
                      "content_family", "ContentFamily"):
            assert token not in src


class TestCommercialData:
    @pytest.mark.asyncio
    async def test_values_verbatim(self):
        res = await resolve_offer_definitions(
            1, definitions=[_row(price=1999, currency="USD", dl=False,
                                 otype="PREMIUM")], mappings=[])
        cand = res[0]
        assert cand.price_minor == 1999
        assert cand.currency == "USD"
        assert cand.allow_download is False
        assert cand.offer_type == "PREMIUM"

    def test_no_pricing_or_scoring_fields(self):
        import dataclasses

        fields = {f.name for f in dataclasses.fields(OfferDefinitionCandidate)}
        assert fields == {
            "definition_id", "creator_id", "stable_key", "version", "offer_type",
            "canonical_vault_item_ids", "family_id", "price_minor", "currency",
            "allow_download", "mapped_drop_ids",
        }

    def test_candidate_immutable(self):
        res_fields = OfferDefinitionCandidate.__dataclass_params__
        assert res_fields.frozen is True


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_ordering_stable_key_version_id(self):
        rows = [_row(did=30, key="b", version=1),
                _row(did=10, key="a", version=2),
                _row(did=20, key="a", version=1),
                _row(did=40, key="a", version=1)]
        res = await resolve_offer_definitions(1, definitions=rows, mappings=[])
        assert [c.definition_id for c in res] == [20, 40, 10, 30]

    @pytest.mark.asyncio
    async def test_repeated_calls_identical(self):
        rows = [_row(did=2, key="b"), _row(did=1, key="a")]
        first = await resolve_offer_definitions(1, definitions=rows, mappings=[_map()])
        second = await resolve_offer_definitions(1, definitions=rows, mappings=[_map()])
        assert first == second


class TestFailure:
    @pytest.mark.asyncio
    async def test_db_exception_propagates(self, monkeypatch):
        import db.offer_definitions as odb

        async def _boom(creator_id, **kw):
            raise RuntimeError("DB down")

        async def _maps(creator_id, *a, **kw):
            return []

        monkeypatch.setattr(odb, "list_offer_definitions", _boom)
        monkeypatch.setattr(odb, "list_offer_definition_drops", _maps)
        with pytest.raises(RuntimeError, match="DB down"):
            await resolve_offer_definitions(1)

    @pytest.mark.asyncio
    async def test_empty_means_no_candidates(self):
        assert await resolve_offer_definitions(1, definitions=[], mappings=[]) == []


class TestBoundary:
    def test_no_forbidden_dependencies(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("get_drop", "create_drop", "attach_drop", "check_drop_status",
                      "get_owned_vault_ids", "classify_vault_overlap",
                      "filter_products_by_ownership", "product_selection",
                      "content_matching", "vault_ranking", "parse_taxonomy",
                      "bundle_group", "llm_worker", "conversational",
                      "commerce_offers", "INSERT INTO", "UPDATE ",
                      "opportunity_id", "seal_opportunity", "create_offer",
                      "create_opportunity", "FanCommercialState"):
            assert token not in src

    def test_uses_existing_dao_primitives(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "list_offer_definitions" in src
        assert "list_offer_definition_drops" in src
        assert "SELECT" not in src  # no redundant SQL

    def test_quarantine_markers_intact(self):
        base = Path(__file__).parent.parent
        for rel in ("commerce/content_matching.py", "commerce/product_selection.py",
                    "commerce/vault_taxonomy.py", "commerce/dao.py",
                    "commerce/conversational.py", "commerce/operational_execution.py"):
            assert "P3.3.4 QUARANTINE" in (base / rel).read_text(encoding="utf-8")

    def test_no_new_migration(self):
        migs = sorted((Path(__file__).parent.parent / "db" / "migrations").glob("*p33*"))
        assert [p.name for p in migs] == [
            "20260917000000_p33_content_families.sql",
            "20260917010000_p33_offer_definitions.sql",
        ]
