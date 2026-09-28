"""P3.3.6 — provider-verified Drop reconciliation/seeding tests (mocked, no I/O).

Covers ``commerce.drop_reconciliation`` with injected provider/DAO fakes
(no live Dropfans, no database): candidate collection, live verification,
Vault identity, commercial values, classification, operator semantics,
idempotency, and safety boundaries (dry-run default, GET-only provider use,
no selector/LLM/taxonomy/family integration, history preservation).
"""

from pathlib import Path

import pytest

from commerce.drop_reconciliation import (
    CreatorReconciliation,
    DropCandidate,
    OperatorSeedInput,
    ReconciliationClassification,
    ReconciliationResult,
    classify_reconciliation,
    collect_drop_candidates,
    definitions_exact_match,
    dollars_to_price_minor,
    reconcile_creator,
    verify_live_drop,
    LiveDropInvalid,
)
from integrations.dropfans.errors import (
    DropfansAuthenticationError,
    DropfansAuthorizationError,
    DropfansNotFoundError,
    DropfansRateLimitError,
    DropfansServerError,
    DropfansTimeoutError,
    DropfansTransportError,
)

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "drop_reconciliation.py"


def _live(cuid="D1", price=20.0, currency="USD", allow_download=True,
          status="APPROVED", vids=("V1", "V2"), **over):
    payload = {
        "id": cuid,
        "name": "Black Lingerie Mega Bundle",
        "price": price,
        "currency": currency,
        "status": status,
        "buy_url": "https://dropfans.test/buy/D1",
        "allow_download": allow_download,
        "media_count": len(vids),
        "media": [{"vault_item_id": v} for v in vids],
        "sales_count": 3,
    }
    payload.update(over)
    return payload


def _def(did=7, vids=("V1", "V2"), price=2000, currency="USD",
         allow_download=True, version=1, status="active"):
    return {
        "id": did, "creator_id": 1, "stable_key": "black-lingerie",
        "version": version, "offer_type": "SMALL_BUNDLE",
        "canonical_vault_item_ids": list(vids),
        "family_id": None, "price_minor": price, "currency": currency,
        "allow_download": allow_download, "status": status, "config": None,
        "created_at": None, "updated_at": None,
    }


def _get_drop(payload=None, exc=None):
    async def _fn(creator_id, cuid):
        _fn.calls.append((creator_id, cuid))
        if exc is not None:
            raise exc
        return payload
    _fn.calls = []
    return _fn


class FakeODB:
    """In-memory stand-in for db.offer_definitions (records writes)."""

    def __init__(self, definitions=None, mappings=None, create_exc=None,
                 by_key=None):
        self.definitions = list(definitions or [])
        self.mappings = list(mappings or [])
        self.create_exc = create_exc
        self.by_key = by_key
        self.created = []
        self.activated = []
        self.mapped = []

    async def list_offer_definitions(self, creator_id):
        assert creator_id == 1
        return [dict(d) for d in self.definitions]

    async def list_offer_definition_drops(self, creator_id, definition_id=None):
        assert creator_id == 1
        rows = [dict(m) for m in self.mappings]
        if definition_id is not None:
            rows = [m for m in rows if m.get("definition_id") == definition_id]
        return rows

    async def create_offer_definition(self, creator_id, stable_key, offer_type,
                                      vault_ids, price_minor, currency,
                                      allow_download, **kw):
        self.created.append((creator_id, stable_key, offer_type, vault_ids,
                             price_minor, currency, allow_download, kw))
        if self.create_exc is not None:
            raise self.create_exc
        row = _def(did=100 + len(self.created), vids=tuple(vault_ids),
                   price=price_minor, currency=currency,
                   allow_download=allow_download)
        row["stable_key"] = stable_key
        row["offer_type"] = offer_type
        self.definitions.append(row)
        return dict(row)

    async def activate_offer_definition(self, creator_id, definition_id):
        self.activated.append((creator_id, definition_id))
        for d in self.definitions:
            if d["id"] == definition_id:
                d["status"] = "active"
                return dict(d)
        raise ValueError("not found")

    async def get_offer_definition_by_key(self, creator_id, stable_key, version):
        if self.by_key is not None:
            return dict(self.by_key)
        for d in self.definitions:
            if (d["creator_id"] == creator_id and d["stable_key"] == stable_key
                    and d["version"] == version):
                return dict(d)
        return None

    async def map_offer_definition_drop(self, creator_id, definition_id,
                                        definition_version, cuid):
        self.mapped.append((creator_id, definition_id, definition_version, cuid))
        row = {"definition_id": definition_id, "definition_version": definition_version,
               "creator_id": creator_id, "dropfans_product_id": cuid, "created_at": None}
        self.mappings.append(row)
        return dict(row)

    def install(self, monkeypatch):
        import db.offer_definitions as odb
        monkeypatch.setattr(odb, "list_offer_definitions", self.list_offer_definitions)
        monkeypatch.setattr(odb, "list_offer_definition_drops", self.list_offer_definition_drops)
        monkeypatch.setattr(odb, "create_offer_definition", self.create_offer_definition)
        monkeypatch.setattr(odb, "activate_offer_definition", self.activate_offer_definition)
        monkeypatch.setattr(odb, "get_offer_definition_by_key",
                            self.get_offer_definition_by_key)
        monkeypatch.setattr(odb, "map_offer_definition_drop", self.map_offer_definition_drop)


def _cand(cuid="D1", **kw):
    return DropCandidate(creator_id=1, dropfans_product_id=cuid, **kw)


# ── Candidate collection ─────────────────────────────────────────────

class TestCandidateCollection:
    def test_mirror_cuid_discovered(self):
        cands = collect_drop_candidates(1, mirror_rows=[
            {"dropfans_product_id": "D1", "vault_item_ids": ["V1"],
             "price_minor": 2000, "is_downloadable": True}])
        assert len(cands) == 1
        assert cands[0].dropfans_product_id == "D1"
        assert cands[0].mirror_price_minor == 2000
        assert cands[0].mirror_allow_download is True

    def test_intent_cuid_discovered(self):
        cands = collect_drop_candidates(1, intent_cuids=["D9"])
        assert [c.dropfans_product_id for c in cands] == ["D9"]
        assert cands[0].mirror_vault_ids is None

    def test_historical_offer_cuid_discovered(self):
        cands = collect_drop_candidates(1, offer_cuids=["D7"])
        assert [c.dropfans_product_id for c in cands] == ["D7"]

    def test_duplicate_candidates_deduplicated(self):
        cands = collect_drop_candidates(
            1,
            mirror_rows=[{"dropfans_product_id": "D1", "vault_item_ids": ["V1"]}],
            intent_cuids=["D1", "D2"],
            offer_cuids=["D1", "D2", "D3"])
        assert sorted(c.dropfans_product_id for c in cands) == ["D1", "D2", "D3"]

    def test_empty_cuid_excluded(self):
        cands = collect_drop_candidates(
            1, mirror_rows=[{"dropfans_product_id": "  "}],
            intent_cuids=["", "  ", "D1"], offer_cuids=[None, "D1"])
        assert [c.dropfans_product_id for c in cands] == ["D1"]

    def test_creator_isolation(self):
        cands = collect_drop_candidates(2, mirror_rows=[
            {"dropfans_product_id": "D1"}])
        assert all(c.creator_id == 2 for c in cands)
        with pytest.raises(ValueError):
            collect_drop_candidates(0, mirror_rows=[{"dropfans_product_id": "D1"}])

    def test_synthetic_id_never_used(self):
        cands = collect_drop_candidates(1, mirror_rows=[
            {"id": 12345, "dropfans_product_id": "D1"}])
        assert cands[0].dropfans_product_id == "D1"


# ── Provider verification ────────────────────────────────────────────

class TestProviderVerification:
    def test_approved_drop_verifies(self):
        live = verify_live_drop(_live())
        assert live.vault_item_ids == ["V1", "V2"]
        assert live.price_minor == 2000
        assert live.currency == "USD"
        assert live.allow_download is True
        assert live.status == "APPROVED"

    @pytest.mark.asyncio
    async def test_pending_rejected_flagged_are_ambiguous(self):
        for status in ("PENDING", "REJECTED", "FLAGGED"):
            fn = _get_drop(_live(status=status))
            report = await reconcile_creator(
                1, candidates=[_cand()], get_drop=fn,
                existing_definitions=[], existing_mappings=[])
            assert report.results[0].classification == "ambiguous"
            assert "provider_status_not_approved" in report.results[0].reason

    @pytest.mark.asyncio
    async def test_404_is_unreconcilable(self):
        fn = _get_drop(exc=DropfansNotFoundError("get_drop", "missing", 404))
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        res = report.results[0]
        assert res.classification == "unreconcilable"
        assert res.reason == "drop_not_found"
        assert res.live_vault_item_ids is None

    @pytest.mark.asyncio
    async def test_credential_revoked_stops_creator(self):
        fn = _get_drop(exc=DropfansAuthenticationError("get_drop", "bad", 401))
        report = await reconcile_creator(
            1, candidates=[_cand("D1"), _cand("D2")], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert report.credential_failed is True
        assert all(r.classification == "needs_provider_refresh" for r in report.results)
        assert all(r.reason == "credential_failure" for r in report.results)
        assert len(fn.calls) == 1  # stopped after first failure

    @pytest.mark.asyncio
    async def test_403_authorization_stops_creator(self):
        fn = _get_drop(exc=DropfansAuthorizationError("get_drop", "denied", 403))
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert report.credential_failed is True
        assert report.results[0].reason == "credential_failure"

    @pytest.mark.asyncio
    async def test_429_needs_refresh(self):
        fn = _get_drop(exc=DropfansRateLimitError("get_drop", "slow", 429))
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        res = report.results[0]
        assert res.classification == "needs_provider_refresh"
        assert res.reason == "rate_limited"

    @pytest.mark.asyncio
    async def test_5xx_needs_refresh(self):
        fn = _get_drop(exc=DropfansServerError("get_drop", "boom", 500))
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert report.results[0].classification == "needs_provider_refresh"

    @pytest.mark.asyncio
    async def test_timeout_needs_refresh(self):
        fn = _get_drop(exc=DropfansTimeoutError("get_drop", "slow"))
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert report.results[0].classification == "needs_provider_refresh"

    @pytest.mark.asyncio
    async def test_transport_needs_refresh(self):
        fn = _get_drop(exc=DropfansTransportError("get_drop", "down"))
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert report.results[0].classification == "needs_provider_refresh"

    @pytest.mark.asyncio
    async def test_malformed_response_needs_refresh(self):
        fn = _get_drop({"unexpected": "shape"})
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert report.results[0].classification == "needs_provider_refresh"


# ── Vault identity ───────────────────────────────────────────────────

class TestVaultIdentity:
    def test_canonical_ordering(self):
        live = verify_live_drop(_live(vids=["V3", "V1", "V2"]))
        assert live.vault_item_ids == ["V1", "V2", "V3"]

    def test_duplicate_collapse(self):
        live = verify_live_drop(_live(vids=["V1", "V2", "V2"]))
        assert live.vault_item_ids == ["V1", "V2"]

    def test_empty_rejected(self):
        with pytest.raises(LiveDropInvalid) as ei:
            verify_live_drop(_live(vids=[]))
        assert ei.value.reason == "empty_vault_set"

    def test_over_limit_rejected(self):
        with pytest.raises(LiveDropInvalid) as ei:
            verify_live_drop(_live(vids=[f"V{i}" for i in range(11)]))
        assert ei.value.reason == "vault_set_over_limit"

    def test_invalid_cuid_rejected(self):
        with pytest.raises(LiveDropInvalid):
            verify_live_drop(_live(vids=["V1", "  "]))


# ── Commercial values ────────────────────────────────────────────────

class TestCommercialValues:
    def test_live_price_wins_over_stale_mirror(self):
        cand = _cand(mirror_price_minor=9999)
        live = verify_live_drop(_live(price=20.0))
        assert live.price_minor == 2000
        cls, reason = classify_reconciliation(cand, live)
        assert cls == ReconciliationClassification.AMBIGUOUS
        assert "price" in reason  # stale mirror fails closed, never seeded

    def test_price_conversion_exact(self):
        assert dollars_to_price_minor(19.99) == 1999
        assert dollars_to_price_minor(5) == 500
        assert dollars_to_price_minor(0) == 0
        with pytest.raises(LiveDropInvalid) as ei:
            dollars_to_price_minor(10000)
        assert ei.value.reason == "price_out_of_range"

    def test_seller_earning_never_used(self):
        live = verify_live_drop(_live(price=20.0, seller_earning=5000))
        assert live.price_minor == 2000
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "seller_earning" not in src

    def test_live_currency_used(self):
        assert verify_live_drop(_live(currency="usd")).currency == "USD"

    def test_non_usd_rejected(self):
        with pytest.raises(LiveDropInvalid) as ei:
            verify_live_drop(_live(currency="EUR"))
        assert ei.value.reason == "non_usd_currency"

    def test_live_allow_download_used(self):
        assert verify_live_drop(_live(allow_download=False)).allow_download is False

    def test_missing_allow_download_rejected(self):
        payload = _live()
        del payload["allow_download"]
        with pytest.raises(LiveDropInvalid) as ei:
            verify_live_drop(payload)
        assert ei.value.reason == "missing_allow_download"


# ── Reconciliation ───────────────────────────────────────────────────

class TestReconciliation:
    @pytest.mark.asyncio
    async def test_safe_to_seed(self):
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        res = report.results[0]
        assert res.classification == "safe_to_seed"
        assert res.live_vault_item_ids == ["V1", "V2"]
        assert res.live_price_minor == 2000
        assert res.live_currency == "USD"
        assert res.live_allow_download is True
        assert res.live_status == "APPROVED"
        assert res.wrote_definition is False  # dry-run default

    @pytest.mark.asyncio
    async def test_safe_to_map(self):
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[_def()], existing_mappings=[])
        res = report.results[0]
        assert res.classification == "safe_to_map"
        assert res.existing_definition_id == 7
        assert res.existing_definition_version == 1

    @pytest.mark.asyncio
    async def test_needs_provider_refresh(self):
        fn = _get_drop(exc=DropfansRateLimitError("get_drop", "slow", 429))
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert report.results[0].classification == "needs_provider_refresh"

    @pytest.mark.asyncio
    async def test_ambiguous_on_mismatch(self):
        # Mapped definition no longer matches live (§12): operator action.
        fn = _get_drop(_live(price=99.0))  # live differs from mapped definition
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[_def()],
            existing_mappings=[{"definition_id": 7, "definition_version": 1,
                                "creator_id": 1, "dropfans_product_id": "D1",
                                "created_at": None}])
        res = report.results[0]
        assert res.classification == "ambiguous"
        assert res.reason == "definition_commercial_mismatch"

    @pytest.mark.asyncio
    async def test_unreconcilable_deleted(self):
        fn = _get_drop(exc=DropfansNotFoundError("get_drop", "gone", 404))
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert report.results[0].classification == "unreconcilable"

    def test_exact_match_semantics(self):
        live = verify_live_drop(_live())
        assert definitions_exact_match(_def(), live) is True
        assert definitions_exact_match(_def(price=9999), live) is False
        assert definitions_exact_match(_def(vids=("V1",), price=2000), live) is False
        assert definitions_exact_match(_def(allow_download=False), live) is False
        assert definitions_exact_match(_def(currency="EUR"), live) is False


# ── Operator semantics ───────────────────────────────────────────────

class TestOperatorSemantics:
    @pytest.mark.asyncio
    async def test_stable_key_required(self, monkeypatch):
        odb = FakeODB()
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn)
        res = report.results[0]
        assert res.classification == "safe_to_seed"
        assert res.reason == "awaiting_operator_input"
        assert odb.created == [] and odb.mapped == []

    @pytest.mark.asyncio
    async def test_stable_key_collision_fail_closed(self, monkeypatch):
        import asyncpg

        odb = FakeODB(
            definitions=[_def(did=7, vids=("V9",), price=9000)],
            create_exc=asyncpg.exceptions.UniqueViolationError("duplicate key"),
        )
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn,
            operator_inputs={"D1": OperatorSeedInput("black-lingerie", "SINGLE")})
        res = report.results[0]
        assert res.classification == "ambiguous"
        assert res.reason == "stable_key_collision"
        assert odb.mapped == []  # existing definition never overwritten

    @pytest.mark.asyncio
    async def test_offer_type_explicit(self, monkeypatch):
        odb = FakeODB()
        odb.install(monkeypatch)
        fn = _get_drop(_live(vids=("V1",)))  # one item: still needs explicit type
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn,
            operator_inputs={"D1": OperatorSeedInput("k1", "EVERYTHING_BUNDLE")})
        res = report.results[0]
        assert res.classification == "ambiguous"
        assert res.reason.startswith("invalid_operator_input")
        assert odb.created == []

    @pytest.mark.asyncio
    async def test_no_title_inference(self):
        fn = _get_drop(_live())  # title screams "Mega Bundle"
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        res = report.results[0]
        assert res.stable_key is None and res.offer_type is None
        assert res.classification == "safe_to_seed"

    def test_no_taxonomy_or_family_mechanics(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("parse_taxonomy", "VaultTaxonomy", "bundle_group",
                      "bundle_related", "db.families", "families.",
                      "create_content_family", "add_content_family_member",
                      "get_families_for_vault_item", "FanCommercialState",
                      "opportunity", "content_family"):
            assert token not in src

    @pytest.mark.asyncio
    async def test_family_remains_null(self, monkeypatch):
        odb = FakeODB()
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn,
            operator_inputs={"D1": OperatorSeedInput("k1", "SMALL_BUNDLE")})
        assert report.results[0].reason == "seeded_v1_active_mapped"
        assert odb.created[0][7]["family_id"] is None

    @pytest.mark.asyncio
    async def test_creator_isolation(self):
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            existing_definitions=[], existing_mappings=[])
        assert fn.calls == [(1, "D1")]
        assert report.creator_id == 1
        assert all(r.creator_id == 1 for r in report.results)


# ── Idempotency ──────────────────────────────────────────────────────

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_same_drop_rerun_no_duplicates(self, monkeypatch):
        odb = FakeODB(
            definitions=[_def()],
            mappings=[{"definition_id": 7, "definition_version": 1, "creator_id": 1,
                       "dropfans_product_id": "D1", "created_at": None}])
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn)
        res = report.results[0]
        assert res.classification == "safe_to_map"
        assert res.reason == "mapping_verified_current"
        assert odb.created == [] and odb.mapped == []

    @pytest.mark.asyncio
    async def test_same_mapping_rerun_verified(self, monkeypatch):
        odb = FakeODB(
            definitions=[_def()],
            mappings=[{"definition_id": 7, "definition_version": 1, "creator_id": 1,
                       "dropfans_product_id": "D1", "created_at": None}])
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        first = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn)
        second = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn)
        assert first.results[0].reason == second.results[0].reason == \
            "mapping_verified_current"
        assert odb.created == [] and odb.mapped == []

    @pytest.mark.asyncio
    async def test_unique_conflict_exact_match_maps(self, monkeypatch):
        import asyncpg

        # Concurrent seeder created the exact row after our read: our
        # prefetch holds only a stale same-key row, so create collides and
        # the reload discovers the exact match.
        odb = FakeODB(
            definitions=[_def(did=7, vids=("V9",), price=9000)],
            create_exc=asyncpg.exceptions.UniqueViolationError("duplicate key"),
            by_key=_def(did=8),
        )
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn,
            operator_inputs={"D1": OperatorSeedInput("black-lingerie", "SINGLE")})
        res = report.results[0]
        assert res.reason == "idempotent_conflict_exact_match"
        assert res.wrote_mapping is True and res.wrote_definition is False

    @pytest.mark.asyncio
    async def test_unique_conflict_mismatch_ambiguous(self, monkeypatch):
        import asyncpg

        odb = FakeODB(
            definitions=[_def(did=7, vids=("V9",), price=9000)],
            create_exc=asyncpg.exceptions.UniqueViolationError("duplicate key"),
        )
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn,
            operator_inputs={"D1": OperatorSeedInput("black-lingerie", "SINGLE")})
        assert report.results[0].reason == "stable_key_collision"

    @pytest.mark.asyncio
    async def test_no_duplicate_definition_or_version(self, monkeypatch):
        odb = FakeODB(definitions=[_def()])
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn,
            operator_inputs={"D1": OperatorSeedInput("other-key", "SINGLE")})
        # Exact definition exists → map path, never a second definition/version.
        assert report.results[0].classification == "safe_to_map"
        assert odb.created == []
        assert len(odb.mapped) == 1


# ── Safety ───────────────────────────────────────────────────────────

class TestSafety:
    @pytest.mark.asyncio
    async def test_dry_run_default_writes_nothing(self, monkeypatch):
        odb = FakeODB()
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, candidates=[_cand()], get_drop=fn,
            operator_inputs={"D1": OperatorSeedInput("k1", "SINGLE")},
            existing_definitions=[], existing_mappings=[])
        assert isinstance(report, CreatorReconciliation)
        assert report.dry_run is True
        assert odb.created == [] and odb.activated == [] and odb.mapped == []

    @pytest.mark.asyncio
    async def test_seeded_path_uses_offer_definition_dao(self, monkeypatch):
        odb = FakeODB()
        odb.install(monkeypatch)
        fn = _get_drop(_live())
        report = await reconcile_creator(
            1, dry_run=False, candidates=[_cand()], get_drop=fn,
            operator_inputs={"D1": OperatorSeedInput("k1", "SMALL_BUNDLE")})
        res = report.results[0]
        assert res.reason == "seeded_v1_active_mapped"
        assert res.wrote_definition and res.wrote_mapping
        creator, key, otype, vids, price, curr, dl, kw = odb.created[0]
        assert (creator, key, otype) == (1, "k1", "SMALL_BUNDLE")
        assert vids == ["V1", "V2"] and price == 2000 and curr == "USD" and dl is True
        assert kw["version"] == 1 and kw["status"] == "draft"
        assert odb.activated == [(1, 101)]
        assert odb.mapped == [(1, 101, 1, "D1")]

    def test_no_dropfans_write_methods_reachable(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        # Call/import mechanics (prose mentions in comments are fine).
        for token in ("create_drop(", "attach_drop(", "check_drop_status(",
                      "upload_vault_item(", "delete_vault_item(",
                      "create_post(", "import create_drop", "import attach_drop"):
            assert token not in src
        # The only provider surface used is the read-only GET path.
        assert "_service.get_drop" in src
        assert src.count("integrations.dropfans") <= 3

    def test_no_selector_or_llm_integration(self):
        import inspect

        import commerce.content_matching
        import commerce.product_selection
        import commerce.vault_ranking
        import commerce.drop_reconciliation as mod

        for legacy in (commerce.product_selection, commerce.content_matching,
                       commerce.vault_ranking):
            src = inspect.getsource(legacy).lower()
            assert "drop_reconciliation" not in src
            assert "offer_definition" not in src
        own = inspect.getsource(mod)
        for token in ("product_selection", "content_matching", "vault_ranking",
                      "llm_worker", "conversational", "present_offer"):
            assert token not in own

    def test_historical_offers_never_mutated(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "UPDATE commerce_offers" not in src
        assert "DELETE FROM commerce_offers" not in src
        assert "INSERT INTO commerce_offers" not in src
        # Candidate discovery reads CUIDs only.
        assert "SELECT DISTINCT dropfans_product_id" in src
        assert "FROM commerce_offers" in src

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
            "20260917020000_p33_vault_deliveries.sql",
            "20260917030000_p33_segments.sql",
            "20260917040000_p33_automation.sql",
        ]

    def test_audit_result_shape(self):
        res = ReconciliationResult(
            creator_id=1, dropfans_product_id="D1",
            classification=ReconciliationClassification.SAFE_TO_SEED.value,
            reason="live_verified_approved")
        assert res.creator_id == 1 and res.dropfans_product_id == "D1"
        assert res.existing_definition_id is None
        assert res.wrote_definition is False and res.wrote_mapping is False
